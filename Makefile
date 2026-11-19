# Run without Docker. Two processes share one SQLite database (MARKET_DB):
#
#   make run       activate the Python venv if needed, run main.py (the worker)
#   make run-web   build and run the Go web server (variables from .env)
#   make build     build web/bin/koruxa-web
#   make venv      create the venv and install the Python packages
#   make clean     remove the web build
#
# With Docker: docker compose up -d --build
#
# Pass VENV=/path/to/venv to use another venv. A venv that is already
# active in your shell is used as it is.

VENV ?= venv
BIN  := web/bin/koruxa-web

.PHONY: build run run-web venv clean

build: $(BIN)

$(BIN): $(wildcard web/*.go web/pages/*.html) web/go.mod web/go.sum
	cd web && go build -o bin/koruxa-web .

$(VENV)/bin/activate:
	python3 -m venv $(VENV)
	. $(VENV)/bin/activate && pip install -r requirements.txt

venv: $(VENV)/bin/activate

run: $(VENV)/bin/activate
	@if [ -n "$$VIRTUAL_ENV" ]; then \
		echo "Using active venv $$VIRTUAL_ENV"; \
	else \
		echo "Activating $(VENV)"; \
		. $(VENV)/bin/activate; \
	fi; \
	exec python main.py

run-web: build
	set -a; . ./.env; set +a; exec $(BIN)

clean:
	rm -rf web/bin
