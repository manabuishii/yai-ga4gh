"""DRS resolver ((a) of system.md sections 3 and 4, D-007). Turns drs:// into signed URLs.

Used by the WES (its resolver part) and, when `drs.enabled` is set, by the TES for drs://
inputs it resolves itself (D-069).

- Hostname form drs://<host>/<id> and compact form drs://<prefix>:<accession> (local table,
  G-DRS-02)
- Only for unit tests, a "hostname -> real URL" override table (G-DRS-01)
- Check OPTIONS first; if None, GET without sending Passports (C-26). Only for PassportAuth,
    and only when (1) the DRS host is allowed for sending (2) passport_auth_issuers overlaps the
    Visa iss (3) redirects are not followed, Passports are sent in the POST body
    (DRS-AUTH-07)
- An AccessURL returned with headers is rejected (C-03)
- Targets are checked against the same allowed host/port table as the shared fetcher (on the URL
  after overrides).
    IDs are encoded as one path segment. Response size is limited
"""

from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

from yai.common.fetch import Fetcher, FetchError, stream

MAX_RESPONSE = 1024 * 1024  # limit for DRS responses (DrsObject, AccessURL)


class ResolveError(Exception):
    """status: the DRS's HTTP status when it answered (401/403: refused), 503 when it could not be
    reached in time, None for everything else (shape, policy of this resolver)."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


@dataclass
class DrsResolver:
    passport_hosts: list[str] = field(default_factory=lambda: ["drs.yai.test"])
    host_override: dict[str, str] = field(default_factory=dict)  # hostname -> real URL root
    compact: dict[str, str] = field(default_factory=dict)  # prefix -> URL pattern ({id})
    timeout: float = 10.0
    fetcher: Fetcher = field(default_factory=Fetcher)  # allowed targets (allowed hosts and ports)

    def base_url(self, uri: str) -> tuple[str, str, str]:
        """drs:// -> (DRS API root, object ID, hostname)."""
        if not uri.startswith("drs://"):
            raise ResolveError("not a drs uri")
        rest = uri[len("drs://") :]
        if "/" in rest:  # hostname form (DRS-URI-03)
            host, _, oid = rest.partition("/")
            if not host or not oid or ":" in host or "@" in host:
                raise ResolveError("bad hostname-based drs uri")  # a port is invalid (DRS-URI-02)
            root = self.host_override.get(host, f"https://{host}")
            return f"{root}/ga4gh/drs/v1", quote(oid, safe=""), host
        prefix, sep, accession = rest.partition(":")  # split at the first ":" (DRS-URI-04)
        if not sep or prefix not in self.compact:
            raise ResolveError("unknown compact identifier prefix")
        pattern = self.compact[prefix]
        url = pattern.replace("{id}", quote(accession, safe=""))
        api, _, oid = url.rpartition("/objects/")
        return api, oid, urlsplit(api).hostname or ""

    def resolve(self, uri: str, passports: list[str] | None) -> str:
        api, oid, host = self.base_url(uri)
        try:
            self.fetcher.check(api)  # before sending (no connection at all)
        except FetchError as exc:
            raise ResolveError(f"drs host: {exc.reason}") from exc

        def send(method: str, url: str, **kw: Any) -> tuple[int, bytes]:
            """self.timeout seconds in total per request (also slow senders)."""
            deadline = time.monotonic() + self.timeout
            try:
                with stream(url, deadline, self.timeout, self.timeout, method, **kw) as r:
                    body = b""
                    for chunk in r.iter_bytes():
                        body += chunk
                        if len(body) > MAX_RESPONSE:
                            raise ResolveError("DRS response too large")
                    if time.monotonic() > deadline:
                        raise ResolveError("DRS too slow", 503)
                    return r.status_code, body
            except FetchError as exc:
                raise ResolveError(f"DRS: {exc.reason}") from exc
            except httpx.TransportError as exc:
                raise ResolveError(f"DRS unreachable ({type(exc).__name__})", 503) from exc

        opts = self._json(send("OPTIONS", f"{api}/objects/{oid}"), allow_204=True)
        meta_types = (opts or {}).get("supported_types") or ["None"]
        if not isinstance(meta_types, list):
            raise ResolveError("DRS answered supported_types of an unexpected shape")
        use_passport = "PassportAuth" in meta_types
        if use_passport:
            body = {"passports": self._passports_for(host, opts or {}, passports)}
            obj = self._json(send("POST", f"{api}/objects/{oid}", json=body))
        else:
            obj = self._json(send("GET", f"{api}/objects/{oid}"))
        methods = obj.get("access_methods") or []
        if not isinstance(methods, list) or not all(isinstance(m, dict) for m in methods):
            raise ResolveError("DRS answered access_methods of an unexpected shape")
        for method in methods:
            if method.get("access_url"):
                if not isinstance(method["access_url"], dict):
                    raise ResolveError("DRS answered an access_url of an unexpected shape")
                return self._url_only(method["access_url"])
            if method.get("access_id"):
                if not isinstance(method["access_id"], str):
                    raise ResolveError("DRS answered an access_id of an unexpected shape")
                aid = quote(method["access_id"], safe="")
                auth = method.get("authorizations") or {}
                if not isinstance(auth, dict):
                    raise ResolveError("DRS answered authorizations of an unexpected shape")
                types = auth.get("supported_types") or []
                if not isinstance(types, list):
                    raise ResolveError("DRS answered supported_types of an unexpected shape")
                if "PassportAuth" in types:
                    body = {"passports": self._passports_for(host, auth, passports)}
                    r = send("POST", f"{api}/objects/{oid}/access/{aid}", json=body)
                else:
                    r = send("GET", f"{api}/objects/{oid}/access/{aid}")
                return self._url_only(self._json(r))
        raise ResolveError("no usable access method")

    def _passports_for(self, host: str, auth: dict[str, Any], passports: list[str] | None):
        if host not in self.passport_hosts:
            raise ResolveError(f"not allowed to send passports to {host}")
        if not passports:
            raise ResolveError("a passport is required for this input")
        listed = auth.get("passport_auth_issuers") or []
        if not isinstance(listed, list) or not all(isinstance(x, str) for x in listed):
            raise ResolveError("DRS answered passport_auth_issuers of an unexpected shape")
        issuers = set(listed)
        chosen = [p for p in passports if issuers & visa_issuers(p)]
        if not chosen:
            raise ResolveError("no passport has a visa from passport_auth_issuers")
        return chosen

    @staticmethod
    def _url_only(access_url: dict[str, Any]) -> str:
        if access_url.get("headers"):
            raise ResolveError("access url needs headers; cannot pass it to TES")  # C-03
        url = access_url.get("url")
        if not isinstance(url, str):
            raise ResolveError("access url has no url")
        return url

    @staticmethod
    def _json(resp: tuple[int, bytes], allow_204: bool = False) -> Any:
        status, body = resp
        if 300 <= status < 400:
            raise ResolveError("redirect not followed", status)
        if allow_204 and status in (204, 405):
            return None
        if status != 200:
            raise ResolveError(f"DRS answered {status}", status)
        try:
            value = json.loads(body)
        except ValueError as exc:
            raise ResolveError("DRS answered something that is not JSON") from exc
        if not isinstance(value, dict):
            raise ResolveError("DRS answered something that is not a JSON object")
        return value


def _claims(token: str) -> dict[str, Any]:
    try:
        part = token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    except (IndexError, ValueError):
        return {}


def visa_issuers(passport: str) -> set[str]:
    """Visa iss inside the Passport (used only to decide whether to send; DRS checks signatures)."""
    out = set()
    for visa in _claims(passport).get("ga4gh_passport_v1") or []:
        iss = _claims(visa).get("iss") if isinstance(visa, str) else None
        if isinstance(iss, str):
            out.add(iss)
    return out
