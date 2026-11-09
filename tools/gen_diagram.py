"""Generate docs/spec-gaps.md, docs/diagram.html and the table in docs/design/system.md
section 9 from the diagram sources, and check that the diagram matches the data.

Sources: docs/data/diagram.yaml (boundaries, boxes, arrows, tests), docs/data/spec-gaps.yaml
         (gaps), docs/data/tre-layer.yaml (TRE layer; checked and drawn by tools/gen_tre.py),
         docs/data/aai-base.yaml (AAI base technologies and GA4GH's differences),
         docs/data/c-checks.yaml (consistency check table C-01 onward)
Checks: duplicate IDs and missing references, boxes inside their boundaries, required fields
per certainty (spec=quote from the original, yai=decision D number, open=reason and source),
the responsibility field on boundary-crossing arrows, quotes present in the originals under
specs/, and the counts of boxes, arrows, boundaries and gaps in the generated HTML equal the data.
C rows: row count and numbering, verdict words, linked boxes/arrows/gaps/tests exist, G numbers
in the table text are in gaps, and the counts in system.md 9.1 equal the tool's counts.
Tests: linked targets exist, they include the places of their C rows, status words.
Whether test statuses match build/junit.xml is checked by tools/test_status.py (end of
make check).
Usage: python tools/gen_diagram.py [--check]
  --check: only run the checks above and compare the output with the current files (no writes)
"""

from __future__ import annotations

import html
import itertools
import json
import re
import sys
from collections import Counter
from pathlib import Path

import ext_specs
import gen_tre
import yaml

ROOT = Path(__file__).resolve().parent.parent
SPECS = ext_specs.EXT  # the pinned originals (specs/SOURCES.yaml), downloaded by ext_specs
DATA = ROOT / "docs" / "data"
DECISIONS = ROOT / "docs" / "decisions.md"
OUT_MD = ROOT / "docs" / "spec-gaps.md"
OUT_HTML = ROOT / "docs" / "diagram.html"
SYSTEM_MD = ROOT / "docs" / "design" / "system.md"
OUT_ARTIFACT = (
    ROOT / "build" / "diagram-artifact.html"
)  # standalone page (no document wrapper); not in git

KINDS = ["食い違い", "漏れ", "古い", "組み合わせ"]
CERTAINTY = {
    "spec": "仕様で決まっている",
    "yai": "yai が仮に決めた",
    "open": "曖昧(誰も決めていない)",
}
VERDICTS = ["合わない", "注意", "合う"]  # worst first (boxes/arrows use the worst one)
STATUSES = ["失敗", "一部", "未実施", "通過"]
C_BEGIN = "<!-- C 表ここから: docs/data/c-checks.yaml から tools/gen_diagram.py で作る。表の中は手で直さない -->"
C_END = "<!-- C 表ここまで -->"
C_HEAD = (
    "| # | 呼び出し | 呼ぶ側の前提 | 呼ばれる側の仕様 | 判定 | 合わせ方 | 段階 | 試験 | 期待する結果 |\n"
    "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"
)
C_COLS = ["id", "call", "assume", "spec", "verdict", "fix", "stage", "test", "expect"]
RELATION = {"as-is": "そのまま使う", "added": "GA4GH が足した", "changed": "変えた・制限した"}
VIEW_W, VIEW_H = 1080, 860

MD_HEADER = """# 仕様の考慮漏れ・食い違い(spec-gaps)

<!-- 生成物: docs/data/spec-gaps.yaml から tools/gen_diagram.py で作る。手で直さない -->

- 作成: 2026-10-15。出典の `file:line` は `specs/` の下(`docs/spec-pins.md` の版。`aai/`・`passport/` は写しを置かず、`specs/SOURCES.yaml` の固定した commit の原文)
- 列: 種類=**食い違い**(仕様の中で矛盾)/**漏れ**(決まっていない)/**古い**(参照先が無い・名前が古い)/**組み合わせ**(1 つずつは正しいが、4 つ組み合わせると合わない)
- 確かめ: **原文**=原文を読んで確かめた/**作る**=作らないと分からない(確かめる試験を `docs/test-plan.md` に入れた)
- yai の解釈で「仮」のものは `docs/decisions.md` に同じ D 番号
- 図の場所: 関係図(`docs/diagram.html`、原本 `docs/data/diagram.yaml`)のどの箱・矢印の問題か。矢印の ①〜⑯ は `docs/design/system.md` 4 節の番号
"""


class Bad(Exception):
    pass


def load(name: str) -> dict:
    return yaml.safe_load((DATA / name).read_text(encoding="utf-8"))


# ── Quote and reference checks


def find_lines(base: Path, rel: str, quote: str) -> list[int]:
    path = base / rel
    if not path.is_file():
        raise Bad(f"original file not found: {rel}")
    text = path.read_text(encoding="utf-8").splitlines()
    hits = [i + 1 for i, line in enumerate(text) if quote in line]
    if not hits:
        raise Bad(f"quote not found: {rel}: {quote!r}")
    return hits


def cite(at: list[list[str]], base: Path = SPECS) -> list[str]:
    return [f"{rel}:{','.join(map(str, find_lines(base, rel, q)))}" for rel, q in at]


def decision_ids() -> set[str]:
    return set(re.findall(r"^\| (D-\d{3}) \|", DECISIONS.read_text(encoding="utf-8"), re.M))


def check_certainty(kind: str, item: dict, decisions: set[str]) -> None:
    c = item.get("certainty")
    where = f"{kind} {item['id']}"
    if c not in CERTAINTY:
        raise Bad(f"{where}: invalid certainty: {c}")
    if c == "spec" and not item.get("at"):
        raise Bad(f"{where}: spec needs at (quote from the original)")
    if c == "yai" and item.get("decision") not in decisions:
        raise Bad(f"{where}: yai needs a decision listed in decisions.md: {item.get('decision')}")
    if c == "open" and not (item.get("reason") and item.get("at")):
        raise Bad(f"{where}: open needs reason and at")


def c_tests(r: dict) -> list[str]:
    """Test IDs a C row points to ("後" means none)."""
    if "tests" in r:
        return r["tests"]
    return [] if r["test"] == "後" else [t.strip() for t in r["test"].split(",")]


def validate_c(rows: list[dict], dia: dict, gap_ids: set[str], places: set[str]) -> None:
    want = [f"C-{n:02d}" for n in range(1, len(rows) + 1)]
    if [r["id"] for r in rows] != want:
        raise Bad("C row numbers are not consecutive from C-01")
    tests = {t["id"]: t for t in dia.get("tests") or []}
    for r in rows:
        if r["verdict"] not in VERDICTS:
            raise Bad(f"{r['id']}: invalid verdict: {r['verdict']}")
        if not r.get("where"):
            raise Bad(f"{r['id']}: missing where (place on the diagram)")
        for w in r["where"]:
            if w not in places:
                raise Bad(f"{r['id']}: no such box/arrow in where: {w}")
        for g in r["gaps"]:
            if g not in gap_ids:
                raise Bad(f"{r['id']}: no such gap: {g}")
        named = set(re.findall(r"G-[A-Z]+-\d+", " ".join(str(r[k]) for k in C_COLS)))
        if named - set(r["gaps"]):
            raise Bad(
                f"{r['id']}: G numbers in table text not in gaps: {sorted(named - set(r['gaps']))}"
            )
        for t in c_tests(r):
            if t not in tests:
                raise Bad(f"{r['id']}: test {t} is not in tests of diagram.yaml")
            if set(r["where"]) - set(tests[t]["targets"]):
                raise Bad(
                    f"test {t}: targets do not include the places {r['where']} of C row {r['id']}"
                )
    by_id = {r["id"]: r for r in rows}
    for t in tests.values():
        for c in t.get("c_rows", []):
            if c not in by_id:
                raise Bad(f"test {t['id']}: no such row in c_rows: {c}")
            if set(by_id[c]["where"]) - set(t["targets"]):
                raise Bad(f"test {t['id']}: targets do not include the places of c_rows {c}")


def validate(dia: dict, gaps: dict, base: list[dict], rows: list[dict]) -> dict:
    """Validate, and return the index used to draw the diagram."""
    decisions = decision_ids()
    bounds = {b["id"]: b for b in dia["boundaries"]}
    boxes: dict[str, dict] = {}
    for b in dia["boxes"]:
        if b["id"] in boxes or b["id"] in bounds:
            raise Bad(f"duplicate ID: {b['id']}")
        o = bounds.get(b["boundary"])
        if o is None:
            raise Bad(f"box {b['id']}: no such boundary: {b['boundary']}")
        inside = (
            o["x"] < b["x"]
            and b["x"] + b["w"] < o["x"] + o["w"]
            and o["y"] + 24 < b["y"]
            and b["y"] + b["h"] < o["y"] + o["h"]
        )
        if not inside:
            raise Bad(f"box {b['id']} sticks out of boundary {o['id']}")
        boxes[b["id"]] = b
    for o in bounds.values():
        check_certainty("boundary", o, decisions)
        if not (
            o["x"] >= 0 and o["x"] + o["w"] <= VIEW_W and o["y"] >= 0 and o["y"] + o["h"] <= VIEW_H
        ):
            raise Bad(f"boundary {o['id']} sticks out of the diagram")
        cite(o["at"])
    arrows: dict[str, dict] = {}
    for a in dia["arrows"]:
        if a["id"] in arrows or a["id"] in boxes or a["id"] in bounds:
            raise Bad(f"duplicate ID: {a['id']}")
        for end in ("from", "to"):
            if a[end] not in boxes:
                raise Bad(f"arrow {a['id']}: no such {end} box: {a[end]}")
        if a["kind"] not in ("call", "trust"):
            raise Bad(f"arrow {a['id']}: invalid kind")
        check_certainty("arrow", a, decisions)
        if a.get("at"):
            cite(a["at"])
        crosses = boxes[a["from"]]["boundary"] != boxes[a["to"]]["boundary"]
        if crosses and not a.get("responsible"):
            raise Bad(f"arrow {a['id']}: crosses a boundary, so needs responsible")
        arrows[a["id"]] = a
    sections = [s["key"] for s in gaps["sections"]]
    seen: set[str] = set()
    for g in gaps["gaps"]:
        if g["id"] in seen:
            raise Bad(f"duplicate gap ID: {g['id']}")
        seen.add(g["id"])
        if g["kind"] not in KINDS:
            raise Bad(f"{g['id']}: invalid kind: {g['kind']}")
        if g["section"] not in sections:
            raise Bad(f"{g['id']}: invalid section: {g['section']}")
        if not g.get("where"):
            raise Bad(f"{g['id']}: missing where (place on the diagram)")
        for w in g["where"]:
            if w not in boxes and w not in arrows:
                raise Bad(f"{g['id']}: no such box/arrow in where: {w}")
    test_ids: set[str] = set()
    for t in dia.get("tests") or []:
        if t["id"] in test_ids:
            raise Bad(f"duplicate test ID: {t['id']}")
        test_ids.add(t["id"])
        if not t.get("targets"):
            raise Bad(f"test {t['id']}: missing targets")
        if t.get("status") not in STATUSES:
            raise Bad(f"test {t['id']}: invalid status: {t.get('status')}")
        if not t.get("pytest") and t["status"] != "未実施":
            raise Bad(f"test {t['id']}: no pytest but status is {t['status']}")
        if t.get("partial") and t["status"] == "通過":
            raise Bad(f"test {t['id']}: has partial but status is 通過")
        for w in t.get("targets", []):
            if w not in boxes and w not in arrows:
                raise Bad(f"test {t['id']}: no such box/arrow in targets: {w}")
        for gid in t.get("gaps", []):
            if gid not in seen:
                raise Bad(f"test {t['id']}: no such gap: {gid}")
    ids = set()
    for r in base:
        if r["id"] in ids:
            raise Bad(f"duplicate AAI base technology ID: {r['id']}")
        ids.add(r["id"])
        if r["relation"] not in RELATION:
            raise Bad(f"{r['id']}: invalid relation: {r['relation']}")
        if r["confidence"] not in ("原文", "推定"):
            raise Bad(f"{r['id']}: invalid confidence: {r['confidence']}")
        find_lines(SPECS / "base", r["file"], r["base_at"]["quote"])
        cite(r["ga4gh_at"])
        for w in r.get("links", []):
            if w not in boxes and w not in arrows:
                raise Bad(f"{r['id']}: no such box/arrow in links: {w}")
    validate_c(rows, dia, seen, set(boxes) | set(arrows))
    return {"bounds": bounds, "boxes": boxes, "arrows": arrows}


# ── spec-gaps.md


def render_md(gaps: dict) -> str:
    out = [MD_HEADER]
    for n, sec in enumerate(gaps["sections"], start=1):
        out.append(f"\n## {n}. {sec['title']}\n")
        out.append("| 番号 | 種類 | 中身 | 出典 | yai の解釈 | 確かめ | 図の場所 |")
        out.append("| --- | --- | --- | --- | --- | --- | --- |")
        for g in gaps["gaps"]:
            if g["section"] == sec["key"]:
                where = "、".join(g["where"])
                out.append(
                    f"| {g['id']} | {g['kind']} | {g['body']} | {g['source']} | {g['yai']} | {g['check']} | {where} |"
                )
    out.append(f"\n## {len(gaps['sections']) + 1}. {gaps['unknowns_title']}\n")
    out.append("| もの | 何を確かめるか | 試験 |")
    out.append("| --- | --- | --- |")
    for u in gaps["unknowns"]:
        out.append(f"| {u['thing']} | {u['what']} | {u['test']} |")
    c = Counter(g["kind"] for g in gaps["gaps"])
    out.append(f"\n## {len(gaps['sections']) + 2}. 数(道具が数えたもの)\n")
    out.append("| 仕様 | 行 | " + " | ".join(KINDS) + " |")
    out.append("| --- | --- | " + " | ".join("---" for _ in KINDS) + " |")
    for sec in gaps["sections"]:
        cs = Counter(g["kind"] for g in gaps["gaps"] if g["section"] == sec["key"])
        out.append(
            f"| {sec['title']} | {sum(cs.values())} | "
            + " | ".join(str(cs.get(k, 0)) for k in KINDS)
            + " |"
        )
    out.append(f"| 計 | {sum(c.values())} | " + " | ".join(str(c.get(k, 0)) for k in KINDS) + " |")
    return "\n".join(out) + "\n"


# ── Table in system.md section 9


def render_c_table(rows: list[dict]) -> str:
    lines = [C_HEAD] + ["| " + " | ".join(str(r[k]) for k in C_COLS) + " |" for r in rows]
    return "\n".join(lines)


def render_system(rows: list[dict]) -> str:
    text = SYSTEM_MD.read_text(encoding="utf-8")
    if text.count(C_BEGIN) != 1 or text.count(C_END) != 1:
        raise Bad("system.md must have exactly one each of the C table begin/end markers")
    head, rest = text.split(C_BEGIN)
    _, tail = rest.split(C_END)
    m = re.search(
        r"(\d+) 行。判定は 合う (\d+)・注意 (\d+)・合わない (\d+)。段階は 2 が (\d+)、4 が (\d+)(?:[^、]*)、1 の単体の試験が (\d+)",
        tail,
    )
    if not m:
        raise Bad("sentence with the counts in system.md 9.1 not found")
    v = Counter(r["verdict"] for r in rows)
    st = Counter(r["stage"] for r in rows)
    want = [len(rows), v["合う"], v["注意"], v["合わない"], st["2"], st["4"], st["1(単体)"]]
    if [int(x) for x in m.groups()] != want:
        raise Bad(
            f"counts in system.md 9.1 {list(m.groups())} differ from the tool's counts {want}"
        )
    return head + C_BEGIN + "\n" + render_c_table(rows) + "\n" + C_END + tail


# ── Diagram (SVG)


def center(b: dict) -> tuple[float, float]:
    return b["x"] + b["w"] / 2, b["y"] + b["h"] / 2


def clip(b: dict, frm: tuple[float, float], toward: tuple[float, float]) -> tuple[float, float]:
    """Point where a line from frm (inside box b) toward toward leaves the box edge."""
    (x0, y0), (x1, y1) = frm, toward
    dx, dy = x1 - x0, y1 - y0
    ts = []
    if dx:
        ts += [(b["x"] - x0) / dx, (b["x"] + b["w"] - x0) / dx]
    if dy:
        ts += [(b["y"] - y0) / dy, (b["y"] + b["h"] - y0) / dy]
    t = min(t for t in ts if t > 0)
    return x0 + dx * t, y0 + dy * t


def exit_point(
    o: dict, p: tuple[float, float], q: tuple[float, float]
) -> tuple[float, float] | None:
    """Point where segment p->q (p inside boundary o) leaves the boundary (None if it does not)."""
    (x0, y0), (x1, y1) = p, q
    best = None
    for t in _hits(o, x0, y0, x1 - x0, y1 - y0):
        if 0 < t <= 1 and (best is None or t < best):
            best = t
    return None if best is None else (x0 + (x1 - x0) * best, y0 + (y1 - y0) * best)


def _hits(o: dict, x0: float, y0: float, dx: float, dy: float) -> list[float]:
    out = []
    for edge_x in (o["x"], o["x"] + o["w"]):
        if dx:
            t = (edge_x - x0) / dx
            y = y0 + dy * t
            if o["y"] <= y <= o["y"] + o["h"]:
                out.append(t)
    for edge_y in (o["y"], o["y"] + o["h"]):
        if dy:
            t = (edge_y - y0) / dy
            x = x0 + dx * t
            if o["x"] <= x <= o["x"] + o["w"]:
                out.append(t)
    return out


def arrow_points(a: dict, idx: dict, offset: float) -> list[tuple[float, float]]:
    fb, tb = idx["boxes"][a["from"]], idx["boxes"][a["to"]]
    c0, c1 = center(fb), center(tb)
    via = [tuple(v) for v in a.get("via", [])]
    if not via and offset:
        dx, dy = c1[0] - c0[0], c1[1] - c0[1]
        n = (dx * dx + dy * dy) ** 0.5
        ox, oy = -dy / n * offset, dx / n * offset
        c0, c1 = (c0[0] + ox, c0[1] + oy), (c1[0] + ox, c1[1] + oy)
    first = via[0] if via else c1
    last = via[-1] if via else c0
    return [clip(fb, c0, first), *via, clip(tb, c1, last)]


def along(pts: list[tuple[float, float]], frac: float) -> tuple[float, float]:
    """Point at fraction frac of the polyline length (where the tag goes)."""
    segs = [
        ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5 for (x0, y0), (x1, y1) in itertools.pairwise(pts)
    ]
    rest = sum(segs) * frac
    for ((x0, y0), (x1, y1)), n in zip(itertools.pairwise(pts), segs, strict=True):
        if rest <= n:
            t = rest / n if n else 0
            return x0 + (x1 - x0) * t, y0 + (y1 - y0) * t
        rest -= n
    return pts[-1]


def gap_index(gaps: dict) -> dict[str, list[str]]:
    on: dict[str, list[str]] = {}
    for g in gaps["gaps"]:
        for w in g["where"]:
            on.setdefault(w, []).append(g["id"])
    return on


def kind_counts(ids: list[str], by_id: dict[str, dict]) -> dict[str, int]:
    c = Counter(by_id[i]["kind"] for i in ids)
    return {k: c[k] for k in KINDS if c[k]}


def c_index(rows: list[dict]) -> dict[str, list[str]]:
    on: dict[str, list[str]] = {}
    for r in rows:
        for w in r["where"]:
            on.setdefault(w, []).append(r["id"])
    return on


def test_index(dia: dict) -> dict[str, list[str]]:
    on: dict[str, list[str]] = {}
    for t in dia.get("tests") or []:
        for w in t["targets"]:
            on.setdefault(w, []).append(t["id"])
    return on


def worst(cids: list[str], by_id: dict[str, dict]) -> str:
    vs = {by_id[c]["verdict"] for c in cids}
    return next((v for v in VERDICTS if v in vs), "")


def text_w(s: str, ascii_w: float = 6.6, wide_w: float = 11.0) -> float:
    return sum(ascii_w if ord(ch) < 128 else wide_w for ch in s)


def cbadge(x: float, y: float, cids: list[str], tids: list[str], by_id: dict[str, dict]) -> str:
    """Tag with the counts of C rows and tests. Colored by the worst verdict (gray for tests only
    when there is no C row). Top-left corner is (x, y)."""
    label = " ".join(
        p for p in (f"C{len(cids)}" if cids else "", f"試{len(tids)}" if tids else "") if p
    )
    w = 12 + text_w(label)
    v = worst(cids, by_id)
    cls = f"cb v{VERDICTS.index(v)}" if v else "cb vt"
    return (
        f'<g class="{cls}" aria-hidden="true"><rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="17" rx="8.5"/>'
        f'<text x="{x + w / 2:.1f}" y="{y + 12.5:.1f}" class="cb-text">{esc(label)}</text></g>'
    )


def esc(s: str) -> str:
    return html.escape(str(s), quote=True)


def place_attrs(pid: str, con: dict, ton: dict, c_by: dict) -> str:
    cids, tids = con.get(pid, []), ton.get(pid, [])
    return f'data-c="{len(cids)}" data-tests="{len(tids)}" data-verdict="{worst(cids, c_by)}" '


def svg(dia: dict, gaps: dict, idx: dict, rows: list[dict]) -> str:
    on = gap_index(gaps)
    by_id = {g["id"]: g for g in gaps["gaps"]}
    con, ton = c_index(rows), test_index(dia)
    c_by = {r["id"]: r for r in rows}
    parts = [
        f'<svg id="map" viewBox="0 0 {VIEW_W} {VIEW_H}" role="img" aria-label="4 つの API と人・組織の関係図">',
        "<defs>",
        '<pattern id="hatch" width="8" height="8" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">'
        '<rect width="8" height="8" class="hatch-bg"/><line x1="0" y1="0" x2="0" y2="8" class="hatch-line"/></pattern>',
        '<marker id="head" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
        '<path d="M0,0 L10,5 L0,10 z" class="head"/></marker>',
        "</defs>",
    ]
    for o in dia["boundaries"]:
        cls = f"bound c-{o['certainty']}"
        parts.append(
            f'<g class="{cls}" data-kind="boundary" data-id="{esc(o["id"])}" tabindex="0" role="button" '
            f'aria-label="枠 {esc(o["name"])}">'
            f'<rect x="{o["x"]}" y="{o["y"]}" width="{o["w"]}" height="{o["h"]}" rx="10"/>'
            f'<text x="{o["x"] + 12}" y="{o["y"] + 19}" class="bound-name">{esc(o["name"])}</text>'
        )
        if o["certainty"] == "open":
            tx = o["x"] + o["w"] - 46
            parts.append(
                f'<rect x="{tx}" y="{o["y"] + 6}" width="38" height="18" rx="9" class="vague-tag"/>'
                f'<text x="{tx + 19}" y="{o["y"] + 19}" class="vague-text">曖昧</text>'
            )
        parts.append("</g>")
    pair_count: Counter = Counter()
    pair_seen: Counter = Counter()
    for a in dia["arrows"]:
        if not a.get("via"):
            pair_count[frozenset((a["from"], a["to"]))] += 1
    for a in dia["arrows"]:
        key = frozenset((a["from"], a["to"]))
        offset = 0.0
        if not a.get("via") and pair_count[key] > 1:
            k = pair_seen[key]
            pair_seen[key] += 1
            sign = 1 if a["from"] < a["to"] else -1  # put reverse arrows on the same side too
            offset = (k - (pair_count[key] - 1) / 2) * 16 * sign
        pts = arrow_points(a, idx, offset)
        d = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in pts)
        ids = on.get(a["id"], [])
        counts = kind_counts(ids, by_id)
        cls = f"arrow c-{a['certainty']} k-{a['kind']}" + (" has-gap" if ids else "")
        tag = a.get("sys") or a["id"]
        mx, my = along(pts, a.get("tag_at", 0.5))
        parts.append(
            f'<g class="{cls}" data-kind="arrow" data-id="{esc(a["id"])}" data-gaps="{len(ids)}" '
            + place_attrs(a["id"], con, ton, c_by)
            + f'data-kinds=\'{esc(json.dumps(counts, ensure_ascii=False))}\' tabindex="0" role="button" '
            f'aria-label="矢印 {esc(tag)} {esc(a["label"])}">'
            f'<path d="{d}" class="hit"/><path d="{d}" class="line" marker-end="url(#head)"/>'
        )
        fbnd = idx["bounds"][idx["boxes"][a["from"]]["boundary"]]
        if fbnd["id"] != idx["boxes"][a["to"]]["boundary"]:
            for p, q in itertools.pairwise(pts):
                ep = exit_point(fbnd, p, q)
                if ep:
                    parts.append(
                        f'<path d="M{ep[0]:.1f},{ep[1] - 6:.1f} l6,6 l-6,6 l-6,-6 z" class="duty" '
                        f'data-kind="duty" data-of="{esc(a["id"])}"/>'
                    )
                    break
        w = 16 + 8 * len(tag) if not a.get("sys") else 22 + 13 * (len(tag) - 1)
        tcls = "tag-text" + (" sys" if a.get("sys") else "")
        parts.append(
            f'<rect x="{mx - w / 2:.1f}" y="{my - 11:.1f}" width="{w}" height="22" rx="11" class="tag"/>'
            f'<text x="{mx:.1f}" y="{my + 5:.1f}" class="{tcls}">{esc(tag)}</text>'
        )
        if ids:
            parts.append(
                f'<circle cx="{mx + w / 2 + 2:.1f}" cy="{my - 9:.1f}" r="9" class="count"/>'
                f'<text x="{mx + w / 2 + 2:.1f}" y="{my - 5.5:.1f}" class="count-text">{len(ids)}</text>'
            )
        if con.get(a["id"]) or ton.get(a["id"]):
            dx, dy = a.get("badge_at", [-w / 2, 12])
            parts.append(cbadge(mx + dx, my + dy, con.get(a["id"], []), ton.get(a["id"], []), c_by))
        parts.append("</g>")
    for b in dia["boxes"]:
        ids = on.get(b["id"], [])
        counts = kind_counts(ids, by_id)
        cls = "box" + (" person" if b.get("kind") == "person" else "") + (" has-gap" if ids else "")
        parts.append(
            f'<g class="{cls}" data-kind="box" data-id="{esc(b["id"])}" data-gaps="{len(ids)}" '
            + place_attrs(b["id"], con, ton, c_by)
            + f'data-kinds=\'{esc(json.dumps(counts, ensure_ascii=False))}\' tabindex="0" role="button" '
            f'aria-label="箱 {esc(b["name"])}">'
            f'<rect x="{b["x"]}" y="{b["y"]}" width="{b["w"]}" height="{b["h"]}" rx="{20 if b.get("kind") == "person" else 6}"/>'
            f'<text x="{b["x"] + b["w"] / 2}" y="{b["y"] + b["h"] / 2 - 3}" class="box-name">{esc(b["name"])}</text>'
            f'<text x="{b["x"] + b["w"] / 2}" y="{b["y"] + b["h"] / 2 + 13}" class="box-yai">yai: {esc(b["yai"])}</text>'
        )
        x = b["x"] + b["w"] - 4
        for k in reversed(KINDS):
            if counts.get(k):
                parts.append(
                    f'<circle cx="{x}" cy="{b["y"]}" r="10" class="kdot k{KINDS.index(k)}"/>'
                    f'<text x="{x}" y="{b["y"] + 4}" class="kdot-text">{counts[k]}</text>'
                )
                x -= 22
        if con.get(b["id"]) or ton.get(b["id"]):
            parts.append(
                cbadge(
                    b["x"] + 6,
                    b["y"] + b["h"] - 9,
                    con.get(b["id"], []),
                    ton.get(b["id"], []),
                    c_by,
                )
            )
        parts.append("</g>")
    parts.append("</svg>")
    return "\n".join(parts)


# ── Two-layer AAI diagram


def aai_svg(base: list[dict]) -> str:
    """Draw base technologies in the lower layer and GA4GH's differences to them (counts per
    relation) in the upper layer."""
    techs: list[str] = []
    for r in base:
        if r["tech"] not in techs:
            techs.append(r["tech"])
    col = 132
    width = max(720, 32 + col * len(techs))
    height = 330
    parts = [
        f'<svg id="aai-map" viewBox="0 0 {width} {height}" role="img" aria-label="AAI の元の技術と GA4GH の層">',
        f'<rect x="10" y="10" width="{width - 20}" height="150" rx="8" class="layer ga4gh"/>',
        '<text x="22" y="30" class="layer-name">GA4GH の層(AAI 1.2・Passport 1.2)</text>',
        f'<rect x="10" y="200" width="{width - 20}" height="120" rx="8" class="layer base"/>',
        '<text x="22" y="220" class="layer-name">元の技術(IETF・OpenID Foundation)</text>',
    ]
    for i, t in enumerate(techs):
        x = 20 + i * col
        w = col - 10
        mid = x + w / 2
        mine = [r for r in base if r["tech"] == t]
        parts.append(f'<line x1="{mid}" y1="44" x2="{mid}" y2="232" class="rel-line"/>')
        parts.append(
            f'<g class="tech" data-kind="tech" data-id="{esc(t)}" tabindex="0" role="button" aria-label="{esc(t)}">'
            f'<rect x="{x}" y="232" width="{w}" height="74" rx="6"/>'
            f'<text x="{mid}" y="262" class="tech-name">{esc(t)}</text>'
            f'<text x="{mid}" y="282" class="tech-ref">{len(mine)} 項目</text>'
            "</g>"
        )
        y = 44
        for k, label in RELATION.items():
            n = sum(1 for r in mine if r["relation"] == k)
            if n:
                parts.append(
                    f'<g class="rel r-{k}" data-kind="rel" data-tech="{esc(t)}" data-rel="{k}" tabindex="0" '
                    f'role="button" aria-label="{esc(t)} {label} {n} 件">'
                    f'<rect x="{x}" y="{y}" width="{w}" height="30" rx="15"/>'
                    f'<text x="{mid}" y="{y + 20}" class="rel-text">{label} {n}</text></g>'
                )
                y += 36
    parts.append("</svg>")
    return "\n".join(parts)


# ── HTML


def md_inline(s: str) -> str:
    s = esc(s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    return s


def page_data(dia: dict, gaps: dict, base: list[dict], idx: dict, rows: list[dict]) -> dict:
    on = gap_index(gaps)
    con, ton = c_index(rows), test_index(dia)
    c_of_test: dict[str, list[str]] = {}
    for r in rows:
        for t in c_tests(r):
            c_of_test.setdefault(t, []).append(r["id"])

    def cited(item: dict) -> list[str]:
        return cite(item["at"]) if item.get("at") else []

    return {
        "boundaries": {
            o["id"]: {
                "name": o["name"],
                "certainty": o["certainty"],
                "reason": o.get("reason", ""),
                "role": o["role"],
                "yai": o["yai"],
                "at": cited(o),
                "boxes": [b["id"] for b in dia["boxes"] if b["boundary"] == o["id"]],
            }
            for o in dia["boundaries"]
        },
        "boxes": {
            b["id"]: {
                "name": b["name"],
                "boundary": b["boundary"],
                "yai": b["yai"],
                "gaps": on.get(b["id"], []),
                "c": con.get(b["id"], []),
                "tests": ton.get(b["id"], []),
                "arrows": [a["id"] for a in dia["arrows"] if b["id"] in (a["from"], a["to"])],
            }
            for b in dia["boxes"]
        },
        "arrows": {
            a["id"]: {
                "sys": a.get("sys", ""),
                "label": a["label"],
                "from": a["from"],
                "to": a["to"],
                "kind": a["kind"],
                "certainty": a["certainty"],
                "decision": a.get("decision", ""),
                "reason": a.get("reason", ""),
                "responsible": a.get("responsible", ""),
                "crosses": idx["boxes"][a["from"]]["boundary"] != idx["boxes"][a["to"]]["boundary"],
                "yai": a.get("yai", ""),
                "at": cited(a),
                "gaps": on.get(a["id"], []),
                "c": con.get(a["id"], []),
                "tests": ton.get(a["id"], []),
            }
            for a in dia["arrows"]
        },
        "gaps": {
            g["id"]: {
                "kind": g["kind"],
                "section": g["section"],
                "body": md_inline(g["body"]),
                "source": md_inline(g["source"]),
                "yai": md_inline(g["yai"]),
                "check": g["check"],
                "where": g["where"],
            }
            for g in gaps["gaps"]
        },
        "aai": [
            {
                "id": r["id"],
                "tech": r["tech"],
                "tech_detail": r["tech_detail"],
                "ref": r["ref"],
                "relation": r["relation"],
                "item": r["item"],
                "base": r["base"],
                "base_at": f"{r['ref']} {r['base_at']['section']}",
                "base_quote": r["base_at"]["quote"],
                "ga4gh": r["ga4gh"],
                "ga4gh_at": cite(r["ga4gh_at"]),
                "confidence": r["confidence"],
                "note": r.get("note", ""),
                "links": r.get("links", []),
            }
            for r in base
        ],
        "crows": {
            r["id"]: {
                **{k: md_inline(str(r[k])) for k in C_COLS if k != "id"},
                "where": r["where"],
                "gaps": r["gaps"],
                "tests": c_tests(r),
            }
            for r in rows
        },
        "tests": {
            t["id"]: {
                "plan": t["plan"],
                "stage": str(t.get("stage", "")),
                "targets": t["targets"],
                "gaps": t.get("gaps", []),
                "c": sorted(set(c_of_test.get(t["id"], []) + t.get("c_rows", []))),
                "pytest": t.get("pytest", []),
                "status": t["status"],
                "partial": t.get("partial", ""),
                "note": t.get("note", ""),
            }
            for t in dia.get("tests") or []
        },
    }


def stats(dia: dict, gaps: dict, base: list[dict], idx: dict, rows: list[dict]) -> dict:
    on = gap_index(gaps)
    crossing = [
        a
        for a in dia["arrows"]
        if idx["boxes"][a["from"]]["boundary"] != idx["boxes"][a["to"]]["boundary"]
    ]
    return {
        "boundaries": len(dia["boundaries"]),
        "boxes": len(dia["boxes"]),
        "arrows": len(dia["arrows"]),
        "crossing": len(crossing),
        "gaps": len(gaps["gaps"]),
        "open": sum(1 for x in dia["boundaries"] + dia["arrows"] if x["certainty"] == "open"),
        "yai": sum(1 for x in dia["boundaries"] + dia["arrows"] if x["certainty"] == "yai"),
        "spec": sum(1 for x in dia["boundaries"] + dia["arrows"] if x["certainty"] == "spec"),
        "kinds": dict(Counter(g["kind"] for g in gaps["gaps"])),
        "on_boxes": {b["id"]: len(on.get(b["id"], [])) for b in dia["boxes"]},
        "on_arrows": {a["id"]: len(on.get(a["id"], [])) for a in dia["arrows"]},
        "aai_rel": dict(Counter(r["relation"] for r in base)),
        "aai_guess": sum(1 for r in base if r["confidence"] == "推定"),
        "c_rows": len(rows),
        "verdicts": dict(Counter(r["verdict"] for r in rows)),
        "tests": len(dia.get("tests") or []),
        "test_status": dict(Counter(t["status"] for t in dia.get("tests") or [])),
        "c_on": {k: len(v) for k, v in c_index(rows).items()},
        "ng_on": sorted({w for r in rows if r["verdict"] == "合わない" for w in r["where"]}),
    }


def render_page(
    dia: dict, gaps: dict, base: list[dict], idx: dict, rows: list[dict], tre: dict, tidx: dict
) -> str:
    tpl = (ROOT / "tools" / "diagram_template.html").read_text(encoding="utf-8")
    data = page_data(dia, gaps, base, idx, rows)
    st = stats(dia, gaps, base, idx, rows)
    js = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    ts = gen_tre.stats(tre, tidx)
    lay, cert = ts["layers"], ts["certainty"]
    repl = {
        "{{TRE_SVG}}": gen_tre.svg(tre, tidx),
        "{{TRE_DATA}}": gen_tre.to_json(gen_tre.page_data(tre, tidx, cite)),
        "{{TRE_NOTE}}": esc(tre["note"]),
        "{{T_BOUND}}": f"{ts['bounds']}(新 {ts['new_bounds']})",
        "{{T_BOX}}": f"{ts['boxes']}(新 {ts['new_boxes']})",
        "{{T_ARROW}}": f"{ts['arrows']}(新 {ts['new_arrows']})",
        "{{T_CERT}}": " / ".join(str(cert.get(k, 0)) for k in gen_tre.CERTAINTY),
        "{{T_LAYER}}": " / ".join(str(lay.get(k, 0)) for k in gen_tre.LAYERS),
        "{{T_IMPLS}}": str(ts["impls"]),
        "{{T_EV}}": str(ts["evidence"]),
        "{{T_PROB}}": str(ts["problems"]),
        "{{T_HEAVY}}": str(ts["heavy_gaps"]),
        "{{T_STD}}": f"{ts['standards_in_map']}(一覧だけ {ts['standards_list']})",
        "{{SVG}}": svg(dia, gaps, idx, rows),
        "{{AAI_SVG}}": aai_svg(base),
        "{{DATA}}": js,
        "{{YAI_NOTE}}": esc(dia["yai_note"]),
        "{{N_BOUND}}": str(st["boundaries"]),
        "{{N_BOX}}": str(st["boxes"]),
        "{{N_ARROW}}": str(st["arrows"]),
        "{{N_CROSS}}": str(st["crossing"]),
        "{{N_GAP}}": str(st["gaps"]),
        "{{N_OPEN}}": str(st["open"]),
        "{{N_YAI}}": str(st["yai"]),
        "{{N_SPEC}}": str(st["spec"]),
        "{{N_AAI}}": str(len(base)),
        "{{N_C}}": str(st["c_rows"]),
        "{{N_NG}}": str(st["verdicts"].get("合わない", 0)),
        "{{N_WARN}}": str(st["verdicts"].get("注意", 0)),
        "{{N_OK}}": str(st["verdicts"].get("合う", 0)),
        "{{N_TEST}}": str(st["tests"]),
        "{{N_T_PASS}}": str(st["test_status"].get("通過", 0)),
        "{{N_T_PART}}": str(st["test_status"].get("一部", 0)),
        "{{N_T_FAIL}}": str(st["test_status"].get("失敗", 0)),
        "{{N_T_NONE}}": str(st["test_status"].get("未実施", 0)),
    }
    for k, v in repl.items():
        tpl = tpl.replace(k, v)
    if "{{" in tpl:
        raise Bad("unfilled placeholders remain in the template")
    return tpl


def wrap_document(body: str) -> str:
    return (
        '<!doctype html>\n<html lang="ja">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
        "</head>\n<body>\n" + body + "</body>\n</html>\n"
    )


def check_drawn(page: str, dia: dict, gaps: dict, rows: list[dict]) -> None:
    """Read back the counts of boxes, arrows, boundaries, gaps, C rows and tests from the diagram
    in the generated HTML, and compare them with the data."""
    on = gap_index(gaps)
    con, ton = c_index(rows), test_index(dia)
    for kind in ("box", "arrow"):
        for i, c, t in re.findall(
            rf'data-kind="{kind}" data-id="([^"]+)" data-gaps="\d+" data-c="(\d+)" data-tests="(\d+)"',
            page,
        ):
            if int(c) != len(con.get(i, [])) or int(t) != len(ton.get(i, [])):
                raise Bad(f"diagram {i}: C/test counts {c}/{t} differ from data")
    drawn_c = sum(
        int(c)
        for c in re.findall(
            r'data-kind="(?:box|arrow)" data-id="[^"]+" data-gaps="\d+" data-c="(\d+)"', page
        )
    )
    if drawn_c != sum(len(r["where"]) for r in rows):
        raise Bad(f"total of C tags on the diagram {drawn_c} differs from the total C row places")
    drawn_ng = len(re.findall(r'data-verdict="合わない"', page))
    want_ng = len({w for r in rows if r["verdict"] == "合わない" for w in r["where"]})
    if drawn_ng != want_ng:
        raise Bad(f"places marked 合わない: diagram {drawn_ng}, data {want_ng}")
    drawn = {
        k: re.findall(rf'data-kind="{k}" data-id="([^"]+)"', page)
        for k in ("boundary", "box", "arrow")
    }
    want = {
        "boundary": [o["id"] for o in dia["boundaries"]],
        "box": [b["id"] for b in dia["boxes"]],
        "arrow": [a["id"] for a in dia["arrows"]],
    }
    for k in drawn:
        if sorted(drawn[k]) != sorted(want[k]):
            raise Bad(
                f"diagram {k} differs from data: diagram {len(drawn[k])}, data {len(want[k])}"
            )
    for kind in ("box", "arrow"):
        for i, n in re.findall(rf'data-kind="{kind}" data-id="([^"]+)" data-gaps="(\d+)"', page):
            if int(n) != len(on.get(i, [])):
                raise Bad(f"diagram {i}: gap count {n} differs from data {len(on.get(i, []))}")
    duty = re.findall(r'data-kind="duty" data-of="([^"]+)"', page)
    box_of = {b["id"]: b["boundary"] for b in dia["boxes"]}
    crossing = sorted(a["id"] for a in dia["arrows"] if box_of[a["from"]] != box_of[a["to"]])
    if sorted(duty) != crossing:
        raise Bad(
            f"responsibility marks do not match boundary-crossing arrows: "
            f"marks {sorted(duty)}, arrows {crossing}"
        )


def main() -> None:
    ext_specs.require("gen_diagram")
    dia = load("diagram.yaml")
    gaps = load("spec-gaps.yaml")
    base = load("aai-base.yaml")["rows"]
    rows = load("c-checks.yaml")["rows"]
    tre = load("tre-layer.yaml")
    try:
        idx = validate(dia, gaps, base, rows)
        tidx = gen_tre.validate(tre, dia, gaps, decision_ids(), cite)
        md = render_md(gaps)
        system = render_system(rows)
        body = render_page(dia, gaps, base, idx, rows, tre, tidx)
        check_drawn(body, dia, gaps, rows)
        gen_tre.check_drawn(body, tidx)
    except (Bad, gen_tre.Bad) as e:
        raise SystemExit(f"gen_diagram: {e}") from None
    page = wrap_document(body)
    st = stats(dia, gaps, base, idx, rows)
    if "--check" in sys.argv:
        if SYSTEM_MD.read_text(encoding="utf-8") != system:
            raise SystemExit(
                "the table in docs/design/system.md section 9 is out of date; "
                "regenerate with tools/gen_diagram.py"
            )
        if OUT_MD.read_text(encoding="utf-8") != md:
            raise SystemExit(
                "docs/spec-gaps.md is out of date; regenerate with tools/gen_diagram.py"
            )
        if OUT_HTML.read_text(encoding="utf-8") != page:
            raise SystemExit(
                "docs/diagram.html is out of date; regenerate with tools/gen_diagram.py"
            )
    else:
        OUT_MD.write_text(md, encoding="utf-8")
        OUT_HTML.write_text(page, encoding="utf-8")
        SYSTEM_MD.write_text(system, encoding="utf-8")
        OUT_ARTIFACT.parent.mkdir(exist_ok=True)
        OUT_ARTIFACT.write_text(body, encoding="utf-8")
    if "--stats" in sys.argv:
        print(json.dumps(st, ensure_ascii=False, indent=1))
        print(json.dumps(gen_tre.stats(tre, tidx), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
