"""Shared parts of the interop scripts (run inside a container). Gets credentials from yai's
test endpoint and writes results as JSON to /out."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx

AAI = "https://aai.yai.test"
SECRETS = Path(os.environ.get("YAI_SECRETS", "/yai/local/secrets"))
OUT = Path(os.environ.get("YAI_REPORT_DIR", "/out"))


def login(user: str, aud: list[str]) -> dict[str, Any]:
    r = httpx.post(AAI + "/test/login", json={"username": user, "aud": aud}, timeout=20)
    r.raise_for_status()
    return r.json()


def access_token(user: str, aud: str) -> str:
    return login(user, [aud])["access_tokens"][aud]


def passport(user: str) -> str:
    psat = login(user, [])["passport_scoped_access_token"]
    data = {
        "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
        "requested_token_type": "urn:ga4gh:params:oauth:token-type:passport",
        "subject_token": psat,
        "subject_token_type": "urn:ietf:params:oauth:token-type:access_token",
    }
    secret = (SECRETS / "test-client-secret").read_text().strip()
    r = httpx.post(AAI + "/token", data=data, auth=("yai-test-client", secret), timeout=20)
    r.raise_for_status()
    return r.json()["access_token"]


class Report:
    """Report for one pairing. step() records pass/fail and excerpts for each check."""

    def __init__(self, pair: str, partner: str):
        self.doc: dict[str, Any] = {
            "pair": pair,
            "partner": partner,
            "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "steps": [],
        }

    def step(self, name: str, ok: bool, **detail: Any) -> None:
        self.doc["steps"].append({"name": name, "ok": ok, **detail})
        print(("ok   " if ok else "FAIL ") + name, file=sys.stderr)

    def save(self, name: str) -> int:
        self.doc["finished"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self.doc["passed"] = sum(1 for s in self.doc["steps"] if s["ok"])
        self.doc["failed"] = sum(1 for s in self.doc["steps"] if not s["ok"])
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / name).write_text(json.dumps(self.doc, ensure_ascii=False, indent=1, default=str))
        print(f"{name}: passed={self.doc['passed']} failed={self.doc['failed']}", file=sys.stderr)
        return 0 if self.doc["failed"] == 0 else 1


def excerpt(obj: Any, limit: int = 600) -> str:
    s = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False, default=str)
    return s if len(s) <= limit else s[:limit] + "…"
