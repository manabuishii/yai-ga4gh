"""Signing keys (aai.md section 7). Read from files at startup (var/aai/keys/<role>/<kid>.jwk,
not in git).

If none exist, one ES256 key is created. The JWKS lists all keys of the role (for rotation tests);
signing uses the last key in name order.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from joserfc import jwt
from joserfc.jwk import ECKey, import_key

from yai.common.ids import ulid


class SigningKeys:
    def __init__(self, directory: Path):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)
        files = sorted(directory.glob("*.jwk"))
        if not files:
            kid = "k" + ulid()
            key = ECKey.generate_key("P-256", parameters={"kid": kid}, private=True)
            path = directory / f"{kid}.jwk"
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(key.as_dict(private=True), f)
            files = [path]
        self.keys = [import_key(json.loads(p.read_text(encoding="utf-8"))) for p in files]
        self.active = self.keys[-1]

    @property
    def kid(self) -> str:
        return str(self.active.kid)

    def jwks(self) -> dict[str, Any]:
        return {"keys": [k.as_dict(private=False) for k in self.keys]}

    def sign(self, claims: dict[str, Any], typ: str, **header: Any) -> str:
        head = {"alg": "ES256", "typ": typ, "kid": self.kid, **header}
        return jwt.encode(head, claims, self.active)
