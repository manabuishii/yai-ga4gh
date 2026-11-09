"""Shared JWT steps (headers read before signature checks, algorithm rules)."""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

ALLOWED_ALGS = ("ES256", "RS256")  # AAI-FMT-03. none and HS algorithms are rejected


class TokenError(Exception):
    """Reason kinds (aai.md section 8): expired, untrusted_issuer, untrusted_jku, bad_signature,
    wrong_token_kind, no_matching_visa, malformed."""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason


def _b64(part: str) -> bytes:
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


def unverified_parts(token: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Read header and claims before verifying the signature (only to tell kinds and pick keys)."""
    if not isinstance(token, str) or token.count(".") != 2:
        raise TokenError("malformed", "not a JWS compact token")
    head, body, _ = token.split(".")
    try:
        header = json.loads(_b64(head))
        claims = json.loads(_b64(body))
    except ValueError as exc:
        raise TokenError("malformed", "bad base64 or json") from exc
    if not isinstance(header, dict) or not isinstance(claims, dict):
        raise TokenError("malformed", "header or claims is not an object")
    # Values used to tell kinds and pick keys must be "a string or absent" (arrays etc. must not
    # raise anything other than TokenError)
    for name in ("typ", "kid", "jku", "alg"):
        if name in header and not isinstance(header[name], str):
            raise TokenError("malformed", f"header {name} must be a string")
    for name in ("iss", "sub"):
        if name in claims and not isinstance(claims[name], str):
            raise TokenError("malformed", f"{name} must be a string")
    return header, claims


def token_ref(token: str, claims: dict[str, Any]) -> str:
    """Reference for the record: jti, otherwise the first 16 chars of the signature SHA-256
    (aai.md section 4)."""
    jti = claims.get("jti")
    if isinstance(jti, str) and jti:
        return jti
    return hashlib.sha256(token.rsplit(".", 1)[-1].encode()).hexdigest()[:16]


def scopes_of(claims: dict[str, Any]) -> frozenset[str]:
    scope = claims.get("scope", "")
    if isinstance(scope, str):
        return frozenset(scope.split())
    if isinstance(scope, list):
        return frozenset(str(s) for s in scope)
    return frozenset()


def check_times(claims: dict[str, Any], now: int, skew: int) -> None:
    """exp and iat are required integers. nbf is checked if present (aai.md section 3)."""
    for name in ("exp", "iat"):
        if not isinstance(claims.get(name), int) or isinstance(claims.get(name), bool):
            raise TokenError("malformed", f"{name} missing")
    if claims["exp"] + skew <= now:
        raise TokenError("expired")
    if claims["iat"] - skew > now:
        raise TokenError("malformed", "iat in the future")
    nbf = claims.get("nbf")
    if nbf is not None:
        if not isinstance(nbf, int) or isinstance(nbf, bool):
            raise TokenError("malformed", "nbf")
        if nbf - skew > now:
            raise TokenError("not_yet_valid")
