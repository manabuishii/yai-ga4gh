"""Shared part building the service-info 1.0.0 Service (C-22)."""

from __future__ import annotations

from typing import Any

from yai import __version__

ORGANIZATION = {"name": "yai (personal learning project)", "url": "https://yai.test/"}


def service_info(
    *, service_id: str, name: str, artifact: str, spec_version: str, **extra: Any
) -> dict[str, Any]:
    info: dict[str, Any] = {
        "id": service_id,
        "name": name,
        "type": {"group": "org.ga4gh", "artifact": artifact, "version": spec_version},
        "description": f"yai {artifact}, a learning implementation",
        "organization": ORGANIZATION,
        "version": __version__,
        "environment": "test",
    }
    info.update(extra)
    return info
