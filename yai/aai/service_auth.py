"""The real R1 and R2 used by each service (verify Bearer yai ATs; aai.md sections 2 and 3).

Settings (auth in each service's config):
    mode: aai | anonymous | static-bearer   (anonymous and static-bearer are fakes, common/fakes.py)
    issuer: AT issuer (exact match with the yai-aai issuer)
    jwks_uri: its JWKS
    audience: own service name (yai-drs, yai-tes, yai-wes)
    insecure_http_hosts: hosts allowed over http, unit tests only
"""

from __future__ import annotations

from typing import Any

from fastapi import Request

from yai.aai.clearinghouse import AccessTokenVerifier
from yai.aai.jwtutil import TokenError
from yai.aai.keyfetch import KeyFetcher
from yai.common.errors import ApiError
from yai.common.roles import CredentialKind, Principal, VerifiedCredential
from yai.common.timeutil import Clock


def bearer_token(request: Request) -> str | None:
    header = request.headers.get("authorization")
    if header is None:
        return None
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise ApiError(401, "malformed authorization header")
    return token.strip()


class JwtAuthenticator:
    """R1: yai AT from Authorization: Bearer. Empty list if absent (R3 decides if it is needed)."""

    def __init__(self, auth: dict[str, Any], clock: Clock, *, passport_scoped: bool = False):
        """passport_scoped: accept a Passport-Scoped Access Token addressed to this service. Only
        the TES that exchanges it for the caller's Passport passes True (D-069); it is not a
        configuration key, so no other service can be made to accept PSATs on its Bearer."""
        fetcher = KeyFetcher(
            cache_seconds=int(auth.get("key_cache_seconds", 3600)),
            refetch_min_interval=int(auth.get("key_refetch_min_interval_seconds", 60)),
            insecure_http_hosts=auth.get("insecure_http_hosts", []),
        )
        self.verifier = AccessTokenVerifier(
            auth["issuer"], auth["jwks_uri"], auth["audience"], fetcher,
            passport_scoped=passport_scoped,
        )  # fmt: skip
        self.clock = clock

    def verify_token(self, token: str) -> VerifiedCredential:
        try:
            claims = self.verifier.verify(token, self.clock.now())
        except TokenError as exc:
            # reason details are not exposed (aai.md section 8)
            raise ApiError(401, "invalid access token") from exc
        service = claims.get("client_id") == claims["sub"]
        scope = claims.get("scope", "")
        act = claims.get("act")
        return VerifiedCredential(
            kind=CredentialKind.AT_SERVICE if service else CredentialKind.AT_USER,
            iss=claims["iss"],
            sub=claims["sub"],
            scopes=frozenset(scope.split()) if isinstance(scope, str) else frozenset(),
            exp=claims["exp"],
            client_id=claims.get("client_id"),
            actor=act["sub"] if isinstance(act, dict) else None,  # shape checked by the verifier
        )

    def authenticate(self, request: Request) -> list[VerifiedCredential]:
        token = bearer_token(request)
        return [] if token is None else [self.verify_token(token)]


class AtPrincipalResolver:
    """R2: Principal from one AT. None if absent."""

    def resolve(self, credentials: list[VerifiedCredential]) -> Principal | None:
        for c in credentials:
            if c.kind in (CredentialKind.AT_USER, CredentialKind.AT_SERVICE):
                kind = "service" if c.kind == CredentialKind.AT_SERVICE else "user"
                return Principal(c.iss, c.sub, kind, c.scopes, credentials=(c,), actor=c.actor)
        return None
