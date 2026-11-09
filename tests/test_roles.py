"""Stub implementations of the 4 roles (aai.md section 2): all public, fixed Bearer,
valid but unauthorized, different user.

Roles are components (a library), so they are mounted on a small test app and called over HTTP.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import Request
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from yai.common.app import base_app
from yai.common.fakes import fake_roles
from yai.common.security import authenticate, authorize

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = json.loads((ROOT / "docs/data/principal.schema.json").read_text(encoding="utf-8"))
OPS = {"read": {"scope": ["x.read"], "owner": True}}
TOKENS = {
    "tok-alice": {"sub": "alice", "scopes": ["x.read"]},
    "tok-bob": {"sub": "bob", "scopes": ["x.read"]},
    "tok-carol": {"sub": "carol", "scopes": []},
}


def make_client(auth: dict) -> TestClient:
    roles = fake_roles(auth, OPS)
    app = base_app("roles-test")

    @app.get("/things/{owner}")
    def read(owner: str, request: Request):
        principal = authenticate(roles, request)
        resource = {"owner": ("urn:yai:fake", owner)}
        authorize(roles, principal, "read", resource)
        return principal.to_json()

    return TestClient(app)


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_fake_anonymous_allows_everything():
    # All public: passes without credentials; R3 allows everything without checking the owner
    c = make_client({"mode": "anonymous"})
    assert c.get("/things/anyone").status_code == 200
    assert c.get("/things/anyone", headers=bearer("whatever")).status_code == 200


def test_fake_static_bearer():
    c = make_client({"mode": "static-bearer", "tokens": TOKENS})
    assert c.get("/things/alice").status_code == 401  # no credentials
    assert c.get("/things/alice", headers=bearer("wrong")).status_code == 401
    r = c.get("/things/alice", headers=bearer("tok-alice"))
    assert r.status_code == 200
    Draft202012Validator(SCHEMA["$defs"]["Principal"]).validate(r.json())


def test_fake_valid_but_no_permission():
    c = make_client({"mode": "static-bearer", "tokens": TOKENS})
    r = c.get("/things/carol", headers=bearer("tok-carol"))
    assert r.status_code == 403
    assert r.json() == {"msg": "forbidden", "status_code": 403}


def test_fake_other_user_is_not_visible():
    c = make_client({"mode": "static-bearer", "tokens": TOKENS})
    r = c.get("/things/alice", headers=bearer("tok-bob"))
    assert r.status_code == 404  # D-013: other users' resources give 404


@pytest.mark.parametrize("name", ["Principal", "VerifiedCredential", "AcceptedVisa"])
def test_principal_schema_is_valid(name):
    Draft202012Validator.check_schema(SCHEMA)
    assert name in SCHEMA["$defs"]
