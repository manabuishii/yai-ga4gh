"""Limit body size by counting while receiving (D-042).

Checking only Content-Length misses chunked requests, so wrap the ASGI receive, count what is
actually received and stop at the limit. JSON parsing comes after (never read a large body
fully, parse, and only then check the limit).
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import Request

from yai.common.errors import ApiError


def limited_request(request: Request, max_bytes: int, status: int = 400) -> Request:
    """Return a Request that reads the body only up to max_bytes (ApiError(status) beyond it)."""
    msg = f"request body larger than {max_bytes} bytes"
    length = request.headers.get("content-length")
    if length and length.isdigit() and int(length) > max_bytes:
        raise ApiError(status, msg)
    seen = 0
    receive = request.receive

    async def limited():
        nonlocal seen
        message = await receive()
        seen += len(message.get("body", b""))
        if seen > max_bytes:
            raise ApiError(status, msg)
        return message

    return Request(request.scope, receive=limited)


def json_body(max_bytes: int, status: int = 400):
    """For Depends: read up to the limit, then parse JSON. An empty body is None (as Body(None)).

    The dependency is async, so the body is read on the event loop; sync handlers still run in the
    threadpool (checks do not block other requests)."""

    async def dependency(request: Request) -> Any:
        raw = await limited_request(request, max_bytes, status).body()
        if not raw:
            return None
        try:
            return json.loads(raw)
        except (ValueError, RecursionError):
            raise ApiError(400, "request body is not valid JSON") from None

    return dependency


def too_deep(value: Any, limit: int) -> bool:
    """Whether array/object nesting exceeds limit levels. Counted with a stack, not recursion
    (never walk deep values with Python recursion, D-060 (2))."""
    stack = [(value, 1)]
    while stack:
        item, depth = stack.pop()
        if isinstance(item, dict):
            children: Any = item.values()
        elif isinstance(item, list):
            children = item
        else:
            continue
        if depth > limit:
            return True
        stack.extend((child, depth + 1) for child in children)
    return False
