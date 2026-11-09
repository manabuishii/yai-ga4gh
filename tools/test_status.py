"""Check the test statuses in the diagram (status of tests in docs/data/diagram.yaml) against
the results of make check.

How a status is decided (never "通過" (pass) by guess):
  no pytest field -> 未実施 (not run)
  a listed pytest function is missing from junit -> error (misspelt name, or test removed)
  any case failed -> 失敗 (failed)
  all cases skipped -> 未実施 (the function exists but never ran; D-036)
  some cases skipped, or partial (a reason for being partial) is given -> 一部 (partial)
  otherwise -> 通過 (pass)
It is also an error when junit has a function named after the test ID (test_<ID with - as _>)
that is not in the pytest field.
Cases whose skip reason contains "waiting for Docker" are counted and printed as pending (D-036).
If results from the compose environment exist (tools/compose_e2e.sh copies them to
build/junit-compose.xml), they are merged: for the same case (class name + name) the stronger
result wins, in the order failed > passed > skipped (D-038).
Usage: python tools/test_status.py build/junit.xml [build/junit-compose.xml]
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


PENDING = "waiting for Docker"
STRENGTH = {"fail": 2, "pass": 1, "skip": 0}


def cases(junits: list[Path]) -> dict[str, tuple[str, str]]:
    """Case (class name + name) -> (result, skip reason). Across junits the stronger result wins."""
    out: dict[str, tuple[str, str]] = {}
    for junit in junits:
        for tc in ET.parse(junit).getroot().iter("testcase"):  # noqa: S314 our own file
            sk = tc.find("skipped")
            if tc.find("failure") is not None or tc.find("error") is not None:
                r, why = "fail", ""
            elif sk is not None:
                r, why = "skip", sk.get("message") or ""
            else:
                r, why = "pass", ""
            key = f"{tc.get('classname', '')}::{tc.get('name', '')}"
            if key not in out or STRENGTH[r] > STRENGTH[out[key][0]]:
                out[key] = (r, why)
            elif r == "skip" and out[key][0] == "skip" and PENDING in out[key][1]:
                out[key] = (r, why)  # both skipped: prefer a reason other than "waiting for Docker"
    return out


def results(junits: list[Path]) -> dict[str, list[str]]:
    """Function name (without parameters) -> result of each case (pass, fail, skip)."""
    out: dict[str, list[str]] = {}
    for key, (r, _) in cases(junits).items():
        out.setdefault(key.rsplit("::", 1)[1].split("[")[0], []).append(r)
    return out


def pending_functions(junits: list[Path]) -> set[str]:
    """Names of functions that have a case skipped as "waiting for Docker"."""
    return {key.rsplit("::", 1)[1].split("[")[0] for key, (r, why) in cases(junits).items()
            if r == "skip" and PENDING in why}  # fmt: skip


def pending(junits: list[Path]) -> int:
    """Number of cases skipped as "waiting for Docker" (pending; neither failed nor passed)."""
    return sum(1 for r, why in cases(junits).values() if r == "skip" and PENDING in why)


def status_of(t: dict, res: dict[str, list[str]]) -> str:
    names = t.get("pytest") or []
    if not names:
        return "未実施"
    missing = [n for n in names if n not in res]
    if missing:
        raise SystemExit(f"test_status: {t['id']}: pytest functions not in junit: {missing}")
    got = [r for n in names for r in res[n]]
    if "fail" in got:
        return "失敗"
    if got and all(r == "skip" for r in got):
        return "未実施"
    if "skip" in got or t.get("partial"):
        return "一部"
    return "通過"


def main() -> None:
    junits = [Path(a) for a in sys.argv[1:]] or [ROOT / "build" / "junit.xml"]
    junits = [junits[0], *[j for j in junits[1:] if j.exists()]]  # the merged one may be missing
    res = results(junits)
    tests = yaml.safe_load((ROOT / "docs" / "data" / "diagram.yaml").read_text(encoding="utf-8"))[
        "tests"
    ]
    # Without the compose junit, tests with "waiting for Docker" cases lack results: skip them
    # (the statuses in the diagram assume the compose results are merged, D-038)
    unknown = pending_functions(junits) if len(junits) == 1 else set()
    bad, not_compared = [], []
    for t in tests:
        same = "test_" + t["id"].replace("-", "_")
        if same in res and same not in (t.get("pytest") or []):
            bad.append(f"{t['id']}: {same} is in junit but not in the pytest field")
        if unknown & set(t.get("pytest") or []):
            not_compared.append(t["id"])
            continue
        want = status_of(t, res)
        if t["status"] != want:
            bad.append(f"{t['id']}: status is {t['status']}, junit says {want}")
    if bad:
        raise SystemExit(
            "test_status: test statuses in the diagram do not match make check"
            " (fix docs/data/diagram.yaml and run tools/gen_diagram.py):\n  " + "\n  ".join(bad)
        )
    n = {s: sum(1 for t in tests if t["status"] == s) for s in ("通過", "一部", "失敗", "未実施")}
    print(f"Diagram tests: {len(tests)}: " + ", ".join(f"{k} {v}" for k, v in n.items()))
    if not_compared:
        print(
            f"Tests not compared because the compose junit is missing: {len(not_compared)}"
            " (create build/junit-compose.xml with tools/compose_e2e.sh to compare them)"
        )
    print(
        f"Pending (waiting for Docker) pytest cases: {pending(junits)}"
        + (f" (merged compose results {junits[1]})" if len(junits) > 1 else "")
    )


if __name__ == "__main__":
    main()
