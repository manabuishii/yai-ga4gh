"""Tests of the four services working together (E2E-01..11 in test-plan.md section 5) and the
25 stage-2 rows of system.md section 9 (INT-... etc.; E2E-10 groups them). Stage-2 groundwork
(D-036, provisional).

There are two environments, and the same tests run against both (the stack fixture parameter):
- local: the four services run as local processes (TES uses the local-process runner, D-024).
  A recording/rewriting relay (tests/proxy.py) sits between WES and TES/DRS, plus a fake DRS
  (tests/fakedrs.py)
- compose: the compose setup in deploy/ (https://*.yai.test, local CA, port 443, D-017). Only when
  YAI_E2E_TARGET=compose (inside the test container, tools/compose_e2e.sh). A local pytest run
  skips compose cases with the PENDING reason, and tools/test_status.py merges the junit from the
  compose run into the counts (D-038)
Tests that need observation (relay records, files, process restarts) are local only; compose
skips them as "local only" (not pending, D-036). Checks that need real Docker carry the docker
marker (YAI_E2E_DOCKER=1, set in the compose test container).
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import yaml

from tests import shape
from tests.fakedrs import FakeDrs
from tests.proxy import RecordingProxy, Seen
from tests.servers import Server, Worker, free_port
from tests.stack import (
    DS1,
    EXCHANGE,
    TEST_CLIENT_SECRET,
    WES_CLIENT_SECRET,
    drs_config,
    register,
    start_aai,
    write_config,
)
from tests.test_tes import tes_config

pytestmark = pytest.mark.e2e
PENDING = "pending (waiting for Docker)"
TERMINAL = {"COMPLETE", "EXECUTOR_ERROR", "SYSTEM_ERROR", "CANCELED"}
COMPOSE = os.environ.get("YAI_E2E_TARGET") == "compose"

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

SH = """\
cwlVersion: v1.2
class: CommandLineTool
baseCommand: [sh, -c]
inputs:
  script: {type: string, inputBinding: {position: 1}}
outputs: []
requirements: {DockerRequirement: {dockerPull: "alpine:3.20"}}
"""


def two_steps(second_input: str) -> str:
    """Two steps in series. Step 1 is sh -c, step 2 is cat (input is second_input)."""
    return f"""\
cwlVersion: v1.2
class: Workflow
inputs:
  script: string
  data: File
outputs:
  final: {{type: File, outputSource: second/out}}
steps:
  first:
    run:
      class: CommandLineTool
      baseCommand: [sh, -c]
      inputs:
        s: {{type: string, inputBinding: {{position: 1}}}}
        d: {{type: File, inputBinding: {{position: 2}}}}
      outputs:
        out: {{type: File, outputBinding: {{glob: a.txt}}}}
      requirements: {{DockerRequirement: {{dockerPull: "alpine:3.20"}}}}
    in: {{s: script, d: data}}
    out: [out]
  second:
    run:
      class: CommandLineTool
      baseCommand: cat
      inputs:
        f: {{type: File, inputBinding: {{position: 1}}}}
      outputs:
        out: {{type: File, outputBinding: {{glob: b.txt}}}}
      stdout: b.txt
      requirements: {{DockerRequirement: {{dockerPull: "alpine:3.20"}}}}
    in: {{f: {second_input}}}
    out: [out]
"""


# --- credentials (local: the test AAI endpoint; compose: the same endpoint over https) ---


class Creds:
    def __init__(self, aai_url: str, test_secret: str, wes_secret: str):
        self.aai, self.test_secret, self.wes_secret = aai_url.rstrip("/"), test_secret, wes_secret

    def login(self, user: str, aud: list[str]) -> dict[str, Any]:
        r = httpx.post(self.aai + "/test/login", json={"username": user, "aud": aud})
        r.raise_for_status()
        return r.json()

    def psat_for(self, user: str, aud: str) -> dict[str, str]:
        """A passport-scoped AT for `aud`: the user lets that service fetch their Passport."""
        r = httpx.post(self.aai + "/test/login",
                       json={"username": user, "aud": [aud], "passport_aud": [aud]})  # fmt: skip
        r.raise_for_status()
        return {"Authorization": f"Bearer {r.json()['access_tokens'][aud]}"}

    def at(self, user: str, aud: str) -> dict[str, str]:
        return {"Authorization": "Bearer " + self.login(user, [aud])["access_tokens"][aud]}

    def passport(self, user: str, audience: str | None = None) -> str:
        psat = self.login(user, [])["passport_scoped_access_token"]
        data = {"grant_type": EXCHANGE,
                "requested_token_type": "urn:ga4gh:params:oauth:token-type:passport",
                "subject_token": psat,
                "subject_token_type": "urn:ietf:params:oauth:token-type:access_token"}  # fmt: skip
        if audience is not None:
            data["audience"] = audience
        r = httpx.post(self.aai + "/token", data=data, auth=("yai-test-client", self.test_secret))
        r.raise_for_status()
        return r.json()["access_token"]

    def delegated(self, user: str, aud: str = "yai-tes") -> dict[str, str]:
        """What the WES holds for the user: the user's AT issued to the WES, exchanged for the TES
        (sub=user, act.sub=yai-wes, D-067)."""
        subject = self.at(user, "yai-wes")["Authorization"].split()[1]
        at_type = "urn:ietf:params:oauth:token-type:access_token"
        data = {"grant_type": EXCHANGE, "subject_token": subject, "audience": aud,
                "subject_token_type": at_type, "requested_token_type": at_type}  # fmt: skip
        r = httpx.post(self.aai + "/token", data=data, auth=("yai-wes", self.wes_secret))
        r.raise_for_status()
        return {"Authorization": "Bearer " + r.json()["access_token"]}

    def service(self, aud: str) -> dict[str, str]:
        r = httpx.post(self.aai + "/token", data={"grant_type": "client_credentials",
                                                  "audience": aud},
                       auth=("yai-wes", self.wes_secret))  # fmt: skip
        r.raise_for_status()
        return {"Authorization": "Bearer " + r.json()["access_token"]}


# --- environment ---


def local_stack(tmp: Path) -> SimpleNamespace:
    aai = start_aai(tmp)
    drs_port = free_port()
    dcfg = drs_config(aai, tmp, drs_port)
    dcfg_path = write_config(tmp / "drs.yaml", dcfg)
    (tmp / "ds1.txt").write_bytes(b"controlled data for E2E\n")
    (tmp / "public.txt").write_bytes(b"public data for E2E\n")
    ids = {
        "ds1": register(dcfg_path, tmp / "ds1.txt", "--policy", "dataset", "--dataset", DS1),
        "public": register(dcfg_path, tmp / "public.txt", "--policy", "public"),
    }
    drs = Server("drs", dcfg, tmp / "drs", port=drs_port).start()
    tcfg = tes_config(aai, tmp, f"127.0.0.1:{drs_port}")
    tes = Server("tes", tcfg, tmp / "tes").start()
    worker = Worker(tcfg, tmp / "worker").start()
    drs_proxy, tes_proxy = RecordingProxy(drs.root), RecordingProxy(tes.root)
    fake = FakeDrs(f"{drs.root}/yai/blobs/{ids['public']}", len(b"public data for E2E\n"))
    (tmp / "wes-secret").write_text(WES_CLIENT_SECRET)
    iss = aai.root + "/"
    wport = free_port()
    near = [f"127.0.0.1:{p}" for p in (drs_port, drs_proxy.port, fake.port)]
    wcfg = {
        "data_dir": str(tmp / "wes-data"),
        "public_base_url": f"http://127.0.0.1:{wport}",
        "auth": {"mode": "aai", "issuer": iss, "jwks_uri": iss + ".well-known/jwks.json",
                 "audience": "yai-wes", "insecure_http_hosts": [f"127.0.0.1:{aai.port}"]},
        "tes": {"url": tes_proxy.root + "/ga4gh/tes/v1", "token_url": iss + "token",
                "client_id": "yai-wes", "client_secret_file": str(tmp / "wes-secret")},
        # the compact-identifier target is judged by the URL host (127.0.0.1 locally). In compose
        # it is drs.yai.test, so 127.0.0.1 is added only to stand in for the local environment.
        # fake.yai.test is added to check that nothing is sent to the "DRS whose issuers do not
        # overlap" of NEG-PASSPORT-SEND-UNTRUSTED (the host is allowed, the issuer check stops it)
        "resolver": {"passport_hosts": ["drs.yai.test", "127.0.0.1", "fake.yai.test"],
                     "host_override": {"drs.yai.test": drs_proxy.root,
                                       "fake.yai.test": fake.root,
                                       "other.yai.test": drs_proxy.root},
                     "compact": {"yaitest.drs": drs_proxy.root + "/ga4gh/drs/v1/objects/{id}"}},
        "engine": {"poll_seconds": 0.3, "nonce_search_seconds": 1, "nonce_search_interval": 0.2,
                   "cancel_retry_seconds": 0.5,
                   "output_root": str(Path(tcfg["files_root"]).resolve())},
        "fetch": {"allow": [], "insecure_http_hosts": near},
    }  # fmt: skip
    wes = Server("wes", wcfg, tmp / "wes", port=wport).start()
    return SimpleNamespace(
        kind="local", tmp=tmp, aai=aai, drs=drs, tes=tes, worker=worker, wes=wes,
        drs_proxy=drs_proxy, tes_proxy=tes_proxy, fake=fake, ids=ids, tcfg=tcfg, wcfg=wcfg,
        wes_url=wes.url, tes_url=tes.url, drs_url=drs.url, drs_host="drs.yai.test",
        creds=Creds(aai.root, TEST_CLIENT_SECRET, WES_CLIENT_SECRET),
        files_root=Path(tcfg["files_root"]).resolve(), output_root=None,
        stop=lambda: [s.stop() for s in (wes, worker, tes, drs, aai, drs_proxy, tes_proxy, fake)],
    )  # fmt: skip


def compose_stack() -> SimpleNamespace:
    secrets = Path(os.environ.get("YAI_E2E_SECRETS", "/yai/local/secrets"))
    ids = json.loads(Path(os.environ.get("YAI_E2E_IDS", "/yai/e2e/ids.json")).read_text())
    return SimpleNamespace(
        kind="compose", ids=ids, drs_host="drs.yai.test",
        wes_url="https://wes.yai.test/ga4gh/wes/v1", tes_url="https://tes.yai.test/ga4gh/tes/v1",
        drs_url="https://drs.yai.test/ga4gh/drs/v1",
        creds=Creds("https://aai.yai.test", (secrets / "test-client-secret").read_text().strip(),
                    (secrets / "wes-client-secret").read_text().strip()),
        files_root=Path(os.environ.get("YAI_E2E_TES_FILES", "/yai/obs/tes-files")),
        output_root="/yai/var/tes/files", stop=lambda: None,
    )  # fmt: skip


@pytest.fixture(scope="module", params=["local", "compose"])
def stack(request, tmp_path_factory):
    if request.param == "compose":
        if not COMPOSE:
            pytest.skip(f"{PENDING}: not run in the compose environment (YAI_E2E_TARGET=compose)")
        s = compose_stack()
    else:
        if COMPOSE:
            pytest.skip("inside the compose test container, the local-process stack is not started")
        s = local_stack(tmp_path_factory.mktemp("e2e"))
    yield s
    s.stop()


def local_only(stack) -> None:
    """Tests that need observers (relay, fake DRS, process control). Not in compose (D-036)."""
    if stack.kind != "local":
        pytest.skip("local only: needs observers and process control (not in compose, D-036)")


# --- helpers ---


def submit(stack, doc: str, params: dict, who: str = "alice", passports: list[str] | None = None,
           status: int = 200) -> str:  # fmt: skip
    data = {"workflow_type": "CWL", "workflow_type_version": "v1.2",
            "workflow_url": "wf.cwl", "workflow_params": json.dumps(params)}  # fmt: skip
    if passports is not None:
        data["yai_passports"] = json.dumps(passports)
    files = [("workflow_attachment", ("wf.cwl", doc.encode()))]
    r = httpx.post(stack.wes_url + "/runs", data=data, files=files,
                   headers=stack.creds.at(who, "yai-wes"), timeout=30)  # fmt: skip
    shape.check("wes", r)
    assert r.status_code == status, r.text
    return r.json().get("run_id", "")


def wes_get(stack, path: str, who: str = "alice", **params) -> httpx.Response:
    r = httpx.get(stack.wes_url + path, headers=stack.creds.at(who, "yai-wes"), params=params)
    shape.check("wes", r)
    return r


def wait(stack, run_id: str, who: str = "alice", states=TERMINAL, timeout=60.0) -> dict:
    deadline = time.monotonic() + timeout
    body: dict = {}
    while time.monotonic() < deadline:
        body = wes_get(stack, f"/runs/{run_id}", who).json()
        if body["state"] in states:
            return body
        time.sleep(0.3)
    raise AssertionError(f"run {run_id} stuck in {body.get('state')}: {body}")


def drs_file(stack, key: str = "ds1", host: str | None = None) -> dict:
    return {"class": "File", "location": f"drs://{host or stack.drs_host}/{stack.ids[key]}"}


def read_path(stack, location: str) -> str:
    """Map an output location (path seen by the runner) to the path seen by the test container."""
    path = location.removeprefix("file://")
    if stack.output_root:
        path = str(stack.files_root) + path.removeprefix(stack.output_root)
    return path


def read_output(stack, location: str) -> str:
    return Path(read_path(stack, location)).read_text()


def tes_tasks_of_run(stack, run_id: str) -> list[dict]:
    """Count the TES tasks of the run, as the WES service identity (observation)."""
    r = httpx.get(stack.tes_url + "/tasks", headers=stack.creds.service("yai-tes"),
                  params={"tag_key": "yai.run_id", "tag_value": run_id,
                          "view": "FULL"})  # fmt: skip
    r.raise_for_status()
    return r.json().get("tasks", [])


def tasks(stack, run_id: str) -> list[dict]:
    return wes_get(stack, f"/runs/{run_id}/tasks").json()["task_logs"]


def tamper(token: str) -> str:
    head, body, sig = token.split(".")
    raw = bytearray(base64.urlsafe_b64decode(sig + "=" * (-len(sig) % 4)))
    raw[0] ^= 1
    return ".".join([head, body, base64.urlsafe_b64encode(bytes(raw)).rstrip(b"=").decode()])


def cat_run(stack, key: str = "ds1", who: str = "alice", **kw) -> dict:
    passport = stack.creds.passport(who)
    run_id = submit(stack, CAT, {"infile": drs_file(stack, key, kw.pop("host", None))}, who,
                    [passport], **kw)  # fmt: skip
    return wait(stack, run_id, who)


# --- section 5 E2E ---


def test_E2E_01(stack):
    """A CWL on one object of alice's ds1 flows WES -> DRS (Passport) -> TES to COMPLETE."""
    log = cat_run(stack)
    assert log["state"] == "COMPLETE", log["run_log"]
    assert read_output(stack, log["outputs"]["out"]["location"]) == "controlled data for E2E\n"
    (task,) = tasks(stack, log["run_id"])
    r = httpx.get(task["tes_uri"], headers=stack.creds.at("alice", "yai-tes"))
    assert r.status_code == 200 and r.json()["state"] == "COMPLETE"


def test_E2E_02(stack):
    """Two steps (step 1 output is step 2 input). Step order and hand-off via file://."""
    passport = stack.creds.passport("alice")
    doc = two_steps("first/out")
    params = {"script": 'cp "$0" a.txt', "data": drs_file(stack)}
    run_id = submit(stack, doc, params, passports=[passport])
    log = wait(stack, run_id)
    assert log["state"] == "COMPLETE", log["run_log"]
    assert read_output(stack, log["outputs"]["final"]["location"]) == "controlled data for E2E\n"
    steps = tasks(stack, run_id)
    assert [t["name"] for t in steps] == ["first", "second"]
    assert steps[0]["end_time"] <= steps[1]["start_time"]
    tes = tes_tasks_of_run(stack, run_id)
    second = next(t for t in tes if t["name"].endswith(".2"))
    first = next(t for t in tes if t["name"].endswith(".1"))
    assert second["inputs"][0]["url"] == first["outputs"][0]["url"]
    assert second["inputs"][0]["url"].startswith("file://")


def test_E2E_03(stack):
    """bob (ds2 only) submits with a ds1 object -> DRS 403 -> SYSTEM_ERROR, nothing sent to TES."""
    log = cat_run(stack, who="bob")
    assert log["state"] == "SYSTEM_ERROR"
    assert any("403" in line for line in log["run_log"]["system_logs"]), log["run_log"]
    assert tes_tasks_of_run(stack, log["run_id"]) == []


def test_E2E_11(stack):
    """A well-formed Passport with a tampered signature -> WES accepts, DRS 401 -> SYSTEM_ERROR."""
    bad = tamper(stack.creds.passport("alice"))
    run_id = submit(stack, CAT, {"infile": drs_file(stack)}, passports=[bad])
    log = wait(stack, run_id)
    assert log["state"] == "SYSTEM_ERROR"
    assert any("401" in line for line in log["run_log"]["system_logs"]), log["run_log"]
    assert tes_tasks_of_run(stack, run_id) == []


def test_E2E_04(stack):
    """Executor exits non-zero -> TES EXECUTOR_ERROR -> run EXECUTOR_ERROR. exit_code is
    copied (C-09)."""
    log = wait(stack, submit(stack, SH, {"script": "exit 3"}))
    assert log["state"] == "EXECUTOR_ERROR"
    (task,) = tasks(stack, log["run_id"])
    assert task["exit_code"] == 3
    assert task["system_logs"][0] == "state=EXECUTOR_ERROR"


def test_E2E_05(stack):
    """Make output storage unwritable -> TES SYSTEM_ERROR -> run SYSTEM_ERROR."""
    local_only(stack)
    place = stack.files_root / "wes"
    place.mkdir(parents=True, exist_ok=True)
    place.chmod(0o555)
    try:
        log = cat_run(stack, key="public")
    finally:
        place.chmod(0o755)
    assert log["state"] == "SYSTEM_ERROR", log["run_log"]
    (tes,) = tes_tasks_of_run(stack, log["run_id"])
    assert tes["state"] == "SYSTEM_ERROR"


def test_E2E_06(stack):
    """CancelRun while running -> CANCELING -> CANCELED; the TES task is CANCELED too."""
    run_id = submit(stack, SH, {"script": "sleep 30"})
    wait(stack, run_id, states={"RUNNING"})
    until(lambda: tasks(stack, run_id)[0].get("system_logs", [""])[0] == "state=RUNNING")
    r = httpx.post(stack.wes_url + f"/runs/{run_id}/cancel",
                   headers=stack.creds.at("alice", "yai-wes"))  # fmt: skip
    assert r.status_code == 200
    seen = wes_get(stack, f"/runs/{run_id}").json()["state"]
    assert seen in ("CANCELING", "CANCELED")
    log = wait(stack, run_id)
    assert log["state"] == "CANCELED"
    (tes,) = tes_tasks_of_run(stack, run_id)
    assert tes["state"] == "CANCELED"


@pytest.mark.docker
def test_E2E_06_no_container_left(stack):
    """The "no container left" part of E2E-06 (needs the real Docker runner)."""
    import subprocess

    local_only_docker(stack)
    run_id = submit(stack, SH, {"script": "sleep 30"})
    wait(stack, run_id, states={"RUNNING"})
    httpx.post(stack.wes_url + f"/runs/{run_id}/cancel", headers=stack.creds.at("alice", "yai-wes"))
    wait(stack, run_id)
    (tes,) = tes_tasks_of_run(stack, run_id)
    out = subprocess.run(["docker", "ps", "-a", "--filter", f"label=yai.tes.task={tes['id']}",
                          "--format", "{{.Names}}"],
                         capture_output=True, text=True, check=True)  # fmt: skip
    assert out.stdout.strip() == ""


SH_OUT = SH.replace(
    "outputs: []", "outputs:\n  out: {type: File, outputBinding: {glob: out.txt}}\nstdout: out.txt"
)


@pytest.mark.docker
def test_NEG_LIMITS_docker(stack):
    """NEG-LIMITS runtime limits: a task without resources still gets the default limits (1 CPU,
    2 GB, pids 256, D-034) on the real container. Checked by reading cgroup v2 values inside the
    container (cpu, memory and pids are delegated and enforced even rootless; checked on a real
    machine on 2026-10-17, D-034)."""
    local_only_docker(stack)
    script = "cat /sys/fs/cgroup/cpu.max /sys/fs/cgroup/memory.max /sys/fs/cgroup/pids.max"
    log = wait(stack, submit(stack, SH_OUT, {"script": script}))
    assert log["state"] == "COMPLETE", log["run_log"]
    cpu, memory, pids = read_output(stack, log["outputs"]["out"]["location"]).split("\n")[:3]
    quota, period = cpu.split()
    assert int(quota) / int(period) == 1.0  # --cpus 1
    assert int(memory) == 2 * 1024**3  # --memory 2048m
    assert pids == "256"  # --pids-limit 256


def test_E2E_07(stack):
    """A task that starts fetching after the signed URL expired -> SYSTEM_ERROR (input fetch
    failure) -> the run too.

    Instead of TES concurrency 0, the runner is stopped to hold the task, and the DRS test clock
    is moved past expiry."""
    local_only(stack)
    stack.worker.stop()
    try:
        log_run = submit(stack, CAT, {"infile": drs_file(stack)},
                         passports=[stack.creds.passport("alice")])  # fmt: skip
        until(lambda: len(tes_tasks_of_run(stack, log_run)) == 1, timeout=20)
        httpx.post(stack.drs.root + "/yai/test/clock", json={"offset": 3700}).raise_for_status()
    finally:
        stack.worker.start()
    try:
        log = wait(stack, log_run)
    finally:
        httpx.post(stack.drs.root + "/yai/test/clock", json={"offset": 0}).raise_for_status()
    assert log["state"] == "SYSTEM_ERROR", log["run_log"]
    (tes,) = tes_tasks_of_run(stack, log_run)
    assert tes["state"] == "SYSTEM_ERROR"
    assert any("input fetch failed" in s for s in tes["logs"][0]["system_logs"])


def test_E2E_08(stack):
    """A compact-identifier input resolves via the local resolver table (C-16)."""
    passport = stack.creds.passport("alice")
    params = {"infile": {"class": "File", "location": f"drs://yaitest.drs:{stack.ids['ds1']}"}}
    log = wait(stack, submit(stack, CAT, params, passports=[passport]))
    assert log["state"] == "COMPLETE", log["run_log"]
    assert read_output(stack, log["outputs"]["out"]["location"]) == "controlled data for E2E\n"


def test_E2E_09(stack):
    """Restart yai-wes while running -> the run continues.

    The Passport is gone, so step 2 with a drs:// input is SYSTEM_ERROR (reading of D-033)."""
    local_only(stack)
    passport = stack.creds.passport("alice")
    doc = two_steps("data").replace("in: {s: script, d: data}", "in: {s: script, d: pub}")
    doc = doc.replace("  data: File\n", "  data: File\n  pub: File\n", 1)
    params = {"script": "sleep 3; cp \"$0\" a.txt", "data": drs_file(stack),
              "pub": drs_file(stack, "public")}  # fmt: skip
    run_id = submit(stack, doc, params, passports=[passport])
    until(lambda: tasks(stack, run_id)[0].get("system_logs", [""])[0] == "state=RUNNING")
    stack.wes.stop()
    stack.wes.start()
    log = wait(stack, run_id)
    assert log["state"] == "SYSTEM_ERROR", log["run_log"]
    assert any("credentials held for this run are gone" in s for s in log["run_log"]["system_logs"])
    first, second = tasks(stack, run_id)
    assert first["exit_code"] == 0 and "tes_uri" in first  # step 1 continued and finished
    assert "tes_uri" not in second  # step 2 was not submitted


# --- system.md section 9 (grouped by E2E-10) ---


def test_INT_DRS_POST(stack):
    """C-01: step 6 is a POST with passports in the body. Sending tokens gets 400."""
    local_only(stack)
    stack.drs_proxy.clear()
    assert cat_run(stack)["state"] == "COMPLETE"
    access = stack.drs_proxy.requests(contains="/access/")
    assert access and all(s.method == "POST" and "passports" in s.json() for s in access)
    obj = f"{stack.drs_url}/objects/{stack.ids['ds1']}"
    r = httpx.post(obj, json={"tokens": [stack.creds.passport("alice")]})
    assert r.status_code == 400


def test_INT_AM_BOTH(stack):
    """C-02: methods with access_id go through /access; those without use access_url as is."""
    local_only(stack)
    stack.drs_proxy.clear()
    assert cat_run(stack)["state"] == "COMPLETE"
    # yai-drs dataset objects return only access_id -> go through /access
    assert stack.drs_proxy.requests(contains="/access/")
    stack.fake.seen.clear()
    params = {"infile": {"class": "File", "location": "drs://fake.yai.test/noaccessid"}}
    log = wait(stack, submit(stack, CAT, params, passports=[]))
    assert log["state"] == "COMPLETE", log["run_log"]
    assert not any("/access/" in path for _, path, _ in stack.fake.seen)
    assert read_output(stack, log["outputs"]["out"]["location"]) == "public data for E2E\n"


def test_INT_HEADERS_REJECT(stack):
    """C-03: an AccessURL with headers -> run SYSTEM_ERROR, no task sent to TES."""
    local_only(stack)
    params = {"infile": {"class": "File", "location": "drs://fake.yai.test/headers"}}
    log = wait(stack, submit(stack, CAT, params, passports=[]))
    assert log["state"] == "SYSTEM_ERROR"
    assert any("headers" in s for s in log["run_log"]["system_logs"])
    assert tes_tasks_of_run(stack, log["run_id"]) == []


def test_INT_TES_STORAGE(stack):
    """C-04: WES does not refuse https inputs based on TES storage (submits without
    service-info)."""
    local_only(stack)
    stack.tes_proxy.clear()
    stack.tes_proxy.rewrite = _drop_https_storage
    try:
        assert cat_run(stack)["state"] == "COMPLETE"
    finally:
        stack.tes_proxy.rewrite = None
    si = httpx.get(stack.tes_proxy.root + "/ga4gh/tes/v1/service-info").json()
    assert "https://" in si["storage"]  # without the relay, the real one lists https


def _drop_https_storage(seen: Seen, status: int, body: Any):
    if seen.path.endswith("/service-info"):
        return status, {**body, "storage": [s for s in body["storage"] if s != "https://"]}
    return None


def test_INT_STATE_MAP(stack):
    """C-06: TES final state -> run state (expected values in tests/cases/INT-STATE-MAP.yaml,
    separate from the table).

    The 4 states the real TES can produce (COMPLETE, EXECUTOR_ERROR, SYSTEM_ERROR, CANCELED by
    someone else) are made
    here; the others (PREEMPTED, UNKNOWN, PAUSED) by rewriting GetTask responses in the relay."""
    local_only(stack)
    want = yaml.safe_load((Path(__file__).parent / "cases" / "INT-STATE-MAP.yaml").read_text())
    got = {
        "COMPLETE": wait(stack, submit(stack, SH, {"script": "true"}))["state"],
        "EXECUTOR_ERROR": wait(stack, submit(stack, SH, {"script": "exit 1"}))["state"],
    }
    run_id = submit(stack, SH, {"script": "sleep 30"})
    until(lambda: len(tes_tasks_of_run(stack, run_id)) == 1)
    tes_id = tes_tasks_of_run(stack, run_id)[0]["id"]
    httpx.post(
        f"{stack.tes_url}/tasks/{tes_id}:cancel", headers=stack.creds.service("yai-tes")
    ).raise_for_status()  # cancel from outside WES
    got["CANCELED"] = wait(stack, run_id)["state"]
    for forced in ("PREEMPTED", "SYSTEM_ERROR"):
        stack.tes_proxy.rewrite = _force_state(forced)
        try:
            got[forced] = wait(stack, submit(stack, SH, {"script": "sleep 1"}))["state"]
        finally:
            stack.tes_proxy.rewrite = None
    run_id = submit(stack, SH, {"script": "sleep 2"})
    stack.tes_proxy.rewrite = _force_state("PAUSED")
    try:
        time.sleep(3)  # the run is not finished while PAUSED or UNKNOWN
        got["PAUSED"] = wes_get(stack, f"/runs/{run_id}").json()["state"]
    finally:
        stack.tes_proxy.rewrite = None
    wait(stack, run_id)
    assert got == want["terminal"] | want["waiting"]


def _force_state(state: str):
    def rewrite(seen: Seen, status: int, body: Any):
        if seen.method == "GET" and "/tasks/" in seen.path and isinstance(body, dict):
            return status, {**body, "state": state}
        return None

    return rewrite


def test_INT_VIEW_FULL(stack):
    """C-07: WES GetTask requests carry view=FULL."""
    local_only(stack)
    stack.tes_proxy.clear()
    assert cat_run(stack, key="public")["state"] == "COMPLETE"
    gets = [s for s in stack.tes_proxy.requests("GET", "/tasks/") if ":" not in s.path]
    assert gets and all(s.query.get("view") == ["FULL"] for s in gets)


def test_INT_LOG_URL(stack):
    """C-08: whether TES stdout is content, truncated or a URL, the WES stdout endpoint gives 200.

    A URL is not served as content."""
    local_only(stack)
    run_id = submit(stack, SH, {"script": "echo hello-log"})
    wait(stack, run_id)
    task_id = f"{run_id}.1"
    got = {}
    for kind, value in [("content", None), ("tail", "...truncated...lo-log\n"),
                        ("url", "https://logs.example/stdout?sig=CANARY-log")]:  # fmt: skip
        stack.tes_proxy.rewrite = None if value is None else _stdout_as(value)
        try:
            # a yai-specific endpoint (C-08), so no OpenAPI shape check
            r = httpx.get(f"{stack.wes_url}/runs/{run_id}/tasks/{task_id}/stdout",
                          headers=stack.creds.at("alice", "yai-wes"))  # fmt: skip
        finally:
            stack.tes_proxy.rewrite = None
        assert r.status_code == 200
        got[kind] = r.text
    assert got["content"] == "hello-log\n"
    assert got["tail"] == "...truncated...lo-log\n"
    assert "CANARY" not in got["url"] and "logs.example" not in got["url"]


def _stdout_as(value: str):
    def rewrite(seen: Seen, status: int, body: Any):
        if seen.method == "GET" and "/tasks/" in seen.path and body.get("logs"):
            body["logs"][-1]["logs"][-1]["stdout"] = value
            return status, body
        return None

    return rewrite


def test_INT_EXIT_CODE(stack):
    """C-09: the exit_code of a one-task, one-executor run is copied."""
    log = wait(stack, submit(stack, SH, {"script": "exit 7"}))
    assert tasks(stack, log["run_id"])[0]["exit_code"] == 7


def test_INT_TIME_FORMAT(stack):
    """C-10: TES times with fractions and offsets become UTC %Y-%m-%dT%H:%M:%SZ."""
    local_only(stack)

    def rewrite(seen: Seen, status: int, body: Any):
        if seen.method == "GET" and "/tasks/" in seen.path and body.get("logs"):
            body["logs"][-1]["start_time"] = "2026-10-17T09:02:03.987654+09:00"
            body["logs"][-1]["end_time"] = "2026-10-17T09:02:04.5+09:00"
            return status, body
        return None

    stack.tes_proxy.rewrite = rewrite
    try:
        run_id = submit(stack, SH, {"script": "true"})
        wait(stack, run_id)
    finally:
        stack.tes_proxy.rewrite = None
    (task,) = tasks(stack, run_id)
    assert task["start_time"] == "2026-10-17T00:02:03Z"
    assert task["end_time"] == "2026-10-17T00:02:04Z"


def test_INT_TIME_EMPTY(stack):
    """C-10 continued: a TES that returns missing times as "" or unreadable text (Funnel v0.11.14
    returns "", INTEROP-TES-2) does not break the run; the time is missing (null)."""
    local_only(stack)

    def rewrite(seen: Seen, status: int, body: Any):
        if seen.method == "GET" and "/tasks/" in seen.path and body.get("logs"):
            body["logs"][-1]["start_time"] = ""
            body["logs"][-1]["end_time"] = "not a time"
            return status, body
        return None

    stack.tes_proxy.rewrite = rewrite
    try:
        run_id = submit(stack, SH, {"script": "true"})
        log = wait(stack, run_id)
    finally:
        stack.tes_proxy.rewrite = None
    assert log["state"] == "COMPLETE", log["run_log"]
    (task,) = tasks(stack, run_id)
    assert task.get("start_time") is None and task.get("end_time") is None


def test_INT_TES_DELEGATE(stack):
    """C-11 (D-067): tasks WES submits are owned by the user, with the WES as the acting service
    (act in the exchanged AT), and no yai.on_behalf_of tag. The user can read but not cancel;
    the WES reads them with its own token too (it is the actor); another user sees nothing."""
    log = cat_run(stack, key="public")
    (tes,) = tes_tasks_of_run(stack, log["run_id"])  # read as the WES service identity
    assert "yai.on_behalf_of" not in tes["tags"]
    assert tes["tags"]["yai.run_id"] == log["run_id"]
    alice = stack.creds.at("alice", "yai-tes")
    assert httpx.get(f"{stack.tes_url}/tasks/{tes['id']}", headers=alice).status_code == 200
    assert httpx.post(f"{stack.tes_url}/tasks/{tes['id']}:cancel", headers=alice).status_code == 403
    bob = stack.creds.at("bob", "yai-tes")
    assert httpx.get(f"{stack.tes_url}/tasks/{tes['id']}", headers=bob).status_code == 404
    if stack.kind == "local":  # owner and actor read from the DB by the observer (D-023)
        db = sqlite3.connect(Path(stack.tcfg["data_dir"]) / "tes.sqlite")
        row = db.execute("SELECT owner_sub, actor FROM tasks WHERE id=?", (tes["id"],)).fetchone()
        db.close()
        assert row[0] == "alice" and row[1].endswith("|yai-wes")
        # the token the WES sent to the TES: for alice, act = the WES, and no Passport scopes
        # (the WES resolves drs:// itself here, so it asks for none, D-069)
        creates = [s for s in stack.tes_proxy.seen
                   if s.method == "POST" and s.path.endswith("/tasks")]  # fmt: skip
        auth = {k.lower(): v for k, v in creates[-1].headers.items()}["authorization"]
        claims = _claims(auth.split(" ", 1)[1])
        assert claims["sub"] == "alice" and claims["act"]["sub"] == "yai-wes"
        assert "ga4gh_passport_v1" not in claims["scope"].split()


def test_INT_TES_URI_READ(stack):
    """C-12: tes_uri gives 200 with the user's own AT, 404 for someone else."""
    log = cat_run(stack, key="public")
    (task,) = tasks(stack, log["run_id"])
    assert httpx.get(task["tes_uri"], headers=stack.creds.at("alice", "yai-tes")).status_code == 200
    assert httpx.get(task["tes_uri"], headers=stack.creds.at("bob", "yai-tes")).status_code == 404


@pytest.mark.parametrize("end", ["absent", "empty", "null"])
def test_INT_PAGING_END(stack, end):
    """C-13: whether TES omits next_page_token, sends "" or null, the WES search ends.

    The relay turns the CreateTask response into 503 (the task is still created), so WES searches
    by nonce."""
    local_only(stack)

    def rewrite(seen: Seen, status: int, body: Any):
        if seen.method == "POST" and seen.path.endswith("/tasks"):
            return 503, {"msg": "lost", "status_code": 503}
        if seen.method == "GET" and seen.path.endswith("/tasks") and isinstance(body, dict):
            body.pop("next_page_token", None)
            if end != "absent":
                body["next_page_token"] = "" if end == "empty" else None
            return status, body
        return None

    stack.tes_proxy.rewrite = rewrite
    try:
        run_id = submit(stack, SH, {"script": "true"})
        log = wait(stack, run_id)
    finally:
        stack.tes_proxy.rewrite = None
    assert log["state"] == "COMPLETE", log["run_log"]
    assert len(tes_tasks_of_run(stack, run_id)) == 1  # not created twice


@pytest.mark.docker
def test_INT_DRS_HOSTNAME(stack):
    """C-15: drs://drs.yai.test/<id> resolves over TLS on 443 without the override table
    (compose only)."""
    if stack.kind != "compose":
        pytest.skip("compose only: https on 443 for drs.yai.test exists only in compose")
    log = cat_run(stack)
    assert log["state"] == "COMPLETE"


def test_INT_DRS_COMPACT(stack):
    """C-16: drs://yaitest.drs:<id> resolves via the local table (same flow as E2E-08,
    with a public object)."""
    params = {"infile": {"class": "File", "location": f"drs://yaitest.drs:{stack.ids['public']}"}}
    log = wait(stack, submit(stack, CAT, params, passports=[]))
    assert log["state"] == "COMPLETE", log["run_log"]


def test_AAI_AUD(stack):
    """C-17: a Passport with another service's aud gets 401; without aud
    follows the setting (default: accept)."""
    obj = f"{stack.drs_url}/objects/{stack.ids['ds1']}"
    good = stack.creds.passport("alice")
    other = stack.creds.passport("alice", audience="yai-wes")
    none = stack.creds.passport("alice", audience="")
    assert httpx.post(obj, json={"passports": [good]}).status_code == 200
    assert httpx.post(obj, json={"passports": [other]}).status_code == 401
    assert httpx.post(obj, json={"passports": [none]}).status_code == 200


def test_NEG_PASSPORT_NOT_IN_TES(stack):
    """C-19: the Passport is not in TES tasks or logs, and never reaches a DRS that is not
    allowed."""
    passport = stack.creds.passport("alice")
    sig = passport.split(".")[2]
    run_id = submit(stack, CAT, {"infile": drs_file(stack)}, passports=[passport])
    assert wait(stack, run_id)["state"] == "COMPLETE"
    (tes,) = tes_tasks_of_run(stack, run_id)
    assert sig not in json.dumps(tes)
    if stack.kind == "local":
        assert sig not in stack.tes.log() + stack.worker.log_path.read_text()
        db = (Path(stack.tcfg["data_dir"]) / "tes.sqlite").read_bytes()
        assert sig.encode() not in db
        # not sent to a DRS that is not allowed (other.yai.test, not in passport_hosts)
        stack.drs_proxy.clear()
        run2 = submit(stack, CAT, {"infile": drs_file(stack, host="other.yai.test")},
                      passports=[passport])  # fmt: skip
        assert wait(stack, run2)["state"] == "SYSTEM_ERROR"
        assert not any(sig.encode() in s.body for s in stack.drs_proxy.seen)


def test_INT_CANCEL_RACE(stack):
    """C-20: canceling a finished run gives 200 and the state does not change.

    No new task is submitted while canceling (mid-step)."""
    done = wait(stack, submit(stack, SH, {"script": "true"}))
    alice = stack.creds.at("alice", "yai-wes")
    r = httpx.post(stack.wes_url + f"/runs/{done['run_id']}/cancel", headers=alice)
    assert r.status_code == 200
    assert wes_get(stack, f"/runs/{done['run_id']}").json()["state"] == "COMPLETE"
    doc = two_steps("first/out")
    run_id = submit(stack, doc, {"script": 'sleep 30; cp "$0" a.txt',
                                 "data": drs_file(stack, "public")}, passports=[])  # fmt: skip
    until(lambda: tasks(stack, run_id)[0].get("system_logs", [""])[0] == "state=RUNNING")
    assert httpx.post(stack.wes_url + f"/runs/{run_id}/cancel", headers=alice).status_code == 200
    assert wait(stack, run_id)["state"] == "CANCELED"
    assert [t["name"] for t in tes_tasks_of_run(stack, run_id)] == [f"{run_id}.1"]


def test_AAI_EXP_OPTB(stack):
    """C-21: no URL is issued when the Visa's remaining time is at most URL expiry + clock skew
    (1 s either side of the boundary)."""
    local_only(stack)  # advances the DRS test clock
    passport = stack.creds.passport("alice")
    claims = _claims(passport)
    visa_exp = min(_claims(v)["exp"] for v in claims["ga4gh_passport_v1"])
    limit = min(visa_exp, claims["exp"]) - 3600 - 60  # Option B boundary (system.md section 5)
    obj = f"{stack.drs_url}/objects/{stack.ids['ds1']}"
    aid = httpx.post(obj, json={"passports": [passport]}).json()["access_methods"][0]["access_id"]
    got = {}
    try:
        for delta in (-2, 1):  # the clock rounds to seconds, so "before" is 2 s early
            offset = limit + delta - int(time.time())
            httpx.post(stack.drs.root + "/yai/test/clock", json={"offset": offset})
            got[delta] = httpx.post(f"{obj}/access/{aid}", json={"passports": [passport]})
    finally:
        httpx.post(stack.drs.root + "/yai/test/clock", json={"offset": 0})
    assert got[-2].status_code == 200
    assert got[1].status_code == 403


def _claims(token: str) -> dict:
    part = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))


def test_ERR_SHAPE(stack):
    """C-23: TES 4xx responses are {msg, status_code} (D-011)."""
    alice = stack.creds.at("alice", "yai-tes")
    cases = [
        httpx.get(stack.tes_url + "/tasks/tnosuchtask", headers=alice),
        httpx.post(stack.tes_url + "/tasks", json={"executors": []}, headers=alice),
        httpx.get(stack.tes_url + "/tasks"),
        httpx.get(stack.tes_url + "/tasks", params={"page_size": 5000}, headers=alice),
    ]
    assert [r.status_code for r in cases] == [404, 400, 401, 400]
    for r in cases:
        body = r.json()
        assert set(body) == {"msg", "status_code"} and body["status_code"] == r.status_code


def test_INT_TES_DRS_DIRECT(stack):
    """D-069: a user submits a task with a drs:// input straight to the TES with a
    passport-scoped AT. The TES exchanges it for the user's Passport at the broker, resolves the
    input at the DRS and runs it; the saved task holds the URL without its signature and the
    drs:// in the system log. A plain AT gets 403, a user without the Visa 403, the WES's own
    token 403 (no user)."""
    out = f"file://{stack.output_root or stack.files_root}/direct/{secrets.token_hex(4)}/out.txt"
    task = {"executors": [{"image": "alpine:3.20", "command": ["cat", "/yai/work/in"],
                           "workdir": "/yai/work", "stdout": "/yai/work/out.txt"}],
            "inputs": [{"path": "/yai/work/in", "url": f"drs://{stack.drs_host}/{stack.ids['ds1']}"}],
            "outputs": [{"path": "/yai/work/out.txt", "url": out}]}  # fmt: skip
    alice = stack.creds.psat_for("alice", "yai-tes")
    r = httpx.post(stack.tes_url + "/tasks", json=task, headers=alice)
    assert r.status_code == 200, r.text
    tid = r.json()["id"]
    deadline = time.monotonic() + 60
    t: dict = {}
    while time.monotonic() < deadline:
        resp = httpx.get(stack.tes_url + f"/tasks/{tid}", params={"view": "FULL"}, headers=alice)
        t = resp.json()
        if t["state"] in TERMINAL:
            break
        time.sleep(0.5)
    assert t["state"] == "COMPLETE", t
    assert read_output(stack, out) == "controlled data for E2E\n"
    assert "sig=" not in t["inputs"][0]["url"] and "drs://" not in t["inputs"][0]["url"]
    assert f"drs://{stack.drs_host}/" in json.dumps(t["logs"][0]["system_logs"])
    plain = stack.creds.at("alice", "yai-tes")  # not passport-scoped: no Passport to fetch
    assert httpx.post(stack.tes_url + "/tasks", json=task, headers=plain).status_code == 403
    bob = stack.creds.psat_for("bob", "yai-tes")
    assert httpx.post(stack.tes_url + "/tasks", json=task, headers=bob).status_code == 403
    svc = stack.creds.service("yai-tes")
    assert httpx.post(stack.tes_url + "/tasks", json=task, headers=svc).status_code == 403


def test_INT_TES_DRS_VIA_WES(stack):
    """D-069 end to end through a WES with resolver.resolve_in_wes: false (local only, a second
    WES on the same TES): the WES asks for the Passport scopes in the exchange, passes drs://
    unchanged without needing a Passport of its own, the TES resolves it for the user and the
    run completes. The token the TES received is a PSAT for alice with act = the WES."""
    if stack.kind != "local":
        pytest.skip("local only: starts a second WES")
    import copy

    cfg = copy.deepcopy(stack.wcfg)
    port = free_port()
    cfg["data_dir"] = str(stack.tmp / "wes2-data")
    cfg["public_base_url"] = f"http://127.0.0.1:{port}"
    cfg["resolver"]["resolve_in_wes"] = False
    wes2 = Server("wes", cfg, stack.tmp / "wes2", port=port).start()
    try:
        stack.tes_proxy.clear()
        data = {"workflow_type": "CWL", "workflow_type_version": "v1.2", "workflow_url": "wf.cwl",
                "workflow_params": json.dumps({"infile": drs_file(stack)})}  # fmt: skip
        files = [("workflow_attachment", ("wf.cwl", CAT.encode()))]
        headers = stack.creds.at("alice", "yai-wes")
        r = httpx.post(wes2.url + "/runs", data=data, files=files, headers=headers, timeout=30)
        assert r.status_code == 200, r.text
        run_id = r.json()["run_id"]
        deadline = time.monotonic() + 60
        state = ""
        while time.monotonic() < deadline:
            state = httpx.get(f"{wes2.url}/runs/{run_id}/status", headers=headers).json()["state"]
            if state in TERMINAL:
                break
            time.sleep(0.5)
        log = httpx.get(f"{wes2.url}/runs/{run_id}", headers=headers).json()
        assert state == "COMPLETE", log["run_log"]
        creates = [s for s in stack.tes_proxy.seen
                   if s.method == "POST" and s.path.endswith("/tasks")]  # fmt: skip
        sent = creates[-1].json()
        assert sent["inputs"][0]["url"].startswith("drs://")  # unchanged by the WES
        auth = {k.lower(): v for k, v in creates[-1].headers.items()}["authorization"]
        claims = _claims(auth.split(" ", 1)[1])
        assert claims["sub"] == "alice" and claims["act"]["sub"] == "yai-wes"
        assert {"openid", "ga4gh_passport_v1"} <= set(claims["scope"].split())
        (tes,) = tes_tasks_of_run(stack, run_id)
        assert tes["inputs"][0]["url"].startswith("http")  # resolved by the TES
        assert read_output(stack, log["outputs"]["out"]["location"]).startswith("controlled")
    finally:
        wes2.stop()


def test_INT_FILE_SAME_RUN(stack):
    """C-25: allowed between steps of the same run (E2E-02); pointing to another user's or
    another run's output gets 400."""
    log = cat_run(stack, key="public")
    out_url = tes_tasks_of_run(stack, log["run_id"])[0]["outputs"][0]["url"]
    task = {"executors": [{"image": "alpine:3.20", "command": ["cat", "/in"]}],
            "inputs": [{"path": "/in", "url": out_url}]}  # fmt: skip
    bob = stack.creds.at("bob", "yai-tes")
    assert httpx.post(stack.tes_url + "/tasks", json=task, headers=bob).status_code == 400
    alice = stack.creds.at("alice", "yai-tes")  # the owner herself, outside the run: 400 (C-25)
    assert httpx.post(stack.tes_url + "/tasks", json=task, headers=alice).status_code == 400
    via_wes = stack.creds.delegated("alice")  # what the WES holds for alice (D-067)
    other_run = {**task, "tags": {"yai.run_id": "rother"}}
    assert httpx.post(stack.tes_url + "/tasks", json=other_run, headers=via_wes).status_code == 400
    same_run = {**task, "tags": {"yai.run_id": log["run_id"]}}
    r = httpx.post(stack.tes_url + "/tasks", json=same_run, headers=via_wes)
    assert r.status_code == 200


def test_INT_DRS_PUBLIC(stack):
    """C-26: a public DRS never receives a Passport, and resolution works."""
    local_only(stack)
    passport = stack.creds.passport("alice")
    stack.drs_proxy.clear()
    stack.fake.seen.clear()
    log = wait(stack, submit(stack, CAT, {"infile": drs_file(stack, "public")},
                             passports=[passport]))  # fmt: skip
    assert log["state"] == "COMPLETE"
    assert not any(b"passports" in s.body for s in stack.drs_proxy.seen)
    params = {"infile": {"class": "File", "location": "drs://fake.yai.test/noaccessid"}}
    assert wait(stack, submit(stack, CAT, params, passports=[passport]))["state"] == "COMPLETE"
    assert stack.fake.passports_received() == 0


# --- stage 2 of test-plan.md section 4 (cases that include WES) ---


def test_NEG_PASSPORT_SEND_UNTRUSTED(stack):
    """Mix drs:// inputs on a host that is not allowed, a DRS whose issuers do not overlap and a
    redirecting DRS ->
    none of them ever receives a Passport, and the run is SYSTEM_ERROR (DRS-AUTH-07)."""
    local_only(stack)
    passport = stack.creds.passport("alice")
    before = stack.fake.passports_received()
    stack.drs_proxy.clear()
    cases = [  # (input, reason in system_logs)
        (drs_file(stack, host="other.yai.test"), "not allowed to send passports to other.yai.test"),
        ({"class": "File", "location": "drs://fake.yai.test/pauth"}, "passport_auth_issuers"),
        ({"class": "File", "location": "drs://fake.yai.test/redirect"}, "redirect not followed"),
    ]
    for infile, reason in cases:
        log = wait(stack, submit(stack, CAT, {"infile": infile}, passports=[passport]))
        assert log["state"] == "SYSTEM_ERROR", log["run_log"]
        assert any(reason in s for s in log["run_log"]["system_logs"]), log["run_log"]
    assert stack.fake.passports_received() == before
    assert not any(b"passports" in s.body for s in stack.drs_proxy.seen)
    assert stack.drs_proxy.requests("POST") == []


def test_NEG_WES_SUBMIT_DUP(stack):
    """With a hook that drops the CreateTask response and one that delays creation (longer than
    the WES 10 s wait), WES searches again by tag_key+tag_value (submit_nonce) and there is only
    one TES task.
    Nonces of different runs do not mix."""
    local_only(stack)
    alice = stack.creds.at("alice", "yai-wes")

    def one_task(run_id: str) -> dict:
        log = wait(stack, run_id, timeout=90)
        assert log["state"] == "COMPLETE", log["run_log"]
        (task,) = tes_tasks_of_run(stack, run_id)
        return task

    def is_create(seen: Seen) -> bool:
        return seen.method == "POST" and seen.path.endswith("/tasks")

    # (1) drop the response: TES created it but WES never hears -> found by nonce, continues
    first = {"left": 1}

    def drop_once(seen: Seen) -> bool:
        if is_create(seen) and first["left"]:
            first["left"] -= 1
            return True
        return False

    stack.tes_proxy.clear()
    stack.tes_proxy.drop = drop_once
    try:
        dropped = one_task(submit(stack, SH, {"script": "true"}))
    finally:
        stack.tes_proxy.clear()
    assert first["left"] == 0
    # (2) delay creation: WES gives up waiting and searches -> none -> resubmits with the same
    #     nonce -> the late one becomes the same task via the yai-tes unique constraint (no dup)
    held = {"left": 1}

    def hold_once(seen: Seen) -> float:
        if is_create(seen) and held["left"]:
            held["left"] -= 1
            return 12.0
        return 0.0

    stack.tes_proxy.hold = hold_once
    try:
        slow_id = submit(stack, SH, {"script": "true"})
        other_id = submit(stack, SH, {"script": "true"})  # another run at the same time
        slow, other = one_task(slow_id), one_task(other_id)
    finally:
        stack.tes_proxy.clear()
    assert held["left"] == 0
    nonces = {t["tags"]["yai.submit_nonce"] for t in (dropped, slow, other)}
    assert len(nonces) == 3  # does not match another run's nonce
    assert slow["tags"]["yai.run_id"] == slow_id and other["tags"]["yai.run_id"] == other_id
    assert httpx.get(stack.wes_url + f"/runs/{slow_id}", headers=alice).status_code == 200


def test_NEG_LONG_RUN(stack):
    """A run longer than the Passport lifetime -> a step submitted after expiry (drs:// input)
    is SYSTEM_ERROR, with the reason in system_logs. DRS decides (wes.md section 2): the DRS test
    clock is moved past the Passport expiry (7200 s),
    and DRS refuses during step 2 resolution."""
    local_only(stack)
    passport = stack.creds.passport("alice")
    doc = two_steps("data")  # step 2 input is drs:// (not step 1 output)
    params = {"script": 'sleep 2; cp "$0" a.txt', "data": drs_file(stack)}
    run_id = submit(stack, doc, params, passports=[passport])
    until(lambda: tasks(stack, run_id)[0].get("system_logs", [""])[0] == "state=RUNNING")
    httpx.post(stack.drs.root + "/yai/test/clock", json={"offset": 7300}).raise_for_status()
    try:
        log = wait(stack, run_id)
    finally:
        httpx.post(stack.drs.root + "/yai/test/clock", json={"offset": 0}).raise_for_status()
    assert log["state"] == "SYSTEM_ERROR", log["run_log"]
    assert any("input resolution failed" in s for s in log["run_log"]["system_logs"])
    first, second = tasks(stack, run_id)
    assert first["exit_code"] == 0 and "tes_uri" not in second  # step 1 done, step 2 not submitted


def test_NEG_SECRET_IN_LOG_e2e(stack):
    """Through the four-service flow, no Passport, AT or signed-URL signature appears in the logs
    of the 5 processes or
    in the DB of finished TES tasks (system.md section 8)."""
    local_only(stack)
    passport = stack.creds.passport("alice")
    alice = stack.creds.at("alice", "yai-wes")["Authorization"].split()[1]
    service = stack.creds.service("yai-tes")["Authorization"].split()[1]
    params = {"script": 'sleep 2; cp "$0" a.txt', "data": drs_file(stack)}
    stack.tes_proxy.clear()
    run_id = submit(stack, two_steps("first/out"), params, passports=[passport])
    until(lambda: tasks(stack, run_id)[0].get("system_logs", [""])[0] == "state=RUNNING")
    # the signed URL as WES sent it to TES (seen by the relay); once the inputs are fetched
    # (RUNNING), TES no longer holds its query part (D-068)
    (sent,) = [s for s in stack.tes_proxy.requests(method="POST", contains="/tasks")
               if s.json().get("tags", {}).get("yai.run_id") == run_id]  # fmt: skip
    url = sent.json()["inputs"][0]["url"]
    query = httpx.URL(url).query.decode()
    assert "sig=" in query, url
    sig = httpx.QueryParams(query)["sig"]
    (running,) = tes_tasks_of_run(stack, run_id)
    assert running["inputs"][0]["url"] == url.split("?")[0]
    assert wait(stack, run_id)["state"] == "COMPLETE"
    logs = "".join(p.log() for p in (stack.aai, stack.drs, stack.tes, stack.worker, stack.wes))
    secrets = {"passport": passport, "passport payload": passport.split(".")[1],
               "passport signature": passport.split(".")[2], "alice AT": alice,
               "service AT": service, "signature": sig, "query": query}  # fmt: skip
    for name, value in secrets.items():
        assert value not in logs, name
    db = (Path(stack.tcfg["data_dir"]) / "tes.sqlite").read_bytes()
    assert sig.encode() not in db and passport.split(".")[2].encode() not in db
    for t in tes_tasks_of_run(stack, run_id):
        assert sig not in json.dumps(t)


# --- security fixes (D-041): the attacks as they are ---


def tes_task(stack, body: dict, who: str = "alice") -> dict:
    """Submit a task directly to TES as alice, wait until it ends and return the FULL view."""
    at = stack.creds.at(who, "yai-tes")
    r = httpx.post(stack.tes_url + "/tasks", json=body, headers=at)
    assert r.status_code == 200, r.text
    tid = r.json()["id"]
    url = stack.tes_url + f"/tasks/{tid}"
    until(lambda: httpx.get(url, headers=at).json()["state"] in TERMINAL, timeout=90)
    return httpx.get(url, headers=at, params={"view": "FULL"}).json()


def test_NEG_CONTENT_AFTER_END_e2e(stack):
    """D-043: FULL of a finished TES task omits inputs[].content (the content was usable for the
    run).
    path and name stay."""
    body = {"inputs": [{"name": "lit", "path": "/data/in.txt", "content": "CANARY-e2e\n"}],
            "executors": [{"image": "alpine:3.20",
                           "command": ["cat", "/data/in.txt"]}]}  # fmt: skip
    t = tes_task(stack, body)
    assert t["state"] == "COMPLETE", t
    assert t["logs"][-1]["logs"][-1]["stdout"] == "CANARY-e2e\n"
    assert t["inputs"] == [{"name": "lit", "path": "/data/in.txt", "type": "FILE"}]


def test_NEG_IMAGE_OPTION_e2e(stack):
    """D-041: an image starting with `-` gets 400 at TES intake, and a CWL dockerPull gets 400 at
    WES intake
    (never becomes a docker run option)."""
    alice = stack.creds.at("alice", "yai-tes")
    for bad in ("--privileged", "-v", "alpine --privileged"):
        task = {"executors": [{"image": bad, "command": ["sh", "-c", "id"]}]}
        r = httpx.post(stack.tes_url + "/tasks", json=task, headers=alice)
        assert r.status_code == 400 and "image" in r.json()["msg"], bad
        submit(stack, SH.replace("alpine:3.20", bad), {"script": "id"}, status=400)


@pytest.mark.docker
def test_NEG_SYMLINK_STDOUT_docker(stack):
    """D-041: the first executor places at the stdout location a link to another task's published
    output (writable by the runner); the second writes there -> the published output is
    unchanged and stdout becomes a regular file."""
    local_only_docker(stack)
    root = stack.output_root or str(stack.files_root)
    nonce = secrets.token_hex(4)
    victim_url = f"file://{root}/sec/{nonce}/victim.txt"
    done = tes_task(stack, {
        "executors": [{"image": "alpine:3.20", "command": ["sh", "-c", "echo victim > /o/v"]}],
        "outputs": [{"path": "/o/v", "url": victim_url}],
    })  # fmt: skip
    assert done["state"] == "COMPLETE", done
    assert read_output(stack, victim_url) == "victim\n"
    target = victim_url.removeprefix("file://")  # path seen by the runner
    done = tes_task(stack, {
        "volumes": ["/data"],
        "executors": [
            {"image": "alpine:3.20", "command": ["ln", "-s", target, "/data/o"]},
            {"image": "alpine:3.20", "command": ["echo", "pwned"], "stdout": "/data/o"},
            {"image": "alpine:3.20", "command": ["sh", "-c", "test -L /data/o || cat /data/o"]},
        ],
    })  # fmt: skip
    assert read_output(stack, victim_url) == "victim\n"  # did not write through the link
    assert done["state"] == "COMPLETE", done
    logs = done["logs"][0]["logs"]
    assert logs[1]["stdout"] == "pwned\n" and logs[2]["stdout"] == "pwned\n"  # regular files


@pytest.mark.docker
def test_NEG_CANCEL_NO_COPY_docker(stack):
    """On cancel, outputs the executor wrote are not copied to storage nor to the declared stdout
    (D-041 (2)). With the real Docker runner, cancel a container that writes output then waits."""
    local_only_docker(stack)
    root = stack.output_root or str(stack.files_root)
    url = f"file://{root}/sec/{secrets.token_hex(4)}/out.txt"
    at = stack.creds.at("alice", "yai-tes")
    body = {"executors": [{"image": "alpine:3.20", "stdout": "/o/log",
                           "command": ["sh", "-c", "echo x > /o/out; echo started; sleep 60"]}],
            "outputs": [{"path": "/o/out", "url": url},
                        {"path": "/o/log", "url": url + ".log"}]}  # fmt: skip
    r = httpx.post(stack.tes_url + "/tasks", json=body, headers=at)
    assert r.status_code == 200, r.text
    task_url = stack.tes_url + f"/tasks/{r.json()['id']}"
    until(lambda: httpx.get(task_url, headers=at).json()["state"] == "RUNNING", timeout=90)
    time.sleep(2)  # until the container writes its output
    assert httpx.post(task_url + ":cancel", headers=at).status_code == 200
    until(lambda: httpx.get(task_url, headers=at).json()["state"] in TERMINAL, timeout=90)
    t = httpx.get(task_url, headers=at, params={"view": "FULL"}).json()
    assert t["state"] == "CANCELED", t
    out_dir = Path(read_path(stack, url)).parent
    assert not out_dir.exists() or list(out_dir.iterdir()) == []  # no output, no temporary name


@pytest.mark.docker
def test_NEG_FIFO_OUTPUT_docker(stack):
    """The container leaves a FIFO at an output and exits normally
    -> publishing copy does not block, SYSTEM_ERROR. The worker slot is freed and the next task
    runs."""
    local_only_docker(stack)
    root = stack.output_root or str(stack.files_root)
    url = f"file://{root}/sec/{secrets.token_hex(4)}/fifo.txt"
    done = tes_task(stack, {
        "executors": [{"image": "alpine:3.20", "command": ["mkfifo", "/o/f"]}],
        "outputs": [{"path": "/o/f", "url": url}],
    })  # fmt: skip
    assert done["state"] == "SYSTEM_ERROR", done
    assert "not a regular file" in " ".join(done["logs"][0].get("system_logs") or [])
    after = tes_task(stack, {"executors": [{"image": "alpine:3.20", "command": ["true"]}]})
    assert after["state"] == "COMPLETE", after


def test_NEG_YAML_ALIAS_e2e(stack):
    """A CWL using aliases gets 400 at WES intake (not expanded)."""
    bomb = "a0: &a0 [x]\n" + "".join(
        f"a{i}: &a{i} [{', '.join([f'*a{i - 1}'] * 9)}]\n" for i in range(1, 9)
    )
    submit(stack, bomb + SH, {"script": "true"}, status=400)


def test_NEG_BODY_CHUNKED_e2e(stack):
    """A body without Content-Length is also counted while read and stopped
    (DRS limit 1 MiB)."""

    def chunks():
        yield b'{"pad": "'
        for _ in range(2):
            yield b"x" * (1 << 20)
        yield b'"}'

    r = httpx.post(stack.drs_url + "/objects", content=chunks(),
                   headers={"content-type": "application/json"}, timeout=30)  # fmt: skip
    assert r.status_code == 413, r.text


@pytest.mark.docker
def test_NEG_SYMLINK_MOUNT_docker(stack):
    """Only output /safe/item is declared (the mount is the parent /safe). Even if the
    first executor turns item into a link to ../../.. (the work directory of all tasks),
    the second does not pick item itself as a mount source
    (in the second, /safe/item only points to / inside the container). The output is a link, so it
    is not copied."""
    local_only_docker(stack)
    root = stack.output_root or str(stack.files_root)
    url = f"file://{root}/sec/{secrets.token_hex(4)}/item"
    done = tes_task(stack, {
        "executors": [
            {"image": "alpine:3.20", "command": ["ln", "-s", "../../..", "/safe/item"]},
            {"image": "alpine:3.20", "command": ["ls", "/safe/item"]},
        ],
        "outputs": [{"path": "/safe/item", "url": url}],
    })  # fmt: skip
    listing = done["logs"][0]["logs"][1]["stdout"]
    assert "etc" in listing.split() and done["id"] not in listing  # container /, not work_dir
    assert done["state"] == "SYSTEM_ERROR"
    assert any("output /safe/item" in line for line in done["logs"][0]["system_logs"]), done
    with pytest.raises(FileNotFoundError):
        read_output(stack, url)


def until(cond, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while True:
        try:
            if cond():
                return
        except (IndexError, KeyError):
            pass
        if time.monotonic() > deadline:
            raise AssertionError("condition not met")
        time.sleep(0.2)


def local_only_docker(stack) -> None:
    if os.environ.get("YAI_E2E_DOCKER") != "1":
        pytest.skip("compose only: the real Docker runner exists only in compose")
