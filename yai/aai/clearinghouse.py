"""Clearinghouse parts (aai.md sections 3 and 4). A library embedded in yai-drs.

The token kind is decided by "where it came from" (never guessed from content):
- verify_access_token: what came in Authorization: Bearer (a yai AT)
- verify_passport:     what came in DRS body passports or WES multipart yai_passports
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from joserfc import jwt
from joserfc.errors import JoseError

from yai.aai.jwtutil import (
    ALLOWED_ALGS,
    TokenError,
    check_times,
    scopes_of,
    token_ref,
    unverified_parts,
)
from yai.aai.keyfetch import KeyFetcher
from yai.common.roles import AcceptedVisa

PASSPORT_TYP = "vnd.ga4gh.passport+jwt"
VISA_TYPS = {None, "vnd.ga4gh.visa+jwt", "JWT"}  # G-AAI-05: accept absent, recommended and JWT
PSAT_SCOPE = "ga4gh_passport_v1"


@dataclass(frozen=True)
class TrustConfig:
    """Contents of config/trust.yaml (aai.md section 5)."""

    brokers: dict[str, str]  # iss -> jwks_uri
    visa_issuers: dict[str, list[str]]  # iss -> allowed jku list
    trusted_sources: frozenset[str]
    accepted_by: frozenset[str] = frozenset({"dac"})
    clock_skew: int = 60
    passport_aud_required: bool = False
    audience: str = "yai-drs"
    max_visas_per_passport: int = 100  # above this, reject the Passport (D-058 prov., G-AAI-10)

    @classmethod
    def from_dict(cls, d: dict[str, Any], audience: str = "yai-drs") -> TrustConfig:
        return cls(
            brokers={b["iss"]: b["jwks_uri"] for b in d.get("brokers", [])},
            visa_issuers={
                v["iss"]: list(v.get("jku_allow", [])) for v in d.get("visa_issuers", [])
            },
            trusted_sources=frozenset(d.get("trusted_sources", [])),
            accepted_by=frozenset(d.get("accepted_by", ["dac"])),
            clock_skew=int(d.get("clock_skew_seconds", 60)),
            passport_aud_required=bool(d.get("passport_aud_required", False)),
            audience=audience,
            max_visas_per_passport=int(d.get("max_visas_per_passport", 100)),
        )


@dataclass(frozen=True)
class VerifiedPassport:
    iss: str
    sub: str
    exp: int
    ref: str
    visas: tuple[AcceptedVisa, ...]
    ignored: tuple[str, ...] = field(default=())  # reasons for ignored Visas (for the record)


def _verify_signature(token: str, header: dict[str, Any], fetcher: KeyFetcher, url: str):
    alg = header.get("alg")
    if alg not in ALLOWED_ALGS:
        raise TokenError("bad_signature", f"alg {alg!r} not allowed")
    keys = fetcher.key(url, header.get("kid"))
    try:
        return jwt.decode(token, keys, algorithms=[alg])
    except (JoseError, ValueError) as exc:
        raise TokenError("bad_signature", type(exc).__name__) from exc


class Clearinghouse:
    def __init__(self, trust: TrustConfig, fetcher: KeyFetcher):
        self.trust = trust
        self.fetcher = fetcher

    # --- Passport (AAI-CH-02, AAI-FMT-01/02, PP-CH-01) ---
    def verify_passport(self, token: str, now: int) -> VerifiedPassport:
        header, claims = unverified_parts(token)
        if header.get("typ") != PASSPORT_TYP:
            raise TokenError("wrong_token_kind", "typ is not a passport")
        iss = claims.get("iss")
        if iss not in self.trust.brokers:
            raise TokenError("untrusted_issuer", "passport issuer")
        decoded = _verify_signature(token, header, self.fetcher, self.trust.brokers[iss])
        claims = decoded.claims
        check_times(claims, now, self.trust.clock_skew)
        if not isinstance(claims.get("sub"), str) or not claims["sub"]:
            raise TokenError("malformed", "sub")
        aud = claims.get("aud")
        if aud is None:
            if self.trust.passport_aud_required:
                raise TokenError("wrong_audience", "aud required")
        else:
            auds = [aud] if isinstance(aud, str) else aud
            if not isinstance(auds, list) or self.trust.audience not in auds:
                raise TokenError("wrong_audience")
        visas_raw = claims.get("ga4gh_passport_v1")
        if not isinstance(visas_raw, list):
            raise TokenError("malformed", "ga4gh_passport_v1 must be an array")
        # Reject by count before verifying any Visa signature (D-058 provisional, G-AAI-10). The
        # count is of the array signed by a trusted Broker (Passport signature, times, aud checked
        # above).
        # Ignoring only the excess would silently drop needed Visas and make results order-dependent
        # so the whole Passport is rejected
        if len(visas_raw) > self.trust.max_visas_per_passport:
            raise TokenError("malformed", "too many visas")
        accepted: list[AcceptedVisa] = []
        ignored: list[str] = []
        for raw in visas_raw:
            try:
                accepted.append(self.verify_visa(raw, now))
            except TokenError as exc:  # PP-CH-02/03: ignore only that Visa
                ignored.append(exc.reason)
            except (TypeError, ValueError, KeyError):  # one broken Visa must not fail all
                ignored.append("malformed")
        return VerifiedPassport(
            iss=iss,
            sub=claims["sub"],
            exp=claims["exp"],
            ref=token_ref(token, claims),
            visas=tuple(accepted),
            ignored=tuple(ignored),
        )

    def verify_passports(self, tokens: Any, now: int) -> list[VerifiedPassport]:
        """All passports of the request. If any one fails, reject the whole request (PP-CH-01)."""
        if not isinstance(tokens, list):
            raise TokenError("malformed", "passports must be an array")
        return [self.verify_passport(t, now) for t in tokens]

    # --- Visa Document Token (AAI-CH-05/07, AAI-FMT-05, PP-VISA-01 to 06) ---
    def verify_visa(self, token: Any, now: int) -> AcceptedVisa:
        if not isinstance(token, str):
            raise TokenError("malformed", "visa is not a string")
        header, claims = unverified_parts(token)
        jku = header.get("jku")
        if not jku:
            # no jku: treated as a Visa Access Token and ignored (G-AAI-02)
            raise TokenError("visa_access_token_ignored")
        if header.get("typ") not in VISA_TYPS:
            raise TokenError("wrong_token_kind", "visa typ")
        iss = claims.get("iss")
        allowed = self.trust.visa_issuers.get(iss) if isinstance(iss, str) else None
        if iss is None or allowed is None:
            raise TokenError("untrusted_issuer", "visa issuer")
        if jku not in allowed:
            # AAI-CH-07: check before fetching (no requests to hosts not in the allow list)
            raise TokenError("untrusted_jku")
        decoded = _verify_signature(token, header, self.fetcher, jku)
        claims = decoded.claims
        if claims.get("iss") != iss:
            raise TokenError("malformed", "iss changed")
        check_times(claims, now, self.trust.clock_skew)
        if not isinstance(claims.get("sub"), str) or not claims["sub"]:
            raise TokenError("malformed", "sub")
        if "openid" in scopes_of(claims):
            raise TokenError("wrong_token_kind", "visa with openid scope")  # AAI-FMT-05
        visa = claims.get("ga4gh_visa_v1")
        if not isinstance(visa, dict):
            raise TokenError("malformed", "ga4gh_visa_v1")
        asserted = visa.get("asserted")
        if not isinstance(asserted, int) or isinstance(asserted, bool):
            raise TokenError("malformed", "asserted")  # PP-VISA-05
        if asserted > now + self.trust.clock_skew:
            raise TokenError("malformed", "asserted in the future")
        for name in ("type", "value", "source"):
            if not isinstance(visa.get(name), str) or not visa[name]:
                raise TokenError("malformed", name)
        by = visa.get("by")
        if visa["type"] == "ControlledAccessGrants" and not isinstance(by, str):
            raise TokenError("malformed", "by required")  # PP-VISA-04
        # URL Claim (Passport URL Claims section): source always, value for ControlledAccessGrants.
        # URL form, at most 255 characters. Not fetched
        if not url_claim_ok(visa["source"]):
            raise TokenError("malformed", "source is not a URL claim")
        if visa["type"] == "ControlledAccessGrants" and not url_claim_ok(visa["value"]):
            raise TokenError("malformed", "value is not a URL claim")
        if visa.get("conditions"):
            raise TokenError("conditions_ignored")  # MAY of PP-VISA-02 (D-018)
        return AcceptedVisa(
            iss=iss,
            sub=claims["sub"],
            type=visa["type"],
            value=visa["value"],
            source=visa["source"],
            by=by if isinstance(by, str) else None,
            asserted=asserted,
            exp=claims["exp"],
            jti=token_ref(token, claims),
        )

    # --- decision (aai.md section 4, Option B of PP-EXP-01) ---
    def grants_dataset(
        self, passport: VerifiedPassport, dataset: str, now: int, access_ttl: int
    ) -> AcceptedVisa | None:
        """Return one Visa that allows the dataset (None if none). One Visa per decision (D-018)."""
        for v in passport.visas:
            if v.type != "ControlledAccessGrants":
                continue  # custom types and unneeded Visas are skipped (PP-CH-02, PP-VISA-03)
            if v.value != dataset:
                continue
            if v.source not in self.trust.trusted_sources or v.by not in self.trust.accepted_by:
                continue  # PP-CH-03
            if now + access_ttl + self.trust.clock_skew < min(v.exp, passport.exp):
                return v
        return None


class AccessTokenVerifier:
    """A yai AT (RFC 9068, typ=at+jwt). Requiring kid is a yai rule."""

    def __init__(
        self, issuer: str, jwks_uri: str, audience: str, fetcher: KeyFetcher, skew: int = 60,
        *, passport_scoped: bool = False,
    ):  # fmt: skip
        self.issuer = issuer
        self.jwks_uri = jwks_uri
        self.audience = audience
        self.fetcher = fetcher
        self.skew = skew
        # D-069: a service that exchanges the caller's token for their Passport (the TES for
        # drs:// inputs) accepts a Passport-Scoped Access Token addressed to it. Everyone else
        # refuses PSATs on the Bearer (D-008, G-AAI-01)
        self.passport_scoped = passport_scoped

    def verify(self, token: str, now: int) -> dict[str, Any]:
        header, claims = unverified_parts(token)
        typ = header.get("typ")
        if typ != "at+jwt":
            raise TokenError("wrong_token_kind", f"typ {typ!r} on bearer")
        if not header.get("kid"):
            raise TokenError("malformed", "kid required")
        if claims.get("iss") != self.issuer:
            raise TokenError("untrusted_issuer")
        claims = _verify_signature(token, header, self.fetcher, self.jwks_uri).claims
        check_times(claims, now, self.skew)
        aud = claims.get("aud")
        auds = [aud] if isinstance(aud, str) else aud
        if not isinstance(auds, list) or self.audience not in auds:
            raise TokenError("wrong_audience")
        if PSAT_SCOPE in scopes_of(claims) and not self.passport_scoped:
            raise TokenError("wrong_token_kind", "passport-scoped access token")  # G-AAI-01
        if not isinstance(claims.get("sub"), str) or not claims["sub"]:
            raise TokenError("malformed", "sub")
        client_id = claims.get("client_id")  # the "content" in aai.md section 3
        if not isinstance(client_id, str) or not client_id:
            raise TokenError("malformed", "client_id")
        act = claims.get("act")  # RFC 8693 section 4.1: {"sub": <actor>, "act": {...}} (D-067)
        if act is not None and (not isinstance(act, dict) or not isinstance(act.get("sub"), str)
                                or not act["sub"]):  # fmt: skip
            raise TokenError("malformed", "act")
        if act is not None and client_id == claims["sub"]:
            raise TokenError("malformed", "act on a service token")
        return claims


def url_claim_ok(value: str) -> bool:
    """RFC 3986 URL form (has scheme and host), at most 255 characters, no whitespace."""
    if len(value) > 255 or any(c.isspace() for c in value):
        return False
    try:
        parts = urlsplit(value)
    except ValueError:  # e.g. a broken IPv6 (not a 500)
        return False
    return bool(parts.scheme) and parts.scheme.isascii() and bool(parts.netloc)
