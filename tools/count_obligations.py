"""Count, from the test results (JUnit XML), the minimal-version ◯ obligations that pass.

Test name = obligation ID (`test_DRS_ID_02` or `test_DRS_ID_02_negative`, test-plan.md
section 3). If one ID has several tests, it counts as passed only when all of them pass.

Usage: python tools/count_obligations.py build/junit.xml [--list]
"""

from __future__ import annotations

import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
# Rows whose subject is the WES resolver (a DRS client) are counted in stage 2
# (test-plan.md section 1)
STAGE2 = {"DRS-URI-01", "DRS-URI-02", "DRS-URI-03", "DRS-URI-04", "DRS-AUTH-07"}


def obligations() -> dict[str, dict]:
    data = yaml.safe_load((ROOT / "docs/data/obligations.yaml").read_text(encoding="utf-8"))
    return {row["id"]: {**row, "api": api} for api, rows in data.items() for row in rows}


def results(junit: Path) -> dict[str, list[bool]]:
    out: dict[str, list[bool]] = {}
    for case in ET.parse(junit).getroot().iter("testcase"):  # noqa: S314 our own file
        name = case.get("name", "").split("[", 1)[0]
        # After the ID's number: end of name or "_lowercase" (test_TES_VOLUMES_2EXEC is not one)
        m = re.match(r"test_([A-Z]+(?:_[A-Z0-9]+)*?_\d+)(?:_[a-z].*)?$", name)
        if not m:
            continue
        oid = m.group(1).replace("_", "-")
        ok = not any(case.find(t) is not None for t in ("failure", "error", "skipped"))
        out.setdefault(oid, []).append(ok)
    return out


def main(argv: list[str]) -> int:
    junit = Path(argv[1]) if len(argv) > 1 else ROOT / "build/junit.xml"
    rows = obligations()
    res = results(junit)
    unknown = sorted(set(res) - set(rows))
    by_api: dict[str, list[int]] = {}
    for oid, row in rows.items():
        if row["min"] != "◯" or oid in STAGE2:
            continue
        counts = by_api.setdefault(row["api"], [0, 0])
        counts[1] += 1
        if res.get(oid) and all(res[oid]):
            counts[0] += 1
    total = [sum(c[0] for c in by_api.values()), sum(c[1] for c in by_api.values())]
    detail = ", ".join(f"{api} {c[0]}/{c[1]}" for api, c in by_api.items())
    print(f"Minimal-version ◯ obligations (stage 1): {total[0]} / {total[1]} passed ({detail})")
    stage2 = sorted(o for o in STAGE2 if rows.get(o, {}).get("min") == "◯")
    print(f"◯ counted in stage 2: {len(stage2)} ({', '.join(stage2)})")
    if unknown:
        print("Tests with IDs not in the obligations table:", ", ".join(unknown))
    if "--list" in argv:
        for oid, row in rows.items():
            if row["min"] == "◯" and oid not in STAGE2:
                mark = "ok" if res.get(oid) and all(res[oid]) else "--"
                print(mark, oid)
    return 1 if unknown else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
