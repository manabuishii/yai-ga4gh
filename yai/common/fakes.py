"""Fake implementations of the four roles (for tests). Lets denial tests be written from the
start (aai.md section 2).

Selected by auth.mode in the settings:
- anonymous:     everything public. No credentials, fixed user anonymous, everything allowed
- static-bearer: fixed Bearer strings -> user table. Not in the table, or absent: 401
    The table can hold "valid but no permissions" (empty scopes) and "another user"
"""

from __future__ import annotations

import hmac
from typing import Any

from fastapi import Request

from yai.common.errors import ApiError
from yai.common.roles import (
    CredentialKind,
    Decision,
    Principal,
    Roles,
    VerifiedCredential,
)

FAKE_ISS = "urn:yai:fake"


class AnonymousAuthenticator:
    def authenticate(self, request: Request) -> list[VerifiedCredential]:
        return [VerifiedCredential(kind=CredentialKind.NONE)]


class StaticBearerAuthenticator:
    """tokens: {string: {sub, scopes}}."""

    def __init__(self, tokens: dict[str, dict[str, Any]]):
        self.tokens = tokens

    def authenticate(self, request: Request) -> list[VerifiedCredential]:
        header = request.headers.get("authorization", "")
        if not header.lower().startswith("bearer "):
            raise ApiError(401, "missing bearer token")
        given = header[7:].strip()
        for token, user in self.tokens.items():
            if hmac.compare_digest(token.encode(), given.encode()):
                return [
                    VerifiedCredential(
                        kind=CredentialKind.STATIC_BEARER,
                        iss=FAKE_ISS,
                        sub=user["sub"],
                        scopes=frozenset(user.get("scopes", [])),
                    )
                ]
        raise ApiError(401, "invalid bearer token")


class FixedPrincipalResolver:
    """Fake R2: the fixed user if the credential is none, its sub if static_bearer."""

    def __init__(self, anonymous_sub: str = "anonymous"):
        self.anonymous_sub = anonymous_sub

    def resolve(self, credentials: list[VerifiedCredential]) -> Principal | None:
        for c in credentials:
            if c.kind == CredentialKind.NONE:
                return Principal(FAKE_ISS, self.anonymous_sub, "anonymous", frozenset({"*"}))
            if c.kind == CredentialKind.STATIC_BEARER:
                return Principal(c.iss, c.sub, "user", c.scopes, credentials=(c,))
        return None


class AllowAll:
    def decide(self, principal, operation, resource) -> Decision:
        return Decision.ALLOW


class DenyAll:
    def decide(self, principal, operation, resource) -> Decision:
        return Decision.FORBIDDEN


class ScopeAndOwner:
    """Fake R3: checks only the required scope (`*` means all) and ownership.

    operations: {operation: {scope: [...], owner: bool}}. Operations not in the table get 403.
    """

    def __init__(self, operations: dict[str, dict[str, Any]]):
        self.operations = operations

    def decide(self, principal, operation, resource) -> Decision:
        rule = self.operations.get(operation)
        if rule is None or principal is None:
            return Decision.FORBIDDEN
        owned = bool(rule.get("owner")) and resource is not None
        if owned and resource and tuple(resource.get("owner", ())) != principal.owner:
            return Decision.NOT_VISIBLE
        need = set(rule.get("scope", []))
        if need and "*" not in principal.scopes and not need & principal.scopes:
            return Decision.FORBIDDEN
        return Decision.ALLOW


class FixedDownstream:
    def __init__(self, value: str = "fake-downstream"):
        self.value = value

    def for_target(self, principal, target) -> dict[str, Any]:
        return {"headers": {"Authorization": f"Bearer {self.value}"}}


def fake_roles(auth: dict[str, Any], operations: dict[str, dict[str, Any]]) -> Roles:
    mode = auth.get("mode", "anonymous")
    if mode == "anonymous":
        return Roles(
            AnonymousAuthenticator(), FixedPrincipalResolver(), AllowAll(), FixedDownstream()
        )
    if mode == "static-bearer":
        return Roles(
            StaticBearerAuthenticator(auth.get("tokens", {})),
            FixedPrincipalResolver(),
            ScopeAndOwner(operations),
            FixedDownstream(),
        )
    raise ValueError(f"unknown fake auth mode: {mode}")
