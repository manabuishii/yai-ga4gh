"""Client calling TES from WES (wes.md sections 3 and 4).

Calls as the WES service identity (client_credentials AT, aud=yai-tes) (D-010).
CreateTask puts a submit_nonce made before sending into tags; if it fails with an unknown
response, it searches by tag_key+tag_value before resubmitting.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

import httpx


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
    _token: str | None = field(default=None, repr=False)
    _exp: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def token(self) -> str:
        with self._lock:
            if self._token is None or time.time() > self._exp - 60:  # refetch 1 min early
                try:
                    r = httpx.post(
                        self.token_url,
                        data={"grant_type": "client_credentials", "audience": "yai-tes"},
                        auth=(self.client_id, self.client_secret),
                        timeout=self.timeout,
                    )
                except httpx.TransportError as exc:
                    raise TesError("token endpoint unreachable", ambiguous=True) from exc
                if r.status_code != 200:
                    raise TesError(f"token endpoint answered {r.status_code}")
                body = r.json()
                self._token = body["access_token"]
                self._exp = time.time() + int(body.get("expires_in", 900))
            assert self._token is not None
            return self._token

    def _call(self, method: str, path: str, **kw: Any) -> httpx.Response:
        headers = {"Authorization": f"Bearer {self.token()}"}
        try:
            return httpx.request(
                method, self.base_url + path, headers=headers, timeout=self.timeout,
                follow_redirects=False, **kw,
            )  # fmt: skip
        except httpx.TimeoutException as exc:
            raise TesError("timeout", ambiguous=True) from exc
        except httpx.TransportError as exc:
            raise TesError("unreachable", ambiguous=method != "GET") from exc

    def create(self, task: dict[str, Any]) -> str:
        r = self._call("POST", "/tasks", json=task)
        if r.status_code != 200:
            raise TesError(f"CreateTask answered {r.status_code}", status=r.status_code,
                           ambiguous=r.status_code >= 500)  # fmt: skip
        return r.json()["id"]

    def get(self, task_id: str) -> dict[str, Any]:
        r = self._call("GET", f"/tasks/{task_id}", params={"view": "FULL"})  # C-07
        if r.status_code != 200:
            raise TesError(f"GetTask answered {r.status_code}", status=r.status_code)
        return r.json()

    def cancel(self, task_id: str) -> None:
        r = self._call("POST", f"/tasks/{task_id}:cancel")
        if r.status_code != 200:
            raise TesError(f"CancelTask answered {r.status_code}", status=r.status_code)

    def find_by_nonce(self, nonce: str) -> str | None:
        """Search by both tag_key and tag_value, following all pages (wes.md section 4)."""
        token: str | None = None
        while True:
            params: dict[str, Any] = {"tag_key": "yai.submit_nonce", "tag_value": nonce,
                                      "view": "MINIMAL"}  # fmt: skip
            if token:
                params["page_token"] = token
            r = self._call("GET", "/tasks", params=params)
            if r.status_code != 200:
                raise TesError(f"ListTasks answered {r.status_code}")
            body = r.json()
            if body.get("tasks"):
                return body["tasks"][0]["id"]
            token = body.get("next_page_token") or None  # absent/null/"" = end (G-X-04)
            if not token:
                return None
