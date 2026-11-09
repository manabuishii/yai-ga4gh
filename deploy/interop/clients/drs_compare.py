"""INTEROP-DRS-1: yai-wes resolver -> partner DRS (passport-playground-drs 5eb7745, DRS 1.5.0).

(1) Directly against the partner: service-info, OPTIONS, GET object, GET access, fetch.
Compare with the same responses from yai-drs and record differences
(2) Real path: alice submits a cat of drs://pgdrs.yai.test/<id> to WES -> the WES
resolver resolves it against the partner, hands it to TES, COMPLETE.
    The TES task input URL must be the partner's access URL (http://pgdrs.yai.test:8645/files/…)
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import httpx
from common import Report, access_token, excerpt, passport

PG = "https://pgdrs.yai.test/ga4gh/drs/v1"
YAI = "https://drs.yai.test/ga4gh/drs/v1"
# data/alice.tsv from the README table
ALICE_TSV = "ee7b2550261f8b0bb5c581c16d5edf6b372247fc61c7e6bf188a390cd25a7850"
CAT = """\
cwlVersion: v1.2
class: CommandLineTool
baseCommand: cat
inputs:
  infile: {type: File, inputBinding: {position: 1}}
outputs:
  out: {type: File, outputBinding: {glob: out.txt}}
stdout: out.txt
requirements: {DockerRequirement: {dockerPull: "alpine:3.20"}}
"""


def probe(rep: Report, label: str, base: str, oid: str, headers: dict | None = None) -> dict:
    out: dict = {}
    c = httpx.Client(timeout=30, headers=headers or {})
    r = c.get(f"{base}/service-info")
    out["service_info"] = {
        "status": r.status_code,
        "type": (r.json().get("type") if r.status_code == 200 else None),
    }
    r = c.options(f"{base}/objects/{oid}")
    out["options"] = {"status": r.status_code, "body": excerpt(r.text, 300)}
    r = c.get(f"{base}/objects/{oid}")
    obj = r.json() if r.status_code == 200 else {}
    out["object"] = {
        "status": r.status_code,
        "keys": sorted(obj),
        "self_uri": obj.get("self_uri"),
        "created_time": obj.get("created_time"),
        "checksums": obj.get("checksums"),
        "access_methods": obj.get("access_methods"),
    }
    am = (obj.get("access_methods") or [{}])[0]
    if am.get("access_url"):  # yai public objects return access_url directly (no access_id)
        acc = am["access_url"]
        out["access"] = {
            "status": "inline",
            "url_scheme_host": "/".join(str(acc.get("url", "")).split("/")[:3]),
            "headers": acc.get("headers"),
        }
        g = c.get(acc["url"])
        out["download"] = {
            "status": g.status_code,
            "bytes": len(g.content),
            "head": excerpt(g.text, 80),
        }
    elif am.get("access_id"):
        r = c.get(f"{base}/objects/{oid}/access/{am['access_id']}")
        acc = r.json() if r.status_code == 200 else {}
        out["access"] = {
            "status": r.status_code,
            "url_scheme_host": "/".join(str(acc.get("url", "")).split("/")[:3]),
            "headers": acc.get("headers"),
        }
        if acc.get("url"):
            g = c.get(acc["url"])
            out["download"] = {
                "status": g.status_code,
                "bytes": len(g.content),
                "head": excerpt(g.text, 80),
            }
    rep.step(
        f"{label}: service-info, OPTIONS, GET object, access, fetch",
        out["object"]["status"] == 200 and out.get("download", {}).get("status") == 200,
        **out,
    )
    return out


def main() -> int:
    rep = Report("INTEROP-DRS-1", "yai-wes resolver → ga4gh/passport-playground-drs 5eb7745")
    ids = json.loads(Path(os.environ.get("YAI_E2E_IDS", "/yai/e2e/ids.json")).read_text())
    pg = probe(rep, "partner (pgdrs) directly", PG, ALICE_TSV)
    yai = probe(
        rep,
        "yai-drs directly (comparison)",
        YAI,
        ids["public"],
        {"Authorization": "Bearer " + access_token("alice", "yai-drs")},
    )
    rep.doc["differences"] = {
        "OPTIONS": {"pgdrs": pg["options"]["status"], "yai": yai["options"]["status"]},
        "created_time": {
            "pgdrs": pg["object"]["created_time"],
            "yai": yai["object"]["created_time"],
        },
        "checksum_types": {
            "pgdrs": [c["type"] for c in pg["object"]["checksums"] or []],
            "yai": [c["type"] for c in yai["object"]["checksums"] or []],
        },
        "access_methods": {
            "pgdrs": pg["object"]["access_methods"],
            "yai": yai["object"]["access_methods"],
        },
        "access_url_scheme_host": {
            "pgdrs": pg.get("access", {}).get("url_scheme_host"),
            "yai": yai.get("access", {}).get("url_scheme_host"),
        },
    }

    # Real path: WES -> resolver -> pgdrs -> TES fetches
    wes = httpx.Client(
        base_url="https://wes.yai.test/ga4gh/wes/v1",
        timeout=60,
        headers={"Authorization": "Bearer " + access_token("alice", "yai-wes")},
    )
    params = {"infile": {"class": "File", "location": f"drs://pgdrs.yai.test/{ALICE_TSV}"}}
    data = {
        "workflow_type": "CWL",
        "workflow_type_version": "v1.2",
        "workflow_url": "wf.cwl",
        "workflow_params": json.dumps(params),
        "yai_passports": json.dumps([passport("alice")]),
    }
    r = wes.post("/runs", data=data, files=[("workflow_attachment", ("wf.cwl", CAT.encode()))])
    rep.step(
        "Submit a cat of drs://pgdrs.yai.test/<id> to WES",
        r.status_code == 200,
        status=r.status_code,
        body=excerpt(r.text),
    )
    if r.status_code != 200:
        return rep.save("INTEROP-DRS-1.json")
    run_id = r.json()["run_id"]
    deadline, state, log = time.monotonic() + 300, "", {}
    while time.monotonic() < deadline:
        log = wes.get(f"/runs/{run_id}").json()
        state = log["state"]
        if state in {"COMPLETE", "EXECUTOR_ERROR", "SYSTEM_ERROR", "CANCELED"}:
            break
        time.sleep(2)
    rep.step(
        "run COMPLETE (resolver resolved the partner; TES fetched from the partner access URL)",
        state == "COMPLETE",
        state=state,
        system_logs=excerpt(log.get("run_log", {}).get("system_logs")),
        outputs=excerpt(log.get("outputs")),
    )
    tes = httpx.Client(
        base_url="https://tes.yai.test/ga4gh/tes/v1",
        timeout=30,
        headers={"Authorization": "Bearer " + access_token("alice", "yai-tes")},
    )
    tasks = (
        tes.get("/tasks", params={"tag_key": "yai.run_id", "tag_value": run_id, "view": "FULL"})
        .json()
        .get("tasks", [])
    )
    urls = [i.get("url") for t in tasks for i in t.get("inputs", [])]
    rep.step(
        "TES task input URL is the partner access URL (http://pgdrs.yai.test:8645/files/…)",
        any(str(u).startswith("http://pgdrs.yai.test:8645/files/") for u in urls),
        input_urls=[str(u).split("?")[0] for u in urls],
        task_states=[t.get("state") for t in tasks],
    )
    return rep.save("INTEROP-DRS-1.json")


if __name__ == "__main__":
    raise SystemExit(main())
