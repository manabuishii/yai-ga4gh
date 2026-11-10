"""Test helper that starts AAI and DRS and obtains credentials for each user."""

from __future__ import annotations

import base64
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import httpx
import yaml

from tests.servers import Server, free_port

DS1 = "https://yai.test/datasets/ds1"
DS2 = "https://yai.test/datasets/ds2"
ALL_SCOPES = ["wes.run", "wes.read", "wes.cancel", "tes.submit", "tes.read", "tes.cancel",
              "drs.read", "drs.access"]  # fmt: skip
USERS = {
    "alice": {"scopes": ALL_SCOPES, "visas": [{"value": DS1}]},
    "bob": {"scopes": ALL_SCOPES, "visas": [{"value": DS2}]},
    "carol": {"scopes": []},
    "mallory": {"scopes": ALL_SCOPES, "visas": [{"value": DS1, "issuer": "untrusted"}]},
}
# Test secret (AAI requires 32+ characters, D-055 (4))
TEST_CLIENT_SECRET = "test-client-secret-0123456789abcdef"
WES_CLIENT_SECRET = "wes-client-secret-0123456789abcdefgh"
TES_CLIENT_SECRET = "tes-client-secret-0123456789abcdefgh"
CLIENTS = {
    "yai-test-client": {"secret": TEST_CLIENT_SECRET},
    "yai-wes": {
        "secret": WES_CLIENT_SECRET,
        "audiences": ["yai-tes", "yai-drs"],
        # with the PSAT scopes the exchanged token lets the TES fetch the user's Passport (D-069)
        "scopes": ["tes.submit", "tes.read", "tes.cancel", "openid", "ga4gh_passport_v1"],
    },
    "yai-tes": {"secret": TES_CLIENT_SECRET},  # client authentication for the exchange (D-069)
}
EXCHANGE = "urn:ietf:params:oauth:grant-type:token-exchange"


def start_aai(tmp: Path, **extra: Any) -> Server:
    port = free_port()
    cfg = {
        "issuer": f"http://127.0.0.1:{port}/",
        "keys_dir": str(tmp / "aai-keys"),
        "users": USERS,
        "clients": CLIENTS,
        "test_endpoints": True,
        "test_visa_endpoint": True,  # used by the revoke-at-issuer test (test_drs_revocation)
        **extra,
    }
    return Server("aai", cfg, tmp / "aai", port=port).start()


def auth_config(aai: Server, audience: str) -> dict[str, Any]:
    iss = aai.root + "/"
    return {
        "mode": "aai",
        "issuer": iss,
        "jwks_uri": iss + ".well-known/jwks.json",
        "audience": audience,
        "insecure_http_hosts": [f"127.0.0.1:{aai.port}"],
    }


def trust_config(aai: Server) -> dict[str, Any]:
    iss = aai.root + "/"
    return {
        "brokers": [{"iss": iss, "jwks_uri": iss + ".well-known/jwks.json"}],
        "visa_issuers": [{"iss": iss + "visa/", "jku_allow": [iss + "visa/jwks.json"]}],
        "trusted_sources": ["https://yai.test/dac"],
        "accepted_by": ["dac"],
        "insecure_http_hosts": [f"127.0.0.1:{aai.port}"],
    }


def drs_config(aai: Server, tmp: Path, port: int, **extra: Any) -> dict[str, Any]:
    return {
        "data_dir": str(tmp / "drs-data"),
        "public_base_url": f"http://127.0.0.1:{port}",
        "hostname": "drs.yai.test",
        "auth": auth_config(aai, "yai-drs"),
        "trust": trust_config(aai),
        "test_endpoints": True,
        **extra,
    }


def register(config_path: Path, file: Path, *args: str) -> str:
    out = subprocess.run(
        [sys.executable, "-m", "yai", "drs", "register", str(file), "--config", str(config_path),
         *args],
        check=True, capture_output=True, text=True,
    )  # fmt: skip
    return out.stdout.strip()


def write_config(path: Path, cfg: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    return path


class Creds:
    def __init__(self, aai: Server):
        self.aai = aai

    def login(self, user: str, aud: list[str], passport_aud: Sequence[str] = ()) -> dict[str, Any]:
        body = {"username": user, "aud": aud, "passport_aud": list(passport_aud)}
        r = httpx.post(self.aai.url + "/test/login", json=body)
        r.raise_for_status()
        return r.json()

    def at(self, user: str, aud: str) -> str:
        return self.login(user, [aud])["access_tokens"][aud]

    def psat_for(self, user: str, aud: str) -> str:
        """AT for `aud` that also carries the PSAT scopes: the user lets that service fetch their
        Passport (D-069)."""
        return self.login(user, [aud], [aud])["access_tokens"][aud]

    def passport(self, user: str) -> str:
        psat = self.login(user, [])["passport_scoped_access_token"]
        basic = base64.b64encode(f"yai-test-client:{TEST_CLIENT_SECRET}".encode()).decode()
        r = httpx.post(
            self.aai.url + "/token",
            data={
                "grant_type": EXCHANGE,
                "requested_token_type": "urn:ga4gh:params:oauth:token-type:passport",
                "subject_token": psat,
                "subject_token_type": "urn:ietf:params:oauth:token-type:access_token",
            },
            headers={"Authorization": f"Basic {basic}"},
        )
        r.raise_for_status()
        return r.json()["access_token"]

    def delegated_at(self, user: str, aud: str = "yai-tes") -> str:
        """AT for `aud` with sub=user and act.sub=yai-wes: the user's AT issued to the WES,
        exchanged by the WES (RFC 8693, D-067)."""
        subject = self.at(user, "yai-wes")
        r = httpx.post(
            self.aai.url + "/token",
            data={"grant_type": EXCHANGE, "subject_token": subject, "audience": aud,
                  "subject_token_type": "urn:ietf:params:oauth:token-type:access_token",
                  "requested_token_type": "urn:ietf:params:oauth:token-type:access_token"},
            auth=("yai-wes", WES_CLIENT_SECRET),
        )  # fmt: skip
        r.raise_for_status()
        return r.json()["access_token"]

    def service_at(self, aud: str) -> str:
        r = httpx.post(
            self.aai.url + "/token",
            data={"grant_type": "client_credentials", "audience": aud},
            auth=("yai-wes", WES_CLIENT_SECRET),
        )
        r.raise_for_status()
        return r.json()["access_token"]


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}
