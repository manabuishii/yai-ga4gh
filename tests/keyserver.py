"""A key endpoint (JWKS) for tests. Counts requests received (AAI-CH-07 checks "never called").

Behavior is set per path: jwks (returns JSON), redirect, large (over 64 KiB), slow (6 seconds).
"""

from __future__ import annotations

import contextlib
import json
import threading
import time
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


class KeyServer:
    def __init__(self) -> None:
        self.routes: dict[str, tuple[str, Any]] = {}
        self.hits: Counter[str] = Counter()
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:
                pass

            def do_GET(self) -> None:
                server.hits[self.path] += 1
                kind, value = server.routes.get(self.path, ("missing", None))
                if kind == "jwks":
                    self._send(200, json.dumps(value).encode())
                elif kind == "redirect":
                    self.send_response(302)
                    self.send_header("Location", value)
                    self.end_headers()
                elif kind == "large":
                    self._send(200, b'{"keys": [], "pad": "' + b"x" * 70_000 + b'"}')
                elif kind == "slow":
                    time.sleep(6)
                    self._send(200, json.dumps({"keys": []}).encode())
                elif kind == "drip":
                    # send 1 byte every 0.5 s forever (never hits the per-read timeout)
                    body = b'{"keys": [], "pad": "' + b"x" * 200 + b'"}'
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                        for i in range(len(body)):
                            self.wfile.write(body[i : i + 1])
                            self.wfile.flush()
                            time.sleep(0.5)
                elif kind == "down":
                    self._send(503, b"{}")
                else:
                    self._send(404, b"{}")

            def _send(self, status: int, body: bytes) -> None:
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                with contextlib.suppress(BrokenPipeError):
                    self.wfile.write(body)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    @property
    def netloc(self) -> str:
        return f"127.0.0.1:{self.port}"

    def url(self, path: str) -> str:
        return f"http://{self.netloc}{path}"

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
