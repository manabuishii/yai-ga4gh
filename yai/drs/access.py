"""DRS authorization (design/drs.md section 3, drs in permissions.yaml) and signed URLs.

- Policies: public, bearer, dataset. metadata_policy for metadata; access_policy for /access and
  the delivery endpoint
- Several credentials in one request are checked separately; any one meeting the policy allows it.
    If any check fails, the whole request is 401 (PP-CH-01, permissions.yaml
  multiple_credentials)
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from fastapi import Request

from yai.aai.clearinghouse import Clearinghouse, TrustConfig, VerifiedPassport
from yai.aai.jwtutil import TokenError
from yai.aai.keyfetch import KeyFetcher
from yai.aai.service_auth import JwtAuthenticator, bearer_token
from yai.common.config import auth_mode, load_secret
from yai.common.errors import ApiError
from yai.common.roles import VerifiedCredential
from yai.common.timeutil import Clock
from yai.drs.store import DrsRecord

log = logging.getLogger("yai.drs")
BEARER_SCOPES = {"drs.read", "drs.access"}
_SIG = re.compile(r"[0-9a-f]{64}")
_EXP = re.compile(r"[0-9]{1,12}")


@dataclass
class Credentials:
    at: VerifiedCredential | None = None
    passports: list[VerifiedPassport] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return self.at is None and not self.passports


class DrsAccess:
    def __init__(self, config: dict[str, Any], clock: Clock, root: Path):
        self.clock = clock
        auth = config.get("auth") or {}
        self.mode = auth_mode(config, ("aai", "anonymous"))
        self.jwt = JwtAuthenticator(auth, clock) if self.mode == "aai" else None
        self.at_issuer = auth.get("issuer")
        trust = config.get("trust", {})
        self.trust = TrustConfig.from_dict(trust, audience=auth.get("audience", "yai-drs"))
        fetcher = KeyFetcher(
            cache_seconds=int(trust.get("key_cache_seconds", 3600)),
            refetch_min_interval=int(trust.get("key_refetch_min_interval_seconds", 60)),
            insecure_http_hosts=trust.get("insecure_http_hosts", []),
        )
        self.clearinghouse = Clearinghouse(self.trust, fetcher)
        self.url_ttl = int(config.get("signed_url_ttl_seconds", 3600))  # D-019
        # D-072 (a yai extension): tell callers which Visa an object needs before they send a
        # Passport. Off when the dataset names must not be public
        authz = config.get("authorizations") or {}
        self.expose_dataset = bool(authz.get("expose_dataset", True))
        self.public_base = str(config.get("public_base_url", "http://127.0.0.1:8402")).rstrip("/")
        self.secret = load_secret(Path(config.get("url_secret_file", root / "url-secret")))

    # --- R1: receive and verify credentials ---
    def credentials(self, request: Request, passports: Any = None) -> Credentials:
        creds = Credentials()
        if self.jwt is None:  # provisional (everything public)
            return creds
        token = bearer_token(request)
        if token is not None:
            creds.at = self.jwt.verify_token(token)
        if passports is not None:
            try:
                creds.passports = self.clearinghouse.verify_passports(passports, self.clock.now())
            except TokenError as exc:
                log.info("passport rejected: %s", exc.reason)
                raise ApiError(401, "invalid passport") from exc
        return creds

    # --- R3: does the policy allow it ---
    def authorize(self, policy: str, record: DrsRecord, creds: Credentials, *, access: bool):
        """None if allowed, otherwise ApiError (401/403)."""
        if self.mode != "aai" or policy == "public":
            return
        if creds.empty:
            raise ApiError(401, "credentials required")
        if policy == "bearer":
            if creds.at is not None and creds.at.scopes & BEARER_SCOPES:
                return
            raise ApiError(403, "forbidden")
        if policy == "dataset":
            if not creds.passports:
                # an AT alone is not enough (a user Visa is needed, C-19). Passports: POST body
                raise ApiError(401, "passport required (send passports in a POST body)")
            ttl = self.url_ttl if access else 0
            now = self.clock.now()
            for p in creds.passports:
                visa = self.clearinghouse.grants_dataset(p, record.dataset or "", now, ttl)
                if visa is not None:
                    log.info("dataset granted by visa iss=%s jti=%s", visa.iss, visa.jti)
                    return
            raise ApiError(403, "forbidden")
        raise ApiError(403, "forbidden")

    def authorizations(self, record: DrsRecord, policy: str) -> dict[str, Any]:
        """OPTIONS and AccessMethod.authorizations (DRS-AUTH-02/04/05, D-072)."""
        out: dict[str, Any] = {"drs_object_id": record.id}
        if self.mode != "aai" or policy == "public":
            out["supported_types"] = ["None"]
        elif policy == "bearer":
            out["supported_types"] = ["BearerAuth"]
            if self.at_issuer:
                out["bearer_auth_issuers"] = [self.at_issuer]
        else:
            out["supported_types"] = ["PassportAuth"]
            out["passport_auth_issuers"] = sorted(self.trust.visa_issuers)
            if self.expose_dataset and record.dataset:
                # Not in DRS 1.5.0 (Authorizations has no "which Visa" field, G-DRS-17). A router
                # can pick a site by dataset without reading the Passport first (D-072)
                out["yai_required_visas"] = [
                    {"type": "ControlledAccessGrants", "value": record.dataset}
                ]
        return out

    # --- signed URLs (drs.md section 2) ---
    def signature(self, object_id: str, access_id: str, exp: int) -> str:
        msg = f"{object_id}|{access_id}|{exp}".encode()
        return hmac.new(self.secret, msg, hashlib.sha256).hexdigest()

    def blob_url(self, object_id: str) -> str:
        return f"{self.public_base}/yai/blobs/{object_id}"

    def signed_url(self, object_id: str, access_id: str) -> str:
        exp = self.clock.now() + self.url_ttl
        query = urlencode(
            {"exp": exp, "aid": access_id, "sig": self.signature(object_id, access_id, exp)}
        )
        return f"{self.blob_url(object_id)}?{query}"

    def check_signature(self, object_id: str, exp: str, aid: str, sig: str) -> int:
        # check the shape first (no exception on non-ASCII or length mismatch)
        if not _SIG.fullmatch(sig) or not _EXP.fullmatch(exp):
            raise ApiError(403, "invalid signature")
        exp_i = int(exp)
        expected = self.signature(object_id, aid, exp_i)
        if not hmac.compare_digest(expected.encode(), sig.encode()):
            raise ApiError(403, "invalid signature")
        if exp_i <= self.clock.now():
            raise ApiError(403, "url expired")
        return exp_i
