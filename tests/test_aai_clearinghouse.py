"""Tests for the Clearinghouse components (library). aai.md sections 3-4, obligations AAI-*, PP-*.

Key URLs point to a test KeyServer that counts requests. These are unit tests, so http is
allowed (insecure_http_hosts setting).
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from tests.keyserver import KeyServer
from tests.tokens import Issuer, alg_none, b64, hs256_with_public_key, now
from yai.aai.clearinghouse import AccessTokenVerifier, Clearinghouse, TrustConfig
from yai.aai.jwtutil import TokenError
from yai.aai.keyfetch import KeyFetcher

DS1 = "https://yai.test/datasets/ds1"
DS2 = "https://yai.test/datasets/ds2"
TTL = 3600


class FakeMonotonic:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def env():
    ks = KeyServer()
    broker = Issuer("https://broker.test/", kid="b1")
    visa = Issuer("https://visa.test/", kid="v1")
    evil = Issuer("https://evil.test/", kid="e1")
    ks.routes["/broker/jwks.json"] = ("jwks", broker.jwks())
    ks.routes["/visa/jwks.json"] = ("jwks", visa.jwks())
    ks.routes["/evil/jwks.json"] = ("jwks", evil.jwks())
    trust = {
        "brokers": [{"iss": broker.iss, "jwks_uri": ks.url("/broker/jwks.json")}],
        "visa_issuers": [{"iss": visa.iss, "jku_allow": [ks.url("/visa/jwks.json")]}],
        "trusted_sources": ["https://yai.test/dac"],
        "accepted_by": ["dac"],
    }
    mono = FakeMonotonic()

    def make(**over):
        cfg = {**trust, **over}
        fetcher = KeyFetcher(insecure_http_hosts=[ks.netloc], monotonic=mono)
        return Clearinghouse(TrustConfig.from_dict(cfg), fetcher)

    yield SimpleNamespace(
        ks=ks,
        broker=broker,
        visa=visa,
        evil=evil,
        ch=make(),
        make=make,
        mono=mono,
        jku=ks.url("/visa/jwks.json"),
        evil_jku=ks.url("/evil/jwks.json"),
    )
    ks.close()


def reason(fn, *args) -> str:
    with pytest.raises(TokenError) as exc:
        fn(*args)
    return exc.value.reason


# --- AAI-CH-01/02: trusted Brokers, Passport validity ---


def test_AAI_CH_01(env):
    good = env.broker.passport([])
    assert env.ch.verify_passport(good, now()).sub == "alice"
    stranger = Issuer("https://stranger.test/", kid="s1").passport([])
    assert reason(env.ch.verify_passport, stranger, now()) == "untrusted_issuer"
    # With no trusted Broker at all, no Passport passes
    assert reason(env.make(brokers=[]).verify_passport, good, now()) == "untrusted_issuer"


def test_AAI_CH_02(env):
    good = env.broker.passport([env.visa.visa(env.jku)])
    p = env.ch.verify_passport(good, now())
    assert len(p.visas) == 1
    head, body, sig = good.split(".")
    tampered = f"{head}.{body}.{'A' * len(sig)}"
    assert reason(env.ch.verify_passport, tampered, now()) == "bad_signature"
    expired = env.broker.passport([], ttl=-120)
    assert reason(env.ch.verify_passport, expired, now()) == "expired"


def test_AAI_FMT_01(env):
    assert env.ch.verify_passport(env.broker.passport([]), now())
    for typ in (None, "JWT", "at+jwt"):
        bad = env.broker.passport([], typ=typ)
        assert reason(env.ch.verify_passport, bad, now()) == "wrong_token_kind"


def test_AAI_FMT_02(env):
    assert env.ch.verify_passport(env.broker.passport([]), now()).visas == ()  # empty array is OK
    assert env.ch.verify_passport(env.broker.passport([], aud=None), now())  # aud is optional
    for name in ("iss", "sub", "iat", "exp", "ga4gh_passport_v1"):
        bad = env.broker.passport([], drop=(name,))
        assert reason(env.ch.verify_passport, bad, now()) in {"malformed", "untrusted_issuer"}
    not_array = env.broker.passport([], ga4gh_passport_v1="x")
    assert reason(env.ch.verify_passport, not_array, now()) == "malformed"
    # G-AAI-03: if aud is present it must include us. Rejecting a missing aud is configurable
    other = env.broker.passport([], aud=("yai-wes",))
    assert reason(env.ch.verify_passport, other, now()) == "wrong_audience"
    strict = env.make(passport_aud_required=True)
    no_aud = env.broker.passport([], aud=None)
    assert reason(strict.verify_passport, no_aud, now()) == "wrong_audience"


def test_AAI_FMT_03(env):
    rsa = Issuer("https://rsa.test/", kid="r1", kind="RSA")
    env.ks.routes["/rsa/jwks.json"] = ("jwks", rsa.jwks())
    ch = env.make(brokers=[{"iss": rsa.iss, "jwks_uri": env.ks.url("/rsa/jwks.json")}])
    assert ch.verify_passport(rsa.passport([]), now())  # RS256
    assert env.ch.verify_passport(env.broker.passport([]), now())  # ES256
    claims = {"iss": env.broker.iss, "sub": "alice", "iat": now(), "exp": now() + 60,
              "ga4gh_passport_v1": [], "aud": ["yai-drs"]}  # fmt: skip
    typ = "vnd.ga4gh.passport+jwt"
    none_token = alg_none(claims, typ=typ, kid="b1")
    assert reason(env.ch.verify_passport, none_token, now()) == "bad_signature"
    hs = hs256_with_public_key(env.broker, claims, typ=typ, kid="b1")
    assert reason(env.ch.verify_passport, hs, now()) == "bad_signature"


# --- Visa ---


def passport_with(env, *visas, **kw):
    return env.ch.verify_passport(env.broker.passport(list(visas), **kw), now())


def test_AAI_FMT_05(env):
    assert len(passport_with(env, env.visa.visa(env.jku)).visas) == 1
    no_jku = passport_with(env, env.visa.visa(None, scope="ga4gh_passport_v1"))
    assert no_jku.visas == () and no_jku.ignored == ("visa_access_token_ignored",)
    with_openid = passport_with(env, env.visa.visa(env.jku, scope="openid"))
    assert with_openid.visas == () and with_openid.ignored == ("wrong_token_kind",)


def test_NEG_VISA_KINDS(env):
    # Accept a Visa with jku and scope (no openid): scope is not used to tell token types apart
    assert len(passport_with(env, env.visa.visa(env.jku, scope="ga4gh_passport_v1")).visas) == 1
    future_nbf = passport_with(env, env.visa.visa(env.jku, nbf=now() + 600))
    assert future_nbf.visas == ()


def test_AAI_CH_05(env):
    assert len(passport_with(env, env.visa.visa(env.jku)).visas) == 1
    assert passport_with(env, env.visa.visa(env.jku, ttl=-120)).ignored == ("expired",)
    assert passport_with(env, env.visa.visa(env.jku, iat=now() + 600)).visas == ()
    # Accept within the clock-skew allowance (60 seconds)
    assert len(passport_with(env, env.visa.visa(env.jku, ttl=-30)).visas) == 1


def test_AAI_CH_07(env):
    """Visa with a jku not on the allow list: no request ever reaches that jku."""
    v = env.visa.visa(env.evil_jku)  # trusted iss, but jku not on the allow list
    p = passport_with(env, v)
    assert p.visas == () and p.ignored == ("untrusted_jku",)
    assert env.ks.hits["/evil/jwks.json"] == 0
    assert len(passport_with(env, env.visa.visa(env.jku)).visas) == 1
    assert env.ks.hits["/visa/jwks.json"] == 1


def test_PP_CH_01(env):
    good = env.broker.passport([env.visa.visa(env.jku)])
    assert len(env.ch.verify_passports([good], now())) == 1
    bad = env.broker.passport([], ttl=-120)
    assert reason(env.ch.verify_passports, [good, bad], now()) == "expired"
    assert reason(env.ch.verify_passports, "not-a-list", now()) == "malformed"


def test_PP_CH_02(env):
    """Irrelevant Visas (other dataset, custom type, malformed) are ignored and do not
    affect the decision."""
    p = passport_with(
        env,
        env.visa.visa(env.jku, DS2),
        env.visa.visa(env.jku, visa={"type": "x-custom"}),
        env.visa.visa(env.jku, visa={"asserted": "soon"}),
        env.visa.visa(env.jku, DS1),
    )
    assert env.ch.grants_dataset(p, DS1, now(), TTL).value == DS1
    assert env.ch.grants_dataset(p, "https://yai.test/datasets/ds9", now(), TTL) is None


def test_NEG_VISA_COUNT(env):
    """D-058 (provisional), G-AAI-10: a Passport with more Visas than max_visas_per_passport is
    rejected as a whole without checking any Visa signature. Exactly at the limit passes.
    One needed Visa plus irrelevant ones is still rejected when over the count (the test
    records the mismatch with PP-CH-02's "ignore irrelevant Visas")."""
    ch = env.make(max_visas_per_passport=3)
    four = env.broker.passport([env.visa.visa(env.jku)] * 4)
    assert reason(ch.verify_passport, four, now()) == "malformed"
    assert env.ks.hits["/visa/jwks.json"] == 0
    needed_and_extra = env.broker.passport(
        [env.visa.visa(env.jku, DS1)] + [env.visa.visa(env.jku, visa={"type": "x-custom"})] * 3
    )
    assert reason(ch.verify_passport, needed_and_extra, now()) == "malformed"
    assert env.ks.hits["/visa/jwks.json"] == 0
    three = env.broker.passport([env.visa.visa(env.jku)] * 3)
    assert len(ch.verify_passport(three, now()).visas) == 3
    assert TrustConfig.from_dict({}).max_visas_per_passport == 100


def test_PP_CH_03(env):
    untrusted_issuer = env.evil.visa(env.evil_jku)
    p = passport_with(env, untrusted_issuer)
    assert p.visas == () and p.ignored == ("untrusted_issuer",)
    bad_source = passport_with(env, env.visa.visa(env.jku, visa={"source": "https://x.test/"}))
    assert env.ch.grants_dataset(bad_source, DS1, now(), TTL) is None
    bad_by = passport_with(env, env.visa.visa(env.jku, visa={"by": "self"}))
    assert env.ch.grants_dataset(bad_by, DS1, now(), TTL) is None
    ok = passport_with(env, env.visa.visa(env.jku))
    assert env.ch.grants_dataset(ok, DS1, now(), TTL) is not None


def test_NEG_AAI_UNTRUSTED_ISS(env):
    stranger = Issuer("https://stranger.test/", kid="s1")
    assert reason(env.ch.verify_passport, stranger.passport([]), now()) == "untrusted_issuer"
    assert passport_with(env, env.evil.visa(env.evil_jku)).visas == ()
    assert env.ks.hits["/evil/jwks.json"] == 0


def test_PP_VISA_01(env):
    assert len(passport_with(env, env.visa.visa(env.jku)).visas) == 1
    for name in ("iss", "sub", "iat", "exp"):
        assert passport_with(env, env.visa.visa(env.jku, drop=(name,))).visas == (), name


def test_PP_VISA_03(env):
    p = passport_with(env, env.visa.visa(env.jku, visa={"type": "x-yai-custom"}))
    assert len(p.visas) == 1  # passes verification, but
    assert env.ch.grants_dataset(p, DS1, now(), TTL) is None  # is ignored in the decision


def test_PP_VISA_04(env):
    assert len(passport_with(env, env.visa.visa(env.jku)).visas) == 1
    assert passport_with(env, env.visa.visa(env.jku, drop=("by",))).visas == ()


def test_PP_VISA_05(env):
    assert len(passport_with(env, env.visa.visa(env.jku)).visas) == 1
    assert passport_with(env, env.visa.visa(env.jku, drop=("asserted",))).visas == ()
    assert passport_with(env, env.visa.visa(env.jku, visa={"asserted": 1.5})).visas == ()
    future = env.visa.visa(env.jku, visa={"asserted": now() + 600})
    assert passport_with(env, future).visas == ()


def test_PP_VISA_06(env):
    for typ in (None, "JWT", "vnd.ga4gh.visa+jwt"):
        assert len(passport_with(env, env.visa.visa(env.jku, typ=typ)).visas) == 1, typ
    assert passport_with(env, env.visa.visa(env.jku, typ="vnd.ga4gh.passport+jwt")).visas == ()


def test_AAI_FMT_06(env):
    """No revocation procedure: the Clearinghouse only queries key URLs (no negative case
    by definition)."""
    p = passport_with(env, env.visa.visa(env.jku))
    assert len(p.visas) == 1
    assert set(env.ks.hits) == {"/broker/jwks.json", "/visa/jwks.json"}


# --- Expiry (Option B) ---


def test_PP_EXP_01(env):
    t = now()
    edge = t + TTL + 60  # now + URL lifetime + clock skew
    # Pass the Visa expiry counted from t. visa() reads now again internally, so passing ttl can
    # shift exp by 1 across a second boundary and push at_edge past expiry (failed once on
    # 2026-10-17).
    over = passport_with(env, env.visa.visa(env.jku, iat=t, exp=edge + 1))
    assert env.ch.grants_dataset(over, DS1, t, TTL) is not None
    at_edge = passport_with(env, env.visa.visa(env.jku, iat=t, exp=edge))
    assert env.ch.grants_dataset(at_edge, DS1, t, TTL) is None
    # Several expiries: the minimum wins. A short Passport exp blocks URLs even with a long Visa
    short_passport = passport_with(env, env.visa.visa(env.jku), ttl=TTL)
    assert env.ch.grants_dataset(short_passport, DS1, t, TTL) is None


def test_NEG_AAI_OPTB(env):
    p = passport_with(env, env.visa.visa(env.jku, ttl=TTL))  # Visa remaining = URL lifetime
    assert env.ch.grants_dataset(p, DS1, now(), TTL) is None
    assert env.ch.grants_dataset(p, DS1, now(), 60) is not None


def test_PP_REV_01(env):
    """Check asserted and exp; stop granting new access as expiry approaches."""
    p = passport_with(env, env.visa.visa(env.jku))
    t = now()
    assert env.ch.grants_dataset(p, DS1, t, TTL) is not None
    later = t + 7200 - TTL - 60  # Visa exp minus (URL lifetime + skew)
    assert env.ch.grants_dataset(p, DS1, later, TTL) is None


def test_NEG_AAI_MULTI_ID(env):
    """A Visa with a sub different from the Passport passes if it alone decides (G-AAI-06)."""
    p = passport_with(env, env.visa.visa(env.jku, sub="alice@other-idp"))
    assert env.ch.grants_dataset(p, DS1, now(), TTL).sub == "alice@other-idp"


# --- Key fetching (aai.md section 7) ---


def test_NEG_JKU_REDIRECT(env):
    for path, kind, value in [
        ("/visa/jwks.json", "redirect", env.evil_jku),
        ("/visa/jwks.json", "large", None),
    ]:
        env.ks.routes[path] = (kind, value)
        ch = env.make()
        p = ch.verify_passport(env.broker.passport([env.visa.visa(env.jku)]), now())
        assert p.visas == () and p.ignored == ("key_unavailable",), kind
    assert env.ks.hits["/evil/jwks.json"] == 0  # redirects are not followed


def test_NEG_JKU_SLOW(env):
    env.ks.routes["/visa/jwks.json"] = ("slow", None)
    p = env.make().verify_passport(env.broker.passport([env.visa.visa(env.jku)]), now())
    assert p.ignored == ("key_unavailable",)


def test_NEG_AAI_KEY_ROTATION(env):
    ch = env.make()
    assert ch.verify_passport(env.broker.passport([]), now())
    new = Issuer(env.broker.iss, kid="b2")
    env.ks.routes["/broker/jwks.json"] = ("jwks", new.jwks())  # only the new key, old one dropped
    env.mono.t += 61  # after the refetch interval (60 seconds)
    assert ch.verify_passport(new.passport([]), now()).sub == "alice"
    env.mono.t += 3601  # past the retention period, tokens from the dropped key fail
    assert reason(ch.verify_passport, env.broker.passport([]), now()) == "bad_signature"


def test_key_refetch_is_rate_limited(env):
    ch = env.make()
    ch.verify_passport(env.broker.passport([]), now())
    unknown = Issuer(env.broker.iss, kid="zz").passport([])
    for _ in range(3):
        assert reason(ch.verify_passport, unknown, now()) == "bad_signature"
    assert env.ks.hits["/broker/jwks.json"] == 1  # at most once per minute


# --- yai AT (Bearer) ---


@pytest.fixture
def atv(env):
    return AccessTokenVerifier(
        env.broker.iss,
        env.ks.url("/broker/jwks.json"),
        "yai-drs",
        KeyFetcher(insecure_http_hosts=[env.ks.netloc]),
    )


def test_NEG_AUTH_KIND(env, atv):
    assert atv.verify(env.broker.access_token("yai-drs", "drs.read"), now())["sub"] == "alice"
    passport = env.broker.passport([])
    assert reason(atv.verify, passport, now()) == "wrong_token_kind"
    psat = env.broker.access_token("yai-drs", "openid ga4gh_passport_v1")
    assert reason(atv.verify, psat, now()) == "wrong_token_kind"


def test_NEG_AUTH_ALG(env, atv):
    claims = {"iss": env.broker.iss, "sub": "alice", "aud": "yai-drs", "iat": now(),
              "exp": now() + 60}  # fmt: skip
    assert reason(atv.verify, alg_none(claims, typ="at+jwt", kid="b1"), now()) == "bad_signature"
    hs = hs256_with_public_key(env.broker, claims, typ="at+jwt", kid="b1")
    assert reason(atv.verify, hs, now()) == "bad_signature"


def test_NEG_AUTH_EXPIRED(env, atv):
    assert atv.verify(env.broker.access_token("yai-drs", ttl=-30), now())  # within the skew
    assert reason(atv.verify, env.broker.access_token("yai-drs", ttl=-61), now()) == "expired"


def test_NEG_JKU_DRIP(env):
    """A source that trickles bytes is cut off ~5 s after the fetch starts."""
    env.ks.routes["/visa/jwks.json"] = ("drip", None)
    started = time.monotonic()
    p = env.make().verify_passport(env.broker.passport([env.visa.visa(env.jku)]), now())
    assert p.ignored == ("key_unavailable",)
    assert time.monotonic() - started < 8


def test_key_refetch_limited_when_source_is_down(env):
    """If the first fetch fails, following requests fetch only once."""
    env.ks.routes["/broker/jwks.json"] = ("down", None)
    ch = env.make()
    for _ in range(10):
        assert reason(ch.verify_passport, env.broker.passport([]), now()) == "key_unavailable"
    assert env.ks.hits["/broker/jwks.json"] == 1
    env.ks.routes["/broker/jwks.json"] = ("jwks", env.broker.jwks())
    env.mono.t += 61  # refetch after the interval
    assert ch.verify_passport(env.broker.passport([]), now())


def test_key_fetch_is_single_flight(env):
    """No concurrent fetches of the same source."""
    ch = env.make()
    token = env.broker.passport([])
    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(lambda _: ch.verify_passport(token, now()).sub, range(8)))
    assert results == ["alice"] * 8
    assert env.ks.hits["/broker/jwks.json"] == 1


def test_wrong_types_are_token_errors(env):
    """Values of the wrong type (e.g. arrays) raise TokenError (not 500)."""
    bad_iss = env.broker.passport([], iss=["x"])
    assert reason(env.ch.verify_passport, bad_iss, now()) == "malformed"
    visa_claims = {
        "iss": env.visa.iss, "sub": "alice", "iat": now(), "exp": now() + 600,
        "ga4gh_visa_v1": {"type": "ControlledAccessGrants", "asserted": now(), "value": DS1,
                          "source": "https://yai.test/dac", "by": "dac"},
    }  # fmt: skip
    # Types are checked before the signature, so the signature can be fake (joserfc rejects it)
    weird = f"{b64({'alg': 'ES256', 'typ': ['x'], 'jku': env.jku})}.{b64(visa_claims)}.AAAA"
    p = passport_with(env, weird, env.visa.visa(env.jku))
    assert len(p.visas) == 1 and p.ignored == ("malformed",)  # only the malformed one is ignored
    passport_claims = {"iss": env.broker.iss, "sub": "a", "iat": now(), "exp": now() + 60,
                       "ga4gh_passport_v1": []}  # fmt: skip
    head = {"alg": "ES256", "typ": "vnd.ga4gh.passport+jwt", "kid": ["b1"]}
    bad_kid = f"{b64(head)}.{b64(passport_claims)}.AAAA"
    assert reason(env.ch.verify_passport, bad_kid, now()) == "malformed"


def test_NEG_AUTH_CLIENT_ID(env, atv):
    """The AT client_id must be a non-empty string (aai.md section 3)."""
    assert atv.verify(env.broker.access_token("yai-drs"), now())
    for value in (None, 7, ["x"], ""):
        token = env.broker.access_token("yai-drs", client_id=value)
        assert reason(atv.verify, token, now()) == "malformed", value


def test_PP_URL_CLAIMS(env):
    """ControlledAccessGrants value and every Visa source are URL Claims."""
    long_ok = "https://yai.test/" + "a" * (255 - len("https://yai.test/"))
    assert len(long_ok) == 255
    assert len(passport_with(env, env.visa.visa(env.jku, long_ok)).visas) == 1
    assert passport_with(env, env.visa.visa(env.jku, long_ok + "a")).visas == ()  # 256 characters
    assert passport_with(env, env.visa.visa(env.jku, "ds1")).visas == ()  # not a URL
    assert passport_with(env, env.visa.visa(env.jku, "https://yai.test/a b")).visas == ()
    bad_source = env.visa.visa(env.jku, visa={"source": "dac"})
    assert passport_with(env, bad_source).visas == ()
    # A custom type's value need not be a URL (source stays a URL Claim)
    custom = env.visa.visa(env.jku, "free text", visa={"type": "x-yai-note"})
    assert len(passport_with(env, custom).visas) == 1


def test_NEG_AUTH_WRONG_AUD(env, atv):
    assert reason(atv.verify, env.broker.access_token("yai-tes"), now()) == "wrong_audience"
