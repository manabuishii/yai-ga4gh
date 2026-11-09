"""Exercise the test Broker+Passport Issuer and Visa Issuer (the yai-aai server) over HTTP.

Check what they issue with the Clearinghouse components, so issuing and checking agree.
"""

from __future__ import annotations

import base64
import json

import httpx
import pytest

from tests.servers import Server, free_port
from tests.stack import TEST_CLIENT_SECRET, WES_CLIENT_SECRET
from tests.tokens import now
from yai.aai.clearinghouse import AccessTokenVerifier, Clearinghouse, TrustConfig
from yai.aai.jwtutil import unverified_parts
from yai.aai.keyfetch import KeyFetcher

EXCHANGE = "urn:ietf:params:oauth:grant-type:token-exchange"
PASSPORT_TYPE = "urn:ga4gh:params:oauth:token-type:passport"
AT_TYPE = "urn:ietf:params:oauth:token-type:access_token"
DS1 = "https://yai.test/datasets/ds1"
USERS = {
    "alice": {"scopes": ["wes.run", "wes.read"], "visas": [{"value": DS1}]},
    "bob": {"visas": [{"value": "https://yai.test/datasets/ds2"}]},
    "carol": {},
    "mallory": {"visas": [{"value": DS1, "issuer": "untrusted"}]},
}
CLIENTS = {
    "yai-test-client": {"secret": TEST_CLIENT_SECRET},
    "yai-wes": {
        "secret": WES_CLIENT_SECRET,
        "audiences": ["yai-tes", "yai-drs"],
        "scopes": ["tes.submit", "tes.read", "tes.cancel", "tes.delegate"],
    },
}


@pytest.fixture
def aai(serve, tmp_path):
    port = free_port()
    issuer = f"http://127.0.0.1:{port}/"
    cfg = {
        "issuer": issuer,
        "keys_dir": str(tmp_path / "keys"),
        "users": USERS,
        "clients": CLIENTS,
        "test_endpoints": True,
        "test_visa_endpoint": True,
    }
    s = Server("aai", cfg, tmp_path / "aai", port=port).start()
    yield s
    s.stop()


def login(aai, user="alice", aud=()):
    r = httpx.post(aai.url + "/test/login", json={"username": user, "aud": list(aud)})
    r.raise_for_status()
    return r.json()


def exchange(aai, psat, **over):
    form = {
        "grant_type": EXCHANGE,
        "requested_token_type": PASSPORT_TYPE,
        "subject_token": psat,
        "subject_token_type": AT_TYPE,
    }
    form.update(over)
    form = {k: v for k, v in form.items() if v is not None}
    basic = base64.b64encode(f"yai-test-client:{TEST_CLIENT_SECRET}".encode()).decode()
    return httpx.post(aai.url + "/token", data=form, headers={"Authorization": f"Basic {basic}"})


def clearinghouse(aai) -> Clearinghouse:
    iss = aai.root + "/"
    trust = TrustConfig.from_dict(
        {
            "brokers": [{"iss": iss, "jwks_uri": iss + ".well-known/jwks.json"}],
            "visa_issuers": [{"iss": iss + "visa/", "jku_allow": [iss + "visa/jwks.json"]}],
            "trusted_sources": ["https://yai.test/dac"],
        }
    )
    return Clearinghouse(trust, KeyFetcher(insecure_http_hosts=[f"127.0.0.1:{aai.port}"]))


def test_discovery_and_jwks(aai):
    d = httpx.get(aai.url + "/.well-known/openid-configuration").json()
    assert d["issuer"] == aai.root + "/"
    keys = httpx.get(d["jwks_uri"]).json()["keys"]
    assert keys and all("d" not in k for k in keys)  # no private-key components


def test_AAI_FMT_04(aai):
    psat = login(aai)["passport_scoped_access_token"]
    header, claims = unverified_parts(psat)
    assert {"openid", "ga4gh_passport_v1"} <= set(claims["scope"].split())
    assert not any(k.startswith("ga4gh_") for k in claims)
    assert header["kid"] and header["alg"] == "ES256"


def test_AAI_ISS_01(aai):
    psat = login(aai)["passport_scoped_access_token"]
    r = exchange(aai, psat)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["issued_token_type"] == PASSPORT_TYPE
    # Negative cases: missing or wrong type, not a PSAT, no client authentication
    assert exchange(aai, psat, requested_token_type=None).status_code == 400
    assert exchange(aai, psat, requested_token_type=AT_TYPE).status_code == 400
    assert exchange(aai, psat, subject_token_type=PASSPORT_TYPE).status_code == 400
    assert exchange(aai, body["access_token"]).json()["error"] == "invalid_grant"
    at = login(aai, aud=["yai-wes"])["access_tokens"]["yai-wes"]
    assert exchange(aai, at).json()["error"] == "invalid_grant"  # no passport in scope
    no_client = httpx.post(
        aai.url + "/token",
        data={"grant_type": EXCHANGE, "requested_token_type": PASSPORT_TYPE,
              "subject_token": psat, "subject_token_type": AT_TYPE},
    )  # fmt: skip
    assert no_client.status_code == 401 and no_client.json()["error"] == "invalid_client"


def test_AAI_CLI_01(aai):
    psat = login(aai)
    r = exchange(aai, psat["passport_scoped_access_token"])
    for resp in (r, httpx.post(aai.url + "/test/login", json={"username": "alice"})):
        assert resp.headers["cache-control"] == "no-cache, no-store"
        assert resp.headers["pragma"] == "no-cache"
    bad = exchange(aai, "garbage")
    assert bad.headers["cache-control"] == "no-cache, no-store"


def test_NEG_CACHE_HEADERS(aai):
    form = {"grant_type": "client_credentials", "audience": "yai-tes"}
    r = httpx.post(aai.url + "/token", data=form, auth=("yai-wes", WES_CLIENT_SECRET))
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-cache, no-store" and r.headers["pragma"] == "no-cache"


def test_issued_passport_passes_clearinghouse(aai):
    ch = clearinghouse(aai)
    for user, expect in [("alice", True), ("bob", False), ("carol", False), ("mallory", False)]:
        psat = login(aai, user)["passport_scoped_access_token"]
        passport = exchange(aai, psat).json()["access_token"]
        header, claims = unverified_parts(passport)
        assert header["typ"] == "vnd.ga4gh.passport+jwt"
        assert claims["aud"] == ["yai-drs"]
        p = ch.verify_passport(passport, now())
        granted = ch.grants_dataset(p, DS1, now(), 3600) is not None
        assert granted is expect, user
    mallory = exchange(aai, login(aai, "mallory")["passport_scoped_access_token"])
    _, claims = unverified_parts(mallory.json()["access_token"])
    visa_header, _ = unverified_parts(claims["ga4gh_passport_v1"][0])
    assert visa_header["jku"].endswith("/untrusted-visa/jwks.json")


def test_client_credentials_and_user_at(aai):
    form = {"grant_type": "client_credentials", "audience": "yai-tes"}
    r = httpx.post(aai.url + "/token", data=form, auth=("yai-wes", WES_CLIENT_SECRET))
    at = r.json()["access_token"]
    iss = aai.root + "/"
    fetcher = KeyFetcher(insecure_http_hosts=[f"127.0.0.1:{aai.port}"])
    v = AccessTokenVerifier(iss, iss + ".well-known/jwks.json", "yai-tes", fetcher)
    claims = v.verify(at, now())
    assert set(claims["scope"].split()) == {"tes.submit", "tes.read", "tes.cancel", "tes.delegate"}
    bad_aud = {"grant_type": "client_credentials", "audience": "yai-wes"}
    r = httpx.post(aai.url + "/token", data=bad_aud, auth=("yai-wes", WES_CLIENT_SECRET))
    assert r.status_code == 400
    wrong_secret = httpx.post(aai.url + "/token", data=form, auth=("yai-wes", "nope"))
    assert wrong_secret.status_code == 401
    user_at = login(aai, aud=["yai-wes"])["access_tokens"]["yai-wes"]
    v2 = AccessTokenVerifier(iss, iss + ".well-known/jwks.json", "yai-wes", fetcher)
    assert v2.verify(user_at, now())["sub"] == "alice"


def test_NEG_TEST_ENDPOINTS(serve, tmp_path):
    """D-054: test endpoints are registered only when enabled. /test/clock never exists. The visas
    endpoint exists only with test_visa_endpoint. Setting clock_offset_seconds blocks startup.
    Request bodies are capped at 1 MiB."""
    from yai.common import startup

    paths = [("post", "/test/login"), ("put", "/test/users/alice/visas"),
             ("get", "/untrusted-visa/jwks.json"), ("post", "/test/clock")]  # fmt: skip

    def codes(s):
        return [httpx.request(m, s.url + p, json={"username": "alice", "visas": []}).status_code
                for m, p in paths]  # fmt: skip

    off = serve("aai", {"keys_dir": str(tmp_path / "k")}, name="off")
    assert codes(off) == [404, 404, 404, 404]
    base = {"keys_dir": str(tmp_path / "k"), "users": USERS, "test_endpoints": True}
    on = serve("aai", base, name="on")
    assert codes(on) == [200, 404, 200, 404]
    big = httpx.post(on.url + "/test/login", content=b'{"username": "' + b"a" * (2 << 20) + b'"}')
    assert big.status_code == 400 and "larger than" in big.text
    visas = serve("aai", {**base, "test_visa_endpoint": True}, name="visas")
    assert codes(visas) == [200, 200, 200, 404]
    bad_ttl = httpx.put(visas.url + "/test/users/alice/visas",
                        json={"visas": [{"value": "x", "ttl": 10**9}]})  # fmt: skip
    assert bad_ttl.status_code == 400
    for service in ("aai", "drs"):
        cfg = {"auth": {"mode": "anonymous"}, "clock_offset_seconds": 0}
        with pytest.raises(SystemExit, match="clock_offset_seconds is no longer supported"):
            startup.check(service, cfg)
    drs = serve("drs", {"auth": {"mode": "anonymous"}}, name="drs")
    assert httpx.post(drs.root + "/yai/test/clock", json={"offset": 1}).status_code == 404


def test_keys_persist_across_restart(serve, tmp_path):
    cfg = {"keys_dir": str(tmp_path / "k")}
    a = serve("aai", cfg, name="a")
    first = httpx.get(a.url + "/.well-known/jwks.json").json()
    a.stop()
    b = serve("aai", cfg, name="b")
    assert httpx.get(b.url + "/.well-known/jwks.json").json() == first
    key_file = next((tmp_path / "k" / "broker").glob("*.jwk"))
    assert oct(key_file.stat().st_mode & 0o777) == "0o600"
    assert "d" in json.loads(key_file.read_text())


def test_NEG_TOKEN_BODY_LIMIT(aai):
    """D-042 (same pattern as #31): /token also stops reading the body at 1 MiB and returns
    an OAuth-style 400."""

    def chunks():
        yield b"grant_type=client_credentials&pad="
        for _ in range(2):
            yield b"x" * (1 << 20)

    r = httpx.post(aai.url + "/token", content=chunks(),
                   headers={"content-type": "application/x-www-form-urlencoded"})  # fmt: skip
    assert r.status_code == 400 and r.json()["error"] == "invalid_request"
