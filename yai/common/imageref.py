"""Shape check for container image references (`executors[].image`, CWL `dockerPull`) (D-041).

The TES spec only says "the string is passed as-is as the image argument of the
containerization run command". Values starting with `-` are read as options by the docker CLI
(CWE-88), so only values matching the Docker reference grammar (ReferenceRegexp of
distribution/reference: `[domain/]name[:tag][@digest]`) are accepted; anything else is 400.
"""

from __future__ import annotations

import re

_ALNUM = r"[a-z0-9]+"
_SEP = r"(?:[._]|__|-+)"
_COMPONENT = rf"{_ALNUM}(?:{_SEP}{_ALNUM})*"
_DOMAIN_COMPONENT = r"(?:[a-zA-Z0-9]|[a-zA-Z0-9][a-zA-Z0-9-]*[a-zA-Z0-9])"
_IPV6 = r"\[(?:[a-fA-F0-9:]+)\]"
_HOST = rf"(?:{_DOMAIN_COMPONENT}(?:\.{_DOMAIN_COMPONENT})*|{_IPV6})"
_DOMAIN = rf"{_HOST}(?::[0-9]+)?"
_TAG = r"[\w][\w.-]{0,127}"
_DIGEST = r"[A-Za-z][A-Za-z0-9]*(?:[-_+.][A-Za-z][A-Za-z0-9]*)*:[0-9A-Fa-f]{32,}"
REFERENCE = re.compile(
    rf"(?P<name>(?:{_DOMAIN}/)?{_COMPONENT}(?:/{_COMPONENT})*)(?::{_TAG})?(?:@{_DIGEST})?",
    re.ASCII,
)
NAME_MAX = 255  # NameTotalLengthMax of distribution/reference


class BadImageReference(ValueError):
    pass


def check_image_reference(value: object) -> str:
    """Return the value if it is a valid image reference. Leading `-`, whitespace or bad grammar
    raise BadImageReference."""
    if not isinstance(value, str) or not value:
        raise BadImageReference("image is required")
    m = REFERENCE.fullmatch(value)  # match + `$` would let a trailing newline through
    if m is None:
        raise BadImageReference("image is not a valid image reference (name[:tag][@digest])")
    if len(m.group("name")) > NAME_MAX:
        raise BadImageReference(f"image name is longer than {NAME_MAX} characters")
    return value
