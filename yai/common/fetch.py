"""Shared fetcher for outbound fetches (tes.md section 2, "minimal safety").

- Only targets in the allowed host/port table (https; http only for insecure_http_hosts in tests)
- No redirects. Connect 5 s, read 30 s, overall deadline. Size limit
- The overall deadline is created once at the start and applies across retries, waits and reads
    (at the deadline the connection is closed and reading stops)
- No retry on 401/403 (permission failures). Network failures retry up to 3 times with backoff
  (C-27)
- Logs never contain URL query parts or userinfo (signed URL secrets, system.md section 8)
"""

from __future__ import annotations

import contextlib
import socket
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx


class FetchError(Exception):
    def __init__(self, reason: str, retryable: bool = False):
        super().__init__(reason)
        self.reason = reason
        self.retryable = retryable


def _without_userinfo(url: str) -> str:
    """Drop `user:pass@` from `scheme://user:pass@host/...` (string ops; works on broken URLs)."""
    i = url.find("://")
    if i < 0:
        return url
    start = i + 3
    end = next((j for j in range(start, len(url)) if url[j] in "/?#"), len(url))
    at = url.rfind("@", start, end)
    return url if at < 0 else url[:start] + url[at + 1 :]


def redact(url: str) -> str:
    """URL without query, fragment and userinfo (user:pass@) (for logs)."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return _without_userinfo(url.split("?", 1)[0].split("#", 1)[0])
    return f"{parts.scheme}://{parts.netloc.rpartition('@')[2]}{parts.path}"


def needs_redact(url: str) -> bool:
    """Whether redact changes the URL (it has a query, fragment or userinfo)."""
    return "?" in url or "#" in url or _without_userinfo(url) != url


def host_port(url: str) -> tuple[str, str]:
    """Broken URLs (port, IPv6 etc.) raise FetchError, not ValueError (callers can return 400)."""
    try:
        parts = urlsplit(url)
        hostname, port = parts.hostname, parts.port
    except ValueError as exc:
        raise FetchError("not a valid URL") from exc
    if hostname is None:
        raise FetchError("url has no host")
    port = port or (443 if parts.scheme == "https" else 80)
    return parts.scheme, f"{hostname}:{port}"


@contextlib.contextmanager
def stream(
    url: str, deadline: float, connect: float, read: float, method: str = "GET", **kw: Any
) -> Iterator[httpx.Response]:
    """Close the connection if not done by the deadline (works while blocked in a read)."""
    left = deadline - time.monotonic()
    if left <= 0:
        raise FetchError("too slow")
    timeout = httpx.Timeout(min(read, left), connect=min(connect, left))
    with (
        httpx.Client(follow_redirects=False, timeout=timeout) as client,
        client.stream(method, url, **kw) as resp,
    ):
        sock = resp.extensions["network_stream"].get_extra_info("socket")

        def cut() -> None:
            with contextlib.suppress(OSError):
                sock.shutdown(socket.SHUT_RDWR)

        timer = threading.Timer(max(0.0, deadline - time.monotonic()), cut)
        timer.daemon = True
        timer.start()
        try:
            yield resp
        except httpx.TransportError as exc:
            if time.monotonic() >= deadline:
                raise FetchError("too slow") from exc
            raise
        finally:
            timer.cancel()


@dataclass
class Fetcher:
    allow: list[str] = field(default_factory=lambda: ["drs.yai.test:443"])
    insecure_http_hosts: list[str] = field(default_factory=list)
    connect_timeout: float = 5.0
    read_timeout: float = 30.0
    retries: int = 3
    backoff: float = 0.5

    def check(self, url: str) -> None:
        scheme, hp = host_port(url)
        if scheme == "http" and hp in self.insecure_http_hosts:
            return  # http allowed by test settings; https uses the allow table below (D-055 (6))
        if scheme != "https":
            raise FetchError("only https is allowed")
        if hp not in self.allow:
            raise FetchError("host not allowed")

    def download(self, url: str, dest: Path, *, max_bytes: int, total_timeout: float) -> int:
        """Write the content of url to dest (a file not yet existing). Returns the size written."""
        self.check(url)
        deadline = time.monotonic() + total_timeout  # one deadline for everything, retries included
        last: FetchError | None = None
        for attempt in range(self.retries + 1):
            try:
                return self._once(url, dest, max_bytes=max_bytes, deadline=deadline)
            except FetchError as exc:
                dest.unlink(missing_ok=True)
                if not exc.retryable:
                    raise
                last = exc
                wait = self.backoff * (2**attempt)
                if time.monotonic() + wait >= deadline:
                    raise FetchError(f"too slow (last: {exc.reason})") from exc
                time.sleep(wait)
        assert last is not None
        raise last

    def _once(self, url: str, dest: Path, *, max_bytes: int, deadline: float) -> int:
        size = 0
        try:
            with stream(url, deadline, self.connect_timeout, self.read_timeout) as resp:
                if resp.status_code in (401, 403):
                    raise FetchError(f"denied ({resp.status_code})")
                if 300 <= resp.status_code < 400:
                    raise FetchError("redirect not followed")
                if resp.status_code >= 500:
                    raise FetchError(f"server error ({resp.status_code})", retryable=True)
                if resp.status_code != 200:
                    raise FetchError(f"status {resp.status_code}")
                with open(dest, "xb") as f:
                    for chunk in resp.iter_bytes():
                        size += len(chunk)
                        if size > max_bytes:
                            raise FetchError("too large")
                        if time.monotonic() > deadline:
                            raise FetchError("too slow")
                        f.write(chunk)
                if time.monotonic() > deadline:  # a cut at the deadline looks like an early end
                    raise FetchError("too slow")
        except httpx.TransportError as exc:
            raise FetchError(f"transport error ({type(exc).__name__})", retryable=True) from exc
        return size

    def get_small(self, url: str, *, max_bytes: int, total_timeout: float) -> bytes:
        """Fetch a small document (WES workflow_url)."""
        self.check(url)
        deadline = time.monotonic() + total_timeout
        body = b""
        try:
            with stream(url, deadline, self.connect_timeout, self.read_timeout) as resp:
                if resp.status_code != 200:
                    raise FetchError(f"status {resp.status_code}")
                for chunk in resp.iter_bytes():
                    body += chunk
                    if len(body) > max_bytes:
                        raise FetchError("too large")
                    if time.monotonic() > deadline:
                        raise FetchError("too slow")
                if time.monotonic() > deadline:  # a cut at the deadline looks like an early end
                    raise FetchError("too slow")
        except httpx.TransportError as exc:
            raise FetchError(f"transport error ({type(exc).__name__})") from exc
        return body
