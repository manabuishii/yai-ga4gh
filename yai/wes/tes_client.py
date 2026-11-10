"""Client calling TES from WES (wes.md sections 3 and 4).

Per run, calls carry an AT exchanged from the user's AT (RFC 8693, `act` = this WES, D-067) when
the engine holds one; otherwise the WES service identity (client_credentials AT, aud=yai-tes,
D-010: an anonymous WES, and watching or cancelling after a restart lost the exchanged token).
CreateTask puts a submit_nonce made before sending into tags; if it fails with an unknown
response, it searches by tag_key+tag_value before resubmitting.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

TOKEN_EXCHANGE = "urn:ietf:params:oauth:grant-type:token-exchange"  # noqa: S105 a type name, not a secret
ACCESS_TOKEN_TYPE = "urn:ietf:params:oauth:token-type:access_token"  # noqa: S105 a type name, not a secret


class TesError(Exception):
    def __init__(self, reason: str, *, ambiguous: bool = False, status: int | None = None):
        super().__init__(reason)
        self.reason = reason
        self.ambiguous = ambiguous  # unknown whether it arrived
        self.status = status


@dataclass
class TesClient:
    base_url: str  # .../ga4gh/tes/v1
    token_url: str
    client_id: str
    client_secret: str = field(repr=False)  # keep the secret out of repr and logs (review N-2)
    timeout: float = 10.0
    audience: str = "yai-tes"  # audience of the tokens (own and exchanged) for this TES
    _token: str | None = field(default=None, repr=False)
    _exp: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def token(self) -> str:
        with self._lock:
            if self._token is None or time.time() > self._exp - 60:  # refetch 1 min early
                body = self._token_request(
                    {"grant_type": "client_credentials", "audience": self.audience}
                )
                self._token = body["access_token"]
                self._exp = time.time() + int(body.get("expires_in", 900))
            assert self._token is not None
            return self._token

    def exchange(
        self, subject_token: str, scope: str | None = None, *, require: frozenset[str] = frozenset()
    ) -> str:
        """The user's AT (issued to this WES) -> an AT for the TES with `act` = this WES (RFC 8693,
        D-067). The broker checks that the user's token was issued to this client. `scope`
        narrows the client's scopes to what this WES needs (no Passport scopes unless the TES
        is to resolve drs:// itself, D-069); `require` are scopes the answer must grant (the
        broker narrows silently to its client's scopes)."""
        form = {
            "grant_type": TOKEN_EXCHANGE,
            "subject_token": subject_token,
            "subject_token_type": ACCESS_TOKEN_TYPE,
            "requested_token_type": ACCESS_TOKEN_TYPE,
            "audience": self.audience,
        }
        if scope:
            form["scope"] = scope
        body = self._token_request(form)
        token = body.get("access_token")
        if not isinstance(token, str) or not token:
            raise TesError("token exchange returned no access_token")
        granted = body.get("scope")
        if require and not require <= set(str(granted or "").split()):
            raise TesError("the broker did not grant the Passport scopes this WES is configured "
                           "to need (resolve_in_wes: false needs openid ga4gh_passport_v1 in the "
                           "client's scopes)")  # fmt: skip
        return token

    def _token_request(self, data: dict[str, str]) -> dict[str, Any]:
        try:
            r = httpx.post(
                self.token_url, data=data, auth=(self.client_id, self.client_secret),
                timeout=self.timeout,
            )  # fmt: skip
        except httpx.TransportError as exc:
            raise TesError("token endpoint unreachable", ambiguous=True) from exc
        if r.status_code != 200:
            raise TesError(f"token endpoint answered {r.status_code}", status=r.status_code)
        body = r.json()
        if not isinstance(body, dict):
            raise TesError("token endpoint answered something that is not a JSON object")
        return body

    def _call(
        self, method: str, path: str, *, token: str | None = None, **kw: Any
    ) -> httpx.Response:
        headers = {"Authorization": f"Bearer {token or self.token()}"}
        try:
            return httpx.request(
                method, self.base_url + path, headers=headers, timeout=self.timeout,
                follow_redirects=False, **kw,
            )  # fmt: skip
        except httpx.TimeoutException as exc:
            raise TesError("timeout", ambiguous=True) from exc
        except httpx.TransportError as exc:
            raise TesError("unreachable", ambiguous=method != "GET") from exc

    def create(self, task: dict[str, Any], token: str | None = None) -> str:
        r = self._call("POST", "/tasks", json=task, token=token)
        if r.status_code != 200:
            raise TesError(f"CreateTask answered {r.status_code}", status=r.status_code,
                           ambiguous=r.status_code >= 500)  # fmt: skip
        return r.json()["id"]

    def get(self, task_id: str, token: str | None = None) -> dict[str, Any]:
        r = self._call("GET", f"/tasks/{task_id}", params={"view": "FULL"}, token=token)  # C-07
        if r.status_code != 200:
            raise TesError(f"GetTask answered {r.status_code}", status=r.status_code)
        return r.json()

    def cancel(self, task_id: str, token: str | None = None) -> None:
        r = self._call("POST", f"/tasks/{task_id}:cancel", token=token)
        if r.status_code != 200:
            raise TesError(f"CancelTask answered {r.status_code}", status=r.status_code)

    def find_by_nonce(self, nonce: str, token: str | None = None) -> str | None:
        """Search by both tag_key and tag_value, following all pages (wes.md section 4)."""
        page: str | None = None
        while True:
            params: dict[str, Any] = {"tag_key": "yai.submit_nonce", "tag_value": nonce,
                                      "view": "MINIMAL"}  # fmt: skip
            if page:
                params["page_token"] = page
            r = self._call("GET", "/tasks", params=params, token=token)
            if r.status_code != 200:
                raise TesError(f"ListTasks answered {r.status_code}", status=r.status_code)
            body = r.json()
            if body.get("tasks"):
                return body["tasks"][0]["id"]
            page = body.get("next_page_token") or None  # absent/null/"" = end (G-X-04)
            if not page:
                return None
