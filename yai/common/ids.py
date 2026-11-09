"""ID shape (D-016): one kind letter + ULID (26 lowercase alphanumerics)."""

from __future__ import annotations

import os
import time

_ALPHABET = "0123456789abcdefghjkmnpqrstvwxyz"  # lowercase Crockford base32


def ulid() -> str:
    value = (int(time.time() * 1000) << 80) | int.from_bytes(os.urandom(10), "big")
    out = []
    for _ in range(26):
        out.append(_ALPHABET[value & 31])
        value >>= 5
    return "".join(reversed(out))


def new_id(kind: str) -> str:
    """kind is `o` for DRS objects, `t` for TES tasks, `r` for WES runs."""
    if kind not in {"o", "t", "r"}:
        raise ValueError(kind)
    return kind + ulid()
