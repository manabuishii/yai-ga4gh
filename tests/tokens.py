"""Helpers that build JWTs on the test side (trusted, untrusted and broken ones, freely)."""

from __future__ import annotations

import base64
import json
import time
from typing import Any

from joserfc import jwt
from joserfc.jwk import ECKey, OctKey, RSAKey


def now() -> int:
    return int(time.time())


class Issuer:
    """An issuer holding a key pair. Put jwks() on a KeyServer to use it."""

    def __init__(self, iss: str, kid: str = "k1", kind: str = "EC"):
        self.iss = iss
        if kind == "RSA":
            self.key = RSAKey.generate_key(2048, parameters={"kid": kid}, private=True)
            self.alg = "RS256"
        else:
            self.key = ECKey.generate_key("P-256", parameters={"kid": kid}, private=True)
            self.alg = "ES256"
        self.kid = kid

    def jwks(self) -> dict[str, Any]:
        return {"keys": [self.key.as_dict(private=False)]}

    def sign(self, claims: dict[str, Any], **header: Any) -> str:
        head = {"alg": self.alg, "kid": self.kid, **header}
        head = {k: v for k, v in head.items() if v is not None}
        return jwt.encode(head, claims, self.key)

    def visa(
        self,
        jku: str | None,
        value: str = "https://yai.test/datasets/ds1",
        *,
        sub: str = "alice",
        ttl: int = 7200,
        typ: str | None = "vnd.ga4gh.visa+jwt",
        visa: dict[str, Any] | None = None,
        drop: tuple[str, ...] = (),
        **extra: Any,
    ) -> str:
        t = now()
        body = {
            "type": "ControlledAccessGrants",
            "asserted": t - 10,
            "value": value,
            "source": "https://yai.test/dac",
            "by": "dac",
        }
        body.update(visa or {})
        claims = {"iss": self.iss, "sub": sub, "iat": t, "exp": t + ttl, "ga4gh_visa_v1": body}
        claims.update(extra)
        for name in drop:
            claims.pop(name, None)
            body.pop(name, None)
        return self.sign(claims, typ=typ, jku=jku)

    def passport(
        self,
        visas: list[str],
        *,
        sub: str = "alice",
        ttl: int = 7200,
        aud: Any = ("yai-drs",),
        typ: str | None = "vnd.ga4gh.passport+jwt",
        drop: tuple[str, ...] = (),
        **extra: Any,
    ) -> str:
        t = now()
        claims: dict[str, Any] = {
            "iss": self.iss,
            "sub": sub,
            "iat": t,
            "exp": t + ttl,
            "ga4gh_passport_v1": visas,
        }
        if aud is not None:
            claims["aud"] = list(aud) if isinstance(aud, tuple) else aud
        claims.update(extra)
        for name in drop:
            claims.pop(name, None)
        return self.sign(claims, typ=typ)

    def access_token(self, aud: str, scope: str = "", *, sub: str = "alice", ttl: int = 900,
                     typ: str = "at+jwt", **extra: Any) -> str:  # fmt: skip
        t = now()
        claims = {"iss": self.iss, "sub": sub, "aud": aud, "iat": t, "exp": t + ttl,
                  "scope": scope, "client_id": "test"}  # fmt: skip
        claims.update(extra)
        return self.sign(claims, typ=typ)


def b64(data: dict[str, Any]) -> str:
    return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()


def alg_none(claims: dict[str, Any], **header: Any) -> str:
    return f"{b64({'alg': 'none', **header})}.{b64(claims)}."


def hs256_with_public_key(issuer: Issuer, claims: dict[str, Any], **header: Any) -> str:
    """The classic attack that uses the public key as the HMAC secret."""
    secret = json.dumps(issuer.key.as_dict(private=False), sort_keys=True).encode()
    key = OctKey.import_key(secret)
    return jwt.encode({"alg": "HS256", **header}, claims, key, algorithms=["HS256"])
