"""drs:// inputs resolved by the TES itself (D-069, a yai extension; tes.md section 2).

TES 1.1 has no field for the credentials an input needs (G-TES-05), so a TES that accepts
drs:// must obtain them itself. The caller's Bearer must then be a Passport-Scoped Access Token
of the user (the user's own, or one a service exchanged for the user with `act`); yai-tes
exchanges it at the broker for the user's Passport exactly as AAI 1.2 prescribes for a client
(client authentication, `requested_token_type=passport`), resolves each drs:// with the shared
resolver as the WES does (OPTIONS first, Passports only to allowed hosts, no redirects), and
keeps only the signed URL that comes back. Neither the Passport nor the caller's token is stored
or logged.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from yai.common.errors import ApiError
from yai.common.resolver import DrsResolver, ResolveError

log = logging.getLogger("yai.tes")
TOKEN_EXCHANGE = "urn:ietf:params:oauth:grant-type:token-exchange"  # noqa: S105 a type name, not a secret
PASSPORT_TOKEN_TYPE = "urn:ga4gh:params:oauth:token-type:passport"  # noqa: S105 a type name, not a secret
ACCESS_TOKEN_TYPE = "urn:ietf:params:oauth:token-type:access_token"  # noqa: S105 a type name, not a secret


@dataclass
class DrsInputs:
    token_url: str
    client_id: str
    client_secret: str
    resolver: DrsResolver
    audience: str = "yai-drs"
    timeout: float = 10.0

    def passport(self, subject_token: str) -> str:
        """The caller's Passport, exchanged from their access token as this service."""
        form = {
            "grant_type": TOKEN_EXCHANGE,
            "requested_token_type": PASSPORT_TOKEN_TYPE,
            "subject_token": subject_token,
            "subject_token_type": ACCESS_TOKEN_TYPE,
            "audience": self.audience,
        }
        try:
            r = httpx.post(self.token_url, data=form, auth=(self.client_id, self.client_secret),
                           timeout=self.timeout)  # fmt: skip
        except httpx.HTTPError as exc:
            log.warning("passport exchange: broker unreachable (%s)", type(exc).__name__)
            raise ApiError(500, "the token broker could not be reached") from exc
        try:
            body = r.json()
        except ValueError:
            body = None
        if not isinstance(body, dict):
            body = {}
        if r.status_code == 200:
            token = body.get("access_token")
            if isinstance(token, str) and token:
                return token
            raise ApiError(500, "the token broker answered without a passport")
        error = str(body.get("error", ""))
        if error in ("invalid_grant", "invalid_target"):
            # not a valid PSAT any more (revoked, D-070) or an audience not allowed
            msg = "drs:// inputs: the caller's token cannot be exchanged for a passport"
            raise ApiError(403, msg)
        log.warning("passport exchange failed: %s %s", r.status_code, error)
        raise ApiError(500, "the token broker refused the passport exchange")

    def resolve(self, uris: list[tuple[int, str]], subject_token: str) -> dict[int, str]:
        """(input index, drs://) -> signed URL for each. One Passport per task, then dropped."""
        if not uris:
            return {}
        passport = self.passport(subject_token)
        out: dict[int, str] = {}
        for i, uri in uris:
            try:
                out[i] = self.resolver.resolve(uri, [passport])
            except ResolveError as exc:
                if exc.status in (401, 403):
                    raise ApiError(403, f"inputs[{i}].url: the DRS refused access") from exc
                if exc.status is not None and exc.status >= 500:  # unreachable, slow or failing
                    raise ApiError(500, f"inputs[{i}].url: {exc}") from exc
                raise ApiError(400, f"inputs[{i}].url: {exc}") from exc
        return out
