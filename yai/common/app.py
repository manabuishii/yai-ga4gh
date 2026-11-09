"""Shared steps for building the FastAPI app."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI

from yai.common.errors import install_error_handlers

UNSAFE = frozenset({"POST", "PUT", "PATCH", "DELETE"})
REFUSED = json.dumps({"msg": "cross-site request refused", "status_code": 403}).encode()


def base_app(title: str) -> FastAPI:
    app = FastAPI(title=title, docs_url=None, redoc_url=None, openapi_url=None)
    install_error_handlers(app)
    app.add_middleware(CrossSiteGuard)
    return app


def cross_site(headers: dict[str, str]) -> bool:
    """Whether the request comes from another site's page, per browser headers (D-061).
    Sec-Fetch-Site is cross-site or same-site, or Origin is present and differs from Host
    (including `null`).
    Behind the front proxy (Caddy) the scheme changes, so only host and port are compared.
    The port is filled from the Origin scheme's default before comparing (`example.org` and
    `example.org:443` are equal)."""
    if headers.get("sec-fetch-site") in ("cross-site", "same-site"):
        return True
    origin = headers.get("origin")
    if origin is None:
        return False  # non-browser clients (py-tes, Sapporo, tests) do not send them
    try:
        o = urlsplit(origin)
        default = {"http": 80, "https": 443}.get(o.scheme.lower())
        h = urlsplit("//" + headers.get("host", ""))
        if default is None or not o.hostname or not h.hostname:
            return True
        return (o.hostname, o.port or default) != (h.hostname, h.port or default)
    except ValueError:  # e.g. a broken port
        return True


class CrossSiteGuard:
    """ASGI layer that returns 403 for state-changing requests (POST, PUT, PATCH, DELETE) from
    another site's page, before auth and body. Browsers send multipart and text/plain POSTs without
    preflight, so another site's page could submit runs and tasks to an anonymous WES or TES."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] == "http" and scope["method"] in UNSAFE:
            headers = {k.decode("latin-1"): v.decode("latin-1") for k, v in scope["headers"]}
            if cross_site(headers):
                head = [(b"content-type", b"application/json"),
                        (b"content-length", str(len(REFUSED)).encode())]  # fmt: skip
                await send({"type": "http.response.start", "status": 403, "headers": head})
                await send({"type": "http.response.body", "body": REFUSED})
                return
        await self.app(scope, receive, send)
