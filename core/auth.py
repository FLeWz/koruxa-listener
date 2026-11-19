import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import threading
import time

from core.store import now

SESSION_SECONDS = 30 * 24 * 3600  # one month, renewed on every visit

USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")
MIN_PASSWORD = 8
MAX_PASSWORD = 128

# Failed attempts allowed per (client, username) inside the window.
MAX_FAILURES = 5
FAILURE_WINDOW = 15 * 60


class AuthError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def hash_password(password):
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)

    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password, stored):
    try:
        _, salt, digest = stored.split("$")
        expected = bytes.fromhex(digest)
        actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1, dklen=len(expected))
    except (ValueError, TypeError):
        return False

    return hmac.compare_digest(actual, expected)


# Checked for unknown usernames so the response time does not reveal whether a user exists.
DUMMY_HASH = hash_password("dummy password")


def clean_username(value):
    if not isinstance(value, str) or not USERNAME_RE.match(value.strip()):
        raise AuthError(400, "Username must be 3-32 characters: letters, digits, . _ -")

    return value.strip()


def clean_password(value):
    if not isinstance(value, str) or not MIN_PASSWORD <= len(value) <= MAX_PASSWORD:
        raise AuthError(400, f"Password must be {MIN_PASSWORD}-{MAX_PASSWORD} characters")

    return value


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


class Auth:
    """Users, password hashing and sessions, on top of the SQLite store."""

    def __init__(self, store):
        self.store = store

        self.failures = {}
        self.failures_lock = threading.Lock()

    # ---- brute force protection ----------------------------------------

    def _blocked(self, key):
        cutoff = time.time() - FAILURE_WINDOW

        with self.failures_lock:
            recent = [t for t in self.failures.get(key, []) if t > cutoff]

            if recent:
                self.failures[key] = recent
            else:
                self.failures.pop(key, None)

            return len(recent) >= MAX_FAILURES

    def _fail(self, key):
        with self.failures_lock:
            self.failures.setdefault(key, []).append(time.time())

    def _clear(self, key):
        with self.failures_lock:
            self.failures.pop(key, None)

    # ---- login ---------------------------------------------------------

    def needs_setup(self):
        with self.store.connect() as db:
            return db.execute("SELECT 1 FROM users LIMIT 1").fetchone() is None

    def setup(self, username, password):
        """Create the admin. Works only while there are no users at all."""
        username = clean_username(username)
        password = clean_password(password)

        with self.store.connect() as db:
            cursor = db.execute(
                "INSERT INTO users (username, password_hash, is_admin, created_at) "
                "SELECT ?, ?, 1, ? WHERE NOT EXISTS (SELECT 1 FROM users)",
                (username, hash_password(password), now()),
            )

            if cursor.rowcount == 0:
                raise AuthError(400, "Setup is already done")

            return cursor.lastrowid

    def login(self, username, password, client):
        """Return ("ok", user_id), or ("set_password", None) when registration is pending."""
        if not isinstance(username, str) or not isinstance(password, str) or len(password) > MAX_PASSWORD:
            raise AuthError(400, "Invalid request")

        key = ("login", client, username.strip().lower())

        if self._blocked(key):
            raise AuthError(429, "Too many failed attempts. Try again later.")

        with self.store.connect() as db:
            user = db.execute("SELECT id, password_hash FROM users WHERE username = ?", (username.strip(),)).fetchone()

        if user is not None and user["password_hash"] is None:
            return "set_password", None

        stored = user["password_hash"] if user else DUMMY_HASH

        if not verify_password(password, stored) or user is None:
            self._fail(key)
            raise AuthError(401, "Invalid username or password")

        self._clear(key)
        return "ok", user["id"]

    def register(self, username, password, client):
        """Set the first password of a user the admin added. Returns the user id."""
        key = ("register", client)

        if self._blocked(key):
            raise AuthError(429, "Too many failed attempts. Try again later.")

        password = clean_password(password)

        if not isinstance(username, str):
            raise AuthError(400, "Invalid request")

        with self.store.connect() as db:
            cursor = db.execute(
                "UPDATE users SET password_hash = ? WHERE username = ? AND password_hash IS NULL",
                (hash_password(password), username.strip()),
            )

            if cursor.rowcount == 0:
                self._fail(key)
                raise AuthError(400, "This username cannot be registered")

            return db.execute("SELECT id FROM users WHERE username = ?", (username.strip(),)).fetchone()["id"]

    # ---- sessions ------------------------------------------------------

    def create_session(self, user_id):
        token = secrets.token_urlsafe(32)

        with self.store.connect() as db:
            db.execute("DELETE FROM sessions WHERE expires_at < ?", (int(time.time()),))
            db.execute(
                "INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
                (token_hash(token), user_id, int(time.time()) + SESSION_SECONDS),
            )

        return token

    def user_for_token(self, token):
        """The logged in user for a session cookie, or None. A valid session is renewed."""
        if not token:
            return None

        digest = token_hash(token)
        current = int(time.time())

        with self.store.connect() as db:
            row = db.execute(
                "SELECT u.id, u.username, u.is_admin FROM sessions s JOIN users u ON u.id = s.user_id "
                "WHERE s.token_hash = ? AND s.expires_at >= ?",
                (digest, current),
            ).fetchone()

            if row is None:
                return None

            db.execute("UPDATE sessions SET expires_at = ? WHERE token_hash = ?", (current + SESSION_SECONDS, digest))

        return {"id": row["id"], "username": row["username"], "is_admin": bool(row["is_admin"])}

    def delete_session(self, token):
        if token:
            with self.store.connect() as db:
                db.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash(token),))

    # ---- admin ---------------------------------------------------------

    def list_users(self):
        with self.store.connect() as db:
            rows = db.execute("SELECT id, username, is_admin, password_hash FROM users ORDER BY id").fetchall()

        return [
            {"id": r["id"], "username": r["username"], "is_admin": bool(r["is_admin"]), "has_password": r["password_hash"] is not None}
            for r in rows
        ]

    def add_user(self, username):
        username = clean_username(username)

        try:
            with self.store.connect() as db:
                cursor = db.execute(
                    "INSERT INTO users (username, password_hash, is_admin, created_at) VALUES (?, NULL, 0, ?)",
                    (username, now()),
                )
        except sqlite3.IntegrityError:
            raise AuthError(400, "User already exists")

        return {"id": cursor.lastrowid}

    def reset_password(self, user_id):
        """Back to empty: the user registers again on next login. The admin cannot be reset."""
        if isinstance(user_id, bool) or not isinstance(user_id, int):
            raise AuthError(400, "Invalid user")

        with self.store.connect() as db:
            user = db.execute("SELECT is_admin FROM users WHERE id = ?", (user_id,)).fetchone()

            if user is None:
                raise AuthError(404, "User not found")

            if user["is_admin"]:
                raise AuthError(400, "The admin password cannot be reset")

            db.execute("UPDATE users SET password_hash = NULL WHERE id = ?", (user_id,))
            db.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
