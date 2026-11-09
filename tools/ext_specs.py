"""Download the pinned specification files that this repository does not copy.

- What to fetch: the `files` of each entry of specs/SOURCES.yaml. A value is either a path in
  the GitHub repository `repo` at `commit`, or a full URL.
- Where: build/specs-ext/<local path> (not in git). Every file is checked against
  specs/EXTERNAL.SHA256SUMS; a mismatch always fails.
- Files that are already there with the right sha256 are not downloaded again, so once the
  cache is filled, `make check` needs no network.
- The tools that read the specifications (bundle_specs, gen_obligations, gen_diagram) read
  them from `EXT` and call `require()` first.

Usage: python tools/ext_specs.py   (exit 1 if a file is missing and cannot be downloaded)
"""

from __future__ import annotations

import hashlib
import sys
import urllib.request
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SPECS = ROOT / "specs"
EXT = ROOT / "build" / "specs-ext"
SUMS = SPECS / "EXTERNAL.SHA256SUMS"


def _sources() -> dict[str, tuple[str, str]]:
    """{local path: (URL, sha256)}"""
    src = yaml.safe_load((SPECS / "SOURCES.yaml").read_text(encoding="utf-8"))
    sums = {}
    for line in SUMS.read_text(encoding="utf-8").splitlines():
        if line.strip():
            digest, rel = line.split(maxsplit=1)
            sums[rel] = digest
    out = {}
    for item in src.values():
        for rel, where in item.get("files", {}).items():
            if "://" in where:
                url = where
            else:
                owner_repo = item["repo"].removeprefix("https://github.com/")
                url = f"https://raw.githubusercontent.com/{owner_repo}/{item['commit']}/{where}"
            if rel not in sums:
                raise SystemExit(
                    f"ext_specs: in SOURCES.yaml but not in EXTERNAL.SHA256SUMS: {rel}"
                )
            out[rel] = (url, sums[rel])
    missing = set(sums) - set(out)
    if missing:
        raise SystemExit(f"ext_specs: in EXTERNAL.SHA256SUMS but not in SOURCES.yaml: {missing}")
    return out


EXTERNAL = _sources()


def _ok(path: Path, digest: str) -> bool:
    return path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == digest


def available() -> bool:
    """Whether every external file is in the cache with the right sha256."""
    return all(_ok(EXT / rel, d) for rel, (_, d) in EXTERNAL.items())


def require(tool: str) -> None:
    bad = sorted(
        rel for rel, (_, d) in EXTERNAL.items() if (EXT / rel).is_file() and not _ok(EXT / rel, d)
    )
    if bad:
        raise SystemExit(
            f"{tool}: cached files in build/specs-ext/ have the wrong sha256: {bad}; "
            "run `python tools/ext_specs.py` to download them again"
        )
    if not available():
        raise SystemExit(
            f"{tool}: the specification files are missing from build/specs-ext/; "
            "run `python tools/ext_specs.py` (needs network once)"
        )


def fetch() -> int:
    unreachable, mismatched = [], []
    for rel, (url, digest) in EXTERNAL.items():
        dest = EXT / rel
        if _ok(dest, digest):
            continue
        try:
            with urllib.request.urlopen(url, timeout=30) as r:  # noqa: S310  fixed https URL
                body = r.read()
        except OSError as e:
            state = "cached file has the wrong sha256, and " if dest.is_file() else ""
            unreachable.append(f"{rel}: {state}cannot download {url} ({e})")
            continue
        if hashlib.sha256(body).hexdigest() != digest:
            mismatched.append(f"{rel}: sha256 differs from specs/EXTERNAL.SHA256SUMS ({url})")
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(body)
    for m in mismatched + unreachable:
        print(f"ext_specs: {m}", file=sys.stderr)
    return 1 if mismatched or unreachable else 0


if __name__ == "__main__":
    sys.exit(fetch())
