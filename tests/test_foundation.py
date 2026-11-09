"""Foundation: the 4 servers that return service-info, shape checks, and stub implementations
of the 4 roles."""

from __future__ import annotations

import httpx
import pytest

from tests import shape


@pytest.mark.parametrize(
    ("service", "artifact", "version"),
    [("drs", "drs", "1.5.0"), ("tes", "tes", "1.1.0"), ("wes", "wes", "1.1.0")],
)
def test_SI_ALL(serve, service, artifact, version):
    """C-22: the 3 service-info responses match service-info 1.0.0 (and each spec's shape)."""
    s = serve(service, {"auth": {"mode": "anonymous"}})
    r = httpx.get(s.url + "/service-info")
    assert r.status_code == 200
    shape.check(service, r)
    body = r.json()
    assert body["type"] == {"group": "org.ga4gh", "artifact": artifact, "version": version}


def test_aai_service_info(serve):
    s = serve("aai")
    r = httpx.get(s.url + "/service-info")
    assert r.status_code == 200
    assert r.json()["name"] == "yai-aai"


def test_shape_check_detects_wrong_shape(serve):
    """Negative case for the shape check itself: a response missing a required field (top-level
    maxBulkRequestLength) fails."""
    s = serve("drs", {"auth": {"mode": "anonymous"}})
    r = httpx.get(s.url + "/service-info")
    body = r.json()
    del body["maxBulkRequestLength"]
    broken = httpx.Response(200, json=body, request=r.request)
    with pytest.raises(Exception):  # noqa: B017
        shape.check("drs", broken)


def test_unknown_path_error_shape(serve):
    """Error shape {msg, status_code} (D-011)."""
    s = serve("tes", {"auth": {"mode": "anonymous"}})
    r = httpx.get(s.url + "/no-such-path")
    assert r.status_code == 404
    shape.check_error_shape(r)


@pytest.mark.parametrize("held", ["", "\n", "short"])
def test_NEG_EMPTY_SECRET(tmp_path, held):
    """Empty or short url-secret is not accepted as an HMAC key; startup fails
    (D-044).
    Same pattern: TES/WES page-secret (same load_secret) and empty AAI client secrets."""
    from yai.aai.app import create_app
    from yai.common.timeutil import Clock
    from yai.drs.access import DrsAccess

    (tmp_path / "url-secret").write_text(held)
    with pytest.raises(SystemExit, match="at least 32"):
        DrsAccess({"auth": {"mode": "anonymous"}}, Clock(), tmp_path)
    # AAI client secrets: at least 32 characters and not the sample value (D-055 (4), replaces
    # D-044 (1))
    for client_secret in (held, "x" * 31, "CHANGE-ME"):
        with pytest.raises(SystemExit, match="at least 32 characters"):
            create_app({"clients": {"c": {"secret": client_secret}}})


def test_missing_secret_is_made_long_and_private(tmp_path):
    from yai.common.config import load_secret

    secret = load_secret(tmp_path / "d" / "page-secret")
    assert len(secret) == 64 and (tmp_path / "d" / "page-secret").stat().st_mode & 0o777 == 0o600
    assert load_secret(tmp_path / "d" / "page-secret") == secret
