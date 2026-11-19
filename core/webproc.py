import logging
import os
import subprocess
import threading
import time
from pathlib import Path

logger = logging.getLogger("Web")

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
BINARY = WEB_DIR / "bin" / "koruxa-web"

RESTART_DELAY = 5  # seconds before a crashed web server is started again


class WebProcess:
    """Runs the Go web server (built by `make build`) and forwards its log lines.

    The web server does not depend on Chrome: it is up as soon as it is
    started, whether or not the browser or the watcher work.
    """

    def __init__(self, config):
        self.config = config
        self.process = None
        self.stopping = False

    @staticmethod
    def built():
        if BINARY.exists():
            return True

        logger.error("Web server is not built (%s). Run `make build`, or start with `make run`.", BINARY)

        return False

    def _env(self):
        env = dict(os.environ)
        env.update(
            MARKET_DB=str(Path(self.config.market_db).resolve()),
            MARKET_HOST=str(self.config.market_host),
            MARKET_PORT=str(self.config.market_port),
            WORKER_SOCKET=str(Path(self.config.worker_socket).resolve()),
            KORUXA_PARENT="1",  # makes the web server exit when this process does
        )

        return env

    @staticmethod
    def _log_line(line):
        """Go prints "LEVEL message"; map it to the matching log level."""
        level, _, message = line.partition(" ")
        method = {"ERROR": logger.error, "WARNING": logger.warning}.get(level)

        if method:
            method("%s", message)
        else:
            logger.info("%s", line[5:] if level == "INFO" else line)

    def _run(self):
        while not self.stopping:
            self.process = subprocess.Popen(
                [str(BINARY)],
                cwd=WEB_DIR,
                env=self._env(),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )

            for line in self.process.stdout:
                self._log_line(line.rstrip())

            code = self.process.wait()

            if self.stopping:
                return

            logger.error("Web server exited with code %s. Restarting in %ss.", code, RESTART_DELAY)
            time.sleep(RESTART_DELAY)

    def start(self):
        if not self.built():
            return False

        threading.Thread(target=self._run, name="web", daemon=True).start()

        return True

    def stop(self):
        self.stopping = True

        if self.process and self.process.poll() is None:
            self.process.terminate()
