"""Shared log redactor (system.md section 8, "rules for not leaking secrets").

Before writing, mask JWT-shaped strings, URL query parts and userinfo, `sig=` values and
Authorization.
httpx/httpcore request logs (which write URLs verbatim) are suppressed below WARNING.
"""

from __future__ import annotations

import logging
import re

# Mask with linear scans only (D-042). The old regex re-read to line end from each start, O(n^2):
# a line of 8,000 `http://` took 3 s. Now each chunk split at delimiters is checked once
_WORD = re.compile(r"[^\s\"']+")  # unit for finding URLs (up to whitespace or quotes)
_B64 = re.compile(r"[A-Za-z0-9_.-]+")  # unit for finding JWTs (runs of base64url and `.`)
_SIG = re.compile(r"(sig=)[0-9A-Fa-f]+")
_AUTH = re.compile(r"(?i)(authorization:?\s*(bearer|basic)\s+)\S+")
MAX_MESSAGE = 16 * 1024  # per-line limit. Truncate after masking (truncating first breaks JWTs)


def _jwt(m: re.Match[str]) -> str:
    """`eyJ...(5+ chars)....(5+ chars)....` -> [jwt] (same shape as the old regex)."""
    segs = m.group().split(".")
    out: list[str] = []
    i = 0
    while i < len(segs):
        p = segs[i].find("eyJ")  # if the first eyJ is too short, later ones are too
        if p >= 0 and len(segs[i]) - p >= 8 and i + 2 < len(segs) and len(segs[i + 1]) >= 5:
            out.append(segs[i][:p] + "[jwt]")
            i += 3
        else:
            out.append(segs[i])
            i += 1
    return ".".join(out)


def _url(m: re.Match[str]) -> str:
    """Mask from ? to the end of the word in `http(s)://...?...`, and `user:pass@` (#8)."""
    word = m.group()
    i = word.find("://")
    if i > 0 and word[:i].endswith(("http", "https")):
        start = i + 3
        end = min((j for j in (word.find(c, start) for c in "/?#") if j >= 0), default=len(word))
        at = word.rfind("@", start, end)
        if at >= 0:
            word = word[:start] + "[redacted]" + word[at:]
        q = word.find("?", start)
        if q > start:
            return word[:q] + "?[redacted]"
    return word


def redact(text: str) -> str:
    text = _B64.sub(_jwt, text)
    text = _WORD.sub(_url, text)
    text = _SIG.sub(r"\1[redacted]", text)
    return _AUTH.sub(r"\1[redacted]", text)


class RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        cleaned = redact(message)
        if len(cleaned) > MAX_MESSAGE:
            cleaned = cleaned[:MAX_MESSAGE] + f"...[{len(cleaned) - MAX_MESSAGE} more]"
        if cleaned != message:
            record.msg, record.args = cleaned, None
        return True


def setup_logging(level: int = logging.INFO) -> None:
    logging.basicConfig(level=level, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    for handler in logging.getLogger().handlers:
        handler.addFilter(RedactFilter())
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
