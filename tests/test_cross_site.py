"""Refuse state-changing requests from other sites' pages (D-061).

Browsers send multipart and text/plain POSTs without preflight, so a page on another site could
submit runs and tasks to anonymous WES/TES. Only the browser-set Sec-Fetch-Site and Origin are
checked (py-tes, Sapporo and the test clients do not send them, so they are unaffected).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

ANON = {"mode": "anonymous"}


def app_of(service: str, tmp_path):
    import importlib

    cfg: dict = {"data_dir": str(tmp_path / service)}
    if service == "aai":
        cfg = {"keys_dir": str(tmp_path / "keys")}
    elif service == "tes":
        cfg |= {"auth": ANON, "files_root": str(tmp_path / "files")}
    elif service == "wes":
        cfg |= {"auth": ANON, "tes": {"url": "http://127.0.0.1:9/ga4gh/tes/v1"},
                "engine": {"output_root": str(tmp_path / "files")}}  # fmt: skip
    else:
        cfg |= {"auth": ANON}
    return importlib.import_module(f"yai.{service}.app").create_app(cfg)


POSTS = {"aai": "/token", "drs": "/ga4gh/drs/v1/objects", "tes": "/ga4gh/tes/v1/tasks",
         "wes": "/ga4gh/wes/v1/runs"}  # fmt: skip


@pytest.mark.parametrize("service", list(POSTS))
def test_NEG_CROSS_SITE(service, tmp_path):
    app = app_of(service, tmp_path)
    try:
        client = TestClient(app)
        path = POSTS[service]

        def post(**headers: str) -> int:
            return client.post(path, content=b"x", headers={"content-type": "text/plain",
                                                             **headers}).status_code  # fmt: skip

        for headers in ({"Sec-Fetch-Site": "cross-site"}, {"Sec-Fetch-Site": "same-site"},
                        {"Origin": "https://evil.example"}, {"Origin": "null"}):  # fmt: skip
            assert post(**headers) == 403, headers
        r = client.post(path, headers={"Origin": "https://evil.example"})
        assert r.json() == {"msg": "cross-site request refused", "status_code": 403}
        # Non-browser clients, same origin and read-only requests pass (only this layer refuses)
        assert post() != 403
        assert post(**{"Sec-Fetch-Site": "same-origin", "Origin": "http://testserver"}) != 403
        assert post(**{"Sec-Fetch-Site": "none"}) != 403
        # Different spellings of the default port are the same origin
        for origin, host in (("https://example.org", "example.org:443"),
                             ("https://example.org:443", "example.org"),
                             ("http://[::1]", "[::1]:80"),
                             ("HTTP://Example.ORG", "example.org")):  # fmt: skip
            assert post(Origin=origin, Host=host) != 403, (origin, host)
        for origin, host in (("https://example.org:8443", "example.org"),
                             ("http://example.org", "example.org:443"),
                             ("https://example.org.evil", "example.org")):  # fmt: skip
            assert post(Origin=origin, Host=host) == 403, (origin, host)
        assert client.get(path, headers={"Sec-Fetch-Site": "cross-site"}).status_code != 403
    finally:
        if service == "wes":
            app.state.lock.close()
