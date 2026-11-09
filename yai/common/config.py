"""Settings (config/*.yaml). No secrets inside; only the locations of secret files."""

from __future__ import annotations

import os
import secrets
from pathlib import Path
from typing import Any

import yaml


def auth_mode(config: dict[str, Any], allowed: tuple[str, ...]) -> str:
    """Check auth.mode at startup. Typos and unknown values fail startup.

    Omitting it fails startup (D-055 (1); omitting it used to mean anonymous = no gate, so one
    forgotten line silently removed protection). For aai, issuer, jwks_uri and audience are also
    checked, with a one-line message.
    """
    auth = config.get("auth") or {}
    if "mode" not in auth:
        raise SystemExit(f"auth.mode is required ({', '.join(allowed)})")
    mode = auth["mode"]
    if mode not in allowed:
        raise SystemExit(f"auth.mode must be one of {', '.join(allowed)} (got {mode!r})")
    if mode == "aai":
        for key in ("issuer", "jwks_uri", "audience"):
            if not auth.get(key):
                raise SystemExit(f"auth.{key} is required when auth.mode is aai")
    return mode


MIN_SECRET = 32  # minimum key file length (chars; generated keys are 64 hex chars, D-044)


def load_secret(path: Path) -> bytes:
    """HMAC key files (page-secret, url-secret). Created if absent. Too short or empty fails startup
    (an empty file is never accepted as a key)."""
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(secrets.token_hex(32))
    secret = path.read_text().strip().encode()
    if len(secret) < MIN_SECRET:
        raise SystemExit(f"{path.name} must hold at least {MIN_SECRET} characters")
    return secret


def config_path(path: str | Path | None) -> str:
    """--config or YAI_CONFIG. Without either, no startup (D-055 (2); it used to start empty)."""
    if path is None:
        path = os.environ.get("YAI_CONFIG")
    if not path:
        raise SystemExit("--config or YAI_CONFIG is required")
    return str(path)


def load_config(path: str | Path | None) -> dict[str, Any]:
    with open(config_path(path), encoding="utf-8") as f:
        return yaml.safe_load(f) or {}
