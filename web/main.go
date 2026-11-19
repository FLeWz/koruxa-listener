// koruxa-web serves the Herblore market helper and tracker.
//
// It only reads and writes the SQLite database, so it stays up when Chrome or
// the Python worker is not running. Market refreshes are handed to the Python
// worker over a Unix socket (see worker.go).
package main

import (
	"database/sql"
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

// The schema is created by the Python side (core/store.py) before this starts.
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

	app := &App{
		db:     db,
		auth:   newAuth(db),
		worker: newWorker(env("WORKER_SOCKET", "worker.sock")),
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

	// Started by the Python process: do not outlive it.
	if parent := os.Getppid(); env("KORUXA_PARENT", "") != "" {
		go func() {
			for range time.Tick(2 * time.Second) {
				if os.Getppid() != parent {
					logf("INFO", "Parent process is gone, shutting down")
					os.Exit(0)
				}
			}
		}()
	}

	logf("INFO", "Web server at http://%s/ (database %s)", addr, dbPath)

	if err := server.ListenAndServe(); err != nil {
		logf("ERROR", "Web server stopped: %v", err)
		os.Exit(1)
	}
}
