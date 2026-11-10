"""Exercise the test Broker+Passport Issuer and Visa Issuer (the yai-aai server) over HTTP.

Check what they issue with the Clearinghouse components, so issuing and checking agree.
"""

from __future__ import annotations

import base64
import json
import time

import httpx
import pytest

from tests.servers import Server, free_port
from tests.stack import TES_CLIENT_SECRET, TEST_CLIENT_SECRET, WES_CLIENT_SECRET
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
        "scopes": ["tes.submit", "tes.read", "tes.cancel", "openid", "ga4gh_passport_v1"],
    },
    # also a second service in the chain: the TES exchanges the token it got for the DRS
    "yai-tes": {"secret": TES_CLIENT_SECRET, "audiences": ["yai-drs"], "scopes": ["drs.read"]},
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


def login(aai, user="alice", aud=(), passport_aud=()):
    r = httpx.post(aai.url + "/test/login", json={"username": user, "aud": list(aud),
                                                  "passport_aud": list(passport_aud)})  # fmt: skip
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
    assert set(claims["scope"].split()) == {"tes.submit", "tes.read", "tes.cancel"}
    bad_aud = {"grant_type": "client_credentials", "audience": "yai-wes"}
    r = httpx.post(aai.url + "/token", data=bad_aud, auth=("yai-wes", WES_CLIENT_SECRET))
    assert r.status_code == 400
    wrong_secret = httpx.post(aai.url + "/token", data=form, auth=("yai-wes", "nope"))
    assert wrong_secret.status_code == 401
    user_at = login(aai, aud=["yai-wes"])["access_tokens"]["yai-wes"]
    v2 = AccessTokenVerifier(iss, iss + ".well-known/jwks.json", "yai-wes", fetcher)
    assert v2.verify(user_at, now())["sub"] == "alice"


def exchange_at(aai, subject, client=("yai-wes", WES_CLIENT_SECRET), **over):
    """RFC 8693 exchange of an access token for the next service (D-067), as the given client."""
    form = {
        "grant_type": EXCHANGE,
        "requested_token_type": AT_TYPE,
        "subject_token": subject,
        "subject_token_type": AT_TYPE,
        "audience": "yai-tes",
    }
    form.update(over)
    form = {k: v for k, v in form.items() if v is not None}
    return httpx.post(aai.url + "/token", data=form, auth=client)


def test_AAI_EXCHANGE_AT(aai):
    """D-067: the WES exchanges the user's AT (issued to the WES) for an AT for the TES. The
    result names the user as sub and the WES in act.sub, carries the client's scopes for that
    audience, lives as long as a Passport by default, and passes the TES's verifier."""
    subject = login(aai, aud=["yai-wes"])["access_tokens"]["yai-wes"]
    r = exchange_at(aai, subject)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["issued_token_type"] == AT_TYPE and body["token_type"] == "Bearer"
    assert body["expires_in"] == 7200
    header, claims = unverified_parts(body["access_token"])
    assert header["typ"] == "at+jwt"
    assert claims["sub"] == "alice" and claims["aud"] == "yai-tes"
    assert claims["act"] == {"sub": "yai-wes"} and claims["client_id"] == "yai-wes"
    # the client's scopes; with openid ga4gh_passport_v1 the token is a PSAT the TES can exchange
    assert set(claims["scope"].split()) == {"tes.submit", "tes.read", "tes.cancel", "openid",
                                            "ga4gh_passport_v1"}  # fmt: skip
    fetcher = KeyFetcher(insecure_http_hosts=[f"127.0.0.1:{aai.port}"])
    v = AccessTokenVerifier(aai.root + "/", aai.root + "/.well-known/jwks.json", "yai-tes", fetcher)
    with pytest.raises(Exception, match="passport-scoped"):
        v.verify(body["access_token"], now())  # a PSAT: refused unless the service exchanges it
    v = AccessTokenVerifier(aai.root + "/", aai.root + "/.well-known/jwks.json", "yai-tes",
                            fetcher, passport_scoped=True)  # fmt: skip
    assert v.verify(body["access_token"], now())["act"]["sub"] == "yai-wes"
    # a narrower scope may be asked for; unknown ones are dropped
    r = exchange_at(aai, subject, scope="tes.read tes.delegate")
    assert r.json()["scope"] == "tes.read"
    # the exchanged token is for the TES, not for the WES: the WES cannot exchange it again
    r2 = exchange_at(aai, body["access_token"], audience="yai-drs")
    assert r2.status_code == 400 and r2.json()["error"] == "invalid_grant"


def test_NEG_EXCHANGE_AT(aai):
    """D-067: a client can only act for users who called it. A token issued to another service,
    a PSAT, a service's own token, or an audience the client may not hold are refused."""
    other = login(aai, aud=["yai-tes"])["access_tokens"]["yai-tes"]  # not issued to the WES
    r = exchange_at(aai, other)
    assert r.status_code == 400 and r.json()["error"] == "invalid_grant"
    psat = login(aai)["passport_scoped_access_token"]
    assert exchange_at(aai, psat).json()["error"] == "invalid_grant"
    subject = login(aai, aud=["yai-wes"])["access_tokens"]["yai-wes"]
    assert exchange_at(aai, subject, audience="yai-wes").json()["error"] == "invalid_target"
    assert exchange_at(aai, subject, audience=None).json()["error"] == "invalid_target"
    basic = ("yai-wes", WES_CLIENT_SECRET)
    svc = httpx.post(aai.url + "/token", auth=basic,
                     data={"grant_type": "client_credentials", "audience": "yai-tes"})  # fmt: skip
    r = exchange_at(aai, svc.json()["access_token"])
    assert r.status_code == 400 and r.json()["error"] == "invalid_grant"
    # the test client has no audiences: it cannot act for anyone
    r = exchange_at(aai, subject, client=("yai-test-client", TEST_CLIENT_SECRET))
    assert r.json()["error"] == "invalid_target"
    # actor_token is not part of yai's profile (the actor is the authenticated client)
    assert exchange_at(aai, subject, actor_token="x").json()["error"] == "invalid_request"
    # the passport exchange still requires a PSAT
    bad = exchange(aai, subject)
    assert bad.status_code == 400 and bad.json()["error"] == "invalid_grant"


def passport_as(aai, subject, client=("yai-tes", TES_CLIENT_SECRET), **over):
    """Passport exchange as the given client (AAI 1.2 "Conformance For Passport Issuers" 3)."""
    form = {
        "grant_type": EXCHANGE,
        "requested_token_type": PASSPORT_TYPE,
        "subject_token": subject,
        "subject_token_type": AT_TYPE,
    }
    form.update(over)
    form = {k: v for k, v in form.items() if v is not None}
    return httpx.post(aai.url + "/token", data=form, auth=client)


def test_AAI_PASSPORT_FROM_DELEGATED_PSAT(aai):
    """D-069: the AT the WES exchanges for a user (D-067) is a Passport-Scoped Access Token when
    the client's scopes include openid ga4gh_passport_v1. Only the client the PSAT's aud names
    (the TES) gets the user's Passport from it; the WES, which holds the token, cannot (AAI 1.2
    leaves this to the broker; this broker binds the exchange to aud). The Passport is the
    user's own (sub, Visas, the asked aud; no act)."""
    subject = login(aai, aud=["yai-wes"])["access_tokens"]["yai-wes"]
    psat = exchange_at(aai, subject).json()["access_token"]
    r = passport_as(aai, psat, audience="yai-drs")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["issued_token_type"] == PASSPORT_TYPE and body["token_type"] == "N_A"
    header, claims = unverified_parts(body["access_token"])
    assert header["typ"] == "vnd.ga4gh.passport+jwt"
    assert claims["sub"] == "alice" and claims["aud"] == "yai-drs" and "act" not in claims
    assert len(claims["ga4gh_passport_v1"]) == 1
    r = passport_as(aai, psat, client=("yai-wes", WES_CLIENT_SECRET))
    assert r.status_code == 400
    assert r.json()["error_description"] == "subject_token was not issued to this client"
    # the user's own AT for the TES, passport-scoped by the login (consent to fetch the Passport)
    own = login(aai, aud=["yai-tes"], passport_aud=["yai-tes"])["access_tokens"]["yai-tes"]
    assert passport_as(aai, own).status_code == 200
    # a narrowed exchange without the PSAT scopes yields a plain AT: no Passport from it
    plain = exchange_at(aai, subject, scope="tes.submit tes.read tes.cancel").json()
    assert passport_as(aai, plain["access_token"]).json()["error"] == "invalid_grant"
    # a service's own token never carries the PSAT scopes, whatever the client's scopes say
    svc = httpx.post(aai.url + "/token", auth=("yai-wes", WES_CLIENT_SECRET),
                     data={"grant_type": "client_credentials", "audience": "yai-tes"})  # fmt: skip
    assert "ga4gh_passport_v1" not in unverified_parts(svc.json()["access_token"])[1]["scope"]


def test_NEG_PASSPORT_FROM_PLAIN_AT(aai):
    """AAI 1.2: the subject of a Passport exchange must be a PSAT. A plain user AT (no
    ga4gh_passport_v1 scope), a service's own token and a revoked user's PSAT (D-070) are
    refused; client authentication is required; the login PSAT (aud = the login client) cannot
    be exchanged by another client (D-069)."""
    plain = login(aai, aud=["yai-tes"])["access_tokens"]["yai-tes"]
    r = passport_as(aai, plain)
    assert r.status_code == 400 and r.json()["error"] == "invalid_grant"
    login_psat = login(aai)["passport_scoped_access_token"]  # aud: yai-test-client
    r = passport_as(aai, login_psat)  # as yai-tes
    assert r.json()["error_description"] == "subject_token was not issued to this client"
    assert exchange(aai, login_psat).status_code == 200  # as yai-test-client
    svc = httpx.post(aai.url + "/token", auth=("yai-wes", WES_CLIENT_SECRET),
                     data={"grant_type": "client_credentials", "audience": "yai-tes"})  # fmt: skip
    assert passport_as(aai, svc.json()["access_token"]).json()["error"] == "invalid_grant"
    psat = login(aai, aud=["yai-tes"], passport_aud=["yai-tes"])["access_tokens"]["yai-tes"]
    r = httpx.post(aai.url + "/token", data={
        "grant_type": EXCHANGE, "requested_token_type": PASSPORT_TYPE,
        "subject_token": psat, "subject_token_type": AT_TYPE})  # fmt: skip
    assert r.status_code == 401  # no client authentication
    time.sleep(1.1)
    assert httpx.post(aai.url + "/test/revoke", json={"username": "alice"}).status_code == 200
    r = passport_as(aai, psat)
    assert r.json() == {"error": "invalid_grant", "error_description": "subject_token was revoked"}


def test_AAI_EXCHANGE_AT_NESTED(aai):
    """D-067: the next service exchanges the token it received (sub=alice, act.sub=yai-wes) for
    its own downstream audience; the new actor is outermost and the previous one nests
    (RFC 8693 section 4.1). The scopes are the second client's."""
    subject = login(aai, aud=["yai-wes"])["access_tokens"]["yai-wes"]
    for_tes = exchange_at(aai, subject).json()["access_token"]
    r = exchange_at(aai, for_tes, client=("yai-tes", TES_CLIENT_SECRET), audience="yai-drs")
    assert r.status_code == 200, r.text
    claims = unverified_parts(r.json()["access_token"])[1]
    assert claims["sub"] == "alice" and claims["aud"] == "yai-drs"
    assert claims["act"] == {"sub": "yai-tes", "act": {"sub": "yai-wes"}}
    assert claims["scope"] == "drs.read"
    # the WES cannot exchange the TES-addressed token for the DRS (not issued to it)
    assert exchange_at(aai, for_tes, audience="yai-drs").json()["error"] == "invalid_grant"


def test_revocation_covers_the_same_second(tmp_path, monkeypatch):
    """D-070: a token issued in the same second as the revocation is refused too (the cutoff is
    now + 1). Deterministic: the broker runs in-process on a clock that does not move."""
    from fastapi.testclient import TestClient

    from yai.aai import app as aai_app
    from yai.common.timeutil import Clock

    clock = Clock()
    clock.offset = 1_000_000  # a fixed, large offset; the test moves it by hand
    real_now = clock.now()
    clock.now = lambda: real_now  # type: ignore[method-assign]
    monkeypatch.setattr(aai_app, "Clock", lambda: clock)
    cfg = {"issuer": "http://127.0.0.1:1/", "keys_dir": str(tmp_path / "keys"), "users": USERS,
           "clients": CLIENTS, "test_endpoints": True}  # fmt: skip
    c = TestClient(aai_app.create_app(cfg))
    psat = c.post("/test/login", json={"username": "alice", "aud": []}).json()
    psat = psat["passport_scoped_access_token"]
    form = {"grant_type": EXCHANGE, "requested_token_type": PASSPORT_TYPE,
            "subject_token": psat, "subject_token_type": AT_TYPE}  # fmt: skip
    auth = ("yai-test-client", TEST_CLIENT_SECRET)
    assert c.post("/token", data=form, auth=auth).status_code == 200
    assert c.post("/test/revoke", json={"username": "alice"}).status_code == 200  # same second
    r = c.post("/token", data=form, auth=auth)
    assert r.status_code == 400 and r.json()["error_description"] == "subject_token was revoked"
    # a token issued one second after the revocation is fine
    clock.now = lambda: real_now + 1  # type: ignore[method-assign]
    later = c.post("/test/login", json={"username": "alice", "aud": []}).json()
    form["subject_token"] = later["passport_scoped_access_token"]
    assert c.post("/token", data=form, auth=auth).status_code == 200


def test_NEG_REVOKE_AT_EXCHANGE(aai):
    """D-070: after /test/revoke, tokens issued to the user before it are refused at every
    exchange (PSAT -> Passport, AT -> delegated AT); the user table is read again at each
    exchange. A new login lifts the revocation. What was already issued is not recalled."""
    old = login(aai, aud=["yai-wes"])
    psat, at = old["passport_scoped_access_token"], old["access_tokens"]["yai-wes"]
    assert exchange(aai, psat).status_code == 200
    delegated = exchange_at(aai, at).json()["access_token"]
    time.sleep(1.1)  # iat has whole seconds; a revoke in the same second must still cover it
    r = httpx.post(aai.url + "/test/revoke", json={"username": "alice"})
    assert r.status_code == 200 and r.json()["username"] == "alice"
    assert exchange(aai, psat).json()["error"] == "invalid_grant"
    assert exchange_at(aai, at).json()["error"] == "invalid_grant"
    fetcher = KeyFetcher(insecure_http_hosts=[f"127.0.0.1:{aai.port}"])
    v = AccessTokenVerifier(aai.root + "/", aai.root + "/.well-known/jwks.json", "yai-tes",
                            fetcher, passport_scoped=True)  # fmt: skip
    assert v.verify(delegated, now())["sub"] == "alice"  # already issued: valid until exp
    assert httpx.post(aai.url + "/test/revoke", json={"username": "nobody"}).status_code == 404
    time.sleep(1.1)  # the new login must be after the revocation second
    new = login(aai, aud=["yai-wes"])
    assert exchange(aai, new["passport_scoped_access_token"]).status_code == 200
    assert exchange_at(aai, new["access_tokens"]["yai-wes"]).status_code == 200
    assert exchange(aai, psat).status_code == 200  # the new login lifted it


def test_NEG_TEST_ENDPOINTS(serve, tmp_path):
    """D-054: test endpoints are registered only when enabled. /test/clock never exists. The visas
    endpoint exists only with test_visa_endpoint. Setting clock_offset_seconds blocks startup.
    Request bodies are capped at 1 MiB."""
    from yai.common import startup

    paths = [("post", "/test/login"), ("put", "/test/users/alice/visas"),
             ("get", "/untrusted-visa/jwks.json"), ("post", "/test/clock"),
             ("post", "/test/revoke")]  # fmt: skip

    def codes(s):
        return [httpx.request(m, s.url + p, json={"username": "alice", "visas": []}).status_code
                for m, p in paths]  # fmt: skip

    off = serve("aai", {"keys_dir": str(tmp_path / "k")}, name="off")
    assert codes(off) == [404, 404, 404, 404, 404]
    base = {"keys_dir": str(tmp_path / "k"), "users": USERS, "test_endpoints": True}
    on = serve("aai", base, name="on")
    assert codes(on) == [200, 404, 200, 404, 200]
    big = httpx.post(on.url + "/test/login", content=b'{"username": "' + b"a" * (2 << 20) + b'"}')
    assert big.status_code == 400 and "larger than" in big.text
    visas = serve("aai", {**base, "test_visa_endpoint": True}, name="visas")
    assert codes(visas) == [200, 200, 200, 404, 200]
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
