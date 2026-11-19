package main

import (
	"crypto/rand"
	"crypto/sha256"
	"crypto/subtle"
	"database/sql"
	"encoding/base64"
	"encoding/hex"
	"fmt"
	"regexp"
	"strings"
	"sync"
	"time"
	"unicode/utf8"

	"golang.org/x/crypto/scrypt"
)

const (
	sessionSeconds = 30 * 24 * 3600 // one month, renewed on every visit
	minPassword    = 8
	maxPassword    = 128

	// Failed attempts allowed per client and username inside the window.
	maxFailures   = 5
	failureWindow = 15 * time.Minute
)

var usernameRE = regexp.MustCompile(`^[A-Za-z0-9_.-]{3,32}$`)

// AuthError carries the HTTP status for a failed login or user action.
type AuthError struct {
	Status int
	Msg    string
}

func (e *AuthError) Error() string { return e.Msg }

func authErr(status int, msg string) error { return &AuthError{status, msg} }

type User struct {
	ID       int64  `json:"id"`
	Username string `json:"username"`
	IsAdmin  bool   `json:"is_admin"`
}

type UserRow struct {
	ID          int64  `json:"id"`
	Username    string `json:"username"`
	IsAdmin     bool   `json:"is_admin"`
	HasPassword bool   `json:"has_password"`
}

// Hashes use the format written by the earlier Python version: scrypt$salt$hash
// (n=2^14, r=8, p=1, 32 bytes), so existing passwords keep working.
func hashPassword(password string) (string, error) {
	salt := make([]byte, 16)
	if _, err := rand.Read(salt); err != nil {
		return "", err
	}

	key, err := scrypt.Key([]byte(password), salt, 1<<14, 8, 1, 32)
	if err != nil {
		return "", err
	}

	return fmt.Sprintf("scrypt$%x$%x", salt, key), nil
}

func verifyPassword(password, stored string) bool {
	parts := strings.Split(stored, "$")
	if len(parts) != 3 || parts[0] != "scrypt" {
		return false
	}

	salt, err1 := hex.DecodeString(parts[1])
	want, err2 := hex.DecodeString(parts[2])
	if err1 != nil || err2 != nil || len(want) == 0 {
		return false
	}

	got, err := scrypt.Key([]byte(password), salt, 1<<14, 8, 1, len(want))
	if err != nil {
		return false
	}

	return subtle.ConstantTimeCompare(got, want) == 1
}

func tokenHash(token string) string {
	sum := sha256.Sum256([]byte(token))
	return hex.EncodeToString(sum[:])
}

func now() string {
	return time.Now().UTC().Format("2006-01-02T15:04:05+00:00")
}

type Auth struct {
	db        *sql.DB
	dummyHash string // checked for unknown usernames so timing does not reveal them

	mu       sync.Mutex
	failures map[string][]time.Time
}

func newAuth(db *sql.DB) *Auth {
	dummy, _ := hashPassword("dummy password")

	return &Auth{db: db, dummyHash: dummy, failures: map[string][]time.Time{}}
}

func cleanUsername(value string) (string, error) {
	value = strings.TrimSpace(value)

	if !usernameRE.MatchString(value) {
		return "", authErr(400, "Username must be 3-32 characters: letters, digits, . _ -")
	}

	return value, nil
}

func cleanPassword(value string) (string, error) {
	if n := utf8.RuneCountInString(value); n < minPassword || n > maxPassword {
		return "", authErr(400, fmt.Sprintf("Password must be %d-%d characters", minPassword, maxPassword))
	}

	return value, nil
}

// ---- brute force protection ------------------------------------------

func (a *Auth) blocked(key string) bool {
	a.mu.Lock()
	defer a.mu.Unlock()

	cutoff := time.Now().Add(-failureWindow)
	recent := []time.Time{}

	for _, t := range a.failures[key] {
		if t.After(cutoff) {
			recent = append(recent, t)
		}
	}

	if len(recent) == 0 {
		delete(a.failures, key)
	} else {
		a.failures[key] = recent
	}

	return len(recent) >= maxFailures
}

func (a *Auth) fail(key string) {
	a.mu.Lock()
	defer a.mu.Unlock()

	a.failures[key] = append(a.failures[key], time.Now())
}

func (a *Auth) clear(key string) {
	a.mu.Lock()
	defer a.mu.Unlock()

	delete(a.failures, key)
}

// ---- login -----------------------------------------------------------

func (a *Auth) needsSetup() bool {
	var one int

	return a.db.QueryRow("SELECT 1 FROM users LIMIT 1").Scan(&one) == sql.ErrNoRows
}

// setup creates the admin. It works only while there are no users at all.
func (a *Auth) setup(username, password string) (int64, error) {
	username, err := cleanUsername(username)
	if err != nil {
		return 0, err
	}

	if password, err = cleanPassword(password); err != nil {
		return 0, err
	}

	hash, err := hashPassword(password)
	if err != nil {
		return 0, err
	}

	res, err := a.db.Exec(
		"INSERT INTO users (username, password_hash, is_admin, created_at) SELECT ?, ?, 1, ? WHERE NOT EXISTS (SELECT 1 FROM users)",
		username, hash, now(),
	)
	if err != nil {
		return 0, err
	}

	if n, _ := res.RowsAffected(); n == 0 {
		return 0, authErr(400, "Setup is already done")
	}

	return res.LastInsertId()
}

// login returns ("ok", id) or ("set_password", 0) when registration is pending.
func (a *Auth) login(username, password, client string) (string, int64, error) {
	if utf8.RuneCountInString(password) > maxPassword {
		return "", 0, authErr(400, "Invalid request")
	}

	username = strings.TrimSpace(username)
	key := "login|" + client + "|" + strings.ToLower(username)

	if a.blocked(key) {
		return "", 0, authErr(429, "Too many failed attempts. Try again later.")
	}

	var id int64
	var hash sql.NullString

	err := a.db.QueryRow("SELECT id, password_hash FROM users WHERE username = ?", username).Scan(&id, &hash)
	known := err == nil

	if err != nil && err != sql.ErrNoRows {
		return "", 0, err
	}

	if known && !hash.Valid {
		return "set_password", 0, nil
	}

	stored := a.dummyHash
	if known {
		stored = hash.String
	}

	if !verifyPassword(password, stored) || !known {
		a.fail(key)
		return "", 0, authErr(401, "Invalid username or password")
	}

	a.clear(key)

	return "ok", id, nil
}

// register sets the first password of a user the admin added.
func (a *Auth) register(username, password, client string) (int64, error) {
	key := "register|" + client

	if a.blocked(key) {
		return 0, authErr(429, "Too many failed attempts. Try again later.")
	}

	password, err := cleanPassword(password)
	if err != nil {
		return 0, err
	}

	hash, err := hashPassword(password)
	if err != nil {
		return 0, err
	}

	username = strings.TrimSpace(username)

	res, err := a.db.Exec("UPDATE users SET password_hash = ? WHERE username = ? AND password_hash IS NULL", hash, username)
	if err != nil {
		return 0, err
	}

	if n, _ := res.RowsAffected(); n == 0 {
		a.fail(key)
		return 0, authErr(400, "This username cannot be registered")
	}

	var id int64
	err = a.db.QueryRow("SELECT id FROM users WHERE username = ?", username).Scan(&id)

	return id, err
}

// ---- sessions --------------------------------------------------------

func (a *Auth) createSession(userID int64) (string, error) {
	raw := make([]byte, 32)
	if _, err := rand.Read(raw); err != nil {
		return "", err
	}

	token := base64.RawURLEncoding.EncodeToString(raw)
	current := time.Now().Unix()

	a.db.Exec("DELETE FROM sessions WHERE expires_at < ?", current)

	_, err := a.db.Exec(
		"INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
		tokenHash(token), userID, current+sessionSeconds,
	)

	return token, err
}

// userForToken returns the logged in user, or nil. A valid session is renewed.
func (a *Auth) userForToken(token string) *User {
	if token == "" {
		return nil
	}

	digest := tokenHash(token)
	current := time.Now().Unix()
	user := &User{}

	err := a.db.QueryRow(
		"SELECT u.id, u.username, u.is_admin FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token_hash = ? AND s.expires_at >= ?",
		digest, current,
	).Scan(&user.ID, &user.Username, &user.IsAdmin)
	if err != nil {
		return nil
	}

	a.db.Exec("UPDATE sessions SET expires_at = ? WHERE token_hash = ?", current+sessionSeconds, digest)

	return user
}

func (a *Auth) deleteSession(token string) {
	if token != "" {
		a.db.Exec("DELETE FROM sessions WHERE token_hash = ?", tokenHash(token))
	}
}

// ---- admin -----------------------------------------------------------

func (a *Auth) listUsers() ([]UserRow, error) {
	rows, err := a.db.Query("SELECT id, username, is_admin, password_hash IS NOT NULL FROM users ORDER BY id")
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	users := []UserRow{}

	for rows.Next() {
		var u UserRow
		if err := rows.Scan(&u.ID, &u.Username, &u.IsAdmin, &u.HasPassword); err != nil {
			return nil, err
		}

		users = append(users, u)
	}

	return users, rows.Err()
}

func (a *Auth) addUser(username string) (int64, error) {
	username, err := cleanUsername(username)
	if err != nil {
		return 0, err
	}

	res, err := a.db.Exec("INSERT INTO users (username, password_hash, is_admin, created_at) VALUES (?, NULL, 0, ?)", username, now())
	if err != nil {
		if strings.Contains(err.Error(), "UNIQUE") {
			return 0, authErr(400, "User already exists")
		}

		return 0, err
	}

	return res.LastInsertId()
}

// resetPassword empties the password: the user registers again on next login.
// The admin cannot be reset.
func (a *Auth) resetPassword(id int64) error {
	var admin bool

	err := a.db.QueryRow("SELECT is_admin FROM users WHERE id = ?", id).Scan(&admin)
	if err == sql.ErrNoRows {
		return authErr(404, "User not found")
	}

	if err != nil {
		return err
	}

	if admin {
		return authErr(400, "The admin password cannot be reset")
	}

	if _, err := a.db.Exec("UPDATE users SET password_hash = NULL WHERE id = ?", id); err != nil {
		return err
	}

	_, err = a.db.Exec("DELETE FROM sessions WHERE user_id = ?", id)

	return err
}
