"""Time (D-015): internally UTC seconds (int). Converted to each spec's format on output."""

from __future__ import annotations

import time
from datetime import UTC, datetime


class Clock:
    """Clock that tests can advance from outside. Defaults to the real time."""

    def __init__(self) -> None:
        self.offset = 0

    def now(self) -> int:
        return int(time.time()) + self.offset


def rfc3339(ts: int) -> str:
    """DRS/TES format (e.g. 2026-10-15T13:00:00Z)."""
    return datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def wes_time(ts: int) -> str:
    """WES format %Y-%m-%dT%H:%M:%SZ (seconds, no fraction)."""
    return datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_rfc3339(text: str) -> int:
    """RFC 3339 (fractions and offsets included) to UTC seconds. Fractions truncated (C-10)."""
    value = text.strip().replace("Z", "+00:00").replace("z", "+00:00")
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return int(dt.timestamp() // 1)
