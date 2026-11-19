package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"net"
	"net/http"
	"time"
)

// Worker talks to the Python process over a Unix socket (HTTP over the socket).
// The worker does the work that needs Chrome: market refreshes.
type Worker struct {
	client *http.Client
}

var errWorkerDown = errors.New("The worker is not running")

func newWorker(socket string) *Worker {
	return &Worker{client: &http.Client{
		Timeout: 15 * time.Second,
		Transport: &http.Transport{
			DialContext: func(ctx context.Context, _, _ string) (net.Conn, error) {
				return (&net.Dialer{Timeout: 3 * time.Second}).DialContext(ctx, "unix", socket)
			},
		},
	}}
}

// call sends a request and returns the raw JSON answer.
func (w *Worker) call(method, path string, body any) (json.RawMessage, error) {
	var reader io.Reader

	if body != nil {
		raw, err := json.Marshal(body)
		if err != nil {
			return nil, err
		}

		reader = bytes.NewReader(raw)
	}

	req, err := http.NewRequest(method, "http://worker"+path, reader)
	if err != nil {
		return nil, err
	}

	req.Header.Set("Content-Type", "application/json")

	res, err := w.client.Do(req)
	if err != nil {
		return nil, errWorkerDown
	}

	defer res.Body.Close()

	data, err := io.ReadAll(io.LimitReader(res.Body, 1<<20))
	if err != nil {
		return nil, err
	}

	if res.StatusCode >= 400 {
		var failure struct {
			Error string `json:"error"`
		}

		json.Unmarshal(data, &failure)

		if failure.Error == "" {
			failure.Error = "Worker error"
		}

		return nil, userErr(failure.Error)
	}

	return data, nil
}

func (w *Worker) startRefresh(skill string) (json.RawMessage, error) {
	return w.call("POST", "/refresh", map[string]string{"skill": skill})
}

func (w *Worker) refreshStatus(skill string) (json.RawMessage, error) {
	return w.call("GET", "/status?skill="+skill, nil)
}
