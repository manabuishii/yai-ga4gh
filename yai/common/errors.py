"""Error shape {msg, status_code} (spec for DRS and WES, D-011 for TES).

No-secrets rule (system.md section 8): msg never contains request bodies or headers.
Stack traces are never returned in responses.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("yai")


class ApiError(Exception):
    def __init__(self, status_code: int, msg: str, headers: dict[str, str] | None = None):
        super().__init__(msg)
        self.status_code = status_code
        self.msg = msg
        self.headers = headers or {}


def error_body(status_code: int, msg: str) -> dict:
    return {"msg": msg, "status_code": status_code}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            error_body(exc.status_code, exc.msg), exc.status_code, headers=exc.headers
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        msg = exc.detail if isinstance(exc.detail, str) else "error"
        return JSONResponse(error_body(exc.status_code, msg), exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        # body values are not returned; only location and kind
        where = [".".join(str(p) for p in e.get("loc", ())) for e in exc.errors()][:5]
        return JSONResponse(error_body(400, "invalid request: " + ", ".join(where)), 400)

    @app.exception_handler(Exception)
    async def _unexpected(_: Request, exc: Exception) -> JSONResponse:
        log.error("internal error: %s", type(exc).__name__)
        return JSONResponse(error_body(500, "internal error"), 500)
