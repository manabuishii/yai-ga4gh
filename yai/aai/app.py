"""yai-aai: test Broker + Passport Issuer, Visa Issuer and AT issuance (design/aai.md section 6).

Only the Clearinghouse parts can claim AAI conformance. This is a test-only minimum (D-004,
G-AAI-09).
"""

from __future__ import annotations

import base64
import hmac
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from joserfc import jwt
from joserfc.jwk import KeySet

from yai.aai.jwtutil import TokenError, check_times, scopes_of, unverified_parts
from yai.aai.keys import SigningKeys
from yai.common import startup
from yai.common.app import base_app
from yai.common.body import json_body, limited_request
from yai.common.errors import ApiError
from yai.common.ids import ulid
from yai.common.startup import DAY
from yai.common.timeutil import Clock

NO_STORE = {"Cache-Control": "no-cache, no-store", "Pragma": "no-cache"}  # AAI-CLI-01
TOKEN_EXCHANGE = "urn:ietf:params:oauth:grant-type:token-exchange"  # noqa: S105 a type name, not a secret
PASSPORT_TOKEN_TYPE = "urn:ga4gh:params:oauth:token-type:passport"  # noqa: S105 a type name, not a secret
ACCESS_TOKEN_TYPE = "urn:ietf:params:oauth:token-type:access_token"  # noqa: S105 a type name, not a secret
PSAT_SCOPE = "openid ga4gh_passport_v1"
MAX_FORM = 1024 * 1024  # /token body limit (D-042, same as DRS)


class OAuthError(Exception):
    def __init__(self, status: int, error: str, description: str):
        super().__init__(description)
        self.status, self.error, self.description = status, error, description


def create_app(config: dict[str, Any]) -> FastAPI:
    startup.check("aai", config)  # reject before building the gate (D-055)
    app = base_app("yai-aai")
    app.state.config = config
    issuer = config.get("issuer", "http://127.0.0.1:8401/")
    keys_dir = Path(config.get("keys_dir", "var/aai/keys"))
    broker_keys = SigningKeys(keys_dir / "broker")
    visa_keys = SigningKeys(keys_dir / "visa")
    untrusted_keys = SigningKeys(keys_dir / "untrusted-visa")
    visa_iss = issuer + "visa/"
    untrusted_iss = issuer + "untrusted-visa/"
    ttl = config.get("ttl", {})
    at_ttl = int(ttl.get("access_token", 900))
    psat_ttl = int(ttl.get("psat", 900))
    passport_ttl = int(ttl.get("passport", 7200))
    visa_ttl = int(ttl.get("visa", 7200))
    users: dict[str, dict[str, Any]] = config.get("users", {})
    clients: dict[str, dict[str, Any]] = config.get("clients", {})
    # Test endpoints (D-054). /test/login and the untrusted-Visa JWKS are test_endpoints; replacing
    # Visas in the user table is test_visa_endpoint (local tests only). There is no clock endpoint
    test_endpoints = bool(config.get("test_endpoints", False))
    test_visa_endpoint = bool(config.get("test_visa_endpoint", False))
    clock = Clock()

    @app.exception_handler(OAuthError)
    async def _oauth_error(_: Request, exc: OAuthError) -> JSONResponse:
        body = {"error": exc.error, "error_description": exc.description}
        return JSONResponse(body, exc.status, headers=NO_STORE)

    def ok(body: dict[str, Any]) -> JSONResponse:
        return JSONResponse(body, 200, headers=NO_STORE)

    def access_token(sub: str, aud: str, scopes: list[str], client_id: str) -> str:
        now = clock.now()
        claims = {
            "iss": issuer,
            "sub": sub,
            "aud": aud,
            "iat": now,
            "exp": now + at_ttl,
            "jti": ulid(),
            "client_id": client_id,
            "scope": " ".join(scopes),
        }
        return broker_keys.sign(claims, "at+jwt")

    def visa(sub: str, spec: dict[str, Any]) -> str:
        now = clock.now()
        untrusted = spec.get("issuer") == "untrusted"
        keys, iss = (untrusted_keys, untrusted_iss) if untrusted else (visa_keys, visa_iss)
        body = {
            "type": spec.get("type", "ControlledAccessGrants"),
            "asserted": int(spec.get("asserted", now)),
            "value": spec["value"],
            "source": spec.get("source", "https://yai.test/dac"),
            "by": spec.get("by", "dac"),
        }
        if spec.get("conditions"):
            body["conditions"] = spec["conditions"]
        claims = {
            "iss": iss,
            "sub": spec.get("sub", sub),
            "iat": now,
            "exp": now + int(spec.get("ttl", visa_ttl)),
            "jti": ulid(),
            "ga4gh_visa_v1": body,
        }
        return keys.sign(claims, "vnd.ga4gh.visa+jwt", jku=iss + "jwks.json")

    def client_auth(request: Request, form: dict[str, Any]) -> str:
        """OAuth2 client authentication (Basic, or client_id and client_secret in the body)."""
        header = request.headers.get("authorization", "")
        cid = secret = None
        if header.lower().startswith("basic "):
            try:
                cid, _, secret = base64.b64decode(header[6:]).decode().partition(":")
            except ValueError:
                cid = None
        else:
            cid, secret = form.get("client_id"), form.get("client_secret")
        client = clients.get(cid or "")
        given = str(secret or "").encode("utf-8", "replace")  # compare as bytes
        if not client or not hmac.compare_digest(str(client["secret"]).encode(), given):
            raise OAuthError(401, "invalid_client", "client authentication failed")
        return str(cid)

    @app.get("/service-info")
    async def service_info() -> dict[str, Any]:
        # AAI has no service-info rule. Kept to match the shape of the other three
        return {
            "id": config.get("service_id", "test.yai.aai"),
            "name": "yai-aai",
            "type": {"group": "test.yai", "artifact": "aai-test-broker", "version": "1.2"},
            "organization": {"name": "yai (personal learning project)", "url": "https://yai.test/"},
            "version": "0.1.0",
        }

    @app.get("/.well-known/openid-configuration")
    async def discovery() -> dict[str, Any]:
        return {
            "issuer": issuer,
            "jwks_uri": issuer + ".well-known/jwks.json",
            "token_endpoint": issuer + "token",
            "grant_types_supported": [TOKEN_EXCHANGE, "client_credentials"],
            "id_token_signing_alg_values_supported": ["ES256"],
            "scopes_supported": ["openid", "ga4gh_passport_v1"],
        }

    @app.get("/.well-known/jwks.json")
    async def jwks() -> dict[str, Any]:
        return broker_keys.jwks()

    @app.get("/visa/jwks.json")
    async def visa_jwks() -> dict[str, Any]:
        return visa_keys.jwks()

    test_body = Depends(json_body(MAX_FORM))  # test endpoints also count while reading (D-054 (6))

    def body_of(body: Any) -> dict[str, Any]:
        if body is None:
            return {}
        if not isinstance(body, dict):
            raise OAuthError(400, "invalid_request", "body must be a JSON object")
        return body

    if test_endpoints:  # unregistered when off (like DRS /yai/test/clock, D-054 (5))

        @app.get("/untrusted-visa/jwks.json")
        async def untrusted_jwks() -> dict[str, Any]:
            return untrusted_keys.jwks()

        @app.post("/test/login")
        async def test_login(raw: Any = test_body) -> JSONResponse:
            """Test only. User name -> PSAT and an AT per requested aud."""
            body = body_of(raw)
            name = body.get("username")
            user = users.get(name) if isinstance(name, str) else None
            if not isinstance(name, str) or user is None:
                raise OAuthError(401, "access_denied", "unknown user")
            now = clock.now()
            psat_claims = {
                "iss": issuer,
                "sub": name,
                "aud": body.get("client_id", "yai-test-client"),
                "iat": now,
                "exp": now + psat_ttl,
                "jti": ulid(),
                "scope": PSAT_SCOPE,  # AAI-FMT-04: no GA4GH Claims
            }
            result: dict[str, Any] = {
                "passport_scoped_access_token": broker_keys.sign(psat_claims, "at+jwt")
            }
            scopes = list(user.get("scopes", []))
            result["access_tokens"] = {
                aud: access_token(name, aud, scopes, "yai-test-login")
                for aud in body.get("aud", [])
            }
            return ok(result)

    if test_visa_endpoint:

        @app.put("/test/users/{name}/visas")
        async def test_set_visas(name: str, raw: Any = test_body) -> JSONResponse:
            """Test only. Replace Visas in the Visa issuer's user table (revocation tests).
            ttl uses the same range as the startup check (D-055)."""
            if name not in users:
                raise OAuthError(404, "not_found", "unknown user")
            visas = body_of(raw).get("visas", [])
            if not isinstance(visas, list) or not all(isinstance(v, dict) for v in visas):
                raise OAuthError(400, "invalid_request", "visas must be an array of objects")
            for v in visas:
                ttl = v.get("ttl", visa_ttl)
                if isinstance(ttl, bool) or not isinstance(ttl, int) or not 1 <= ttl <= DAY:
                    raise OAuthError(400, "invalid_request", f"visas[].ttl must be 1..{DAY}")
            users[name] = {**users[name], "visas": list(visas)}
            return ok({"username": name, "visas": len(users[name]["visas"])})

    @app.post("/token")
    async def token(request: Request) -> JSONResponse:
        try:
            form = dict(await limited_request(request, MAX_FORM).form())  # count while reading
        except ApiError as exc:
            raise OAuthError(400, "invalid_request", exc.msg) from None
        grant = form.get("grant_type")
        if grant == TOKEN_EXCHANGE:
            client_auth(request, form)
            return ok(exchange(form))
        if grant == "client_credentials":
            cid = client_auth(request, form)
            client = clients[cid]
            auds = list(client.get("audiences", []))
            aud = form.get("audience") or (auds[0] if auds else None)
            if not isinstance(aud, str) or aud not in auds:
                raise OAuthError(400, "invalid_target", "audience not allowed for client")
            scopes = list(client.get("scopes", []))
            asked = form.get("scope")
            if asked:
                scopes = [s for s in str(asked).split() if s in scopes]
            at = access_token(str(client.get("sub", cid)), aud, scopes, cid)
            return ok({"access_token": at, "token_type": "Bearer", "expires_in": at_ttl})
        raise OAuthError(400, "unsupported_grant_type", "grant_type not supported")

    def exchange(form: dict[str, Any]) -> dict[str, Any]:
        """AAI-ISS-01: PSAT -> Passport (Passport Issuer rules of AAI 1.2)."""
        if form.get("requested_token_type") != PASSPORT_TOKEN_TYPE:
            raise OAuthError(400, "invalid_request", "requested_token_type must be passport")
        if form.get("subject_token_type") != ACCESS_TOKEN_TYPE:
            raise OAuthError(400, "invalid_request", "subject_token_type must be access_token")
        subject = str(form.get("subject_token", ""))
        now = clock.now()
        try:
            header, _ = unverified_parts(subject)
            if header.get("kid") not in {str(k.kid) for k in broker_keys.keys}:
                raise TokenError("bad_signature")
            ks = KeySet(list(broker_keys.keys))
            claims = jwt.decode(subject, ks, algorithms=["ES256"]).claims
            check_times(claims, now, 60)
            if claims.get("iss") != issuer or not {"openid", "ga4gh_passport_v1"} <= scopes_of(
                claims
            ):
                raise TokenError("wrong_token_kind")
        except Exception as exc:
            raise OAuthError(400, "invalid_grant", "subject_token is not a valid PSAT") from exc
        sub = claims["sub"]
        user = users.get(sub, {})
        visas = [visa(sub, spec) for spec in user.get("visas", [])]
        audience = form.get("audience")
        passport_claims = {
            "iss": issuer,
            "sub": sub,
            "iat": now,
            "exp": now + passport_ttl,
            "jti": ulid(),
            "ga4gh_passport_v1": visas,
        }
        aud = audience if audience is not None else config.get("passport_aud", ["yai-drs"])
        if aud:
            passport_claims["aud"] = aud
        passport = broker_keys.sign(passport_claims, "vnd.ga4gh.passport+jwt")
        return {
            "access_token": passport,
            "issued_token_type": PASSPORT_TOKEN_TYPE,
            "token_type": "N_A",
            "expires_in": passport_ttl,
        }

    return app
