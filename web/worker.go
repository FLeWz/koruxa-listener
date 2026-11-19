package main

import (
	"database/sql"
	"errors"
	"strconv"
	"time"
)

// Worker is the Python process that does the work needing Chrome: market refreshes.
// The two sides only talk through the database. A refresh is a row in refresh_jobs:
// this side queues it, the worker runs it and writes the progress back. The worker
// proves it is alive by updating meta.worker_heartbeat.
type Worker struct {
	db *sql.DB
}

var errWorkerDown = errors.New("The worker is not running")

// The worker beats every couple of seconds; this is how long a silence is tolerated.
const heartbeatTimeout = 30 * time.Second

func newWorker(db *sql.DB) *Worker { return &Worker{db: db} }

// RefreshJob is the status of a refresh, as shown to the page.
type RefreshJob struct {
	State    string  `json:"state"` // idle, queued, running, done, error
	Progress string  `json:"progress"`
	Error    *string `json:"error"`
}

func (w *Worker) alive() bool {
	var value string

	if err := w.db.QueryRow(`SELECT value FROM meta WHERE key = 'worker_heartbeat'`).Scan(&value); err != nil {
		return false
	}

	beat, err := strconv.ParseInt(value, 10, 64)

	return err == nil && time.Since(time.Unix(beat, 0)) < heartbeatTimeout
}

// startRefresh queues a refresh. One already queued or running for the skill is reported, not doubled.
func (w *Worker) startRefresh(skill string) (*RefreshJob, error) {
	if !w.alive() {
		return nil, errWorkerDown
	}

	_, err := w.db.Exec(`
		INSERT INTO refresh_jobs (skill, state, progress, error, requested_at, started_at, finished_at)
		VALUES (?, 'queued', 'queued', NULL, ?, NULL, NULL)
		ON CONFLICT(skill) DO UPDATE SET
			state = 'queued', progress = 'queued', error = NULL,
			requested_at = excluded.requested_at, started_at = NULL, finished_at = NULL
		WHERE refresh_jobs.state NOT IN ('queued', 'running')`,
		skill, time.Now().Unix())
	if err != nil {
		return nil, err
	}

	return w.refreshStatus(skill)
}

func (w *Worker) refreshStatus(skill string) (*RefreshJob, error) {
	job := &RefreshJob{}

	err := w.db.QueryRow(`SELECT state, progress, error FROM refresh_jobs WHERE skill = ?`, skill).
		Scan(&job.State, &job.Progress, &job.Error)

	if errors.Is(err, sql.ErrNoRows) {
		return &RefreshJob{State: "idle"}, nil
	}

	return job, err
}
