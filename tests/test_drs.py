"""Exercise yai-drs (DRS 1.5.0) over HTTP. Obligations DRS-*, test-plan.md section 4 DRS rows."""

from __future__ import annotations

import hashlib
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import pytest

from tests import shape
from tests.keyserver import KeyServer
from tests.servers import Server, free_port
from tests.stack import DS1, Creds, bearer, drs_config, register, start_aai, write_config
from tests.tokens import Issuer

ID_RE = re.compile(r"^[A-Za-z0-9.\-_~]+$")


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("drs")
    aai = start_aai(tmp)
    port = free_port()
    cfg = drs_config(aai, tmp, port)
    cfg_path = write_config(tmp / "drs.yaml", cfg)
    files = {}
    for name, body in [("pub", b"public bytes\n"), ("bear", b"bearer bytes\n"),
                       ("ds1", b"ACGT" * 5000), ("mixed", b"mixed bytes\n")]:  # fmt: skip
        f = tmp / f"{name}.txt"
        f.write_bytes(body)
        files[name] = f
    ids = {
        "pub": register(cfg_path, files["pub"], "--policy", "public", "--mime-type", "text/plain"),
        "bear": register(cfg_path, files["bear"], "--policy", "bearer"),
        "ds1": register(
            cfg_path,
            files["ds1"],
            "--policy",
            "dataset",
            "--dataset",
            DS1,
            "--created-time",
            "2026-01-02T03:04:05Z",
        ),
        "mixed": register(
            cfg_path,
            files["mixed"],
            "--metadata-policy",
            "public",
            "--access-policy",
            "dataset",
            "--dataset",
            DS1,
        ),
    }
    drs = Server("drs", cfg, tmp / "drs", port=port).start()
    creds = Creds(aai)
    ns = SimpleNamespace(
        aai=aai, drs=drs, ids=ids, files=files, cfg=cfg, cfg_path=cfg_path, tmp=tmp,
        creds=creds, url=drs.url,
        alice_pp=creds.passport("alice"), bob_pp=creds.passport("bob"),
        mallory_pp=creds.passport("mallory"), carol_pp=creds.passport("carol"),
        alice_at=creds.at("alice", "yai-drs"), carol_at=creds.at("carol", "yai-drs"),
    )  # fmt: skip
    yield ns
    drs.stop()
    aai.stop()


def get(env, path, **kw):
    r = httpx.get(env.url + path, **kw)
    shape.check("drs", r)
    return r


def post(env, path, body, **kw):
    r = httpx.post(env.url + path, json=body, **kw)
    shape.check("drs", r)
    return r


def access_url(env, key="ds1", pp=None):
    r = post(env, f"/objects/{env.ids[key]}/access/a1", {"passports": [pp or env.alice_pp]})
    assert r.status_code == 200, r.text
    return r.json()["url"]


# --- ID ---


def test_DRS_ID_01(env):
    for oid in env.ids.values():
        assert ID_RE.match(oid) and oid.startswith("o") and len(oid) == 27
    assert get(env, "/objects/" + "o%3Aunknown").status_code == 404


def test_DRS_ID_02(env):
    oid = env.ids["pub"]
    before = get(env, f"/objects/{oid}").json()
    env.files["pub"].write_bytes(b"changed after registration\n")  # rewrite the original file
    after = get(env, f"/objects/{oid}").json()
    assert before["checksums"] == after["checksums"] and before["size"] == after["size"]
    assert httpx.get(after["access_methods"][0]["access_url"]["url"]).content == b"public bytes\n"
    # Re-registering the same content yields a different ID (no overwrite)
    again = register(env.cfg_path, env.files["bear"], "--policy", "public")
    assert again not in env.ids.values()


def test_DRS_ID_03(env):
    obj = get(env, f"/objects/{env.ids['pub']}").json()
    assert "version" not in obj
    assert httpx.get(env.url + f"/objects/{env.ids['pub']}/versions").status_code == 404


# --- service-info ---


def test_DRS_SI_01(env):
    t = get(env, "/service-info").json()["type"]
    assert t["group"] == "org.ga4gh" and t["artifact"] == "drs" and t["version"] == "1.5.0"


def test_DRS_SI_02(env):
    si = get(env, "/service-info").json()
    assert si["maxBulkRequestLength"] == si["drs"]["maxBulkRequestLength"] == 100
    assert si["drs"]["objectCount"] >= 4 and si["drs"]["totalObjectSize"] > 0


def test_DRS_SI_03(env):
    si = get(env, "/service-info").json()
    assert {"id", "name", "type", "organization", "version"} <= set(si)
    broken = dict(si)
    del broken["organization"]
    with pytest.raises(Exception):  # noqa: B017 the shape check catches the missing field
        shape.check("drs", httpx.Response(200, json=broken, request=httpx.Request("GET",
                    env.url + "/service-info")))  # fmt: skip


# --- Objects ---


def test_DRS_OBJ_01(env):
    obj = get(env, f"/objects/{env.ids['pub']}").json()
    assert {"id", "self_uri", "size", "created_time", "checksums"} <= set(obj)
    assert len(obj["checksums"]) >= 1
    assert obj["created_time"].endswith("Z")
    ds = post(env, f"/objects/{env.ids['ds1']}", {"passports": [env.alice_pp]}).json()
    assert ds["created_time"] == "2026-01-02T03:04:05Z"  # content creation time (D-022)


def test_DRS_OBJ_02(env):
    for key in ("pub", "mixed"):
        obj = get(env, f"/objects/{env.ids[key]}").json()
        assert obj["access_methods"], key


def test_DRS_OBJ_03(env):
    obj = get(env, f"/objects/{env.ids['pub']}").json()
    assert obj["self_uri"] == f"drs://drs.yai.test/{env.ids['pub']}"
    assert ":" not in obj["self_uri"].removeprefix("drs://")  # no port (DRS-URI-02)


def test_DRS_OBJ_05(env):
    obj = get(env, f"/objects/{env.ids['bear']}", headers=bearer(env.alice_at)).json()
    sums = {c["type"]: c["checksum"] for c in obj["checksums"]}
    assert sums["sha-256"] == hashlib.sha256(b"bearer bytes\n").hexdigest()
    assert sums["md5"] == hashlib.md5(b"bearer bytes\n").hexdigest()
    assert all(t in {"sha-256", "md5"} for t in sums)  # IANA names (not sha256)


def test_DRS_OBJ_06(env):
    for r in (get(env, "/objects/onotthere"),
              post(env, "/objects/onotthere", {"passports": [env.alice_pp]}),
              httpx.options(env.url + "/objects/onotthere")):  # fmt: skip
        assert r.status_code == 404
        shape.check_error_shape(r)


# --- AccessMethod and /access ---


def test_DRS_AM_01(env):
    for key, kw in [("pub", {}), ("bear", {"headers": bearer(env.alice_at)})]:
        for m in get(env, f"/objects/{env.ids[key]}", **kw).json()["access_methods"]:
            assert "access_url" in m or "access_id" in m


def test_DRS_AM_02(env):
    obj = post(env, f"/objects/{env.ids['ds1']}", {"passports": [env.alice_pp]}).json()
    aids = [m["access_id"] for m in obj["access_methods"] if "access_id" in m]
    assert len(aids) == len(set(aids)) == 1


def test_DRS_AM_03(env):
    obj = post(env, f"/objects/{env.ids['ds1']}", {"passports": [env.alice_pp]}).json()
    auth = obj["access_methods"][0]["authorizations"]
    assert auth["supported_types"] == ["PassportAuth"] and auth["passport_auth_issuers"]
    bear = get(env, f"/objects/{env.ids['bear']}", headers=bearer(env.alice_at)).json()
    assert bear["access_methods"][0]["authorizations"]["supported_types"] == ["BearerAuth"]


def test_DRS_ACC_01(env):
    r = post(env, f"/objects/{env.ids['ds1']}/access/a1", {"passports": [env.alice_pp]})
    assert set(r.json()) == {"url"}  # no headers (C-03)
    assert httpx.get(r.json()["url"]).content == b"ACGT" * 5000


def test_DRS_ACC_02(env):
    assert post(env, f"/objects/{env.ids['ds1']}/access/a2",
                {"passports": [env.alice_pp]}).status_code == 404  # fmt: skip
    assert get(env, f"/objects/{env.ids['pub']}/access/a1").status_code == 404


def test_NEG_DRS_ACCESSID_REUSE(env):
    url = access_url(env, "ds1")
    q = urlsplit(url).query
    other = f"{env.drs.root}/yai/blobs/{env.ids['mixed']}?{q}"
    assert httpx.get(other).status_code == 403


def test_NEG_DRS_URL_TAMPER(env):
    url = access_url(env)
    parts = urlsplit(url)
    q = {k: v[0] for k, v in parse_qs(parts.query).items()}
    for change in ({"exp": str(int(q["exp"]) + 3600)}, {"aid": "a2"}, {"sig": "0" * 64}):
        bad = f"{parts.scheme}://{parts.netloc}{parts.path}?{urlencode({**q, **change})}"
        assert httpx.get(bad).status_code == 403, change
    assert httpx.get(url).status_code == 200


def test_NEG_DRS_BACKDOOR(env):
    for key in ("ds1", "bear", "mixed"):
        assert httpx.get(f"{env.drs.root}/yai/blobs/{env.ids[key]}").status_code == 403
    assert httpx.get(f"{env.drs.root}/yai/blobs/{env.ids['pub']}").status_code == 200


def test_NEG_DRS_MIXED_POLICY(env):
    oid = env.ids["mixed"]
    assert get(env, f"/objects/{oid}").status_code == 200  # metadata is public
    assert httpx.get(f"{env.drs.root}/yai/blobs/{oid}").status_code == 403
    assert get(env, f"/objects/{oid}/access/a1").status_code == 401  # no Passport
    assert post(env, f"/objects/{oid}/access/a1", {"passports": [env.bob_pp]}).status_code == 403
    assert httpx.get(access_url(env, "mixed")).content == b"mixed bytes\n"
    opts = httpx.options(env.url + f"/objects/{oid}").json()
    assert opts["supported_types"] == ["None"]  # OPTIONS follows the metadata policy


def test_range_and_cache_headers(env):
    url = access_url(env)
    r = httpx.get(url, headers={"Range": "bytes=4-7"})
    assert r.status_code == 206 and r.content == b"ACGT"
    assert r.headers["content-range"] == "bytes 4-7/20000"
    acc = post(env, f"/objects/{env.ids['ds1']}/access/a1", {"passports": [env.alice_pp]})
    assert acc.headers["cache-control"] == "no-cache, no-store"
    assert acc.headers["pragma"] == "no-cache"


def test_NEG_CACHE_HEADERS(env):
    r = get(env, f"/objects/{env.ids['bear']}/access/a1", headers=bearer(env.alice_at))
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-cache, no-store" and r.headers["pragma"] == "no-cache"


# --- Authorization ---


def test_DRS_AUTH_01(env):
    oid = env.ids["ds1"]
    assert get(env, f"/objects/{oid}").status_code == 401
    assert post(env, f"/objects/{oid}", {"passports": [env.bob_pp]}).status_code == 403
    assert post(env, f"/objects/{oid}", {"passports": [env.alice_pp]}).status_code == 200
    assert get(env, f"/objects/{env.ids['bear']}").status_code == 401
    assert get(env, f"/objects/{env.ids['bear']}",
               headers=bearer(env.alice_at)).status_code == 200  # fmt: skip


def test_DRS_AUTH_02(env):
    kinds = {k: httpx.options(env.url + f"/objects/{oid}").json()["supported_types"]
             for k, oid in env.ids.items()}  # fmt: skip
    assert kinds == {"pub": ["None"], "bear": ["BearerAuth"], "ds1": ["PassportAuth"],
                     "mixed": ["None"]}  # fmt: skip
    mixed = get(env, f"/objects/{env.ids['mixed']}").json()
    assert mixed["access_methods"][0]["authorizations"]["supported_types"] == ["PassportAuth"]


def test_DRS_AUTH_03(env):
    oid = env.ids["ds1"]
    assert post(env, f"/objects/{oid}", {"passports": [env.alice_pp]}).status_code == 200
    r = post(env, f"/objects/{oid}", {"tokens": [env.alice_pp]})
    assert r.status_code == 400  # G-DRS-03
    assert post(env, f"/objects/{oid}", {"passports": env.alice_pp}).status_code == 400


def test_DRS_AUTH_04(env):
    r = httpx.options(env.url + f"/objects/{env.ids['ds1']}")
    assert r.status_code == 200
    shape.check("drs", r)
    assert r.json()["drs_object_id"] == env.ids["ds1"]


def test_DRS_AUTH_05(env):
    r = httpx.options(env.url + f"/objects/{env.ids['ds1']}").json()
    assert r["passport_auth_issuers"] == [env.aai.root + "/visa/"]
    assert "passport_auth_issuers" not in httpx.options(
        env.url + f"/objects/{env.ids['pub']}").json()  # fmt: skip


def test_NEG_MULTI_CRED(env):
    oid = env.ids["ds1"]
    # Bearer and passports together: passes if a Passport satisfies it
    r = post(env, f"/objects/{oid}", {"passports": [env.alice_pp]}, headers=bearer(env.alice_at))
    assert r.status_code == 200
    # Two Passports for different people: passes if either one satisfies it
    assert (
        post(env, f"/objects/{oid}", {"passports": [env.bob_pp, env.alice_pp]}).status_code == 200
    )
    # Any one malformed credential gives 401
    broken = env.alice_pp[:-4] + "AAAA"
    assert post(env, f"/objects/{oid}", {"passports": [env.alice_pp, broken]}).status_code == 401
    r = post(env, f"/objects/{oid}", {"passports": [env.alice_pp]}, headers=bearer("nope"))
    assert r.status_code == 401
    # Even for a public object, malformed credentials are not silently ignored
    assert get(env, f"/objects/{env.ids['pub']}", headers=bearer("nope")).status_code == 401


def test_untrusted_visa_issuer_is_forbidden(env):
    assert post(env, f"/objects/{env.ids['ds1']}",
                {"passports": [env.mallory_pp]}).status_code == 403  # fmt: skip


def test_NEG_AUTH_NONE(env):
    for path in (f"/objects/{env.ids['bear']}", f"/objects/{env.ids['bear']}/access/a1"):
        assert get(env, path).status_code == 401


def test_NEG_AUTH_WRONG_AUD(env):
    wes_at = env.creds.at("alice", "yai-wes")
    assert get(env, f"/objects/{env.ids['bear']}", headers=bearer(wes_at)).status_code == 401


def test_NEG_AUTH_KIND(env):
    oid = env.ids["bear"]
    assert get(env, f"/objects/{oid}", headers=bearer(env.alice_pp)).status_code == 401
    psat = env.creds.login("alice", [])["passport_scoped_access_token"]
    assert get(env, f"/objects/{oid}", headers=bearer(psat)).status_code == 401
    # An AT placed in passports does not pass as a Passport
    assert post(env, f"/objects/{env.ids['ds1']}",
                {"passports": [env.alice_at]}).status_code == 401  # fmt: skip


def test_NEG_AUTH_NOSCOPE(env):
    assert get(env, f"/objects/{env.ids['bear']}",
               headers=bearer(env.carol_at)).status_code == 403  # fmt: skip
    assert post(env, f"/objects/{env.ids['ds1']}",
                {"passports": [env.carol_pp]}).status_code == 403  # fmt: skip


def test_DRS_RO_01(env):
    oid = env.ids["pub"]
    for method in ("PUT", "DELETE", "PATCH"):
        assert httpx.request(method, env.url + f"/objects/{oid}").status_code == 405


# --- Bulk ---


def test_DRS_BULK_01(env):
    ids = [env.ids["pub"]] * 101
    assert post(env, "/objects", {"bulk_object_ids": ids}).status_code == 413
    assert (
        post(
            env,
            "/objects/access",
            {
                "bulk_object_access_ids": [
                    {"bulk_object_id": env.ids["ds1"], "bulk_access_ids": ["a1"]}
                ]
                * 101
            },
        ).status_code
        == 413
    )
    r = httpx.request("OPTIONS", env.url + "/objects", json={"bulk_object_ids": ids})
    assert r.status_code == 413
    assert post(env, "/objects", {"bulk_object_ids": ids[:100]}).status_code == 200


def test_DRS_BULK_02(env):
    body = {"bulk_object_ids": [env.ids["pub"], env.ids["ds1"], "onotthere"],
            "passports": [env.bob_pp]}  # fmt: skip
    r = post(env, "/objects", body).json()
    assert r["summary"] == {"requested": 3, "resolved": 1, "unresolved": 2}
    assert {u["error_code"]: u["object_ids"] for u in r["unresolved_drs_objects"]} == {
        403: [env.ids["ds1"]], 404: ["onotthere"]}  # fmt: skip
    # 200 even when nothing resolves (D-021)
    none = post(env, "/objects", {"bulk_object_ids": ["onotthere"]})
    assert none.status_code == 200 and none.json()["summary"]["resolved"] == 0
    acc = post(env, "/objects/access", {"passports": [env.alice_pp], "bulk_object_access_ids": [
        {"bulk_object_id": env.ids["ds1"], "bulk_access_ids": ["a1"]},
        {"bulk_object_id": env.ids["pub"], "bulk_access_ids": ["a1"]}]}).json()  # fmt: skip
    assert acc["summary"] == {"requested": 2, "resolved": 1, "unresolved": 1}
    assert httpx.get(acc["resolved_drs_object_access_urls"][0]["url"]).status_code == 200
    opts = httpx.request("OPTIONS", env.url + "/objects",
                         json={"bulk_object_ids": [env.ids["ds1"], "onotthere"]})  # fmt: skip
    shape.check("drs", opts)
    assert opts.json()["summary"] == {"requested": 2, "resolved": 1, "unresolved": 1}


def test_INT_DRS_REQUIRED_VISAS(env, tmp_path):
    """D-072 (a yai extension): the Authorizations of a dataset object name the Visa it needs, in
    OPTIONS and in access_methods[].authorizations. Not for public or bearer objects; a config
    switch turns it off."""
    need = [{"type": "ControlledAccessGrants", "value": DS1}]

    def options(key: str) -> dict:
        return httpx.options(env.url + f"/objects/{env.ids[key]}").json()

    assert options("ds1")["yai_required_visas"] == need
    obj = post(env, f"/objects/{env.ids['ds1']}", {"passports": [env.alice_pp]}).json()
    assert obj["access_methods"][0]["authorizations"]["yai_required_visas"] == need
    for key in ("pub", "bear"):
        assert "yai_required_visas" not in options(key)
    port = free_port()
    cfg = drs_config(env.aai, tmp_path, port, authorizations={"expose_dataset": False},
                     url_secret_file=str(tmp_path / "s"))  # fmt: skip
    cfg["data_dir"] = env.cfg["data_dir"]
    drs = Server("drs", cfg, tmp_path / "drs-hidden", port=port).start()
    try:
        opts = httpx.options(drs.url + f"/objects/{env.ids['ds1']}").json()
        assert opts["supported_types"] == ["PassportAuth"]
        assert "yai_required_visas" not in opts
    finally:
        drs.stop()


# --- Expiry (test clock) ---


def test_NEG_DRS_URL_EXPIRED(env, tmp_path):
    port = free_port()
    cfg = drs_config(env.aai, tmp_path, port, signed_url_ttl_seconds=2,
                     url_secret_file=str(tmp_path / "s"))  # fmt: skip
    cfg["data_dir"] = env.cfg["data_dir"]
    drs = Server("drs", cfg, tmp_path / "drs2", port=port).start()
    try:
        r = httpx.post(drs.url + f"/objects/{env.ids['ds1']}/access/a1",
                       json={"passports": [env.alice_pp]})  # fmt: skip
        url = r.json()["url"]
        assert httpx.get(url).status_code == 200
        time.sleep(3)
        assert httpx.get(url).status_code == 403
    finally:
        drs.stop()


def set_clock(env, offset):
    httpx.post(env.drs.root + "/yai/test/clock", json={"offset": offset}).raise_for_status()


def test_NEG_DRS_REVOKE(env):
    """New URLs are issued until the Visa exp, stopping at the Option B boundary. URLs expire
    before the Visa."""
    oid = env.ids["ds1"]
    try:
        url = access_url(env)
        exp = int(parse_qs(urlsplit(url).query)["exp"][0])
        # Visa lasts 7200 s. URLs are issued while now + 3600 + 60 < exp (under 3540 s after issue)
        set_clock(env, 3500)
        assert post(env, f"/objects/{oid}/access/a1", {"passports": [env.alice_pp]}).status_code \
            == 200  # fmt: skip
        set_clock(env, 3545)
        assert post(env, f"/objects/{oid}/access/a1", {"passports": [env.alice_pp]}).status_code \
            == 403  # fmt: skip
        # A URL issued earlier expires on its own lifetime (3600 s after issue)
        assert httpx.get(url).status_code == 200
        set_clock(env, exp - int(time.time()) + 1)
        assert httpx.get(url).status_code == 403
    finally:
        set_clock(env, 0)


def test_NEG_SECRET_IN_LOG(env):
    """No Passport, AT or signature ever appears in DRS logs (DRS unit part).

    Signed URLs are searched for after sending a normal fetch, a Range and a tampered URL to the
    delivery endpoint."""
    url = access_url(env)
    assert httpx.get(url).status_code == 200
    assert httpx.get(url, headers={"Range": "bytes=0-3"}).status_code == 206
    assert httpx.get(url.replace("sig=", "sig=0")).status_code == 403
    get(env, f"/objects/{env.ids['bear']}", headers=bearer(env.alice_at))
    post(env, f"/objects/{env.ids['ds1']}", {"passports": [env.alice_pp[:-4] + "AAAA"]})
    log = env.drs.log()
    query = urlsplit(url).query
    sig = parse_qs(query)["sig"][0]
    for secret in (env.alice_pp, env.alice_at, sig, query, env.alice_pp.split(".")[1]):
        assert secret not in log


def test_bad_credential_before_not_found(env):
    """D-025 (option A): check credentials, then existence, then policy."""
    assert get(env, "/objects/onotthere", headers=bearer("broken")).status_code == 401
    assert post(env, "/objects/onotthere", {"passports": ["x.y.z"]}).status_code == 401
    assert get(env, "/objects/onotthere").status_code == 404  # 404 without credentials
    assert get(env, "/objects/onotthere", headers=bearer(env.alice_at)).status_code == 404


def test_unknown_auth_mode_refuses_to_start(tmp_path):
    """A typo or unknown mode never silently makes all public (startup fails)."""
    for service, mode in (("drs", "AAI"), ("drs", "static-bearer"), ("tes", "aii"),
                          ("wes", "static-bearer")):  # fmt: skip
        cfg = {"data_dir": str(tmp_path / f"{service}-{mode}"), "auth": {"mode": mode}}
        with pytest.raises(RuntimeError, match=r"auth\.mode must be one of"):
            Server(service, cfg, tmp_path / f"{service}-{mode}").start()


def test_slow_key_source_does_not_block_others(env, tmp_path):
    """Other requests are answered while a slow key source is being fetched."""
    ks = KeyServer()
    slow_broker = Issuer("https://slow.test/", kid="s1")
    ks.routes["/jwks.json"] = ("slow", None)
    port = free_port()
    cfg = drs_config(env.aai, tmp_path, port)
    cfg["trust"]["brokers"].append({"iss": slow_broker.iss, "jwks_uri": ks.url("/jwks.json")})
    cfg["trust"]["insecure_http_hosts"].append(ks.netloc)
    cfg["data_dir"] = env.cfg["data_dir"]
    drs = Server("drs", cfg, tmp_path / "drs-slow", port=port).start()
    try:
        url = drs.url + f"/objects/{env.ids['ds1']}"
        with ThreadPoolExecutor(1) as pool:
            slow = pool.submit(httpx.post, url, json={"passports": [slow_broker.passport([])]},
                               timeout=20)  # fmt: skip
            time.sleep(0.5)  # while the key fetch is in progress
            started = time.monotonic()
            assert httpx.get(drs.url + "/service-info", timeout=5).status_code == 200
            assert time.monotonic() - started < 2
            assert slow.result().status_code == 401  # key not fetched, so that Passport is rejected
    finally:
        drs.stop()
        ks.close()


def test_DRS_limits(env):
    """Limits on body size, Passport count and length, and bulk /access items."""
    oid = env.ids["ds1"]
    inner = [{"bulk_object_id": oid, "bulk_access_ids": ["a1"] * 101}]
    assert post(env, "/objects/access", {"bulk_object_access_ids": inner}).status_code == 413
    many = [env.alice_pp] * 11
    assert post(env, f"/objects/{oid}", {"passports": many}).status_code == 400
    assert post(env, f"/objects/{oid}", {"passports": ["x" * (64 * 1024 + 1)]}).status_code \
        == 400  # fmt: skip
    huge = {"passports": [env.alice_pp], "pad": "x" * (1024 * 1024)}
    assert post(env, f"/objects/{oid}", huge).status_code == 400
    assert post(env, "/objects", {"bulk_object_ids": [oid], "pad": "x" * (1024 * 1024)}) \
        .status_code == 413  # fmt: skip
    assert post(env, f"/objects/{oid}", {"passports": [env.alice_pp] * 10}).status_code == 200


def test_NEG_DRS_URL_TAMPER_shapes(env):
    """Non-ASCII, wrong-length or malformed signatures and expiries give 403
    (not 500)."""
    parts = urlsplit(access_url(env))
    q = {k: v[0] for k, v in parse_qs(parts.query).items()}
    base = f"{parts.scheme}://{parts.netloc}{parts.path}"
    for change in ({"sig": "あ"}, {"sig": q["sig"][:-1]}, {"sig": q["sig"].upper()},
                   {"exp": "１２３"}, {"exp": "-1"}, {"exp": "9" * 40}):  # fmt: skip
        r = httpx.get(f"{base}?{urlencode({**q, **change})}")
        assert r.status_code == 403, change
        shape.check_error_shape(r)


def test_NEG_DRS_URL_EXPIRES_MID_TRANSFER(env):
    """Streaming stops when the URL expires mid-transfer; later Range gets 403."""
    big = env.tmp / "big.bin"
    big.write_bytes(os.urandom(8 * 1024 * 1024))
    oid = register(env.cfg_path, big, "--policy", "dataset", "--dataset", DS1)
    r = post(env, f"/objects/{oid}/access/a1", {"passports": [env.alice_pp]})
    url = r.json()["url"]
    exp = int(parse_qs(urlsplit(url).query)["exp"][0])
    received = 0
    try:
        with httpx.stream("GET", url) as resp:
            assert resp.status_code == 200
            for chunk in resp.iter_raw(64 * 1024):
                received += len(chunk)
                if received >= 256 * 1024:
                    set_clock(env, exp - int(time.time()) + 1)  # move past expiry mid-transfer
    except httpx.HTTPError:
        pass  # httpx may treat a body shorter than the declared length as an error
    try:
        assert received < 8 * 1024 * 1024  # not everything arrives
        assert httpx.get(url, headers={"Range": f"bytes={received}-"}).status_code == 403
    finally:
        set_clock(env, 0)


def test_NEG_DRS_BODY_CHUNKED(env):
    """Chunked bodies without Content-Length also stop at 1 MiB while reading.
    Bulk operations return 413, single operations 400. Previously the body was read and parsed
    before checking its length."""
    oid = env.ids["ds1"]

    def chunks():
        yield b'{"pad": "'
        for _ in range(2):
            yield b"x" * (1 << 20)
        yield b'"}'

    json_type = {"content-type": "application/json"}
    r = httpx.post(env.url + "/objects", content=chunks(), headers=json_type)
    assert r.status_code == 413 and "larger than" in r.json()["msg"]
    r = httpx.post(env.url + f"/objects/{oid}", content=chunks(), headers=json_type)
    assert r.status_code == 400 and "larger than" in r.json()["msg"]
