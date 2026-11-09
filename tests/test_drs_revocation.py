"""Window until access stops after a permission is removed (DRS part).

Background: aai.md section 4.
- NEG-DRS-REVOKE-AT-ISSUER: after removing a permission at the issuer (the test Visa issuer),
  already-issued Visas/Passports pass /access until exp (spec limit, AAI-FMT-06). New Passports
  are rejected
- NEG-DRS-REASSIGN: right after the object's dataset changes on the DRS side, new /access is
  rejected. Signed URLs already issued work until their own expiry
"""

from __future__ import annotations

import subprocess
import sys
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from tests.servers import Server, free_port
from tests.stack import DS1, DS2, Creds, drs_config, register, start_aai, write_config


@pytest.fixture
def env(tmp_path):
    aai = start_aai(tmp_path)
    port = free_port()
    cfg = drs_config(aai, tmp_path, port)
    cfg_path = write_config(tmp_path / "drs.yaml", cfg)
    f = tmp_path / "data.txt"
    f.write_bytes(b"controlled\n")
    oid = register(cfg_path, f, "--policy", "dataset", "--dataset", DS1)
    drs = Server("drs", cfg, tmp_path / "drs", port=port).start()
    ns = SimpleNamespace(aai=aai, drs=drs, oid=oid, cfg_path=cfg_path, creds=Creds(aai))
    yield ns
    drs.stop()
    aai.stop()


def access(env, passport) -> httpx.Response:
    return httpx.post(env.drs.url + f"/objects/{env.oid}/access/a1", json={"passports": [passport]})


def set_clock(env, offset: int) -> None:
    httpx.post(env.drs.root + "/yai/test/clock", json={"offset": offset}).raise_for_status()


def test_NEG_DRS_REVOKE_AT_ISSUER(env):
    issued = env.creds.passport("alice")
    assert access(env, issued).status_code == 200
    # Remove the permission at the issuer (Visa Document Tokens have no revocation procedure)
    r = httpx.put(env.aai.url + "/test/users/alice/visas", json={"visas": []})
    assert r.status_code == 200
    assert access(env, issued).status_code == 200  # already-issued ones keep passing
    fresh = env.creds.passport("alice")
    assert access(env, fresh).status_code == 403  # newly issued ones are rejected
    # Already-issued ones also stop after the Option B window (Visa exp - URL lifetime - skew)
    set_clock(env, 7200 - 3600 - 60 + 5)
    assert access(env, issued).status_code == 403


def test_NEG_DRS_REASSIGN(env):
    alice = env.creds.passport("alice")
    url = access(env, alice).json()["url"]
    subprocess.run(
        [sys.executable, "-m", "yai", "drs", "set-dataset", env.oid, DS2,
         "--config", str(env.cfg_path)],
        check=True,
    )  # fmt: skip
    assert access(env, alice).status_code == 403  # new /access is rejected immediately
    assert access(env, env.creds.passport("bob")).status_code == 200  # the new assignment
    assert httpx.get(url).content == b"controlled\n"  # an already-issued URL works until it expires
    exp = int(parse_qs(urlsplit(url).query)["exp"][0])
    set_clock(env, exp - int(time.time()) + 1)
    assert httpx.get(url).status_code == 403  # stops at the URL expiry
