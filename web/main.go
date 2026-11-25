// koruxa-web serves the Herblore market helper and tracker.
//
// It only reads and writes the SQLite database, so it stays up when Chrome or
// the Python worker is not running. Market refreshes are handed to the Python
// worker through the same database (see worker.go).
package main

import (
	"database/sql"
	"errors"
	"fmt"
	"net"
	"net/http"
	"os"
	"time"

	_ "modernc.org/sqlite"
)

// Log lines are "LEVEL message". The Python watcher adds the timestamp.
func logf(level, format string, args ...any) {
	fmt.Printf(level+" "+format+"\n", args...)
}

func env(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}

	return fallback
}

// The schema is created by the Python side (core/store.py). At a first start both
// run together, so wait for it to appear.
func waitForSchema(db *sql.DB) error {
	var found int

	for i := 0; i < 60; i++ {
		// The schema exists and the trackers are per user (the worker migrates older databases).
		err := db.QueryRow(`
			SELECT 1 WHERE EXISTS (SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'refresh_jobs')
			AND EXISTS (SELECT 1 FROM pragma_table_info('players') WHERE name = 'user_id')
			AND EXISTS (SELECT 1 FROM pragma_table_info('arcana_prices') WHERE name = 'user_id')
			AND EXISTS (SELECT 1 FROM pragma_table_info('base') WHERE name = 'player_id')
			AND EXISTS (SELECT 1 FROM pragma_table_info('drops') WHERE name = 'player_id')`).Scan(&found)
		if err == nil {
			return nil
		}

		if i == 0 {
			logf("INFO", "Waiting for the worker to create the database schema")
		}

		time.Sleep(time.Second)
	}

	return errors.New("the database has no schema: is the worker running?")
}

func openDB(path string) (*sql.DB, error) {
	db, err := sql.Open("sqlite", "file:"+path+"?_pragma=busy_timeout(30000)&_pragma=foreign_keys(1)")
	if err != nil {
		return nil, err
	}

	db.SetMaxOpenConns(4)

	return db, db.Ping()
}

func main() {
	dbPath := env("MARKET_DB", "db/koruxa-listener.db")
	addr := net.JoinHostPort(env("MARKET_HOST", "127.0.0.1"), env("MARKET_PORT", "8091"))

	db, err := openDB(dbPath)
	if err != nil {
		logf("ERROR", "Cannot open database %s: %v", dbPath, err)
		os.Exit(1)
	}

	if err := waitForSchema(db); err != nil {
		logf("ERROR", "%v", err)
		os.Exit(1)
	}

	app := &App{
		db:     db,
		auth:   newAuth(db),
		worker: newWorker(db),
	}

	server := &http.Server{
		Addr:              addr,
		Handler:           app,
		ReadHeaderTimeout: 10 * time.Second,
		ReadTimeout:       30 * time.Second,
		WriteTimeout:      60 * time.Second,
		IdleTimeout:       60 * time.Second,
		MaxHeaderBytes:    16 << 10,
	}

	logf("INFO", "Web server at http://%s/ (database %s)", addr, dbPath)

	if err := server.ListenAndServe(); err != nil {
		logf("ERROR", "Web server stopped: %v", err)
		os.Exit(1)
	}
}
