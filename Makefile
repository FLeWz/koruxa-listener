# Build the Go web server and run the watcher (which starts it).
#
#   make build   build web/bin/koruxa-web
#   make run     build, activate the Python venv if needed, run main.py
#   make venv    create the venv and install the Python packages
#   make clean   remove the web build
#
# Pass VENV=/path/to/venv to use another venv. A venv that is already
# active in your shell is used as it is.

VENV ?= venv
BIN  := web/bin/koruxa-web

.PHONY: build run venv clean

build: $(BIN)

$(BIN): $(wildcard web/*.go web/pages/*.html) web/go.mod web/go.sum
	cd web && go build -o bin/koruxa-web .

$(VENV)/bin/activate:
	python3 -m venv $(VENV)
	. $(VENV)/bin/activate && pip install requests python-dotenv playwright

venv: $(VENV)/bin/activate

run: build $(VENV)/bin/activate
	@if [ -n "$$VIRTUAL_ENV" ]; then \
		echo "Using active venv $$VIRTUAL_ENV"; \
	else \
		echo "Activating $(VENV)"; \
		. $(VENV)/bin/activate; \
	fi; \
	exec python main.py

clean:
	rm -rf web/bin
