"""The four roles (design/aai.md section 2). Shapes in docs/data/principal.schema.json.

R1 receive and verify credentials   -> list of VerifiedCredential, or 401
R2 user and verified attributes     -> Principal
R3 allow per resource and operation -> Decision (allow / 403 / 404)
R4 prepare downstream credentials   -> what to attach to the request

Swap the internals to move from the fakes (fakes below) to the real implementation.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Protocol

from fastapi import Request


class CredentialKind(enum.StrEnum):
    NONE = "none"
    AT_USER = "at_user"
    AT_SERVICE = "at_service"
    PASSPORT = "passport"
    STATIC_BEARER = "static_bearer"


@dataclass(frozen=True)
class AcceptedVisa:
    """A Visa that passed checks (ControlledAccessGrants etc.). Does not hold the token itself."""

    iss: str
    sub: str
    type: str
    value: str
    source: str
    by: str | None
    asserted: int
    exp: int
    jti: str


@dataclass(frozen=True)
class VerifiedCredential:
    kind: CredentialKind
    iss: str = ""
    sub: str = ""
    scopes: frozenset[str] = frozenset()
    exp: int | None = None
    visas: tuple[AcceptedVisa, ...] = ()
    client_id: str | None = None
    actor: str | None = None  # `act.sub` of a delegated AT: the service acting for sub (D-067)


@dataclass(frozen=True)
class Principal:
    iss: str
    sub: str
    kind: str  # "user" | "service" | "anonymous"
    scopes: frozenset[str] = frozenset()
    visas: tuple[AcceptedVisa, ...] = ()
    credentials: tuple[VerifiedCredential, ...] = field(default=(), compare=False)
    actor: str | None = None  # service acting for this user (`act.sub` of the AT), else None

    @property
    def owner(self) -> tuple[str, str]:
        return (self.iss, self.sub)

    @property
    def actor_ref(self) -> str | None:
        """`<iss>|<sub>` of the acting service, or None when the subject acts for itself."""
        return None if self.actor is None else f"{self.iss}|{self.actor}"

    @property
    def self_ref(self) -> str:
        """`<iss>|<sub>` of this principal (what a task stores as its actor when it submits)."""
        return f"{self.iss}|{self.sub}"

    def to_json(self) -> dict[str, Any]:
        return {
            "iss": self.iss,
            "sub": self.sub,
            "kind": self.kind,
            "scopes": sorted(self.scopes),
            "visas": [v.__dict__ for v in self.visas],
            "actor": self.actor,
        }


class Decision(enum.Enum):
    ALLOW = "allow"
    FORBIDDEN = "forbidden"  # 403: visible, but no permission for that operation
    NOT_VISIBLE = "not_visible"  # 404: does not leak existence (D-013)


class Authenticator(Protocol):  # R1
    # synchronous (may fetch keys, so handlers are def and run in threads)
    def authenticate(self, request: Request) -> list[VerifiedCredential]: ...


class PrincipalResolver(Protocol):  # R2
    def resolve(self, credentials: list[VerifiedCredential]) -> Principal | None: ...


class Authorizer(Protocol):  # R3
    def decide(
        self, principal: Principal | None, operation: str, resource: dict[str, Any] | None
    ) -> Decision: ...


class DownstreamCredentials(Protocol):  # R4
    def for_target(self, principal: Principal, target: str) -> dict[str, Any]: ...


@dataclass
class Roles:
    authenticator: Authenticator
    resolver: PrincipalResolver
    authorizer: Authorizer
    downstream: DownstreamCredentials
