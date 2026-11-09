"""Exercise yai-wes (WES 1.1.0) over HTTP. Obligations WES-*, stage-1 WES rows of test-plan.md
section 4.

WES runs by submitting to TES (built-in engine, D-014), so AAI, DRS and TES (+ executor) are
started too.
Cross-service checks (INT-*, E2E-*) are stage 2. Here they are used only as needed for WES
obligations.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import yaml

from tests import shape
from tests.servers import Server, Worker, free_port
from tests.stack import (
    DS1,
    WES_CLIENT_SECRET,
    Creds,
    bearer,
    drs_config,
    register,
    start_aai,
    write_config,
)
from tests.test_tes import tes_config
from tests.tokens import Issuer

TERMINAL = {"COMPLETE", "EXECUTOR_ERROR", "SYSTEM_ERROR", "CANCELED"}
TIME_RE = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")

CAT_CWL = """\
cwlVersion: v1.2
class: CommandLineTool
label: cat-tool
baseCommand: cat
inputs:
  infile:
    type: File
    inputBinding: {position: 1}
outputs:
  out:
    type: File
    outputBinding: {glob: out.txt}
stdout: out.txt
requirements:
  DockerRequirement: {dockerPull: "alpine:3.20"}
"""

SH_CWL = """\
cwlVersion: v1.2
class: CommandLineTool
baseCommand: [sh, -c]
inputs:
  script:
    type: string
    inputBinding: {position: 1}
outputs: []
requirements:
  DockerRequirement: {dockerPull: "alpine:3.20"}
"""


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("wes")
    aai = start_aai(tmp)
    drs_port = free_port()
    dcfg = drs_config(aai, tmp, drs_port)
    dcfg_path = write_config(tmp / "drs.yaml", dcfg)
    pub = tmp / "pub.txt"
    pub.write_bytes(b"public data\n")
    cwl_file = tmp / "cat.cwl"
    cwl_file.write_text(CAT_CWL)
    ids = {
        "pub": register(dcfg_path, pub, "--policy", "public"),
        "cwl": register(dcfg_path, cwl_file, "--policy", "public"),
        "ds1": register(dcfg_path, pub, "--policy", "dataset", "--dataset", DS1),
    }
    drs = Server("drs", dcfg, tmp / "drs", port=drs_port).start()
    drs_netloc = f"127.0.0.1:{drs_port}"
    tcfg = tes_config(aai, tmp, drs_netloc)
    tes = Server("tes", tcfg, tmp / "tes").start()
    worker = Worker(tcfg, tmp / "worker").start()
    secret = tmp / "wes-client-secret"
    secret.write_text(WES_CLIENT_SECRET)
    iss = aai.root + "/"
    wport = free_port()
    wcfg = {
        "data_dir": str(tmp / "wes-data"),
        "public_base_url": f"http://127.0.0.1:{wport}",
        "auth": {"mode": "aai", "issuer": iss, "jwks_uri": iss + ".well-known/jwks.json",
                 "audience": "yai-wes", "insecure_http_hosts": [f"127.0.0.1:{aai.port}"]},
        "tes": {"url": tes.url, "token_url": iss + "token", "client_id": "yai-wes",
                "client_secret_file": str(secret)},
        "resolver": {"passport_hosts": ["drs.yai.test"],
                     "host_override": {"drs.yai.test": drs.root}},
        "engine": {"poll_seconds": 0.3, "nonce_search_seconds": 1, "nonce_search_interval": 0.2,
                   "cancel_retry_seconds": 0.5,
                   "output_root": str(Path(tcfg["files_root"]).resolve())},
        "fetch": {"allow": [], "insecure_http_hosts": [drs_netloc]},
    }  # fmt: skip
    wes = Server("wes", wcfg, tmp / "wes", port=wport).start()
    creds = Creds(aai)
    ns = SimpleNamespace(
        aai=aai, drs=drs, tes=tes, worker=worker, wes=wes, url=wes.url, ids=ids, tmp=tmp,
        creds=creds, alice=bearer(creds.at("alice", "yai-wes")),
        bob=bearer(creds.at("bob", "yai-wes")), carol=bearer(creds.at("carol", "yai-wes")),
        alice_tes=bearer(creds.at("alice", "yai-tes")),
    )  # fmt: skip
    yield ns
    for s in (wes, worker, tes, drs, aai):
        s.stop()


def blob(env, key: str) -> str:
    return f"{env.drs.root}/yai/blobs/{env.ids[key]}"


def form(params: dict | None = None, **fields) -> dict:
    data = {"workflow_type": "CWL", "workflow_type_version": "v1.2", **fields}
    if params is not None:
        data["workflow_params"] = json.dumps(params)
    return {k: v for k, v in data.items() if v is not None}


def submit(env, data: dict, files=None, who=None) -> httpx.Response:
    # Always attach files so the request is multipart (WES-RUN-01)
    r = httpx.post(env.url + "/runs", data=data, files=files or {"_": ("", b"")},
                   headers=who or env.alice)  # fmt: skip
    shape.check("wes", r)
    return r


def cat_run(env, who=None, **extra) -> httpx.Response:
    params = {"infile": {"class": "File", "location": blob(env, "pub")}}
    return submit(env, form(params, workflow_url=blob(env, "cwl"), **extra), who=who)


def attached(cwl_text: str, params: dict, name="tool.cwl", extra_files=(), **fields):
    files = [("workflow_attachment", (name, cwl_text.encode()))]
    files += [("workflow_attachment", f) for f in extra_files]
    return form(params, workflow_url=name, **fields), files


def wait(env, run_id, who=None, states=TERMINAL, timeout=40.0) -> dict:
    deadline = time.monotonic() + timeout
    body: dict = {}
    while time.monotonic() < deadline:
        r = httpx.get(env.url + f"/runs/{run_id}", headers=who or env.alice)
        shape.check("wes", r)
        body = r.json()
        if body["state"] in states:
            return body
        time.sleep(0.2)
    raise AssertionError(f"run {run_id} stuck in {body.get('state')}: {body}")


def get(env, path, who=None, **params) -> httpx.Response:
    r = httpx.get(env.url + path, headers=who or env.alice, params=params)
    shape.check("wes", r)
    return r


# --- service-info and authentication ---


def test_WES_SI_01(env):
    r = httpx.get(env.url + "/service-info")
    shape.check("wes", r)
    si = r.json()
    assert si["workflow_type_versions"] == {"CWL": {"workflow_type_version": ["v1.2"]}}
    assert si["supported_wes_versions"] == ["1.1.0"]
    assert set(si["supported_filesystem_protocols"]) == {"drs", "https"}  # WES-SI-PROTO
    assert set(si["system_state_counts"]) >= {"QUEUED", "COMPLETE"}
    assert "yai.cwl_subset" in si["tags"]


def test_WES_AUTH_01(env):
    assert httpx.get(env.url + "/runs").status_code == 401
    assert httpx.get(env.url + "/runs", headers=bearer("x")).status_code == 401
    assert get(env, "/runs").status_code == 200
    si = httpx.get(env.url + "/service-info").json()
    assert si["auth_instructions_url"].startswith("https://")


def test_WES_AUTH_02(env):
    assert cat_run(env, who=env.carol).status_code == 403  # valid but lacks wes.run


def test_NEG_AUTH_WRONG_AUD_and_KIND(env):
    assert get(env, "/runs", who=bearer(env.creds.at("alice", "yai-tes"))).status_code == 401
    assert get(env, "/runs", who=bearer(env.creds.passport("alice"))).status_code == 401


# --- RunWorkflow ---


def test_WES_RUN_01(env):
    r = httpx.post(env.url + "/runs", json=form({}), headers=env.alice)
    assert r.status_code == 400  # not multipart
    data = form({"infile": {"class": "File", "location": blob(env, "pub")}}, workflow_url="t.cwl")
    no_name = [("workflow_attachment", ("", CAT_CWL.encode()))]
    assert submit(env, data, files=no_name).status_code == 400


def test_WES_RUN_02(env):
    params = {"infile": {"class": "File", "location": blob(env, "pub")}}
    for name in ("../x.cwl", "/etc/x.cwl", "a/../../x.cwl"):
        data, files = attached(CAT_CWL, params, name=name)
        assert submit(env, data, files=files).status_code == 400, name


def test_NEG_ATTACH_NAME(env):
    test_WES_RUN_02(env)


def test_WES_RUN_03(env):
    assert cat_run(env).status_code == 200  # absolute URL (fetched and stored)
    params = {"infile": {"class": "File", "location": blob(env, "pub")}}
    data, files = attached(CAT_CWL, params, name="sub/tool.cwl")
    assert submit(env, data, files=files).status_code == 200  # URL relative to an attachment
    assert submit(env, form(params, workflow_url="missing.cwl")).status_code == 400
    bad_host = form(params, workflow_url="https://evil.example/t.cwl")
    assert submit(env, bad_host).status_code == 400  # NEG-SSRF(WES)


def test_WES_RUN_04(env):
    run_id = cat_run(env).json()["run_id"]
    log = wait(env, run_id)
    assert log["state"] == "COMPLETE", log
    out = log["outputs"]["out"]
    assert Path(out["location"].removeprefix("file://")).read_bytes() == b"public data\n"
    # Inputs given as URLs relative to attachments are fetched too
    data, files = attached(
        CAT_CWL, {"infile": {"class": "File", "location": "in.txt"}},
        extra_files=[("in.txt", b"attached\n")],
    )  # fmt: skip
    log = wait(env, submit(env, data, files=files).json()["run_id"])
    assert log["state"] == "COMPLETE"
    loc = log["outputs"]["out"]["location"]
    assert Path(loc.removeprefix("file://")).read_bytes() == b"attached\n"


def test_WES_RUN_05(env):
    params = {"infile": {"class": "File", "location": blob(env, "pub")}}
    for over in ({"workflow_type": "WDL"}, {"workflow_type_version": "v1.0"},
                 {"workflow_engine": "cwltool"},
                 {"workflow_engine": "yai-mini", "workflow_engine_version": "9"}):  # fmt: skip
        assert submit(env, form(params, workflow_url=blob(env, "cwl"), **over)).status_code == 400
    ok = form(params, workflow_url=blob(env, "cwl"), workflow_engine="yai-mini",
              workflow_engine_version="0.1.0")  # fmt: skip
    assert submit(env, ok).status_code == 200


def test_WES_RUN_06(env):
    params = {"infile": {"class": "File", "location": blob(env, "pub")}}
    data = form(params, workflow_url=blob(env, "cwl"), workflow_engine_version="0.1.0")
    assert submit(env, data).status_code == 400


def test_WES_RUN_07(env):
    assert submit(env, form(None, workflow_url=blob(env, "cwl"))).status_code == 400
    assert submit(env, form(None, workflow_url=blob(env, "cwl"),
                            workflow_params="[]")).status_code == 400  # fmt: skip


def test_cwl_subset_is_enforced(env):
    params = {"script": "true"}
    for bad in (SH_CWL.replace("CommandLineTool", "Workflow"),
                SH_CWL.replace("v1.2", "v1.0"),
                SH_CWL + "hints: []\n$namespaces: {x: y}\n",
                SH_CWL.replace("requirements:", "requirements:\n  ShellCommandRequirement: {}\n"
                               "  _x:"),
                SH_CWL.replace("type: string", "type: Directory")):  # fmt: skip
        data, files = attached(bad, params)
        assert submit(env, data, files=files).status_code == 400


def test_cwl_wrong_types_and_unknown_fields_are_400(env):
    """Wrong types and unsupported keys give 400, not 500 or silent ignore."""
    params = {"script": "true"}
    docker = 'DockerRequirement: {dockerPull: "alpine:3.20"}'
    for bad in (SH_CWL.replace("type: string", 'type: [string, "null"]'),
                SH_CWL.replace("requirements:\n  " + docker, "requirements: null"),
                SH_CWL.replace(docker, "DockerRequirement: x"),
                SH_CWL.replace("inputBinding: {position: 1}",
                               "inputBinding: {position: 1}\n    valueFrom: x"),
                SH_CWL.replace('"alpine:3.20"}', '"alpine:3.20", dockerFile: x}'),
                SH_CWL.replace("baseCommand", "label: {a: 1}\nbaseCommand")):  # fmt: skip
        data, files = attached(bad, params)
        r = submit(env, data, files=files)
        assert r.status_code == 400, (bad, r.text)


def runs_on_disk(env) -> set[str]:
    root = env.tmp / "wes-data" / "runs"
    return {p.name for p in root.iterdir()} if root.exists() else set()


def test_attachment_limits_and_no_residue(env):
    """Limits on one attachment, attachment count and document size. A 400 leaves no run
    directory."""
    before = runs_on_disk(env)
    params = {"script": "true"}
    big = ("big.bin", b"x" * (1024 * 1024 + 1))
    data, files = attached(SH_CWL, params, extra_files=[big])
    assert submit(env, data, files=files).status_code == 400  # even for an unused attachment
    many = [(f"f{i}.txt", b"x") for i in range(40)]
    data, files = attached(SH_CWL, params, extra_files=many)
    assert submit(env, data, files=files).status_code == 400
    huge_doc = SH_CWL + "#" + "x" * (1024 * 1024) + "\n"
    data, files = attached(huge_doc, params)
    assert submit(env, data, files=files).status_code == 400  # attached documents: 1 MiB max too
    data, files = attached(SH_CWL.replace("type: string", "type: Directory"), params,
                           extra_files=[("ok.txt", b"x")])  # fmt: skip
    assert submit(env, data, files=files).status_code == 400  # outside the subset
    assert runs_on_disk(env) == before


def test_default_attachment_and_binary_attachment(env):
    """CWL default File attachments are resolved at submission. Non-UTF-8 attachments and
    unresolvable locations give 400 at submission, matching execution."""
    tool = CAT_CWL.replace("    inputBinding: {position: 1}",
                           "    inputBinding: {position: 1}\n    default: {class: File, "
                           "location: in.txt}")  # fmt: skip
    data, files = attached(tool, {}, extra_files=[("in.txt", b"from default\n")])
    run_id = submit(env, data, files=files).json()["run_id"]
    log = wait(env, run_id)
    assert log["state"] == "COMPLETE", log
    loc = log["outputs"]["out"]["location"]
    assert Path(loc.removeprefix("file://")).read_bytes() == b"from default\n"
    # Attachments reach TES as inputs[].content and do not remain in FULL views of finished tasks
    # (D-043)
    tes_uri = get(env, f"/runs/{run_id}/tasks").json()["task_logs"][0]["tes_uri"]
    tes_task = httpx.get(tes_uri, headers=env.alice_tes, params={"view": "FULL"}).json()
    assert tes_task["inputs"] and all("content" not in i for i in tes_task["inputs"])
    data, files = attached(CAT_CWL, {"infile": {"class": "File", "location": "b.bin"}},
                           extra_files=[("b.bin", b"\xff\xfe\x00")])  # fmt: skip
    assert submit(env, data, files=files).status_code == 400
    data, files = attached(CAT_CWL, {"infile": {"class": "File", "location": "ftp://h/x"}})
    assert submit(env, data, files=files).status_code == 400
    data, files = attached(CAT_CWL, {"infile": {"class": "File", "location": "https://[::1/x"}})
    assert submit(env, data, files=files).status_code == 400


def test_run_workflow_does_not_block_other_requests(env):
    """Other requests are answered while RunWorkflow waits for a DB lock."""
    import sqlite3
    import threading

    run_id = cat_run(env).json()["run_id"]
    db = sqlite3.connect(env.tmp / "wes-data" / "wes.sqlite", timeout=5, isolation_level=None)
    db.execute("BEGIN EXCLUSIVE")  # WAL: reads work, writes wait
    result: dict = {}
    t = threading.Thread(target=lambda: result.setdefault("r", cat_run(env)), daemon=True)
    try:
        t.start()
        time.sleep(0.5)
        started = time.monotonic()
        r = httpx.get(env.url + f"/runs/{run_id}/status", headers=env.alice, timeout=10)
        assert r.status_code == 200
        assert time.monotonic() - started < 1.0
    finally:
        time.sleep(1.0)
        db.execute("COMMIT")
        db.close()
    t.join(30)
    assert result["r"].status_code == 200


def test_relative_output_root_is_made_absolute(tmp_path, monkeypatch):
    """file:// URLs are correct even with a relative output_root as in the sample config."""
    from yai.tes.model import file_url_path
    from yai.wes.app import create_app

    monkeypatch.chdir(tmp_path)
    example = yaml.safe_load((Path(__file__).parent.parent / "config" / "wes.example.yaml")
                             .read_text())  # fmt: skip
    cfg = {"data_dir": str(tmp_path / "wes"), "auth": {"mode": "anonymous"},
           "engine": {"output_root": example["engine"]["output_root"]}}  # fmt: skip
    app = create_app(cfg)
    root = app.state.engine.cfg.output_root
    assert Path(root).is_absolute()
    url = f"file://{root}/wes/r1/out/out.txt"
    assert file_url_path(url, str((tmp_path / "var/tes/files").resolve())) == "wes/r1/out/out.txt"
    app.state.lock.close()


def test_run_limit_is_429(tmp_path):
    """RunWorkflow returns 429 at the per-user limit of unfinished runs (#32, D-044).
    TES is unreachable in this setup, so accepted runs stay unfinished."""
    from fastapi.testclient import TestClient

    from yai.wes.app import create_app

    cfg = {"data_dir": str(tmp_path / "wes"), "auth": {"mode": "anonymous"},
           "max_active_runs_per_user": 2, "tes": {"url": "http://127.0.0.1:9/ga4gh/tes/v1"},
           "engine": {"output_root": str(tmp_path / "files")}}  # fmt: skip
    app = create_app(cfg)
    try:
        client = TestClient(app)
        data, files = attached(CAT_CWL, {"infile": {"class": "File", "location": "attachment:in"}},
                               extra_files=[("in", b"hi")])  # fmt: skip
        codes = [client.post("/ga4gh/wes/v1/runs", data=data, files=files).status_code
                 for _ in range(3)]  # fmt: skip
        assert codes == [200, 200, 429], codes
        r = client.post("/ga4gh/wes/v1/runs", data=data, files=files)
        assert r.json()["msg"] == "too many unfinished runs (at most 2)"
    finally:
        stopped = app.state.engine.stop()  # do not leave watchers for accepted runs (D-062)
        app.state.lock.close()

    assert stopped


def wes_app(tmp_path, keys, **extra):
    from yai.wes.app import create_app

    cfg = {"data_dir": str(tmp_path / "wes"), "auth": {"mode": "anonymous"},
           "tes": {"url": "http://127.0.0.1:9/ga4gh/tes/v1"},
           "fetch": {"insecure_http_hosts": [keys.netloc]},
           "engine": {"output_root": str(tmp_path / "files")}, **extra}  # fmt: skip
    return create_app(cfg)


def test_NEG_WES_LIMIT_BEFORE_FETCH(tmp_path):
    """D-057 (1): a user at the limit gets 429 from RunWorkflow without fetching workflow_url."""
    from fastapi.testclient import TestClient

    from tests.keyserver import KeyServer

    keys = KeyServer()
    keys.routes["/wf/tool.cwl"] = ("jwks", yaml.safe_load(CAT_CWL))
    app = wes_app(tmp_path, keys, max_active_runs_per_user=1)
    try:
        client = TestClient(app)
        data, files = attached(CAT_CWL, {"infile": {"class": "File", "location": "attachment:in"}},
                               extra_files=[("in", b"hi")])  # fmt: skip
        assert client.post("/ga4gh/wes/v1/runs", data=data, files=files).status_code == 200
        data = form({"infile": {"class": "File", "location": "attachment:in"}},
                    workflow_url=keys.url("/wf/tool.cwl"))  # fmt: skip
        r = client.post(
            "/ga4gh/wes/v1/runs", data=data, files=[("workflow_attachment", ("in", b"hi"))]
        )
        assert r.status_code == 429 and keys.hits["/wf/tool.cwl"] == 0, (r.text, keys.hits)
    finally:
        stopped = app.state.engine.stop()  # do not leave watchers for accepted runs (D-062)
        app.state.lock.close()
        keys.close()
    assert stopped


def test_NEG_WORKFLOW_NAME(tmp_path):
    """D-057 (2): a workflow_url whose last path segment is `..` gives 400 (previously 500 because
    the stored name was `..`). It is not fetched."""
    from fastapi.testclient import TestClient

    from tests.keyserver import KeyServer

    keys = KeyServer()
    for path in ("/x/..", "/"):  # the document is returned whether or not httpx normalizes the path
        keys.routes[path] = ("jwks", yaml.safe_load(CAT_CWL))
    app = wes_app(tmp_path, keys)
    try:
        client = TestClient(app, raise_server_exceptions=False)
        data = form({"infile": {"class": "File", "location": "attachment:in"}},
                    workflow_url=keys.url("/x/.."))  # fmt: skip
        r = client.post(
            "/ga4gh/wes/v1/runs", data=data, files=[("workflow_attachment", ("in", b"hi"))]
        )
        assert r.status_code == 400 and r.json()["msg"] == "workflow_url: bad file name", r.text
        assert sum(keys.hits.values()) == 0
    finally:
        app.state.lock.close()
        keys.close()


def test_NEG_JSON_DEPTH(tmp_path):
    """D-060 (2): workflow_params, tags and workflow_engine_parameters nested
    deeper than 64 levels give 400 at submission (previously accepted, then scrub_request's
    recursion at the final transition raised RecursionError and the run never finished). Only
    array and object levels count."""
    from fastapi.testclient import TestClient

    from yai.wes.app import create_app

    cfg = {"data_dir": str(tmp_path / "wes"), "auth": {"mode": "anonymous"},
           "tes": {"url": "http://127.0.0.1:9/ga4gh/tes/v1"},
           "engine": {"output_root": str(tmp_path / "files")}}  # fmt: skip
    app = create_app(cfg)

    def nested(depth: int) -> object:
        value: object = "x"
        for _ in range(depth - 1):
            value = [value]
        return value

    infile = {"class": "File", "location": "attachment:in"}
    try:
        client = TestClient(app)
        for depth, code in ((70, 400), (2000, 400), (60, 200)):
            data, files = attached(CAT_CWL, {"infile": {**infile, "extra": nested(depth)}},
                                   extra_files=[("in", b"hi")])  # fmt: skip
            r = client.post("/ga4gh/wes/v1/runs", data=data, files=files)
            assert r.status_code == code, (depth, r.text[:200])
        for field in ("tags", "workflow_engine_parameters"):
            data, files = attached(CAT_CWL, {"infile": infile}, extra_files=[("in", b"hi")])
            data[field] = json.dumps({"k": nested(65)})
            r = client.post("/ga4gh/wes/v1/runs", data=data, files=files)
            assert r.status_code == 400 and "nested too deeply" in r.json()["msg"], (field, r.text)
    finally:
        stopped = app.state.engine.stop()  # do not leave watchers for accepted runs (D-062)
        app.state.lock.close()

    assert stopped


def test_tes_client_repr_hides_secret():
    """Review N-2: TesClient repr/str do not reveal the WES client secret (nor fetched tokens)."""
    from yai.wes.tes_client import TesClient

    client = TesClient("https://tes.yai.test/ga4gh/tes/v1", "https://aai.yai.test/token",
                       "yai-wes", "s3cret-value-for-test")  # fmt: skip
    client._token = "tok-value-for-test"
    for text in (repr(client), str(client)):
        assert "yai-wes" in text
        assert "s3cret-value-for-test" not in text and "tok-value-for-test" not in text


# --- State and logs ---


def test_WES_ST_01(env):
    states = {"UNKNOWN", "QUEUED", "INITIALIZING", "RUNNING", "PAUSED", "COMPLETE",
              "EXECUTOR_ERROR", "SYSTEM_ERROR", "CANCELED", "CANCELING", "PREEMPTED"}  # fmt: skip
    for r in get(env, "/runs").json()["runs"]:
        assert r["state"] in states


def test_missing_cwl_output_is_executor_error(env):
    """wes.md section 1, D-026: the executor exits 0 but the CWL output is missing -> run is
    EXECUTOR_ERROR. The TES task stays SYSTEM_ERROR (output transfer failed, TES-ST-02)."""
    tool = SH_CWL.replace("outputs: []", "outputs:\n  o:\n    type: File\n"
                          "    outputBinding: {glob: never.txt}")  # fmt: skip
    data, files = attached(tool, {"script": "true"})
    log = wait(env, submit(env, data, files=files).json()["run_id"])
    assert log["state"] == "EXECUTOR_ERROR"
    assert any("never.txt" in s for s in log["run_log"]["system_logs"])
    task = get(env, f"/runs/{log['run_id']}/tasks").json()["task_logs"][0]
    assert task["exit_code"] == 0


def test_missing_outputs_only_after_all_executors_exit_0():
    """Startup or input-fetch failures (no exit code), non-zero exits and unmarked transfer
    failures stay SYSTEM_ERROR."""
    from yai.wes.engine import Engine

    a, b = "/yai/work/a", "/yai/work/b"

    def missing(codes, mark=None):
        meta = {"yai.missing_outputs": mark} if mark is not None else {}
        execs = [{"exit_code": c} for c in codes]
        task = {"executors": [{}] * 2, "outputs": [{"path": a}, {"path": b}],
                "logs": [{"logs": execs, "metadata": meta}]}  # fmt: skip
        return Engine.missing_outputs(task)

    assert missing([0, 0], b) == [b]
    assert missing([0, 0], f"{a},{b}") == [a, b]
    assert missing([0, 0]) == []  # unwritable location, link, other TES
    assert missing([0, 0], "/elsewhere") == []  # undeclared path
    assert missing([0], b) == []  # the second executor never started
    assert missing([0, None], b) == []
    assert missing([0, 3], b) == []
    assert missing([], b) == []


def test_executor_error_maps(env):
    data, files = attached(SH_CWL, {"script": "echo boom >&2; exit 4"})
    log = wait(env, submit(env, data, files=files).json()["run_id"])
    assert log["state"] == "EXECUTOR_ERROR"
    tasks = get(env, f"/runs/{log['run_id']}/tasks").json()["task_logs"]
    assert tasks[0]["exit_code"] == 4


def test_WES_LOG_01(env):
    data, files = attached(SH_CWL, {"script": "echo to-stdout; echo to-stderr >&2"})
    run_id = submit(env, data, files=files).json()["run_id"]
    assert wait(env, run_id)["state"] == "COMPLETE"
    task = get(env, f"/runs/{run_id}/tasks").json()["task_logs"][0]
    out = httpx.get(task["stdout"], headers=env.alice)
    assert out.status_code == 200 and out.text == "to-stdout\n"
    assert httpx.get(task["stderr"], headers=env.alice).text == "to-stderr\n"
    assert httpx.get(task["stdout"], headers=env.bob).status_code == 404
    assert httpx.get(task["stdout"]).status_code == 401


def test_WES_LOG_02(env):
    run_id = cat_run(env).json()["run_id"]
    log = wait(env, run_id)
    for value in (log["run_log"]["start_time"], log["run_log"]["end_time"]):
        assert TIME_RE.match(value), value
    task = get(env, f"/runs/{run_id}/tasks").json()["task_logs"][0]
    assert TIME_RE.match(task["start_time"]) and TIME_RE.match(task["end_time"])
    for r in get(env, "/runs").json()["runs"]:
        assert all(TIME_RE.match(r[k]) for k in ("start_time", "end_time") if k in r)


def test_WES_LOG_03(env):
    run_id = cat_run(env).json()["run_id"]
    log = wait(env, run_id)
    assert "task_logs" not in log or log["task_logs"] is None
    assert log["task_logs_url"].startswith("http://")
    r = httpx.get(log["task_logs_url"], headers=env.alice)
    assert r.status_code == 200 and r.json()["task_logs"][0]["id"] == f"{run_id}.1"


def test_WES_TSK_01(env):
    run_id = cat_run(env).json()["run_id"]
    wait(env, run_id)
    listed = get(env, f"/runs/{run_id}/tasks").json()["task_logs"]
    one = get(env, f"/runs/{run_id}/tasks/{run_id}.1").json()
    assert [t["id"] for t in listed] == [one["id"]]
    assert get(env, f"/runs/{run_id}/tasks/{run_id}.9").status_code == 404


def test_WES_TSK_02(env):
    run_id = cat_run(env).json()["run_id"]
    wait(env, run_id)
    task = get(env, f"/runs/{run_id}/tasks").json()["task_logs"][0]
    assert task["id"] == f"{run_id}.1" and task["name"] == "cat-tool"
    assert task["cmd"][0] == "cat" and task["system_logs"][0] == "state=COMPLETE"
    # tes_uri can be followed with the user's own TES AT (C-12)
    r = httpx.get(task["tes_uri"], headers=env.alice_tes, params={"view": "BASIC"})
    assert r.status_code == 200 and r.json()["tags"]["yai.run_id"] == run_id


def test_WES_ERR_01(env):
    for r in (get(env, "/runs/rnope"), get(env, "/runs/rnope/status"),
              httpx.post(env.url + "/runs/rnope/cancel", headers=env.alice),
              get(env, "/runs", page_size=0)):  # fmt: skip
        assert r.status_code in (400, 404)
        assert set(r.json()) == {"msg", "status_code"} and r.json()["status_code"] == r.status_code


# --- Listing ---


def test_WES_LST_01(env):
    made = []
    data, files = attached(SH_CWL, {"script": "true"})
    for _ in range(5):
        made.append(submit(env, data, files=files).json()["run_id"])
    seen, token = [], None
    late = None
    while True:
        params = {"page_size": 2, **({"page_token": token} if token else {})}
        body = get(env, "/runs", **params).json()
        assert len(body["runs"]) <= 2  # WES-LST-02
        if late is None:
            late = submit(env, data, files=files).json()["run_id"]  # created midway
        seen += [r["run_id"] for r in body["runs"]]
        token = body["next_page_token"]
        if token == "":  # WES-LST-04
            break
    assert set(made) <= set(seen) and len(seen) == len(set(seen))
    assert late not in seen  # the listing as of the first page request (WES-LST-01)


def test_WES_LST_02(env):
    assert len(get(env, "/runs", page_size=1).json()["runs"]) <= 1
    assert get(env, "/runs", page_size=1001).status_code == 400


def test_WES_LST_03(env):
    bob_run = cat_run(env, who=env.bob).json()["run_id"]
    seen, token = [], ""
    while True:
        body = get(env, "/runs", page_size=50, **({"page_token": token} if token else {})).json()
        seen += [r["run_id"] for r in body["runs"]]
        token = body["next_page_token"]
        if not token:
            break
    assert bob_run not in seen
    assert get(env, f"/runs/{bob_run}").status_code == 404  # NEG-OTHER-USER(WES)
    wait(env, bob_run, who=env.bob)


def test_WES_LST_04(env):
    assert get(env, "/runs", page_size=1000).json()["next_page_token"] == ""


def test_WES_LST_05(env):
    run_id = cat_run(env, tags=json.dumps({"k": "v"})).json()["run_id"]
    wait(env, run_id)
    runs = get(env, "/runs", page_size=1000).json()["runs"]
    mine = next(r for r in runs if r["run_id"] == run_id)
    assert mine["tags"] == {"k": "v"} and "end_time" in mine


def test_NEG_PAGING_TAMPER_wes(env):
    data, files = attached(SH_CWL, {"script": "true"})
    for _ in range(2):
        submit(env, data, files=files)
    token = get(env, "/runs", page_size=1).json()["next_page_token"]
    assert get(env, "/runs", page_size=1, page_token=token + "x").status_code == 400
    assert get(env, "/runs", who=env.bob, page_size=1, page_token=token).status_code == 400


# --- Cancellation ---


def test_cancel_run(env):
    data, files = attached(SH_CWL, {"script": "sleep 30"})
    run_id = submit(env, data, files=files).json()["run_id"]
    wait(env, run_id, states={"RUNNING"})
    time.sleep(0.5)
    r = httpx.post(env.url + f"/runs/{run_id}/cancel", headers=env.alice)
    shape.check("wes", r)
    assert r.json() == {"run_id": run_id}
    log = wait(env, run_id)
    assert log["state"] == "CANCELED"
    task = get(env, f"/runs/{run_id}/tasks").json()["task_logs"][0]
    tes_task = httpx.get(task["tes_uri"], headers=env.alice_tes).json()
    assert tes_task["state"] == "CANCELED"
    again = httpx.post(env.url + f"/runs/{run_id}/cancel", headers=env.alice)
    assert again.status_code == 200 and wait(env, run_id)["state"] == "CANCELED"  # D-020
    assert httpx.post(env.url + f"/runs/{run_id}/cancel", headers=env.bob).status_code == 404
    assert run_id not in runs_on_disk(env)  # ended by cancel: no documents/attachments kept (D-030)


def test_finished_run_keeps_no_signed_url_query(env):
    """D-030 option A: no signed-URL query string remains in RunLog, DB or logs of a finished run.
    Public objects ignore the query, so a marker does not stop the fetch (execution as before)."""
    canary = f"CANARY-{os.urandom(4).hex()}"
    params = {"infile": {"class": "File", "location": blob(env, "pub") + f"?sig={canary}a"}}
    data = form(params, workflow_url=blob(env, "cwl") + f"?sig={canary}w")
    run_id = submit(env, data).json()["run_id"]
    log = wait(env, run_id)
    assert log["state"] == "COMPLETE", log
    assert canary not in json.dumps(log)
    assert log["request"]["workflow_url"] == blob(env, "cwl")
    assert log["request"]["workflow_params"]["infile"]["location"] == blob(env, "pub")
    out = log["outputs"]["out"]["location"].removeprefix("file://")
    assert Path(out).read_bytes() == b"public data\n"  # fetched via a URL with a query string
    with sqlite3.connect(env.tmp / "wes-data" / "wes.sqlite") as db:
        rows = db.execute("SELECT request_json, system_logs_json FROM runs WHERE id=?",
                          (run_id,)).fetchall()  # fmt: skip
    assert rows and canary not in json.dumps(rows)
    assert canary not in env.wes.log()


def test_finished_run_keeps_no_documents(env):
    """D-030 (extended to runs/<id>/ on 2026-10-17): a CWL whose File default is a signed URL,
    and its attachments, are used for execution, then runs/<id>/ is removed when the run ends."""
    canary = f"CANARY-{os.urandom(4).hex()}"
    tool = CAT_CWL.replace("    inputBinding: {position: 1}",
                           "    inputBinding: {position: 1}\n    default: {class: File, "
                           f"location: '{blob(env, 'pub')}?sig={canary}d'}}")  # fmt: skip
    data, files = attached(tool, {}, extra_files=[("note.txt", f"{canary}n".encode())])
    run_id = submit(env, data, files=files).json()["run_id"]
    log = wait(env, run_id)
    assert log["state"] == "COMPLETE", log
    out = log["outputs"]["out"]["location"].removeprefix("file://")
    assert Path(out).read_bytes() == b"public data\n"  # fetched via the URL in the document default
    assert run_id not in runs_on_disk(env)
    assert canary not in json.dumps(log)


# --- Passport(yai_passports) ---


def test_NEG_PASSPORT_BAD_SHAPE(env):
    before = {r["run_id"] for r in get(env, "/runs", page_size=1000).json()["runs"]}
    stranger = Issuer("https://x.test/")
    bad = [
        "not-a-jwt",
        json.dumps("x"),
        stranger.sign({"sub": "a", "exp": int(time.time()) + 600}, typ="JWT"),  # wrong typ
        stranger.sign(
            {"sub": "a", "exp": int(time.time()) + 600}, typ="vnd.ga4gh.passport+jwt"
        ),  # no ga4gh_passport_v1
        stranger.passport([], ttl=-120),  # expired (beyond the 60 s skew)
    ]
    for value in bad:
        raw = value if value.startswith("[") or value.startswith('"') else json.dumps([value])
        r = cat_run(env, yai_passports=raw)
        assert r.status_code == 400, value[:40]
    after = {r["run_id"] for r in get(env, "/runs", page_size=1000).json()["runs"]}
    assert after == before  # no run is created


def test_NEG_PASSPORT_BIG(env):
    issuer = Issuer("https://big.test/")
    visas = [issuer.visa("https://big.test/jwks.json", f"https://yai.test/d{i}") for i in range(20)]
    big = issuer.passport(visas)
    assert len(big) > 10_000
    assert cat_run(env, yai_passports=json.dumps([big])).status_code == 200


def test_NEG_PASSPORT_NOT_LOGGED(env):
    passport = env.creds.passport("alice")
    params = {"infile": {"class": "File", "location": f"drs://drs.yai.test/{env.ids['ds1']}"}}
    data = form(params, workflow_url=blob(env, "cwl"), yai_passports=json.dumps([passport]))
    run_id = submit(env, data).json()["run_id"]
    log = wait(env, run_id)
    assert log["state"] == "COMPLETE", log  # the dataset input was resolved with the Passport
    sig = passport.split(".")[2]
    assert sig not in json.dumps(log)  # not in RunLog.request
    assert sig not in env.wes.log()
    db_files = list((env.tmp / "wes-data").glob("wes.sqlite*"))
    assert db_files and all(sig.encode() not in f.read_bytes() for f in db_files)
    # Not in the TES task either (ahead of NEG-PASSPORT-NOT-IN-TES)
    task = get(env, f"/runs/{run_id}/tasks").json()["task_logs"][0]
    tes_task = httpx.get(task["tes_uri"], headers=env.alice_tes, params={"view": "FULL"}).text
    assert sig not in tes_task


def test_passport_needed_but_missing(env):
    params = {"infile": {"class": "File", "location": f"drs://drs.yai.test/{env.ids['ds1']}"}}
    log = wait(env, submit(env, form(params, workflow_url=blob(env, "cwl"))).json()["run_id"])
    assert log["state"] == "SYSTEM_ERROR"
    assert any("passport" in s for s in log["run_log"]["system_logs"])


# --- CWL-PARITY (run the same example with cwltool and compare output contents) ---

CASES = Path(__file__).resolve().parent / "cases" / "cwl"


def run_cwltool(cwltool: str, case: str, outdir: Path) -> dict:
    """With --no-container, cwltool rejects DockerRequirement in requirements, so only the copy
    passed to it moves it to hints (behavior is unchanged)."""
    doc = yaml.safe_load((CASES / f"{case}.cwl").read_text())
    doc["hints"] = doc.pop("requirements")
    copy = CASES / f".{case}.cwltool.cwl"
    copy.write_text(yaml.safe_dump(doc))
    try:
        out = subprocess.run(
            [cwltool, "--no-container", "--quiet", "--outdir", str(outdir), str(copy),
             str(CASES / f"{case}.job.json")],
            check=True, capture_output=True, text=True, cwd=CASES,
        )  # fmt: skip
    finally:
        copy.unlink()
    return json.loads(out.stdout)


@pytest.mark.parametrize("case", ["args_order", "cat_file"])
def test_CWL_PARITY(env, tmp_path, case):
    cwltool = os.environ.get("YAI_CWLTOOL", "")
    if not cwltool:
        pytest.skip("YAI_CWLTOOL is not set")
    theirs = run_cwltool(cwltool, case, tmp_path)
    params = json.loads((CASES / f"{case}.job.json").read_text())
    extra = []
    for v in params.values():
        if isinstance(v, dict) and v.get("class") == "File":
            extra.append((v["location"], (CASES / v["location"]).read_bytes()))
    data, files = attached((CASES / f"{case}.cwl").read_text(), params, extra_files=extra)
    log = wait(env, submit(env, data, files=files).json()["run_id"])
    assert log["state"] == "COMPLETE", log
    for name, out in theirs.items():
        ours = Path(log["outputs"][name]["location"].removeprefix("file://")).read_bytes()
        assert ours == Path(out["path"]).read_bytes(), name


# --- Serial Workflow (wes.md section 1, stage 2 groundwork; E2E-02 with local processes) ---


def test_serial_workflow_two_steps(env):
    """Step 1 output feeds step 2 input (file:// handoff, C-25). ListTasks shows both steps."""
    from tests.test_wes_workflow import TWO_STEPS

    data, files = attached(TWO_STEPS, {"word": "hello"}, name="wf.cwl")
    log = wait(env, submit(env, data, files=files).json()["run_id"])
    assert log["state"] == "COMPLETE", log["run_log"]
    loc = log["outputs"]["final"]["location"]
    assert loc.startswith("file://") and f"/wes/{log['run_id']}/2/" in loc
    assert Path(loc.removeprefix("file://")).read_text() == "hello\n"
    tasks = get(env, f"/runs/{log['run_id']}/tasks").json()["task_logs"]
    assert [t["name"] for t in tasks] == ["first", "second"]
    assert [t["id"] for t in tasks] == [f"{log['run_id']}.1", f"{log['run_id']}.2"]
    assert all(t["exit_code"] == 0 for t in tasks)


def test_serial_workflow_outside_subset_is_400(env):
    from tests.test_wes_workflow import TWO_STEPS

    bad = TWO_STEPS.replace("in: {f: first/out}", "in: {f: third/out}")
    data, files = attached(bad, {"word": "x"}, name="wf.cwl")
    r = submit(env, data, files=files)
    assert r.status_code == 400
    assert "later or unknown" in r.json()["msg"]
