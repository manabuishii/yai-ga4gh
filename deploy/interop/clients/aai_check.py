"""INTEROP-AAI-1: partner Broker/Issuer (GA4GH Passport Playground) -> yai Clearinghouse (yai-drs).

Partner: Broker = Ory Hydra v26.2.0 (per the playground-broker README), Issuer =
playground-issuer bfb0232 (Flask).
Checks: whether yai-drs can verify the partner's Passport/Visa; differences in typ, jku, aud.
(1) Create a client_credentials client in Hydra (scope ga4gh_passport_v1, audience yai-drs)
and get a JWT access token
(2) Send that token to yai-drs as a Passport (POST /objects/{id} passports) -> decision, reason
(3) Inspect the shape of the Issuer's Visa (/api/passport/dataset/DUOS-000001): JWT or JSON
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path

import httpx
from common import Report, excerpt

HYDRA_ADMIN = "http://hydra:4445"
BROKER = "https://broker.yai.test"
ISSUER = "http://issuer:8080"
DRS = "https://drs.yai.test/ga4gh/drs/v1"


def parts(token: str) -> tuple[dict, dict]:
    def dec(p: str) -> dict:
        return json.loads(base64.urlsafe_b64decode(p + "=" * (-len(p) % 4)))

    h, b, _ = token.split(".")
    return dec(h), dec(b)


def main() -> int:
    rep = Report(
        "INTEROP-AAI-1",
        "Passport Playground (Hydra v26.2.0 broker + issuer bfb0232) → yai-drs clearinghouse",
    )
    ids = json.loads(Path(os.environ.get("YAI_E2E_IDS", "/yai/e2e/ids.json")).read_text())
    c = httpx.Client(timeout=30)

    r = c.post(
        f"{HYDRA_ADMIN}/admin/clients",
        json={
            "client_name": "yai-interop",
            "grant_types": ["client_credentials"],
            "response_types": [],
            "scope": "ga4gh_passport_v1",
            "audience": ["yai-drs"],
            "token_endpoint_auth_method": "client_secret_post",
            "access_token_strategy": "jwt",
        },
    )
    rep.step(
        "Create a client via the Hydra admin API (scope ga4gh_passport_v1, audience yai-drs)",
        r.status_code == 201,
        status=r.status_code,
        body=excerpt(
            {k: v for k, v in r.json().items() if k != "client_secret"}
            if r.status_code == 201
            else r.text
        ),
    )
    if r.status_code != 201:
        return rep.save("INTEROP-AAI-1.json")
    cl = r.json()

    r = c.post(
        f"{BROKER}/oauth2/token",
        data={
            "grant_type": "client_credentials",
            "scope": "ga4gh_passport_v1",
            "audience": "yai-drs",
            "client_id": cl["client_id"],
            "client_secret": cl["client_secret"],
        },
    )
    rep.step(
        "Get an access token from the Broker (https://broker.yai.test, local CA)",
        r.status_code == 200,
        status=r.status_code,
        body=excerpt(
            {k: v for k, v in r.json().items() if k != "access_token"}
            if r.status_code == 200
            else r.text
        ),
    )
    if r.status_code != 200:
        return rep.save("INTEROP-AAI-1.json")
    at = r.json()["access_token"]
    header, claims = parts(at)
    rep.step(
        "Access token is a JWT; record typ, iss, aud, scp and presence of ga4gh_passport_v1",
        True,
        header=header,
        claims={k: claims.get(k) for k in ("iss", "aud", "sub", "scp", "scope", "client_id")},
        has_ga4gh_passport_v1="ga4gh_passport_v1" in claims,
    )

    r = c.get(f"{BROKER}/.well-known/jwks.json")
    rep.step(
        "Broker JWKS (fetched by yai-drs via trust.brokers) is reachable",
        r.status_code == 200,
        status=r.status_code,
        kids=[k.get("kid") for k in r.json().get("keys", [])] if r.status_code == 200 else None,
    )

    r = c.post(f"{DRS}/objects/{ids['ds1']}", json={"passports": [at]})
    rep.step(
        "Send the Hydra token to yai-drs as a Passport (POST /objects/{id}) -> yai decision",
        r.status_code in (401, 403),
        status=r.status_code,
        body=excerpt(r.text),
    )
    r = c.get(f"{DRS}/objects/{ids['ds1']}", headers={"Authorization": "Bearer " + at})
    rep.step(
        "Send the Hydra token to yai-drs as Bearer -> yai decision",
        r.status_code in (401, 403),
        status=r.status_code,
        body=excerpt(r.text),
    )

    r = c.get(f"{ISSUER}/api/passport/dataset/DUOS-000001")
    body = r.json() if r.status_code == 200 else {}
    visas = body.get("ga4gh_passport_v1") or []
    kinds = sorted({type(v).__name__ for v in visas})
    rep.step(
        "Shape of the Issuer's Visa (the Passport spec says a signed JWT string; partner?)",
        r.status_code == 200,
        status=r.status_code,
        n_visas=len(visas),
        visa_value_types=kinds,
        first=excerpt(visas[0] if visas else None),
    )
    if visas:
        r = c.post(f"{DRS}/objects/{ids['ds1']}", json={"passports": [json.dumps(body)]})
        rep.step(
            "Send the Issuer's JSON as-is to yai-drs as a Passport -> yai decision",
            r.status_code in (400, 401, 403),
            status=r.status_code,
            body=excerpt(r.text),
        )
    return rep.save("INTEROP-AAI-1.json")


if __name__ == "__main__":
    raise SystemExit(main())
