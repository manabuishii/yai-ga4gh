"""INTEROP-WES-1: yai tests (common kind) -> partner WES. Same script on yai-wes to compare.

Usage: wes_common_check.py --url https://sapporo.yai.test --name sapporo --engine cwltool
--out INTEROP-WES-1.sapporo.json
        wes_common_check.py --url https://wes.yai.test/ga4gh/wes/v1 --name yai --auth alice
        --out INTEROP-WES-1.yai.json
Common tests ("common" at the top of test-plan.md = spec obligations only): service-info
shape, POST /runs (with cat_file.cwl and input.txt attached), state reaching a terminal state,
RunLog of GET /runs/{id}, paging of GET /runs, GET /runs/{id}/tasks, cancel, error shape.
yai-specific features (scopes, tags, page tokens, fault injection) are not tested. Sapporo
differences are noted by number in docs/wes-compatibility.md.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import httpx
from common import Report, access_token, excerpt

CASES = Path("/work/cases")
TERMINAL = {"COMPLETE", "EXECUTOR_ERROR", "SYSTEM_ERROR", "CANCELED"}
SLEEP_CWL = """\
cwlVersion: v1.2
class: CommandLineTool
baseCommand: [sh, -c]
inputs:
  script: {type: string, inputBinding: {position: 1}}
outputs: []
requirements: {DockerRequirement: {dockerPull: "alpine:3.20"}}
"""
SI_KEYS = {
    "workflow_type_versions",
    "supported_wes_versions",
    "supported_filesystem_protocols",
    "workflow_engine_versions",
    "default_workflow_engine_parameters",
    "system_state_counts",
    "auth_instructions_url",
    "tags",
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--engine", default=None)
    ap.add_argument("--auth", default=None)
    ap.add_argument("--out", required=True)
    ap.add_argument("--timeout", type=float, default=600)
    a = ap.parse_args()
    rep = Report("INTEROP-WES-1", a.name)
    headers = {"Authorization": "Bearer " + access_token(a.auth, "yai-wes")} if a.auth else {}
    c = httpx.Client(base_url=a.url.rstrip("/"), headers=headers, timeout=60)

    r = c.get("/service-info")
    si = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    rep.step(
        "GET /service-info is 200 with WES 1.1.0 ServiceInfo keys",
        r.status_code == 200 and set(si) >= SI_KEYS,
        status=r.status_code,
        missing=sorted(SI_KEYS - set(si)),
        supported_wes_versions=si.get("supported_wes_versions"),
        workflow_engine_versions=si.get("workflow_engine_versions"),
        default_workflow_engine_parameters_type=type(
            si.get("default_workflow_engine_parameters")
        ).__name__,
    )

    def submit(cwl_name: str, cwl_text: bytes, params: dict, extra_files=()) -> httpx.Response:
        data = {
            "workflow_type": "CWL",
            "workflow_type_version": "v1.2",
            "workflow_url": cwl_name,
            "workflow_params": json.dumps(params),
        }
        if a.engine:
            data["workflow_engine"] = a.engine
        files = [("workflow_attachment", (cwl_name, cwl_text))] + [
            ("workflow_attachment", f) for f in extra_files
        ]
        return c.post("/runs", data=data, files=files)

    def wait(run_id: str) -> str:
        deadline = time.monotonic() + a.timeout
        state = "UNKNOWN"
        while time.monotonic() < deadline:
            r = c.get(f"/runs/{run_id}/status")
            state = r.json().get("state") if r.status_code == 200 else f"status {r.status_code}"
            if state in TERMINAL:
                return state
            time.sleep(2)
        return state

    r = submit(
        "cat_file.cwl",
        (CASES / "cat_file.cwl").read_bytes(),
        {"infile": {"class": "File", "location": "input.txt"}},
        [("input.txt", (CASES / "input.txt").read_bytes())],
    )
    rep.step(
        "POST /runs (multipart, CWL and input attached) is 200 with run_id",
        r.status_code == 200 and "run_id" in r.json(),
        status=r.status_code,
        body=excerpt(r.text),
    )
    if r.status_code != 200:
        return rep.save(a.out)
    run_id = r.json()["run_id"]
    state = wait(run_id)
    rep.step("GET /runs/{id}/status reaches COMPLETE", state == "COMPLETE", state=state)

    r = c.get(f"/runs/{run_id}")
    log = r.json()
    rl = log.get("run_log") or {}
    rep.step(
        "GET /runs/{id} is a RunLog (run_id, request, state, run_log, outputs)",
        r.status_code == 200 and {"run_id", "request", "state", "run_log", "outputs"} <= set(log),
        keys=sorted(log),
        outputs_type=type(log.get("outputs")).__name__,
        outputs=excerpt(log.get("outputs")),
        run_log_keys=sorted(rl),
        stdout_form=("url" if str(rl.get("stdout", "")).startswith("http") else "other"),
        stdout=excerpt(rl.get("stdout")),
        system_logs=excerpt(rl.get("system_logs")),
    )
    # Output content (if the partner gives a URL, fetch and compare with input.txt; yai uses
    # file://, so this is left to the E2E checks)
    outs = log.get("outputs")
    want = (CASES / "input.txt").read_bytes()
    if isinstance(outs, list) and outs:
        url = next(
            (o.get("file_url") for o in outs if str(o.get("file_name", "")).endswith("copy.txt")),
            None,
        )
        got = c.get(url) if url else None
        rep.step(
            "Output copy.txt matches the input (fetched from the partner file_url)",
            bool(got) and got.content == want,
            file_url=url,
            status=getattr(got, "status_code", None),
        )
    elif isinstance(outs, dict):
        rep.step(
            "Output copy.txt (WES 1.1.0 outputs is a dict; content is file://, checked in E2E)",
            "out" in outs,
            location=excerpt(outs.get("out")),
        )

    r = c.get("/runs", params={"page_size": 1})
    body = r.json() if r.status_code == 200 else {}
    rep.step(
        "GET /runs?page_size=1 is a RunListResponse (1 run)",
        r.status_code == 200 and len(body.get("runs", [])) == 1,
        status=r.status_code,
        n=len(body.get("runs", [])),
        next_page_token=body.get("next_page_token"),
    )

    r = c.get(f"/runs/{run_id}/tasks")
    rep.step(
        "GET /runs/{id}/tasks (defined in WES 1.1.0)",
        r.status_code == 200,
        status=r.status_code,
        body=excerpt(r.text),
    )

    r = submit("sleep.cwl", SLEEP_CWL.encode(), {"script": "sleep 300"})
    if r.status_code == 200:
        rid2 = r.json()["run_id"]
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            st = c.get(f"/runs/{rid2}/status").json().get("state")
            if st in ("RUNNING", "INITIALIZING") or st in TERMINAL:
                break
            time.sleep(2)
        r = c.post(f"/runs/{rid2}/cancel")
        st = wait(rid2)
        rep.step(
            "POST /runs/{id}/cancel → CANCELED",
            r.status_code == 200 and st == "CANCELED",
            cancel_status=r.status_code,
            state=st,
        )
    else:
        rep.step(
            "POST /runs/{id}/cancel → CANCELED",
            False,
            submit_status=r.status_code,
            body=excerpt(r.text),
        )

    r = c.get("/runs/00000000-0000-4000-8000-000000000000")  # nonexistent id in UUID form
    err = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    rep.step(
        "GET /runs/{missing id, UUID form} is 404 with ErrorResponse {msg, status_code}",
        r.status_code == 404 and {"msg", "status_code"} <= set(err),
        status=r.status_code,
        body=excerpt(r.text),
    )
    r = c.get("/runs/does-not-exist-0000")
    err = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    rep.step(
        "GET /runs/{missing id} is 404 with ErrorResponse {msg, status_code}",
        r.status_code == 404 and {"msg", "status_code"} <= set(err),
        status=r.status_code,
        body=excerpt(r.text),
    )
    return rep.save(a.out)


if __name__ == "__main__":
    raise SystemExit(main())
