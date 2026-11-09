"""Check the TRE layer map (docs/data/tre-layer.yaml) and build the SVG and data for the
"TRE layer" section of docs/diagram.html.

Called from tools/gen_diagram.py (not run on its own). Rules are D-045 to D-050
(docs/decisions.md): certainty has 4 levels (spec, policy, yai, open), Five Safes are tags
(safes), 12 standards go on the map, the federation coordinator B-FED is itself a vague
boundary, the single map switches between A official only / B from implementations /
C overlay, and implementation-evidence marks are OSS, commercial, and OSS edition of commercial.
"In the official specs", "in implementations" and their overlay (both, official only,
implementation only, neither) are derived here from certainty and implementation evidence
(never written by hand in the data).
"""

from __future__ import annotations

import html
import itertools
import json
import re
from collections import Counter

CERTAINTY = {
    "spec": "仕様で決まっている",
    "policy": "方針だけ",
    "yai": "yai が仮に決めた",
    "open": "曖昧(誰も決めていない)",
}
OFFICIAL = ("spec", "policy")  # certainties shown in "A official only"
SAFES = {
    "people": "安全な人",
    "projects": "安全なプロジェクト",
    "settings": "安全な場所",
    "data": "安全なデータ",
    "outputs": "安全な出力",
}
IMPL_KINDS = {"oss": "OSS", "commercial": "商用", "commercial-oss": "商用の OSS 版"}
BASIS = {"code": "コード", "release": "リリース", "running": "稼働の記述", "docs": "文書だけ"}
LAYERS = {
    "both": "両方にある",
    "official": "公式だけ(実装なし)",
    "impl": "実装だけ(公式になし)",
    "none": "どちらにも無い",
}
STATUSES = ("Active", "Support Ready", "Dormant", "Current", "提案")


class Bad(Exception):
    pass


def esc(s: object) -> str:
    return html.escape(str(s), quote=True)


def layer_of(item: dict) -> str:
    off = item["certainty"] in OFFICIAL
    imp = bool(item.get("impl"))
    return {(True, True): "both", (True, False): "official", (False, True): "impl"}.get(
        (off, imp), "none"
    )


def confidence(ev: dict, impls: dict) -> str:
    """Confidence of implementation evidence. Commercial is docs-only, so always 推定 (D-050)."""
    if impls[ev["by"]]["kind"] == "commercial" or ev.get("guess"):
        return "推定"
    return "確か"


def _check_src(where: str, item: dict) -> None:
    for s in item.get("src", []):
        if not re.match(r"https?://", s.get("url", "")):
            raise Bad(f"{where}: invalid src url: {s.get('url')}")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(s.get("seen", ""))):
            raise Bad(f"{where}: src needs seen (date observed)")


def check_certainty(where: str, item: dict, decisions: set[str], cite) -> None:
    c = item.get("certainty")
    if c not in CERTAINTY:
        raise Bad(f"{where}: invalid certainty: {c}")
    has_src = bool(item.get("src") or item.get("at"))
    if c in OFFICIAL and not has_src:
        raise Bad(f"{where}: {c} needs src (official document) or at (original under specs/)")
    if c == "yai" and item.get("decision") not in decisions:
        raise Bad(f"{where}: yai needs a decision listed in decisions.md: {item.get('decision')}")
    if c == "open" and not (item.get("reason") and (has_src or item.get("impl"))):
        raise Bad(f"{where}: open needs reason and one of src, at, impl")
    _check_src(where, item)
    if item.get("at"):
        cite(item["at"])


def _check_impl(where: str, item: dict, impls: dict) -> None:
    for ev in item.get("impl", []):
        if ev.get("by") not in impls:
            raise Bad(f"{where}: impl by is not in the implementation list: {ev.get('by')}")
        if ev.get("basis") not in BASIS:
            raise Bad(f"{where}: invalid impl basis: {ev.get('basis')}")
        if impls[ev["by"]]["kind"] == "commercial" and ev["basis"] == "code":
            raise Bad(f"{where}: evidence for commercial {ev['by']} cannot be code")
        if not ev.get("note"):
            raise Bad(f"{where}: impl needs note (what was found)")
        _check_src(where, {"src": [ev]})


def validate(tre: dict, dia: dict, gaps: dict, decisions: set[str], cite) -> dict:
    vw, vh = tre["view"]["w"], tre["view"]["h"]
    dbounds = {b["id"]: b for b in dia["boundaries"]}
    dboxes = {b["id"]: b for b in dia["boxes"]}
    darrows = {a["id"]: a for a in dia["arrows"]}
    gap_ids = {g["id"] for g in gaps["gaps"]}
    impls = {i["id"]: i for i in tre["impls"]}
    ev_on: dict[str, list[dict]] = {}
    for ev in tre["evidence"]:
        ev_on.setdefault(ev["at"], []).append(ev)
    for i in impls.values():
        if i["kind"] not in IMPL_KINDS:
            raise Bad(f"implementation {i['id']}: invalid kind: {i['kind']}")
        _check_src(f"implementation {i['id']}", {"src": [i]})
    bounds: dict[str, dict] = {}
    for o in tre["boundaries"]:
        o["impl"] = ev_on.pop(o["id"], [])
        if o["id"] in bounds:
            raise Bad(f"duplicate TRE boundary ID: {o['id']}")
        if o["id"] in dbounds:
            if o.get("name") or o.get("certainty"):
                raise Bad(
                    f"TRE boundary {o['id']}: same ID as a diagram boundary, so omit name/certainty"
                )
            o = {**dbounds[o["id"]], **o, "ref": True}
        elif not o.get("name"):
            raise Bad(f"TRE boundary {o['id']}: a boundary not in the diagram needs name")
        else:
            check_certainty(f"TRE boundary {o['id']}", o, decisions, cite)
        if not (o["x"] >= 0 and o["x"] + o["w"] <= vw and o["y"] >= 0 and o["y"] + o["h"] <= vh):
            raise Bad(f"TRE boundary {o['id']} sticks out of the map")
        _check_impl(f"TRE boundary {o['id']}", o, impls)
        bounds[o["id"]] = o
    boxes: dict[str, dict] = {}
    for b in tre["boxes"]:
        b["impl"] = ev_on.pop(b["id"], [])
        where = f"TRE box {b['id']}"
        if b["id"] in boxes or b["id"] in bounds:
            raise Bad(f"duplicate TRE ID: {b['id']}")
        if b["id"] in dboxes:
            ref = dboxes[b["id"]]
            if b["boundary"] != ref["boundary"]:
                raise Bad(f"{where}: boundary differs from the diagram ({ref['boundary']})")
            b = {"name": ref["name"], "certainty": "spec", **b, "ref": True}
        elif b["id"].startswith("n-"):
            check_certainty(where, b, decisions, cite)
        else:
            raise Bad(f"{where}: a box not in the diagram must have an ID starting with n-")
        o = bounds.get(b["boundary"])
        if o is None:
            raise Bad(f"{where}: no such boundary: {b['boundary']}")
        inside = (
            o["x"] < b["x"]
            and b["x"] + b["w"] < o["x"] + o["w"]
            and o["y"] + 24 < b["y"]
            and b["y"] + b["h"] < o["y"] + o["h"]
        )
        if not inside:
            raise Bad(f"{where} sticks out of boundary {o['id']}")
        for s in b.get("safes", []):
            if s not in SAFES:
                raise Bad(f"{where}: invalid safes: {s}")
        _check_impl(where, b, impls)
        boxes[b["id"]] = b
    arrows: dict[str, dict] = {}
    for a in tre["arrows"]:
        a["impl"] = ev_on.pop(a["id"], [])
        where = f"TRE arrow {a['id']}"
        if a["id"] in arrows or a["id"] in boxes or a["id"] in bounds:
            raise Bad(f"duplicate TRE ID: {a['id']}")
        if a["id"] in darrows:
            ref = darrows[a["id"]]
            for k in ("from", "to", "label", "certainty", "kind"):
                if k in a:
                    raise Bad(f"{where}: same ID as a diagram arrow, so omit {k}")
            a = {
                **{k: ref[k] for k in ("from", "to", "label", "certainty", "kind")},
                "sys": ref.get("sys", ""),
                "reason": ref.get("reason", ""),
                "responsible": ref.get("responsible", ""),
                **a,
                "ref": True,
            }
        elif a["id"].startswith("r-"):
            check_certainty(where, a, decisions, cite)
        else:
            raise Bad(f"{where}: an arrow not in the diagram must have an ID starting with r-")
        for end in ("from", "to"):
            if a[end] not in boxes:
                raise Bad(f"{where}: {end} box is not on the TRE map: {a[end]}")
        if a.get("kind", "call") not in ("call", "trust"):
            raise Bad(f"{where}: invalid kind")
        a.setdefault("kind", "call")
        crosses = boxes[a["from"]]["boundary"] != boxes[a["to"]]["boundary"]
        if crosses and not a.get("responsible"):
            raise Bad(f"{where}: crosses a boundary, so needs responsible")
        for s in a.get("safes", []):
            if s not in SAFES:
                raise Bad(f"{where}: invalid safes: {s}")
        _check_impl(where, a, impls)
        arrows[a["id"]] = a
    if ev_on:
        raise Bad(f"implementation evidence at is not on the TRE map: {sorted(ev_on)}")
    places = set(boxes) | set(arrows) | set(bounds)
    seen: set[str] = set()
    for p in tre["problems"]:
        if p["id"] in seen:
            raise Bad(f"duplicate TRE problem ID: {p['id']}")
        seen.add(p["id"])
        if p["certainty"] not in CERTAINTY:
            raise Bad(f"{p['id']}: invalid certainty")
        for w in p["where"]:
            if w not in places:
                raise Bad(f"{p['id']}: where place is not on the TRE map: {w}")
        for g in p.get("gaps", []):
            if g not in gap_ids:
                raise Bad(f"{p['id']}: gap not in spec-gaps.yaml: {g}")
    ranks = [p["rank"] for p in tre["problems"] if p.get("rank")]
    if sorted(ranks) != list(range(1, len(ranks) + 1)):
        raise Bad("TRE problem ranks (most severe first) are not consecutive from 1")
    for h in tre["heavy_gaps"]:
        if h["gap"] not in gap_ids:
            raise Bad(f"heavy gap {h['gap']} is not in spec-gaps.yaml")
        for t in h["tre"]:
            if t not in seen:
                raise Bad(f"heavy gap {h['gap']}: no TRE problem {t}")
    in_map = [s for s in tre["standards"] if s["in_map"]]
    if len(in_map) != tre["standards_in_map"]:
        raise Bad(
            f"{len(in_map)} standards on the map (the rule is {tre['standards_in_map']}, D-047)"
        )
    for s in tre["standards"]:
        if s["status"] not in STATUSES:
            raise Bad(f"standard {s['name']}: invalid status: {s['status']}")
        if s["in_map"] and not s.get("places"):
            raise Bad(f"standard {s['name']}: needs places to be on the map")
        for w in s.get("places", []):
            if w not in places:
                raise Bad(f"standard {s['name']}: places entry is not on the TRE map: {w}")
    return {"bounds": bounds, "boxes": boxes, "arrows": arrows, "impls": impls}


# ── Map (SVG)


def _center(b: dict) -> tuple[float, float]:
    return b["x"] + b["w"] / 2, b["y"] + b["h"] / 2


def _clip(b: dict, frm: tuple[float, float], toward: tuple[float, float]) -> tuple[float, float]:
    (x0, y0), (x1, y1) = frm, toward
    dx, dy = x1 - x0, y1 - y0
    ts = []
    if dx:
        ts += [(b["x"] - x0) / dx, (b["x"] + b["w"] - x0) / dx]
    if dy:
        ts += [(b["y"] - y0) / dy, (b["y"] + b["h"] - y0) / dy]
    t = min(t for t in ts if t > 0)
    return x0 + dx * t, y0 + dy * t


def _points(a: dict, idx: dict) -> list[tuple[float, float]]:
    fb, tb = idx["boxes"][a["from"]], idx["boxes"][a["to"]]
    c0, c1 = _center(fb), _center(tb)
    dx0, dy0 = a.get("shift", [0, 0])
    c0, c1 = (c0[0] + dx0, c0[1] + dy0), (c1[0] + dx0, c1[1] + dy0)
    via = [(float(v[0]), float(v[1])) for v in a.get("via", [])]
    first = via[0] if via else c1
    last = via[-1] if via else c0
    return [_clip(fb, c0, first), *via, _clip(tb, c1, last)]


def _along(pts: list[tuple[float, float]], frac: float) -> tuple[float, float]:
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


def _exit(o: dict, pts: list[tuple[float, float]]) -> tuple[float, float] | None:
    """First point where the polyline leaves boundary o."""
    for (x0, y0), (x1, y1) in itertools.pairwise(pts):
        dx, dy = x1 - x0, y1 - y0
        best = None
        for ex in (o["x"], o["x"] + o["w"]):
            if dx:
                t = (ex - x0) / dx
                if 0 < t <= 1 and o["y"] <= y0 + dy * t <= o["y"] + o["h"]:
                    best = t if best is None else min(best, t)
        for ey in (o["y"], o["y"] + o["h"]):
            if dy:
                t = (ey - y0) / dy
                if 0 < t <= 1 and o["x"] <= x0 + dx * t <= o["x"] + o["w"]:
                    best = t if best is None else min(best, t)
        if best is not None:
            return x0 + dx * best, y0 + dy * best
    return None


def _attrs(item: dict, impls: dict) -> str:
    kinds = sorted({impls[e["by"]]["kind"] for e in item.get("impl", [])})
    return (
        f'data-layer="{layer_of(item)}" data-cert="{item["certainty"]}" '
        f'data-safes="{" ".join(item.get("safes", []))}" data-impl="{" ".join(kinds)}" '
    )


def _marks_w(item: dict, impls: dict) -> float:
    kinds = {impls[e["by"]]["kind"] for e in item.get("impl", [])}
    return sum({"oss": 18, "commercial": 22, "commercial-oss": 29}[k] for k in kinds)


def _impl_marks(x: float, y: float, item: dict, impls: dict) -> str:
    """Implementation-evidence marks (from the right: OSS=O, commercial=商, OSS edition=商O).
    Bottom-right corner is (x, y)."""
    kinds = [k for k in IMPL_KINDS if k in {impls[e["by"]]["kind"] for e in item.get("impl", [])}]
    out = []
    for k in reversed(kinds):
        label = {"oss": "O", "commercial": "商", "commercial-oss": "商O"}[k]
        w = 8 + (7 if k == "oss" else 11 if k == "commercial" else 18)
        x -= w
        out.append(
            f'<g class="im im-{k}" aria-hidden="true"><rect x="{x:.1f}" y="{y - 15:.1f}" width="{w}" height="15" rx="7.5"/>'
            f'<text x="{x + w / 2:.1f}" y="{y - 4:.1f}" class="im-text">{label}</text></g>'
        )
        x -= 3
    return "".join(out)


def svg(tre: dict, idx: dict) -> str:
    vw, vh = tre["view"]["w"], tre["view"]["h"]
    impls = idx["impls"]
    parts = [
        f'<svg id="tre-map" viewBox="0 0 {vw} {vh}" role="img" aria-label="TRE の層: 研究者・所属機関・DAC・連合の調整役・計算の運用者・データを持つ機関">',
        "<defs>",
        '<pattern id="tre-hatch" width="8" height="8" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">'
        '<rect width="8" height="8" class="hatch-bg"/><line x1="0" y1="0" x2="0" y2="8" class="hatch-line"/></pattern>',
        '<marker id="tre-head" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
        '<path d="M0,0 L10,5 L0,10 z" class="head"/></marker>',
        "</defs>",
    ]
    for o in idx["bounds"].values():
        cls = f"bound c-{o['certainty']}" + (" new" if not o.get("ref") else "")
        parts.append(
            f'<g class="{cls}" data-kind="tre-boundary" data-id="{esc(o["id"])}" '
            + _attrs(o, impls)
            + f'tabindex="0" role="button" aria-label="枠 {esc(o["name"])}">'
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
    for a in idx["arrows"].values():
        pts = _points(a, idx)
        d = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in pts)
        tag = a.get("sys") or a["id"]
        mx, my = _along(pts, a.get("tag_at", 0.5))
        cls = f"arrow c-{a['certainty']} k-{a['kind']}" + (" ref" if a.get("ref") else "")
        parts.append(
            f'<g class="{cls}" data-kind="tre-arrow" data-id="{esc(a["id"])}" '
            + _attrs(a, impls)
            + f'tabindex="0" role="button" aria-label="矢印 {esc(tag)} {esc(a["label"])}">'
            f'<path d="{d}" class="hit"/><path d="{d}" class="line" marker-end="url(#tre-head)"/>'
        )
        fb = idx["bounds"][idx["boxes"][a["from"]]["boundary"]]
        if fb["id"] != idx["boxes"][a["to"]]["boundary"]:
            ep = _exit(fb, pts)
            if ep:
                parts.append(
                    f'<path d="M{ep[0]:.1f},{ep[1] - 6:.1f} l6,6 l-6,6 l-6,-6 z" class="duty" '
                    f'data-kind="tre-duty" data-of="{esc(a["id"])}"/>'
                )
        w = 14 + 7 * len(tag)
        parts.append(
            f'<rect x="{mx - w / 2:.1f}" y="{my - 10:.1f}" width="{w}" height="20" rx="10" class="tag"/>'
            f'<text x="{mx:.1f}" y="{my + 4.5:.1f}" class="tag-text">{esc(tag)}</text>'
            + _impl_marks(mx + w / 2 + 3 + _marks_w(a, impls), my + 7.5, a, impls)
            + "</g>"
        )
    for b in idx["boxes"].values():
        cls = (
            "box"
            + (" person" if b.get("kind") == "person" else "")
            + (" ref" if b.get("ref") else f" c-{b['certainty']}")
        )
        sub = "4 つの図にもある" if b.get("ref") else CERTAINTY[b["certainty"]]
        parts.append(
            f'<g class="{cls}" data-kind="tre-box" data-id="{esc(b["id"])}" '
            + _attrs(b, impls)
            + f'tabindex="0" role="button" aria-label="箱 {esc(b["name"])}">'
            f'<rect x="{b["x"]}" y="{b["y"]}" width="{b["w"]}" height="{b["h"]}" rx="{20 if b.get("kind") == "person" else 6}"/>'
            f'<text x="{b["x"] + b["w"] / 2}" y="{b["y"] + b["h"] / 2 - 3}" class="box-name">{esc(b["name"])}</text>'
            f'<text x="{b["x"] + b["w"] / 2}" y="{b["y"] + b["h"] / 2 + 13}" class="box-yai">{esc(sub)}</text>'
            + _impl_marks(b["x"] + b["w"] - 3, b["y"] + b["h"] - 3, b, impls)
            + "</g>"
        )
    parts.append("</svg>")
    return "\n".join(parts)


# ── Page data and counts


def _ev(item: dict, impls: dict) -> list[dict]:
    return [
        {
            "by": e["by"],
            "name": impls[e["by"]]["name"],
            "kind": impls[e["by"]]["kind"],
            "basis": e["basis"],
            "url": e["url"],
            "seen": str(e["seen"]),
            "note": e["note"],
            "conf": confidence(e, impls),
        }
        for e in item.get("impl", [])
    ]


def _src(item: dict, cite) -> list[dict]:
    out = [
        {"url": s["url"], "quote": s.get("quote", ""), "seen": str(s["seen"])}
        for s in item.get("src", [])
    ]
    if item.get("at"):
        out += [{"url": "", "quote": c, "seen": ""} for c in cite(item["at"])]
    return out


def page_data(tre: dict, idx: dict, cite) -> dict:
    impls = idx["impls"]
    probs_on: dict[str, list[str]] = {}
    for p in tre["problems"]:
        for w in p["where"]:
            probs_on.setdefault(w, []).append(p["id"])

    def common(x: dict) -> dict:
        return {
            "name": x.get("name") or x.get("label", ""),
            "certainty": x["certainty"],
            "layer": layer_of(x),
            "reason": x.get("reason", ""),
            "owner": x.get("owner", ""),
            "note": x.get("note", ""),
            "safes": x.get("safes", []),
            "src": _src(x, cite) if not x.get("ref") else [],
            "impl": _ev(x, impls),
            "problems": probs_on.get(x["id"], []),
            "ref": bool(x.get("ref")),
            "decision": x.get("decision", ""),
        }

    return {
        "bounds": {
            o["id"]: {**common(o), "role": o.get("role", "")} for o in idx["bounds"].values()
        },
        "boxes": {b["id"]: {**common(b), "boundary": b["boundary"]} for b in idx["boxes"].values()},
        "arrows": {
            a["id"]: {
                **common(a),
                "sys": a.get("sys", ""),
                "from": a["from"],
                "to": a["to"],
                "kind": a["kind"],
                "responsible": a.get("responsible", ""),
                "crosses": idx["boxes"][a["from"]]["boundary"] != idx["boxes"][a["to"]]["boundary"],
            }
            for a in idx["arrows"].values()
        },
        "impls": {
            i["id"]: {
                k: str(i.get(k, "")) for k in ("name", "kind", "url", "release", "seen", "note")
            }
            for i in impls.values()
        },
        "problems": {p["id"]: p for p in tre["problems"]},
        "heavy": tre["heavy_gaps"],
        "standards": tre["standards"],
    }


def stats(tre: dict, idx: dict) -> dict:
    items = (
        list(idx["bounds"].values()) + list(idx["boxes"].values()) + list(idx["arrows"].values())
    )
    new = [x for x in items if not x.get("ref")]
    ev = [e for x in items for e in x.get("impl", [])]
    return {
        "bounds": len(idx["bounds"]),
        "boxes": len(idx["boxes"]),
        "arrows": len(idx["arrows"]),
        "new_bounds": sum(1 for o in idx["bounds"].values() if not o.get("ref")),
        "new_boxes": sum(1 for b in idx["boxes"].values() if not b.get("ref")),
        "new_arrows": sum(1 for a in idx["arrows"].values() if not a.get("ref")),
        "certainty": dict(Counter(x["certainty"] for x in items)),
        "open_new": sum(1 for x in new if x["certainty"] == "open"),
        "layers": dict(Counter(layer_of(x) for x in items)),
        "impl_kinds": dict(Counter(idx["impls"][e["by"]]["kind"] for e in ev)),
        "evidence": len(ev),
        "impls": len(idx["impls"]),
        "problems": len(tre["problems"]),
        "heavy_gaps": len(tre["heavy_gaps"]),
        "standards_in_map": sum(1 for s in tre["standards"] if s["in_map"]),
        "standards_list": sum(1 for s in tre["standards"] if not s["in_map"]),
    }


def check_drawn(page: str, idx: dict) -> None:
    """Read back boundaries, boxes, arrows and their overlay, certainty, tags and implementation
    marks from the TRE map in the generated HTML, and compare them with the data."""
    for kind, want in (
        ("tre-boundary", idx["bounds"]),
        ("tre-box", idx["boxes"]),
        ("tre-arrow", idx["arrows"]),
    ):
        drawn = re.findall(
            rf'data-kind="{kind}" data-id="([^"]+)" data-layer="([a-z]+)" data-cert="([a-z]+)" '
            r'data-safes="([^"]*)" data-impl="([^"]*)"',
            page,
        )
        if sorted(d[0] for d in drawn) != sorted(want):
            raise Bad(f"TRE map {kind} differs from data: map {len(drawn)}, data {len(want)}")
        for i, layer, cert, safes, kinds in drawn:
            x = want[i]
            ks = " ".join(sorted({idx["impls"][e["by"]]["kind"] for e in x.get("impl", [])}))
            if (layer, cert, safes, kinds) != (
                layer_of(x),
                x["certainty"],
                " ".join(x.get("safes", [])),
                ks,
            ):
                raise Bad(f"TRE map {i}: overlay, certainty, tags or impl marks differ from data")
    duty = re.findall(r'data-kind="tre-duty" data-of="([^"]+)"', page)
    crossing = sorted(
        a["id"]
        for a in idx["arrows"].values()
        if idx["boxes"][a["from"]]["boundary"] != idx["boxes"][a["to"]]["boundary"]
    )
    if sorted(duty) != crossing:
        raise Bad(
            f"TRE map responsibility marks do not match boundary-crossing arrows: "
            f"marks {sorted(duty)}, arrows {crossing}"
        )


def to_json(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"), default=str).replace(
        "</", "<\\/"
    )
