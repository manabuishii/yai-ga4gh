"""Log redaction (yai/common/logsetup.py). Switched to a linear scan in D-042."""

from __future__ import annotations

import logging
import time

import pytest

from yai.common.logsetup import MAX_MESSAGE, RedactFilter, redact


@pytest.mark.parametrize(
    ("text", "want"),
    [
        ("GET https://x/y?sig=ab&exp=1 200", "GET https://x/y?[redacted] 200"),
        ("tok eyJhbGciOi.eyJzdWIiOi.sig123 end", "tok [jwt] end"),
        ("a=eyJabcde.fghij.", "a=[jwt]"),
        ("foo.eyJabcde.fghij.klm.eyJzzzzz.yyyyy.x", "foo.[jwt].[jwt]"),
        ("https://a?b https://c?d", "https://a?[redacted] https://c?[redacted]"),
        ('"http://h/p?q"', '"http://h/p?[redacted]"'),
        ("Authorization: Bearer abc.def", "Authorization: Bearer [redacted]"),
        ("sig=deadbeef", "sig=[redacted]"),
        ("eyJabcde.fgh.ijk", "eyJabcde.fgh.ijk"),  # second part is shorter than 5 characters
        ("httpx://a?b http://?x", "httpx://a?b http://?x"),
    ],
)
def test_redact_keeps_the_former_shapes(text, want):
    """Redacts the same spans as the old regex (compared with the pre-fix version on 15 cases)."""
    assert redact(text) == want


@pytest.mark.parametrize("unit", ["http://", "eyJ", "eyJaaaaa.", "sig=", "authorization: bearer"])
def test_NEG_REDOS_LOG(unit):
    """8,000 repeated `http://` used to take 3 s (O(n²)). 1 MiB in under 2 s."""
    text = unit * ((1 << 20) // len(unit))
    start = time.monotonic()
    redact(text)
    assert time.monotonic() - start < 2


def test_long_message_is_redacted_then_cut():
    jwt = "eyJ" + "a" * 20 + "." + "b" * 20 + "." + "c" * 20
    record = logging.LogRecord("t", logging.INFO, __file__, 1, "x" * (MAX_MESSAGE - 10) + " " + jwt,
                               None, None)  # fmt: skip
    RedactFilter().filter(record)
    msg = record.getMessage()
    assert "eyJ" not in msg and len(msg) < MAX_MESSAGE + 40
