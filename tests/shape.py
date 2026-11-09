"""Check responses against OpenAPI (test-plan.md section 3). Reads the bundled build/*.openapi.json.

TES 4xx/5xx are not defined in the spec, so they are checked against the yai error shape
{msg, status_code} (D-011) instead.
"""

from __future__ import annotations

import json
import subprocess
import sys
from functools import cache
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from openapi_core import OpenAPI
from openapi_core.testing import MockRequest, MockResponse

ROOT = Path(__file__).resolve().parent.parent


@cache
def spec(api: str) -> OpenAPI:
    path = ROOT / "build" / f"{api}.openapi.json"
    if not path.exists():
        subprocess.run([sys.executable, str(ROOT / "tools" / "bundle_specs.py")], check=True)
    return OpenAPI.from_dict(json.loads(path.read_text(encoding="utf-8")))


def check_error_shape(resp: httpx.Response) -> None:
    body = resp.json()
    assert set(body) == {"msg", "status_code"}, body
    assert body["status_code"] == resp.status_code
    assert isinstance(body["msg"], str)


def check(api: str, resp: httpx.Response) -> None:
    """Check one response against the matching operation and status code in that API's OpenAPI."""
    if api == "tes" and resp.status_code >= 400:
        check_error_shape(resp)
        return
    req = resp.request
    url = urlsplit(str(req.url))
    args = dict(httpx.QueryParams(url.query).multi_items())
    minimal = args.get("view", "MINIMAL") == "MINIMAL"
    if api == "tes" and req.method == "GET" and "/tasks" in url.path and minimal:
        # G-TES-12: MINIMAL has only id and state (does not match the schema's required executors)
        body = resp.json()
        is_list = url.path.endswith("/tasks")
        tasks = body["tasks"] if is_list else [body]
        assert all(set(t) == {"id", "state"} for t in tasks), tasks
        assert not is_list or set(body) <= {"tasks", "next_page_token"}
        return
    request = MockRequest(
        f"{url.scheme}://{url.netloc}",
        req.method.lower(),
        url.path,
        args=args,
        headers=dict(req.headers),
    )
    response = MockResponse(
        resp.content,
        status_code=resp.status_code,
        content_type=resp.headers.get("content-type", "application/json").split(";")[0],
        headers=dict(resp.headers),
    )
    spec(api).validate_response(request, response)
