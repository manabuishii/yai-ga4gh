"""Page tokens (docs/data/paging.yaml). Used by TES ListTasks, WES ListRuns and ListTasks.

Token = base64url(JSON) + "." + HMAC. Contents: snapshot_seq, after_seq, principal, scope,
filters_hash.
Tampering, another user, or reuse with changed conditions is 400.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Any

from yai.common.errors import ApiError


def _digest(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


@dataclass(frozen=True)
class PageState:
    snapshot_seq: int
    after_seq: int | None


class PageTokens:
    def __init__(self, secret: bytes):
        self.secret = secret

    def _sign(self, body: bytes) -> str:
        return hmac.new(self.secret, body, hashlib.sha256).hexdigest()[:32]

    def make(
        self,
        state: PageState,
        *,
        principal: tuple[str, str],
        scope: str,
        filters: Any,
    ) -> str:
        payload = {
            "snapshot_seq": state.snapshot_seq,
            "after_seq": state.after_seq,
            "principal": _digest(list(principal)),
            "scope": scope,
            "filters_hash": _digest(filters),
        }
        body = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=")
        return body.decode() + "." + self._sign(body)

    def read(
        self,
        token: str,
        *,
        principal: tuple[str, str],
        scope: str,
        filters: Any,
    ) -> PageState:
        body, _, sig = token.partition(".")
        # compare as bytes (no exception on non-ASCII tokens)
        if not body or not hmac.compare_digest(
            self._sign(body.encode()).encode(), sig.encode("utf-8", "replace")
        ):
            raise ApiError(400, "invalid page_token")
        try:
            payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        except ValueError as exc:
            raise ApiError(400, "invalid page_token") from exc
        if (
            payload.get("principal") != _digest(list(principal))
            or payload.get("scope") != scope
            or payload.get("filters_hash") != _digest(filters)
        ):
            raise ApiError(400, "page_token does not match this request")
        return PageState(int(payload["snapshot_seq"]), payload.get("after_seq"))
