"""A relay for tests (observer in the combined tests, D-023).

Sits between WES and TES/DRS and records the requests (INT-DRS-POST, INT-VIEW-FULL, ...).
With rewrite, it can rewrite the upstream JSON response (INT-LOG-URL, INT-TIME-FORMAT,
INT-PAGING-END; mimics other TES servers). hold waits before forwarding upstream (a hook to delay
creation); drop forwards upstream, then closes without a response (a hook to lose the response).
NEG-WES-SUBMIT-DUP.
The relay runs only inside the tests and is not part of the implementation.
"""

from __future__ import annotations

import contextlib
import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx

HOP = {"connection", "keep-alive", "transfer-encoding", "content-length", "host",
       "content-encoding"}  # fmt: skip


@dataclass
class Seen:
    method: str
    path: str
    query: dict[str, list[str]]
    headers: dict[str, str]
    body: bytes

    def json(self) -> Any:
        return json.loads(self.body or b"null")


# rewrite(request, status code, response JSON) -> None (unchanged) or (status code, JSON)
Rewrite = Callable[[Seen, int, Any], tuple[int, Any] | None]


class RecordingProxy:
    def __init__(self, upstream: str):
        self.upstream = upstream.rstrip("/")
        self.seen: list[Seen] = []
        self.rewrite: Rewrite | None = None
        self.hold: Callable[[Seen], float] | None = None  # wait seconds before sending (0 = none)
        self.drop: Callable[[Seen], bool] | None = None  # True: send, then close without reply
        self.lock = threading.Lock()
        proxy = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:
                pass

            def _any(self) -> None:
                length = int(self.headers.get("content-length") or 0)
                body = self.rfile.read(length) if length else b""
                parts = urlsplit(self.path)
                seen = Seen(self.command, parts.path, parse_qs(parts.query),
                            {k.lower(): v for k, v in self.headers.items()}, body)  # fmt: skip
                with proxy.lock:
                    proxy.seen.append(seen)
                if proxy.hold and (delay := proxy.hold(seen)) > 0:
                    time.sleep(delay)
                headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP}
                r = httpx.request(self.command, proxy.upstream + self.path, headers=headers,
                                  content=body, timeout=30)  # fmt: skip
                if proxy.drop and proxy.drop(seen):
                    self.close_connection = True
                    return
                status, content = r.status_code, r.content
                ctype = r.headers.get("content-type", "")
                if proxy.rewrite and "json" in ctype:
                    got = proxy.rewrite(seen, status, r.json())
                    if got is not None:
                        status, content = got[0], json.dumps(got[1]).encode()
                self.send_response(status)
                for k, v in r.headers.items():
                    if k.lower() not in HOP:
                        self.send_header(k, v)
                self.send_header("content-length", str(len(content)))
                self.end_headers()
                with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                    self.wfile.write(content)  # the peer may give up while held (hold)

            do_GET = do_POST = do_OPTIONS = do_DELETE = do_PUT = _any

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.root = f"http://127.0.0.1:{self.port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def netloc(self) -> str:
        return f"127.0.0.1:{self.port}"

    def clear(self) -> None:
        with self.lock:
            self.seen.clear()
        self.rewrite = self.hold = self.drop = None

    def requests(self, method: str | None = None, contains: str = "") -> list[Seen]:
        with self.lock:
            return [s for s in self.seen
                    if (method is None or s.method == method) and contains in s.path]  # fmt: skip

    def stop(self) -> None:
        self.server.shutdown()
