"""Key fetching and caching (aai.md section 7).

- Keys are cached in memory per (source URL, kid) (default 1 hour)
- Per source, refetch only for an unknown kid (default at most once a minute)
- https only (http only for hosts listed in the test setting `insecure_http_hosts`).
    No redirects. Responses up to 64 KiB, cut off after 5 seconds
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

import httpx
from joserfc.jwk import KeySet

from yai.aai.jwtutil import TokenError

MAX_BYTES = 64 * 1024
TIMEOUT = 5.0


class KeyFetcher:
    def __init__(
        self,
        *,
        cache_seconds: int = 3600,
        refetch_min_interval: int = 60,
        insecure_http_hosts: list[str] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        transport: httpx.BaseTransport | None = None,
    ):
        self.cache_seconds = cache_seconds
        self.refetch_min_interval = refetch_min_interval
        self.insecure_http_hosts = set(insecure_http_hosts or [])
        self.monotonic = monotonic
        self.transport = transport
        self._keys: dict[str, tuple[float, dict[str, Any]]] = {}  # url -> (fetch time, jwks)
        self._last_fetch: dict[str, float] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    def _lock_for(self, url: str) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(url, threading.Lock())

    def _check_url(self, url: str) -> None:
        try:
            parts = urlsplit(url)
        except ValueError as exc:  # e.g. a broken IPv6 (not a 500)
            raise TokenError("untrusted_jku", "key url is not a valid URL") from exc
        if parts.scheme == "https":
            return
        if parts.scheme == "http" and parts.netloc in self.insecure_http_hosts:
            return
        raise TokenError("untrusted_jku", "key url must be https")

    def _fetch(self, url: str) -> dict[str, Any]:
        self._check_url(url)
        self._last_fetch[url] = self.monotonic()
        # 5 seconds in total from the start (httpx timeout is per read, so it does not stop a peer
        # that keeps sending slowly)
        deadline = time.monotonic() + TIMEOUT
        try:
            with (
                httpx.Client(
                    follow_redirects=False, timeout=TIMEOUT, transport=self.transport
                ) as client,
                client.stream("GET", url) as resp,
            ):
                if resp.status_code != 200:
                    raise TokenError("key_unavailable", f"status {resp.status_code}")
                body = b""
                for chunk in resp.iter_bytes():  # as it arrives (a fixed size waits to fill)
                    body += chunk
                    if len(body) > MAX_BYTES:
                        raise TokenError("key_unavailable", "key set too large")
                    if time.monotonic() > deadline:
                        raise TokenError("key_unavailable", "key fetch too slow")
        except httpx.HTTPError as exc:
            raise TokenError("key_unavailable", type(exc).__name__) from exc
        try:
            jwks = httpx.Response(200, content=body).json()
            keys = jwks["keys"]
            if not isinstance(keys, list):
                raise TypeError
        except (ValueError, KeyError, TypeError) as exc:
            raise TokenError("key_unavailable", "bad key set") from exc
        self._keys[url] = (self.monotonic(), jwks)
        return jwks

    def _cached(self, url: str) -> dict[str, Any] | None:
        hit = self._keys.get(url)
        if hit and self.monotonic() - hit[0] < self.cache_seconds:
            return hit[1]
        return None

    @staticmethod
    def _pick(jwks: dict[str, Any], kid: str | None) -> dict[str, Any] | None:
        keys = [k for k in jwks.get("keys", []) if isinstance(k, dict)]
        if kid is None:
            # without kid, use the source's key only if it has exactly one (aai.md section 3)
            if len(keys) == 1:
                return keys[0]
            raise TokenError("bad_signature", "no kid and several keys")
        for k in keys:
            if k.get("kid") == kid:
                return k
        return None

    def key(self, url: str, kid: str | None) -> KeySet:
        # no concurrent fetches of one source; needed as handlers run in threads
        with self._lock_for(url):
            jwks = self._cached(url)
            found = self._pick(jwks, kid) if jwks else None
            if found is None:
                # keep the interval from the last attempt, cached or not, failures included
                # (do not fetch once per request while the source is down)
                last = self._last_fetch.get(url)
                if last is not None and self.monotonic() - last < self.refetch_min_interval:
                    reason = "bad_signature" if jwks is not None else "key_unavailable"
                    raise TokenError(reason, "unknown kid (refetch limited)")
                jwks = self._fetch(url)
                found = self._pick(jwks, kid)
                if found is None:
                    raise TokenError("bad_signature", "unknown kid")
        try:
            return KeySet.import_key_set({"keys": [found]})
        except Exception as exc:  # broken key
            raise TokenError("bad_signature", "bad key") from exc
