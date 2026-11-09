"""The WES DRS resolver goes through the same allowed host/port table as the shared fetcher.

Instead of DRS, a tiny HTTP receiver that only records requests is started.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from yai.common.fetch import Fetcher
from yai.wes.resolver import DrsResolver, ResolveError


class Recorder:
    def __init__(self, body: bytes):
        self.paths: list[str] = []
        rec = self

        class Handler(BaseHTTPRequestHandler):
            def _answer(self) -> None:
                rec.paths.append(self.path)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_GET = do_OPTIONS = do_POST = _answer

            def log_message(self, format, *args) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def netloc(self) -> str:
        return f"127.0.0.1:{self.port}"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


OBJ = json.dumps({"access_methods": [{"access_url": {"url": "https://data.test/x?sig=s"}}]})


@pytest.fixture
def drs():
    rec = Recorder(OBJ.encode())
    yield rec
    rec.close()


def resolver(rec: Recorder, allowed: bool) -> DrsResolver:
    fetcher = Fetcher(allow=[], insecure_http_hosts=[rec.netloc] if allowed else [])
    return DrsResolver(host_override={"drs.test": f"http://{rec.netloc}",
                                      "internal.test": f"http://{rec.netloc}"},
                       fetcher=fetcher)  # fmt: skip


def test_host_not_allowed_never_connects(drs):
    with pytest.raises(ResolveError, match=r"host not allowed|only https"):
        resolver(drs, allowed=False).resolve("drs://internal.test/obj1", None)
    assert drs.paths == []  # never connected


def test_internal_ip_is_not_reached_without_override(drs):
    r = DrsResolver(fetcher=Fetcher(allow=["drs.yai.test:443"]))
    with pytest.raises(ResolveError):
        r.resolve("drs://127.0.0.1/obj1", None)  # internal IP (not in the table)
    with pytest.raises(ResolveError):
        r.resolve("drs://user@drs.yai.test/obj1", None)  # userinfo is invalid
    assert drs.paths == []


def test_id_is_one_path_segment(drs):
    url = resolver(drs, allowed=True).resolve("drs://drs.test/../admin?x=1#f", None)
    assert url == "https://data.test/x?sig=s"
    assert drs.paths, "resolver should have asked the DRS"
    for path in drs.paths:
        assert path.startswith("/ga4gh/drs/v1/objects/..%2Fadmin%3Fx%3D1%23f"), path


def test_response_size_is_capped():
    rec = Recorder(b"{" + b" " * (2 * 1024 * 1024) + b"}")
    try:
        with pytest.raises(ResolveError, match="too large"):
            resolver(rec, allowed=True).resolve("drs://drs.test/obj1", None)
    finally:
        rec.close()


def test_trickling_drs_is_cut_at_the_deadline():
    """A trickling DRS is cut by the per-request deadline (same as the fetcher deadline)."""
    import time

    from tests.test_fetch import serve

    server = serve("trickle")
    try:
        netloc = f"127.0.0.1:{server.server_address[1]}"
        r = DrsResolver(host_override={"drs.test": f"http://{netloc}"}, timeout=1,
                        fetcher=Fetcher(allow=[], insecure_http_hosts=[netloc]))  # fmt: skip
        started = time.monotonic()
        with pytest.raises(ResolveError):
            r.resolve("drs://drs.test/obj1", None)
        assert time.monotonic() - started < 1.8
    finally:
        server.shutdown()
        server.server_close()
