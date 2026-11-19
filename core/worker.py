import json
import logging
import os
import re
import socketserver
import threading
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

logger = logging.getLogger("Worker")

MAX_BODY = 4096
SKILL_RE = re.compile(r"^[a-z_]+$")


class UnixHTTPServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


def make_handler(market):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            # Unix socket peers have no address, so the default logger cannot be used.
            logger.info(format, *args)

        def send_json(self, status, payload):
            body = json.dumps(payload).encode()

            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def skill(self, value):
            if not isinstance(value, str) or not SKILL_RE.match(value):
                raise ValueError("Invalid skill")

            return value

        def do_GET(self):
            url = urlparse(self.path)

            try:
                if url.path == "/status":
                    skill = self.skill((parse_qs(url.query).get("skill") or ["herblore"])[0])
                    self.send_json(200, market.job_status(skill))
                else:
                    self.send_json(404, {"error": "Not found"})
            except ValueError as error:
                self.send_json(400, {"error": str(error)})

        def do_POST(self):
            try:
                if self.path == "/refresh":
                    length = int(self.headers.get("Content-Length") or 0)

                    if length <= 0 or length > MAX_BODY:
                        raise ValueError("Invalid request body")

                    body = json.loads(self.rfile.read(length))
                    skill = self.skill(body.get("skill") if isinstance(body, dict) else None)

                    self.send_json(200, market.start_refresh(skill))
                else:
                    self.send_json(404, {"error": "Not found"})
            except (ValueError, json.JSONDecodeError) as error:
                self.send_json(400, {"error": str(error)})
            except Exception:
                logger.exception("Request failed: %s", self.path)
                self.send_json(500, {"error": "Worker error"})

    return Handler


def start_worker(config, market):
    """Serve refresh jobs for the Go web server on a Unix socket (owner access only)."""
    path = str(config.worker_socket)

    try:
        os.unlink(path)
    except FileNotFoundError:
        pass

    old_umask = os.umask(0o177)

    try:
        server = UnixHTTPServer(path, make_handler(market))
    except OSError as error:
        logger.error("Worker socket failed to start: %s", error)
        return None
    finally:
        os.umask(old_umask)

    threading.Thread(target=server.serve_forever, name="worker", daemon=True).start()
    logger.info("Worker socket at %s", path)

    return server
