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
    # AT a service gets when it exchanges a user's AT to call the next service (D-067). As long as
    # the Passport by default: a multi-step run must not lose its credentials halfway
    delegated_ttl = int(ttl.get("delegated_access_token", passport_ttl))
    users: dict[str, dict[str, Any]] = config.get("users", {})
    clients: dict[str, dict[str, Any]] = config.get("clients", {})
    # Test endpoints (D-054). /test/login and the untrusted-Visa JWKS are test_endpoints; replacing
    # Visas in the user table is test_visa_endpoint (local tests only). There is no clock endpoint
    test_endpoints = bool(config.get("test_endpoints", False))
    test_visa_endpoint = bool(config.get("test_visa_endpoint", False))
    clock = Clock()
    # D-070: tokens issued to a user before this time are refused at every exchange (test only;
    # a real revocation endpoint is the identity provider's, G-AAI-09). Memory only
    revoked_before: dict[str, int] = {}

    def check_subject(claims: dict[str, Any]) -> str:
        """The user of a subject token must still exist and the token must not be revoked
        (checked at every exchange, D-070). Returns the user name."""
        sub = claims.get("sub")
        if not isinstance(sub, str) or sub not in users:
            raise OAuthError(400, "invalid_grant", "subject_token is not for a known user")
        if claims.get("iat", 0) < revoked_before.get(sub, 0):
            raise OAuthError(400, "invalid_grant", "subject_token was revoked")
        return sub

    @app.exception_handler(OAuthError)
    async def _oauth_error(_: Request, exc: OAuthError) -> JSONResponse:
        body = {"error": exc.error, "error_description": exc.description}
        return JSONResponse(body, exc.status, headers=NO_STORE)

    def ok(body: dict[str, Any]) -> JSONResponse:
        return JSONResponse(body, 200, headers=NO_STORE)

    def access_token(
        sub: str, aud: str, scopes: list[str], client_id: str, *,
        act: dict[str, Any] | None = None, lifetime: int | None = None,
    ) -> str:  # fmt: skip
        now = clock.now()
        claims: dict[str, Any] = {
            "iss": issuer,
            "sub": sub,
            "aud": aud,
            "iat": now,
            "exp": now + (at_ttl if lifetime is None else lifetime),
            "jti": ulid(),
            "client_id": client_id,
            "scope": " ".join(scopes),
        }
        if act is not None:
            claims["act"] = act  # RFC 8693 section 4.1: who acts for the subject (D-067)
        return broker_keys.sign(claims, "at+jwt")

    def verify_own_at(token: str, now: int) -> dict[str, Any]:
        """Claims of an AT this broker issued (signature, times, issuer). TokenError otherwise."""
        header, _ = unverified_parts(token)
        if header.get("typ") != "at+jwt":
            raise TokenError("wrong_token_kind")
        if header.get("kid") not in {str(k.kid) for k in broker_keys.keys}:
            raise TokenError("bad_signature")
        claims = jwt.decode(token, KeySet(list(broker_keys.keys)), algorithms=["ES256"]).claims
        check_times(claims, now, 60)
        if claims.get("iss") != issuer:
            raise TokenError("untrusted_issuer")
        return claims

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
            revoked_before.pop(name, None)  # a new login is a new consent (D-070)
            scopes = list(user.get("scopes", []))
            # ATs for the audiences in passport_aud also carry the PSAT scopes: the user lets
            # that service fetch their Passport (D-069), as a consent screen would
            with_passport = set(body.get("passport_aud", []))
            result["access_tokens"] = {
                aud: access_token(
                    name, aud,
                    scopes + PSAT_SCOPE.split() if aud in with_passport else scopes,
                    "yai-test-login",
                )
                for aud in body.get("aud", [])
            }  # fmt: skip
            return ok(result)

        @app.post("/test/revoke")
        async def test_revoke(raw: Any = test_body) -> JSONResponse:
            """Test only. Revoke everything issued to a user so far: PSATs and ATs issued before
            now are refused at the next exchange (Passport or delegated AT). Passports, exchanged
            ATs and signed URLs already issued keep working until their exp (what D-070 records
            as the window). A new /test/login lifts it."""
            name = body_of(raw).get("username")
            if not isinstance(name, str) or name not in users:
                raise OAuthError(404, "not_found", "unknown user")
            revoked_before[name] = clock.now() + 1  # iat has whole seconds: include this second
            return ok({"username": name, "revoked_at": revoked_before[name]})

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
            cid = client_auth(request, form)
            if form.get("requested_token_type") == ACCESS_TOKEN_TYPE:
                return ok(exchange_access_token(cid, form))
            return ok(exchange(cid, form))
        if grant == "client_credentials":
            cid = client_auth(request, form)
            client = clients[cid]
            auds = list(client.get("audiences", []))
            aud = form.get("audience") or (auds[0] if auds else None)
            if not isinstance(aud, str) or aud not in auds:
                raise OAuthError(400, "invalid_target", "audience not allowed for client")
            # the PSAT scopes are for tokens exchanged for a user (D-069); a service's own token
            # names no user and never carries them
            scopes = [s for s in client.get("scopes", []) if s not in PSAT_SCOPE.split()]
            asked = form.get("scope")
            if asked:
                scopes = [s for s in str(asked).split() if s in scopes]
            at = access_token(str(client.get("sub", cid)), aud, scopes, cid)
            return ok({"access_token": at, "token_type": "Bearer", "expires_in": at_ttl})
        raise OAuthError(400, "unsupported_grant_type", "grant_type not supported")

    def exchange_access_token(cid: str, form: dict[str, Any]) -> dict[str, Any]:
        """RFC 8693: a user's AT (audience = this client's service) -> an AT for the next service,
        with `act` naming this client (D-067, a yai extension on top of AAI 1.2).

        Conditions: the subject token is a user AT this broker issued and still valid; its `aud`
        contains the service name the client acts as (`clients.<id>.service`, default the
        client id), so a client can only act for users who actually called it; `audience` is
        one the client may hold. The scopes are the client's (`clients.<id>.scopes`, narrowed by
        `scope`): the user authorized the service by calling it. When they include
        `openid ga4gh_passport_v1`, the token is a Passport-Scoped Access Token the next service
        can exchange for the user's Passport (D-069)."""
        client = clients[cid]
        if form.get("subject_token_type") != ACCESS_TOKEN_TYPE:
            raise OAuthError(400, "invalid_request", "subject_token_type must be access_token")
        auds = list(client.get("audiences", []))
        aud = form.get("audience")
        if not isinstance(aud, str) or aud not in auds:
            raise OAuthError(400, "invalid_target", "audience not allowed for client")
        try:
            claims = verify_own_at(str(form.get("subject_token", "")), clock.now())
        except Exception as exc:
            raise OAuthError(
                400, "invalid_grant", "subject_token is not a valid access token"
            ) from exc
        sub = delegable_subject(cid, form, claims)
        act: dict[str, Any] = {"sub": str(client.get("sub", cid))}
        if isinstance(claims.get("act"), dict):
            act["act"] = claims["act"]  # a chain of actors nests (RFC 8693 section 4.1)
        scopes = list(client.get("scopes", []))
        asked = form.get("scope")
        if asked:
            scopes = [s for s in str(asked).split() if s in scopes]
        at = access_token(sub, aud, scopes, cid, act=act, lifetime=delegated_ttl)
        return {
            "access_token": at,
            "issued_token_type": ACCESS_TOKEN_TYPE,
            "token_type": "Bearer",
            "expires_in": delegated_ttl,
            "scope": " ".join(scopes),
        }

    def delegable_subject(cid: str, form: dict[str, Any], claims: dict[str, Any]) -> str:
        """The user a client may act for, from the claims of an AT this broker issued (D-067,
        D-069): a user's token (not a service's own token) whose `aud` contains the service name
        the client acts as (`clients.<id>.service`, default the client id), so a client can only
        act for users who actually called it. A passport-scoped token is accepted as the subject
        too (the next service in a chain exchanges what it received); the audience binding is
        the gate, and the scopes of the result are the client's. The user must still exist and
        the token must not be revoked (D-070); that is checked after the audience, so a token
        for another service reveals nothing. Returns the user name."""
        client = clients[cid]
        if "actor_token" in form:
            raise OAuthError(
                400, "invalid_request", "actor_token is not used: the actor is the client"
            )
        sub, subject_aud = claims.get("sub"), claims.get("aud")
        subject_auds = [subject_aud] if isinstance(subject_aud, str) else subject_aud
        if not isinstance(sub, str) or not sub or not isinstance(subject_auds, list):
            raise OAuthError(400, "invalid_grant", "subject_token is not a valid access token")
        if claims.get("client_id") == sub:
            raise OAuthError(400, "invalid_grant", "subject_token is a service token")
        service = str(client.get("service", cid))
        if service not in subject_auds:
            raise OAuthError(400, "invalid_grant", "subject_token was not issued to this client")
        return check_subject(claims)

    def exchange(cid: str, form: dict[str, Any]) -> dict[str, Any]:
        """AAI-ISS-01: PSAT -> Passport (Passport Issuer rules of AAI 1.2, "Conformance For
        Passport Issuers" 3: client authentication, requested_token_type passport, the subject
        a valid Passport-Scoped Access Token). Which client may exchange which PSAT is left to
        the broker; this broker's policy (D-069): only a client the PSAT's `aud` names (the
        relying party's client id, as the PSAT format requires when aud is present). Every PSAT
        this broker issues has an `aud`, so one without is refused. A PSAT a service holds for a
        user (one exchanged with `act`) is exchanged the same way, by that service only; Passport
        1.2 has no field for who fetched it."""
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
            if claims.get("iss") != issuer or not set(PSAT_SCOPE.split()) <= scopes_of(claims):
                raise TokenError("wrong_token_kind")
        except Exception as exc:
            raise OAuthError(400, "invalid_grant", "subject_token is not a valid PSAT") from exc
        psat_aud = claims.get("aud")
        psat_auds = [psat_aud] if isinstance(psat_aud, str) else psat_aud
        if not isinstance(psat_auds, list) or not psat_auds:
            raise OAuthError(400, "invalid_grant", "subject_token has no aud")
        if str(clients[cid].get("service", cid)) not in psat_auds:
            raise OAuthError(400, "invalid_grant", "subject_token was not issued to this client")
        sub = check_subject(claims)
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
