"""Check the interop diagram (tests/reports/interop/interop.d2) and the README table against the
source of truth for the results (results.yaml) (stage 3).

- For each pair in the source, the diagram has exactly one arrow in the same direction, labelled
  `<short pair name> <status> [<reason numbers>]`, with a line color matching the status
  (pass=green, partial=yellow, fail=red, skip=gray)
- The diagram has no arrow that is not in the source
- The README.md table lists the "first" 5 pairs with the same results as the source
  (the result strings)
- Each reason number is in the reasons table, and the README reasons table has the same number
Called from `make check`. Prints one line per failure and exits 1.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DIR = ROOT / "tests" / "reports" / "interop"
COLORS = {"pass": "#2e8b57", "partial": "#c9a400", "fail": "#c0392b", "skip": "#999999"}
EDGE = re.compile(
    r'^(?P<from>[a-z]+\.[a-z]+) -> (?P<to>[a-z]+\.[a-z]+): "(?P<label>[^"]+)" \{(?P<style>[^}]*)\}'
)
STATUS_WORDS = {"pass": "通過", "partial": "通過", "fail": "通らない", "skip": None}


def main() -> int:
    errors: list[str] = []
    data = yaml.safe_load((DIR / "results.yaml").read_text())
    pairs = {p["id"]: p for p in data["pairs"]}
    reasons = {int(k): v for k, v in data["reasons"].items()}
    edges: dict[tuple[str, str], dict] = {}
    for line in (DIR / "interop.d2").read_text().splitlines():
        m = EDGE.match(line.strip())
        if m:
            src, dst = m["from"].split(".")[1], m["to"].split(".")[1]
            edges[(src, dst)] = {"label": m["label"], "style": m["style"]}
    for pid, p in pairs.items():
        short = pid.removeprefix("INTEROP-")
        e = edges.pop((p["from"], p["to"]), None)
        if e is None:
            errors.append(f"{pid}: no arrow {p['from']} -> {p['to']} in the diagram")
            continue
        nums = ",".join(map(str, p["reasons"]))
        want = f"{short} {p['status']}" + (f" [{nums}]" if nums else "")
        if e["label"] != want:
            errors.append(f"{pid}: label is {e['label']!r}, source says {want!r}")
        if COLORS[p["status"]] not in e["style"]:
            color = COLORS[p["status"]]
            errors.append(
                f"{pid}: line color is not {color} for status {p['status']}: {e['style']}"
            )
        for n in p["reasons"]:
            if n not in reasons:
                errors.append(f"{pid}: reason {n} is not in reasons")
    for (src, dst), e in edges.items():
        errors.append(f"arrow in the diagram but not in the source: {src} -> {dst} ({e['label']})")
    readme = (DIR / "README.md").read_text()
    for pid, p in pairs.items():
        if p["status"] == "skip":
            if f"{pid}(" not in readme and pid not in readme:
                errors.append(f"{pid}: not in the README")
            continue
        row = next((ln for ln in readme.splitlines() if ln.startswith(f"| {pid} ")), None)
        if row is None:
            errors.append(f"{pid}: no row in the README table")
            continue
        word = STATUS_WORDS[p["status"]]
        if word and word not in row:
            errors.append(f"{pid}: README row lacks {word!r} (source says {p['status']})")
        if p["status"] != "pass" and f"[{p['reasons'][0]}]" not in row:
            errors.append(f"{pid}: README row lacks reason number [{p['reasons'][0]}]")
        if p.get("score") and str(p["score"]).split("(")[0].strip() not in row:
            errors.append(f"{pid}: README row lacks result {p['score']!r}")
    for n, r in reasons.items():
        if not re.search(rf"^\| {n} \| {re.escape(r['class'])} \|", readme, re.M):
            errors.append(f"reason {n}: README reasons table has no row with class {r['class']!r}")
    for e in errors:
        print("✗", e)
    print(
        f"interop_diagram_check: {len(pairs)} pairs, {len(reasons)} reasons, {len(errors)} errors"
    )
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
