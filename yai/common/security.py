"""Shared entry applying authn/authz to each operation (calls the four roles in order)."""

from __future__ import annotations

from typing import Any

from fastapi import Request

from yai.common.errors import ApiError
from yai.common.roles import Decision, Principal, Roles


def authenticate(roles: Roles, request: Request) -> Principal:
    creds = roles.authenticator.authenticate(request)
    principal = roles.resolver.resolve(creds)
    if principal is None:
        raise ApiError(401, "authentication required")
    return principal


def authorize(
    roles: Roles, principal: Principal, operation: str, resource: dict[str, Any] | None = None
) -> None:
    decision = roles.authorizer.decide(principal, operation, resource)
    if decision is Decision.NOT_VISIBLE:
        raise ApiError(404, "not found")
    if decision is Decision.FORBIDDEN:
        raise ApiError(403, "forbidden")
