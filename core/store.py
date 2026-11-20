import json
import logging
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone

logger = logging.getLogger("Store")

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- Web login. password_hash NULL means the password is not set yet (registration pending).
-- The first user created is the admin.
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY,
    username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT,
    is_admin      INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL
);

-- Only a SHA-256 of the session token is stored.
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at INTEGER NOT NULL             -- unix seconds, pushed forward on every visit
);

-- Market helper
CREATE TABLE IF NOT EXISTS skills (
    skill      TEXT PRIMARY KEY,
    updated_at TEXT,
    recipes    TEXT NOT NULL            -- JSON list of recipes
);

CREATE TABLE IF NOT EXISTS market_prices (
    skill    TEXT NOT NULL,
    item_key TEXT NOT NULL,
    price    REAL,                      -- NULL: not on the market
    PRIMARY KEY (skill, item_key)
);

CREATE TABLE IF NOT EXISTS manual_prices (
    item_key TEXT PRIMARY KEY,
    price    REAL NOT NULL
);

-- Market refresh requests. The web server queues a job, the Python worker runs it and
-- reports progress here. One row per skill. State: queued, running, done, error.
CREATE TABLE IF NOT EXISTS refresh_jobs (
    skill        TEXT PRIMARY KEY,
    state        TEXT NOT NULL,
    progress     TEXT NOT NULL DEFAULT '',
    error        TEXT,
    requested_at INTEGER NOT NULL,          -- unix seconds
    started_at   INTEGER,
    finished_at  INTEGER
);

-- Arcana sell tracker: sale prices in whole gold (500.6M is 500600000). One list per user.
CREATE TABLE IF NOT EXISTS arcana_prices (
    id         INTEGER PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    price      INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

-- Potion crafting tracker. Players belong to a user; orders, seeds, plantings, drops and
-- hand-overs belong to a user through their player. A name is unique per user.
CREATE TABLE IF NOT EXISTS players (
    id         INTEGER PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name       TEXT NOT NULL COLLATE NOCASE,
    created_at TEXT NOT NULL,
    UNIQUE (user_id, name)
);

CREATE TABLE IF NOT EXISTS orders (
    id         INTEGER PRIMARY KEY,
    player_id  INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    skill      TEXT NOT NULL,
    potion_key TEXT NOT NULL,
    quantity   INTEGER NOT NULL DEFAULT 1,   -- 0: unlimited, output follows from the ingredients
    created_at TEXT NOT NULL
);

-- Potions handed to the player, one row per hand-over. They sum up per order.
CREATE TABLE IF NOT EXISTS potions_given (
    id         INTEGER PRIMARY KEY,
    order_id   INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    quantity   INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

-- Seeds received for an order. Planted in segments, see plantings.
CREATE TABLE IF NOT EXISTS seeds (
    id             INTEGER PRIMARY KEY,
    order_id       INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    item_key       TEXT NOT NULL,       -- ingredient (herb) the seeds grow
    total_received INTEGER NOT NULL,
    created_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS plantings (
    id         INTEGER PRIMARY KEY,
    seed_id    INTEGER NOT NULL REFERENCES seeds(id) ON DELETE CASCADE,
    planted    INTEGER NOT NULL,
    harvested  INTEGER,                 -- NULL until the yield is known
    inv_before INTEGER,                 -- own herbs in the inventory before the harvest
    inv_after  INTEGER,                 -- ... and after it; both set: harvested = after - before
    created_at TEXT NOT NULL
);

-- Monster drop ingredients given by the player or bought by us.
CREATE TABLE IF NOT EXISTS drops (
    id         INTEGER PRIMARY KEY,
    order_id   INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    item_key   TEXT NOT NULL,
    quantity   INTEGER NOT NULL,
    source     TEXT NOT NULL CHECK (source IN ('given', 'bought')),
    price      REAL,                    -- unit price paid, for bought drops
    created_at TEXT NOT NULL
);
"""


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    """SQLite store. One short-lived connection per operation, safe across threads.

    This side owns the schema and writes market data. The Go web server (web/)
    reads and writes the rest (users, sessions, tracker, manual prices).

    The database is also the channel between the two: the web server queues rows in
    refresh_jobs, this side runs them, and meta.worker_heartbeat tells the web server
    that the worker is alive.
    """

    def __init__(self, db_path, legacy_json=None):
        self.path = db_path

        self.path.parent.mkdir(parents=True, exist_ok=True)

        # The Go web server and this process share the file; WAL lets readers and the writer overlap.
        with self.connect() as db:
            db.execute("PRAGMA journal_mode = WAL")

        with self.connect() as db:
            db.executescript(SCHEMA)

            # An earlier version kept one running total in orders.given. Keep it as the first entry.
            columns = {row["name"] for row in db.execute("PRAGMA table_info(orders)")}

            if "given" in columns:
                db.execute(
                    "INSERT INTO potions_given (order_id, quantity, created_at) "
                    "SELECT id, given, created_at FROM orders WHERE given > 0"
                )
                db.execute("ALTER TABLE orders DROP COLUMN given")

        # Databases from before the inventory counts have no such columns.
        with self.connect() as db:
            columns = {row["name"] for row in db.execute("PRAGMA table_info(plantings)")}

            for column in ("inv_before", "inv_after"):
                if column not in columns:
                    db.execute(f"ALTER TABLE plantings ADD COLUMN {column} INTEGER")

        self._migrate_owners()

        if legacy_json is not None:
            self._migrate_json(legacy_json)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")

        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    # ---- per-user trackers -------------------------------------------

    # Trackers used to be shared by everyone. A database from that time has players and
    # arcana_prices without user_id. SQLite cannot change a UNIQUE constraint in place, so
    # both tables are rebuilt (the documented 12-step way) and every existing row goes to
    # the first admin. Runs once: afterwards the column exists.
    OWNED_TABLES = {
        "players": (
            "CREATE TABLE players_new ("
            " id INTEGER PRIMARY KEY,"
            " user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,"
            " name TEXT NOT NULL COLLATE NOCASE,"
            " created_at TEXT NOT NULL,"
            " UNIQUE (user_id, name))",
            "INSERT INTO players_new (id, user_id, name, created_at) SELECT id, ?, name, created_at FROM players",
        ),
        "arcana_prices": (
            "CREATE TABLE arcana_prices_new ("
            " id INTEGER PRIMARY KEY,"
            " user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,"
            " price INTEGER NOT NULL,"
            " created_at TEXT NOT NULL)",
            "INSERT INTO arcana_prices_new (id, user_id, price, created_at) SELECT id, ?, price, created_at FROM arcana_prices",
        ),
    }

    def _migrate_owners(self):
        # Own connection: foreign keys must be off while tables are swapped, and that
        # pragma does not work inside a transaction.
        db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row

        try:
            todo = [
                table
                for table in self.OWNED_TABLES
                if "user_id" not in {row["name"] for row in db.execute(f"PRAGMA table_info({table})")}
            ]

            if not todo:
                return

            owner = db.execute("SELECT id FROM users ORDER BY is_admin DESC, id LIMIT 1").fetchone()
            rows = {table: db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in todo}

            if owner is None and any(rows.values()):
                raise RuntimeError("Tracker data exists but there is no user to give it to")

            owner_id = owner["id"] if owner else None

            db.execute("PRAGMA foreign_keys = OFF")
            db.execute("BEGIN IMMEDIATE")

            try:
                for table in todo:
                    create, copy = self.OWNED_TABLES[table]

                    db.execute(create)
                    db.execute(copy, (owner_id,))
                    db.execute(f"DROP TABLE {table}")
                    db.execute(f"ALTER TABLE {table}_new RENAME TO {table}")

                broken = db.execute("PRAGMA foreign_key_check").fetchall()

                if broken:
                    raise RuntimeError(f"Per-user migration left {len(broken)} broken foreign key(s)")

                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise

            logger.info(
                "Tracker data is now per user: %s given to user %s",
                ", ".join(f"{rows[t]} row(s) of {t}" for t in todo),
                owner_id,
            )
        finally:
            db.close()

    # ---- JSON migration ----------------------------------------------

    def _migrate_json(self, path):
        """Copy the old JSON store into SQLite once. The JSON file is left untouched."""
        with self.connect() as db:
            done = db.execute("SELECT 1 FROM meta WHERE key = 'json_migrated'").fetchone()

            if done or not path.exists():
                return

            try:
                with path.open("r", encoding="utf-8") as f:
                    data = json.load(f)
            except (OSError, json.JSONDecodeError) as error:
                logger.error("Cannot migrate %s: %s", path, error)
                return

            if not isinstance(data, dict):
                logger.error("Cannot migrate %s: unexpected layout", path)
                return

            skills = data.get("skills")
            manual = data.get("manual")

            skills = skills if isinstance(skills, dict) else {}
            manual = manual if isinstance(manual, dict) else {}

            for skill, entry in skills.items():
                if not isinstance(entry, dict):
                    continue

                market = entry.get("market")
                market = market if isinstance(market, dict) else {}

                db.execute(
                    "INSERT OR REPLACE INTO skills (skill, updated_at, recipes) VALUES (?, ?, ?)",
                    (skill, entry.get("updated_at"), json.dumps(entry.get("recipes") or [])),
                )

                for key, price in market.items():
                    db.execute(
                        "INSERT OR REPLACE INTO market_prices (skill, item_key, price) VALUES (?, ?, ?)",
                        (skill, key, price),
                    )

            for key, price in manual.items():
                if isinstance(price, (int, float)) and not isinstance(price, bool):
                    db.execute(
                        "INSERT OR REPLACE INTO manual_prices (item_key, price) VALUES (?, ?)",
                        (key, price),
                    )

            db.execute(
                "INSERT INTO meta (key, value) VALUES ('json_migrated', ?)",
                (now(),),
            )

            logger.info("Migrated %s into %s (%d skill(s), %d manual price(s))", path, self.path, len(skills), len(manual))

    # ---- worker channel ----------------------------------------------

    def heartbeat(self):
        with self.connect() as db:
            db.execute(
                "INSERT INTO meta (key, value) VALUES ('worker_heartbeat', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (str(int(time.time())),),
            )

    def fail_interrupted_jobs(self):
        """A job left 'running' belongs to a worker that is gone."""
        with self.connect() as db:
            db.execute(
                "UPDATE refresh_jobs SET state = 'error', progress = '', error = ?, finished_at = ? WHERE state = 'running'",
                ("Interrupted by a worker restart", int(time.time())),
            )

    def claim_job(self):
        """Take the oldest queued job and mark it running. Returns the skill or None."""
        with self.connect() as db:
            row = db.execute(
                "SELECT skill FROM refresh_jobs WHERE state = 'queued' ORDER BY requested_at, rowid LIMIT 1"
            ).fetchone()

            if row is None:
                return None

            db.execute(
                "UPDATE refresh_jobs SET state = 'running', progress = 'starting', error = NULL, started_at = ? "
                "WHERE skill = ? AND state = 'queued'",
                (int(time.time()), row["skill"]),
            )

            return row["skill"]

    def set_job_progress(self, skill, progress):
        with self.connect() as db:
            db.execute("UPDATE refresh_jobs SET progress = ? WHERE skill = ?", (progress, skill))

    def finish_job(self, skill, error=None):
        with self.connect() as db:
            db.execute(
                "UPDATE refresh_jobs SET state = ?, progress = '', error = ?, finished_at = ? WHERE skill = ?",
                ("error" if error else "done", error, int(time.time()), skill),
            )

    # ---- market refresh ----------------------------------------------

    def save_skill(self, skill, recipes, market):
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO skills (skill, updated_at, recipes) VALUES (?, ?, ?)",
                (skill, now(), json.dumps(recipes)),
            )
            db.execute("DELETE FROM market_prices WHERE skill = ?", (skill,))
            db.executemany(
                "INSERT INTO market_prices (skill, item_key, price) VALUES (?, ?, ?)",
                [(skill, key, price) for key, price in market.items()],
            )
