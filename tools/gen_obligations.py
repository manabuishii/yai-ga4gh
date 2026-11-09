"""Generate docs/spec-obligations.md from docs/data/obligations.yaml.

Line numbers are found by searching the originals under specs/ for each quoted string; a quote
that is not found is an error. Specs with no local copy (aai, passport) are read from the
originals in build/specs-ext/ (tools/ext_specs.py).
Usage: python tools/gen_obligations.py [--check]
  --check: only verify that the output equals the current docs/spec-obligations.md
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

import ext_specs
import yaml

ROOT = Path(__file__).resolve().parent.parent
SPECS = ext_specs.EXT  # the pinned originals (specs/SOURCES.yaml), downloaded by ext_specs
SRC = ROOT / "docs" / "data" / "obligations.yaml"
OUT = ROOT / "docs" / "spec-obligations.md"

TITLES = {
    "drs": "DRS 1.5.0",
    "tes": "TES 1.1",
    "wes": "WES 1.1.0",
    "aai": "AAI 1.2・Passport 1.2(yai は Clearinghouse が本命)",
}
MIN_KEYS = ["◯", "△", "後", "外", "決"]

HEADER = """# 仕様の義務の表(spec-obligations)

<!-- 生成物: docs/data/obligations.yaml から tools/gen_obligations.py で作る。手で直さない -->

- 出典の `file:line` は `specs/` の下(`docs/spec-pins.md` で固定した版。`aai/`・`passport/` は写しを置かず、`specs/SOURCES.yaml` の固定した commit の原文)。行番号は原文の引用文字列から道具が求めたもの
- 1 行 = 1 つの義務。**ID は試験の名前と 1 対 1**(`docs/test-plan.md` 3 節)
- 強さ: 原文の語(MUST・should・shall・Must be・schema の required など)。語が無いものは「定義」「責任」など
- 場所: **本文**=OpenAPI の形の検査では見えない(説明文・Markdown にだけある)/**形**=schema で検査できる
- 最小版: ◯=最初の「動く最小版」で満たして試す/△=一部だけ(何を省くかは各設計書)/後=あとで深める/外=対象外/決=仕様に定めが無く yai が決める
- 解釈: `docs/spec-gaps.md` の番号。yai が仕様の上に足した決まりはここに書かず、設計書と `docs/decisions.md` に書く
"""


def find_lines(rel: str, quote: str) -> list[int]:
    text = (SPECS / rel).read_text(encoding="utf-8").splitlines()
    hits = [i + 1 for i, line in enumerate(text) if quote in line]
    if not hits:
        raise SystemExit(f"quote not found: {rel}: {quote!r}")
    return hits


def cite(at: list[list[str]]) -> str:
    parts = []
    for rel, quote in at:
        lines = find_lines(rel, quote)
        parts.append(f"`{rel}:{','.join(map(str, lines))}`")
    return "<br>".join(parts)


def render(data: dict) -> str:
    out = [HEADER]
    counts: dict[str, Counter] = {}
    seen: set[str] = set()
    for n, (key, title) in enumerate(TITLES.items(), start=1):
        rows = data[key]
        counts[key] = Counter(r["min"] for r in rows)
        out.append(f"\n## {n}. {title}\n")
        out.append("| ID | 対象 | 義務 | 強さ | 出典 | 場所 | 最小版 | 解釈 |")
        out.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
        for r in rows:
            if r["id"] in seen:
                raise SystemExit(f"duplicate ID: {r['id']}")
            seen.add(r["id"])
            if r["min"] not in MIN_KEYS:
                raise SystemExit(f"invalid min: {r['id']}: {r['min']}")
            out.append(
                f"| {r['id']} | {r['target']} | {r['duty']} | {r['strength']} | "
                f"{cite(r['at'])} | {r['place']} | {r['min']} | {r.get('gap', '')} |"
            )
    out.append("\n## 5. 数(道具が数えたもの)\n")
    out.append("| 仕様 | 行 | " + " | ".join(MIN_KEYS) + " |")
    out.append("| --- | --- | " + " | ".join("---" for _ in MIN_KEYS) + " |")
    for key, title in TITLES.items():
        c = counts[key]
        cells = " | ".join(str(c.get(k, 0)) for k in MIN_KEYS)
        out.append(f"| {title.split('(')[0]} | {sum(c.values())} | {cells} |")
    return "\n".join(out) + "\n"


def main() -> None:
    data = yaml.safe_load(SRC.read_text(encoding="utf-8"))
    ext_specs.require("gen_obligations")
    text = render(data)
    if "--check" in sys.argv:
        if OUT.read_text(encoding="utf-8") != text:
            raise SystemExit("docs/spec-obligations.md is out of date; regenerate it")
        return
    OUT.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
