"""Overall deadline of the shared fetcher: slow, trickling or failing sources
stay within it."""

from __future__ import annotations

import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from yai.common.fetch import Fetcher, FetchError


def serve(kind: str):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"

        def do_GET(self) -> None:
            if kind == "slow_headers":
                time.sleep(5)
            if kind == "always_503":
                self.send_response(503)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.send_response(200)
            self.end_headers()  # no length (body runs until close)
            try:
                for _ in range(50):
                    self.wfile.write(b"x")
                    self.wfile.flush()
                    time.sleep(0.2)
            except OSError:
                pass

        def log_message(self, format, *args) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.mark.parametrize("kind", ["trickle", "slow_headers", "always_503"])
@pytest.mark.parametrize("how", ["download", "get_small"])
def test_total_timeout_holds(tmp_path, kind, how):
    server = serve(kind)
    try:
        netloc = f"127.0.0.1:{server.server_address[1]}"
        fetcher = Fetcher(allow=[], insecure_http_hosts=[netloc], retries=3, backoff=0.3)
        url = f"http://{netloc}/x"
        started = time.monotonic()
        with pytest.raises(FetchError):
            if how == "download":
                fetcher.download(url, tmp_path / "out", max_bytes=1 << 20, total_timeout=1)
            else:
                fetcher.get_small(url, max_bytes=1 << 20, total_timeout=1)
        assert time.monotonic() - started < 1.8
        assert not (tmp_path / "out").exists()  # no partial file left behind
    finally:
        server.shutdown()
        server.server_close()


def test_broken_urls_are_fetch_errors():
    """A malformed URL raises FetchError, not ValueError (callers map it to 400 / ResolveError)."""
    from yai.common.fetch import redact

    fetcher = Fetcher(allow=["h:443"])
    for url in ("https://h:bad/x", "https://[::1/x", "https://h:99999/x"):
        with pytest.raises(FetchError):
            fetcher.check(url)
    assert redact("https://[::1/x?sig=s") == "https://[::1/x"


def test_broken_url_claims_and_jku_are_not_500():
    from yai.aai.clearinghouse import url_claim_ok
    from yai.aai.jwtutil import TokenError
    from yai.aai.keyfetch import KeyFetcher

    assert url_claim_ok("https://[::1/x") is False
    with pytest.raises(TokenError):
        KeyFetcher()._check_url("https://[::1/x")


@pytest.mark.parametrize(
    ("url", "want"),
    [
        ("https://u:CANARY@h.test:8/x?sig=s", "https://h.test:8/x"),
        ("https://CANARY@[::1]:8/x", "https://[::1]:8/x"),
        ("http://a@CANARY@h.test/p", "http://h.test/p"),
        ("https://u:CANARY@[::1/x?sig=s", "https://[::1/x"),  # malformed URL (urlsplit raises)
        ("https://h.test/p@x", "https://h.test/p@x"),  # @ in the path is not userinfo
    ],
)
def test_NEG_URL_USERINFO(url, want):
    """Strip user:pass@ from recorded URLs, logs, finished tasks and runs."""
    from yai.common.fetch import needs_redact, redact
    from yai.common.logsetup import redact as redact_text
    from yai.tes.db import scrub_finished
    from yai.wes.db import scrub_request

    assert redact(url) == want
    assert needs_redact(url) == (want != url)
    assert "CANARY" not in redact_text(f"GET {url} 200")
    task = {"inputs": [{"path": "/d/a", "url": url}]}
    assert scrub_finished(task) == (want != url) and task["inputs"][0]["url"] == want
    request = {"workflow_url": url, "workflow_params": {"f": {"class": "File", "location": url}}}
    scrub_request(request)
    assert "CANARY" not in str(request)


def test_NEG_INSECURE_HTTP_ONLY():
    """insecure_http_hosts only allows http to those hosts. https goes through the allow table
    (D-055 (6))."""
    f = Fetcher(allow=[], insecure_http_hosts=["127.0.0.1:8402"])
    f.check("http://127.0.0.1:8402/x")
    with pytest.raises(FetchError, match="host not allowed"):
        f.check("https://127.0.0.1:8402/x")
    Fetcher(allow=["127.0.0.1:8402"], insecure_http_hosts=["127.0.0.1:8402"]).check(
        "https://127.0.0.1:8402/x"
    )
