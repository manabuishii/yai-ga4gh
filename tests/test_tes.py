"""Exercise yai-tes (TES 1.1) over HTTP. Obligations TES-*, TES rows of test-plan.md section 4.

The runner is a local process (D-024). Executor commands use /bin/sh (image is only validated).
"""

from __future__ import annotations

import json
import os
import socket
import time
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import httpx
import pytest

from tests import shape
from tests.servers import Server, Worker, free_port
from tests.stack import DS1, Creds, bearer, drs_config, register, start_aai, write_config
from yai.tes.runner import PathMap

TERMINAL = {"COMPLETE", "EXECUTOR_ERROR", "SYSTEM_ERROR", "CANCELED"}
SH = "/bin/sh"


def tes_config(aai, tmp: Path, drs_netloc: str, **extra) -> dict:
    iss = aai.root + "/"
    return {
        "data_dir": str(tmp / "tes-data"),
        "files_root": str(tmp / "tes-files"),
        "work_dir": str(tmp / "tes-work"),
        "auth": {
            "mode": "aai",
            "issuer": iss,
            "jwks_uri": iss + ".well-known/jwks.json",
            "audience": "yai-tes",
            "insecure_http_hosts": [f"127.0.0.1:{aai.port}"],
        },
        "fetch": {
            "allow": [],
            "insecure_http_hosts": [drs_netloc],
            "retries": 1,
            "backoff_seconds": 0.1,
        },
        **extra,
    }


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("tes")
    aai = start_aai(tmp)
    drs_port = free_port()
    dcfg = drs_config(aai, tmp, drs_port)
    dcfg_path = write_config(tmp / "drs.yaml", dcfg)
    data = tmp / "ds1.txt"
    data.write_bytes(b"hello from drs\n")
    ds1 = register(dcfg_path, data, "--policy", "dataset", "--dataset", DS1)
    drs = Server("drs", dcfg, tmp / "drs", port=drs_port).start()
    # Per-user limit off (0). Many tests in this module only create as alice without waiting,
    # and a slow runner would hit the default 16 and get 429. test_task_limit_is_429 covers it (N-1)
    cfg = tes_config(aai, tmp, f"127.0.0.1:{drs_port}", max_active_tasks_per_user=0)
    tes = Server("tes", cfg, tmp / "tes").start()
    worker = Worker(cfg, tmp / "worker").start()
    creds = Creds(aai)
    ns = SimpleNamespace(
        aai=aai,
        drs=drs,
        tes=tes,
        worker=worker,
        cfg=cfg,
        tmp=tmp,
        url=tes.url,
        ds1=ds1,
        creds=creds,
        files=Path(cfg["files_root"]).resolve(),
        alice=bearer(creds.at("alice", "yai-tes")),
        bob=bearer(creds.at("bob", "yai-tes")),
        carol=bearer(creds.at("carol", "yai-tes")),
        wes=bearer(creds.service_at("yai-tes")),
        alice_pp=creds.passport("alice"),
        iss=aai.root + "/",
    )
    yield ns
    worker.stop()
    tes.stop()
    drs.stop()
    aai.stop()


_n = [0]


def out_url(env, name: str = "out") -> str:
    _n[0] += 1
    return f"file://{env.files}/{name}-{_n[0]}-{time.time_ns()}.txt"


def task(*executors, **extra) -> dict:
    return {"executors": list(executors), **extra}


def sh(script: str, **extra) -> dict:
    return {"image": "alpine:3.20", "command": [SH, "-c", script], **extra}


def create(env, body, who=None) -> httpx.Response:
    r = httpx.post(env.url + "/tasks", json=body, headers=who or env.alice)
    shape.check("tes", r)
    return r


def get(env, tid, view="FULL", who=None) -> httpx.Response:
    r = httpx.get(env.url + f"/tasks/{tid}", params={"view": view}, headers=who or env.alice)
    shape.check("tes", r)
    return r


def wait(env, tid, who=None, states=TERMINAL, timeout=30.0) -> dict:
    deadline = time.monotonic() + timeout
    t: dict = {}
    while time.monotonic() < deadline:
        t = get(env, tid, who=who).json()
        if t["state"] in states:
            return t
        time.sleep(0.2)
    raise AssertionError(f"task {tid} stuck in {t.get('state')}")


def run(env, body, who=None, **kw) -> dict:
    r = create(env, body, who)
    assert r.status_code == 200, r.text
    return wait(env, r.json()["id"], who=who, **kw)


def file_of(url: str) -> bytes:
    return Path(url.removeprefix("file://")).read_bytes()


def listing(env, who=None, **params) -> httpx.Response:
    r = httpx.get(env.url + "/tasks", params=params, headers=who or env.alice)
    shape.check("tes", r)
    return r


# --- auth, service-info ---


def test_TES_AUTH_01(env):
    assert httpx.get(env.url + "/tasks").status_code == 401
    assert httpx.get(env.url + "/tasks", headers=bearer("nope")).status_code == 401
    assert listing(env).status_code == 200


def test_TES_AUTH_02(env):
    r = create(env, task(sh("true")), who=env.carol)
    assert r.status_code == 403  # valid but lacks tes.submit (NEG-AUTH-NOSCOPE)


def test_NEG_AUTH_WRONG_AUD(env):
    wes_aud = bearer(env.creds.at("alice", "yai-wes"))
    assert create(env, task(sh("true")), who=wes_aud).status_code == 401


def test_NEG_AUTH_KIND(env):
    assert create(env, task(sh("true")), who=bearer(env.alice_pp)).status_code == 401


def test_TES_SI_01(env):
    r = httpx.get(env.url + "/service-info")
    shape.check("tes", r)
    si = r.json()
    assert si["type"]["artifact"] == "tes"
    assert si["storage"] and si["tesResources_backend_parameters"] == []
    assert "tags" not in si  # D-040: no tags in TES service-info (py-tes 1.1.4 rejects them)
    assert "local-process" in si["description"]  # runner kind goes in description


# --- CreateTask ---


def test_TES_CRT_01(env):
    assert create(env, {"name": "x"}).status_code == 400
    assert create(env, task({"command": ["true"]})).status_code == 400
    assert create(env, task({"image": "alpine"})).status_code == 400
    assert create(env, task({"image": "alpine", "command": []})).status_code == 400
    assert create(env, task(sh("true"))).status_code == 200


def test_TES_CRT_02(env):
    tid = create(env, task(sh("true"))).json()["id"]
    assert tid.startswith("t") and len(tid) == 27


def test_TES_CRT_03(env):
    for bad in (sh("true", stdout="out.txt"), sh("true", stdin="in"), sh("true", workdir="w")):
        assert create(env, task(bad)).status_code == 400
    rel_input = task(sh("true"), inputs=[{"path": "rel", "content": "x"}])
    assert create(env, rel_input).status_code == 400
    ok = task(sh("true", stdout="/tmp/o"), inputs=[{"path": "/d/in", "content": "x"}])
    assert create(env, ok).status_code == 200


def test_NEG_IMAGE_OPTION(env):
    """D-041 (security review): image follows Docker reference grammar only. Names starting
    with `-`, with spaces or uppercase get 400 (never read as docker run options).
    Spec examples pass."""
    for bad in ("--privileged", "-v", "alpine --privileged", " alpine:3.20", "Alpine", "a:b:c",
                "alpine:", "alpine@sha256:short", "", "alpine\n"):  # fmt: skip
        r = create(env, task(sh("true", image=bad)))
        assert r.status_code == 400, bad
        assert "executors[0].image" in r.json()["msg"]
    for ok in ("ubuntu", "quay.io/aptible/ubuntu", "gcr.io/my-org/my-image",
               "myregistryhost:5000/fedora/httpd:version1.0", "alpine:3.20",
               "quay.io/biocontainers/multiqc:1.35--pyhdfd78af_2",
               "quay.io/ohsu-comp-bio/funnel:v0.11.14@sha256:" + "8e10ba4de436b058bed954271" * 3,
               "localhost:5000/x", "[::1]:5000/x/y_z.w"):  # fmt: skip
        assert create(env, task(sh("true", image=ok))).status_code == 200, ok


def test_NEG_PATH_COLON(env):
    """D-041: container paths cannot contain `:` (the separator in `-v host:container:mode`)."""
    for bad in (sh("true", stdout="/d/o:rw"), sh("true", workdir="/d:z")):
        assert create(env, task(bad)).status_code == 400
    assert create(env, task(sh("true"), volumes=["/d:rshared"])).status_code == 400
    colon_input = task(sh("true"), inputs=[{"path": "/d/a:b", "content": "x"}])
    assert create(env, colon_input).status_code == 400


def test_NEG_PATH(env):
    for path in ("/data/../etc/x", "/proc/self/x", "/etc/passwd", "/dev/null", "/"):
        body = task(sh("true"), inputs=[{"path": path, "content": "x"}])
        assert create(env, body).status_code == 400, path
        body = task(sh("true"), outputs=[{"path": path, "url": out_url(env)}])
        assert create(env, body).status_code == 400, path


def test_TES_CRT_04(env):
    assert create(env, task(sh("true"), inputs=[{"path": "/d/in"}])).status_code == 400
    url = out_url(env)
    inp = {"path": "/d/in", "content": "from content", "url": "https://not-allowed.example/x"}
    body = task(sh("cat /d/in > /d/out"), inputs=[inp], outputs=[{"path": "/d/out", "url": url}])
    t = run(env, body)
    assert t["state"] == "COMPLETE"
    assert "url" not in t["inputs"][0]  # url is ignored when content is given
    assert file_of(url) == b"from content"


def test_TES_CRT_05(env):
    content = "A" * (128 * 1024)
    url = out_url(env)
    body = task(
        sh("cat /d/in > /d/out"),
        inputs=[{"path": "/d/in", "content": content}],
        outputs=[{"path": "/d/out", "url": url}],
    )
    assert run(env, body)["state"] == "COMPLETE"
    assert len(file_of(url)) == 128 * 1024


def test_TES_CRT_07(env):
    res = {"backend_parameters": {"VmSize": "big"}, "backend_parameters_strict": False}
    t = run(env, task(sh("true"), resources=res))
    assert "backend_parameters" not in t["resources"]
    assert any("backend_parameters ignored" in s for s in t["logs"][0]["system_logs"])
    strict = {"backend_parameters": {"VmSize": "big"}, "backend_parameters_strict": True}
    assert create(env, task(sh("true"), resources=strict)).status_code == 400


def test_TES_CRT_08(env):
    body = task(
        sh("true"),
        id="tmine",
        state="COMPLETE",
        creation_time="2000-01-01T00:00:00Z",
        logs=[{"logs": [], "outputs": []}],
    )
    tid = create(env, body).json()["id"]
    assert tid != "tmine"
    t = get(env, tid).json()
    assert t["creation_time"] != "2000-01-01T00:00:00Z"


# --- execution ---


def test_TES_RUN_01(env):
    url = out_url(env)
    first_fails = task(
        sh("exit 3"), sh("echo ran > /d/out"), outputs=[{"path": "/d/out", "url": url}]
    )
    t = run(env, first_fails)
    assert t["state"] == "EXECUTOR_ERROR"
    assert len(t["logs"][0]["logs"]) == 1 and t["logs"][0]["logs"][0]["exit_code"] == 3
    url2 = out_url(env)
    ignored = task(
        sh("exit 3", ignore_error=True),
        sh("echo ran > /d/out"),
        outputs=[{"path": "/d/out", "url": url2}],
    )
    t = run(env, ignored)
    assert t["state"] == "COMPLETE" and file_of(url2) == b"ran\n"


def test_TES_RUN_02(env):
    url = out_url(env)
    body = task(
        sh("ls -A /vol | wc -l > /d/out"),
        volumes=["/vol"],
        outputs=[{"path": "/d/out", "url": url}],
    )
    assert run(env, body)["state"] == "COMPLETE"
    assert file_of(url).strip() == b"0"  # starts as an empty directory


def test_TES_RUN_04(env):
    url = out_url(env)
    body = task(
        sh("cat /d/in > /vol/x"),
        sh("cat /vol/x /d/in > /d/out"),
        volumes=["/vol"],
        inputs=[{"path": "/d/in", "content": "in\n"}],
        outputs=[{"path": "/d/out", "url": url}],
    )
    assert run(env, body)["state"] == "COMPLETE"
    assert file_of(url) == b"in\nin\n"


def test_TES_VOLUMES_2EXEC(env):
    test_TES_RUN_04(env)
    body = task(sh("true"), volumes=["/d"], inputs=[{"path": "/d/in", "content": "x"}])
    assert create(env, body).status_code == 400  # volume overlaps an input


def test_nested_declared_paths_rewrite_once(env):
    """When workdir and an output are parent/child, an output path in args is not
    rewritten twice."""
    url = out_url(env)
    body = task(
        sh("echo hi > /yai/work/out.txt", workdir="/yai/work"),
        outputs=[{"path": "/yai/work/out.txt", "url": url}],
    )
    assert run(env, body)["state"] == "COMPLETE"
    assert file_of(url) == b"hi\n"


def test_same_path_to_two_urls(env):
    """One output source to two destinations: both are placed."""
    u1, u2 = out_url(env), out_url(env)
    body = task(sh("echo two > /d/out"),
                outputs=[{"path": "/d/out", "url": u1}, {"path": "/d/out", "url": u2}])  # fmt: skip
    t = run(env, body)
    assert t["state"] == "COMPLETE", t
    assert file_of(u1) == file_of(u2) == b"two\n"
    assert len(t["logs"][0]["outputs"]) == 2


BAD_FIELDS = [
    {"name": {}},
    {"name": 1},
    {"description": []},
    {"inputs": ""},
    {"inputs": {}},
    {"inputs": 0},
    {"inputs": [{"path": "/d/i", "url": 1}]},
    {"inputs": [{"path": "/d/i", "url": "https://h:bad/x"}]},
    {"inputs": [{"path": "/d/i", "url": "https://[::1/x"}]},
    {"inputs": [{"path": "/d/i", "url": "file://[::1/x"}]},
    {"inputs": [{"path": "/d/i", "content": "x", "name": 3}]},
    {"outputs": ""},
    {"outputs": 0},
    {"outputs": [{"path": "/d/o", "url": "file://[::1/x"}]},
    {"volumes": ""},
    {"volumes": {}},
    {"resources": []},
    {"resources": 0},
    {"resources": {"backend_parameters": 1}},
    {"resources": {"backend_parameters": []}},
    {"resources": {"backend_parameters_strict": "yes"}},
    {"resources": {"preemptible": "no"}},
    {"resources": {"zones": "a"}},
    {"resources": {"zones": [1]}},
    {"resources": {"disk_gb": "1"}},
    {"tags": []},
    {"tags": ""},
]


@pytest.mark.parametrize("extra", BAD_FIELDS, ids=[str(b) for b in BAD_FIELDS])
def test_wrong_types_are_400(env, extra):
    """Wrong types, false values and broken URLs get 400, not 500 or success."""
    r = create(env, task(sh("true"), **extra))
    assert r.status_code == 400, r.text


def test_path_rewrite_is_one_pass(tmp_path):
    pm = PathMap(tmp_path / "w" / "t1" / "fs")
    for p in ("/d", "/d/out", "/w"):
        pm.add(p)
    root = str(pm.fs_root)
    assert pm.rewrite("/d/out") == f"{root}/d/out"
    assert pm.rewrite("x=/d/out:/d") == f"x={root}/d/out:{root}/d"
    assert pm.rewrite("/w/a") == f"{root}/w/a"  # /w and /d inside a host path are not re-mapped


def test_TES_RUN_03(env):
    body = task(
        sh("true"),
        inputs=[{"path": "/d/in", "content": "x"}],
        outputs=[{"path": "/d/o", "url": out_url(env)}],
    )
    t = get(env, create(env, body).json()["id"]).json()
    assert t["inputs"][0]["type"] == "FILE" and t["outputs"][0]["type"] == "FILE"


def test_TES_ST_01(env):
    url = out_url(env)
    t = run(env, task(sh("echo ok > /d/out"), outputs=[{"path": "/d/out", "url": url}]))
    assert t["state"] == "COMPLETE"
    assert t["logs"][0]["outputs"] == [{"url": url, "path": "/d/out", "size_bytes": "3"}]
    # negative case: with unwritable storage, exit 0 from the executor does not mean COMPLETE
    locked = env.files / f"locked-{time.time_ns()}"
    locked.mkdir()
    lurl = f"file://{locked}/out.txt"
    body = task(sh("sleep 1; echo ok > /d/out"), outputs=[{"path": "/d/out", "url": lurl}])
    tid = create(env, body).json()["id"]
    locked.chmod(0o500)
    try:
        t = wait(env, tid)
    finally:
        locked.chmod(0o700)
    assert t["state"] == "SYSTEM_ERROR"
    assert t["logs"][0]["logs"][0]["exit_code"] == 0
    assert "yai.missing_outputs" not in t["logs"][0]["metadata"]  # unwritable is not "missing"


def test_TES_ST_02(env):
    assert run(env, task(sh("exit 1")))["state"] == "EXECUTOR_ERROR"
    missing = run(env, task(sh("true"), outputs=[{"path": "/d/none", "url": out_url(env)}]))
    assert missing["state"] == "SYSTEM_ERROR"
    # marker that the source file was missing (WES maps it to EXECUTOR_ERROR, D-026)
    assert missing["logs"][0]["metadata"]["yai.missing_outputs"] == "/d/none"


def test_TES_ST_03(env):
    tid = create(env, task(sh("sleep 30"))).json()["id"]
    wait(env, tid, states={"RUNNING"})
    r = httpx.post(env.url + f"/tasks/{tid}:cancel", headers=env.alice)
    shape.check("tes", r)
    assert r.status_code == 200 and r.json() == {}
    assert get(env, tid).json()["state"] in ("CANCELING", "CANCELED")
    assert wait(env, tid)["state"] == "CANCELED"


def test_TES_ST_04(env):
    states = {
        "UNKNOWN", "QUEUED", "INITIALIZING", "RUNNING", "PAUSED", "COMPLETE",
        "EXECUTOR_ERROR", "SYSTEM_ERROR", "CANCELED", "CANCELING", "PREEMPTED",
    }  # fmt: skip
    for t in listing(env).json()["tasks"]:
        assert t["state"] in states and t["state"] != ""


def test_NEG_CANCEL_DONE(env):
    tid = run(env, task(sh("true")))["id"]
    for _ in range(2):
        assert httpx.post(env.url + f"/tasks/{tid}:cancel", headers=env.alice).status_code == 200
    assert get(env, tid).json()["state"] == "COMPLETE"


# --- list, view ---


def test_TES_LST_01(env):
    assert listing(env, page_size=2048).status_code == 400
    assert listing(env, page_size=0).status_code == 400
    assert listing(env, page_size=2047).status_code == 200


def test_TES_LST_02(env):
    tag = f"k{time.time_ns()}"
    ids = {}
    cases = [
        ("p-a", {tag: "bar"}),
        ("p-b", {tag: "bat"}),
        ("q-c", {tag: ""}),
        ("q-d", {tag: "bar", "baz": "bat"}),
        ("q-e", {}),
    ]
    for name, tags in cases:
        ids[name] = create(env, task(sh("true"), name=f"{tag}-{name}", tags=tags)).json()["id"]

    def names(**params):
        tasks = listing(env, view="BASIC", **params).json()["tasks"]
        return {t["name"].split("-", 1)[1] for t in tasks if t["name"].startswith(tag)}

    assert names(name_prefix=f"{tag}-p") == {"p-a", "p-b"}
    assert names(tag_key=tag, tag_value="bar") == {"p-a", "q-d"}
    assert names(tag_key=tag) == {"p-a", "p-b", "q-c", "q-d"}  # empty value: key only
    assert names(tag_key=[tag, "baz"], tag_value=["bar", "bat"]) == {"q-d"}
    done = wait(env, ids["q-e"])
    complete = listing(env, state="COMPLETE", page_size=2000).json()["tasks"]
    assert done["id"] in {t["id"] for t in complete}


def page_all(env, who, **params):
    seen, token = [], None
    while True:
        q = {"page_size": 3, **params}
        if token:
            q["page_token"] = token
        r = listing(env, who, **q)
        assert r.status_code == 200, r.text
        body = r.json()
        seen += [t["id"] for t in body["tasks"]]
        token = body.get("next_page_token")
        if not token:
            return seen, body


def test_TES_LST_03(env):
    tag = f"pg{time.time_ns()}"
    made = [create(env, task(sh("true"), tags={tag: ""})).json()["id"] for _ in range(7)]
    seen, last = page_all(env, env.alice, tag_key=tag)
    assert sorted(seen) == sorted(made) and len(seen) == len(set(seen))
    assert "next_page_token" not in last  # not returned at the end (G-X-04)


def test_NEG_PAGING_SNAPSHOT(env):
    tag = f"ps{time.time_ns()}"
    made = [create(env, task(sh("true"), tags={tag: ""})).json()["id"] for _ in range(5)]
    r = listing(env, page_size=2, tag_key=tag)
    first = [t["id"] for t in r.json()["tasks"]]
    first_token = r.json()["next_page_token"]
    create(env, task(sh("true"), tags={tag: ""}))  # created mid-way
    rest, token = [], first_token
    while token:
        r = listing(env, page_size=2, tag_key=tag, page_token=token)
        rest += [t["id"] for t in r.json()["tasks"]]
        token = r.json().get("next_page_token")
    assert sorted(first + rest) == sorted(made)  # no duplicates, gaps or newcomers
    changed = listing(env, page_size=2, tag_key="other", page_token=first_token)
    assert changed.status_code == 400  # filter changed


def test_NEG_PAGING_TAMPER(env):
    for _ in range(3):
        create(env, task(sh("true")))
    token = listing(env, page_size=1).json()["next_page_token"]
    for bad in (token[:-1] + ("0" if token[-1] != "0" else "1"), "garbage", "x.y"):
        r = listing(env, page_size=1, page_token=bad)
        assert r.status_code == 400
        shape.check_error_shape(r)
    assert listing(env, env.bob, page_size=1, page_token=token).status_code == 400  # other user


def test_TES_VIEW_01(env):
    t = run(env, task(sh("echo hi; echo err >&2"), inputs=[{"path": "/d/in", "content": "c"}]))
    tid = t["id"]
    minimal = httpx.get(env.url + f"/tasks/{tid}", headers=env.alice).json()  # default MINIMAL
    assert set(minimal) == {"id", "state"}
    basic = get(env, tid, "BASIC").json()
    assert "content" not in basic["inputs"][0]
    assert "system_logs" not in basic["logs"][0]
    assert "stdout" not in basic["logs"][0]["logs"][0]
    full = get(env, tid, "FULL").json()
    assert "content" not in full["inputs"][0]  # removed when finished (D-043); running: next test
    assert full["logs"][0]["logs"][0]["stdout"] == "hi\n"
    assert full["logs"][0]["logs"][0]["stderr"] == "err\n"
    assert get(env, tid, "WRONG").status_code == 400


def content_task(script: str, env, **extra) -> dict:
    return task(sh(script), inputs=[{"name": "lit", "path": "/d/in", "content": "CANARY-content"},
                                    {"name": "ref", "path": "/d/ref", "url": signed_url(env)}],
                **extra)  # fmt: skip


def assert_content_dropped(env, t: dict) -> None:
    lit, ref = t["inputs"]
    assert lit == {"name": "lit", "path": "/d/in", "type": "FILE"}
    assert set(ref) == {"name", "path", "type", "url"} and ref["url"].startswith("http")
    listed = [t.get("inputs") for t in listing(env, view="FULL").json()["tasks"]]
    # FULL in the list is the same (stdout is task output, so not checked)
    assert "CANARY-content" not in json.dumps(listed)


def test_full_keeps_input_content_while_running(env):
    """D-043: FULL returns inputs[].content while running (BASIC does not). Once canceled to
    CANCELED, the field is not returned at all."""
    tid = create(env, content_task("sleep 30", env)).json()["id"]
    wait(env, tid, states={"RUNNING"})
    assert get(env, tid, "FULL").json()["inputs"][0]["content"] == "CANARY-content"
    assert "content" not in get(env, tid, "BASIC").json()["inputs"][0]
    assert httpx.post(env.url + f"/tasks/{tid}:cancel", headers=env.alice).status_code == 200
    t = wait(env, tid)
    assert t["state"] == "CANCELED"
    assert_content_dropped(env, t)


@pytest.mark.parametrize("script, end", [("cat /d/in /d/ref", "COMPLETE"),
                                         ("exit 3", "EXECUTOR_ERROR"),
                                         ("true", "SYSTEM_ERROR")])  # fmt: skip
def test_full_drops_input_content_when_ended(env, script, end):
    """D-043: once finished (COMPLETE, EXECUTOR_ERROR, SYSTEM_ERROR), FULL omits the
    inputs[].content field. path, name, type and url stay. SYSTEM_ERROR uses a missing output."""
    outs = [{"path": "/d/none", "url": out_url(env)}] if end == "SYSTEM_ERROR" else []
    t = run(env, content_task(script, env, outputs=outs))
    assert t["state"] == end
    if end == "COMPLETE":
        # the content was usable for the run
        assert t["logs"][0]["logs"][0]["stdout"] == "CANARY-contenthello from drs\n"
    assert_content_dropped(env, t)


# --- user isolation, delegation ---


def test_NEG_OTHER_USER(env):
    tid = create(env, task(sh("sleep 5"))).json()["id"]
    assert get(env, tid, who=env.bob).status_code == 404
    assert httpx.post(env.url + f"/tasks/{tid}:cancel", headers=env.bob).status_code == 404
    seen, _ = page_all(env, env.bob)
    assert tid not in seen
    httpx.post(env.url + f"/tasks/{tid}:cancel", headers=env.alice)


def test_NEG_TAG_SPOOF(env):
    obo = {"yai.on_behalf_of": f"{env.iss}|bob"}
    assert create(env, task(sh("true"), tags=obo)).status_code == 400
    assert create(env, task(sh("true"), tags={"yai.run_id": "r1"})).status_code == 400
    assert create(env, task(sh("true"), tags=obo), who=env.wes).status_code == 200


def test_delegated_task_visibility(env):
    """A task the WES service submitted on behalf of alice.

    alice can read it but not cancel it (403); bob cannot see it (404)."""
    tags = {"yai.on_behalf_of": f"{env.iss}|alice", "yai.run_id": "rtest"}
    tid = create(env, task(sh("sleep 5"), tags=tags), who=env.wes).json()["id"]
    assert get(env, tid).status_code == 200
    assert get(env, tid, who=env.bob).status_code == 404
    assert httpx.post(env.url + f"/tasks/{tid}:cancel", headers=env.alice).status_code == 403
    assert httpx.post(env.url + f"/tasks/{tid}:cancel", headers=env.wes).status_code == 200
    assert wait(env, tid, who=env.wes)["state"] == "CANCELED"


def test_submit_nonce_is_idempotent(env):
    tags = {
        "yai.on_behalf_of": f"{env.iss}|alice",
        "yai.run_id": "rn",
        "yai.submit_nonce": f"n{time.time_ns()}",
    }
    a = create(env, task(sh("true"), tags=tags), who=env.wes).json()["id"]
    b = create(env, task(sh("true"), tags=tags), who=env.wes).json()["id"]
    assert a == b


def test_NEG_FILE_CROSS_TASK(env):
    url = out_url(env)
    made = run(env, task(sh("echo secret > /d/out"), outputs=[{"path": "/d/out", "url": url}]))
    assert made["state"] == "COMPLETE"
    as_input = task(sh("cat /d/in"), inputs=[{"path": "/d/in", "url": url}])
    assert create(env, as_input, who=env.bob).status_code == 400  # another user's output
    assert run(env, as_input)["state"] == "COMPLETE"  # the owner can read it
    overwrite = task(sh("true"), outputs=[{"path": "/d/o", "url": url}])
    assert create(env, overwrite).status_code == 400  # URL of an existing output
    outside = task(sh("true"), inputs=[{"path": "/d/in", "url": "file:///etc/passwd"}])
    assert create(env, outside).status_code == 400
    # a link under the root cannot escape it
    link = env.files / f"link-{time.time_ns()}"
    os.symlink("/etc", link)
    body = task(sh("echo x > /d/out"), outputs=[{"path": "/d/out", "url": f"file://{link}/x"}])
    assert run(env, body)["state"] == "SYSTEM_ERROR"


def test_NEG_SYMLINK_OUTPUT(env):
    url = out_url(env)
    body = task(sh("ln -s /etc/passwd /d/out"), outputs=[{"path": "/d/out", "url": url}])
    t = run(env, body)
    assert t["state"] == "SYSTEM_ERROR"
    assert not Path(url.removeprefix("file://")).exists()
    assert "yai.missing_outputs" not in t["logs"][0]["metadata"]  # a link is not "missing"


def test_NEG_LIMITS_api(env):
    """API part of NEG-LIMITS (enforcing resource limits waits on the Docker runner, D-024)."""
    bad = (
        {"cpu_cores": 0},
        {"cpu_cores": -1},
        {"cpu_cores": 3},
        {"ram_gb": 0},
        {"ram_gb": 4.5},
        {"cpu_cores": 1.5},
    )
    for res in bad:
        assert create(env, task(sh("true"), resources=res)).status_code == 400, res
    tid = create(env, task(sh("true"))).json()["id"]
    assert get(env, tid).json()["resources"] == {"cpu_cores": 1, "ram_gb": 2.0}


# --- input fetch (https, shared fetcher) ---


def signed_url(env) -> str:
    r = httpx.post(
        env.drs.url + f"/objects/{env.ds1}/access/a1", json={"passports": [env.alice_pp]}
    )
    return r.json()["url"]


def test_https_input_from_drs_signed_url(env):
    signed = signed_url(env)
    url = out_url(env)
    body = task(
        sh("cat /d/in > /d/out"),
        inputs=[{"path": "/d/in", "url": signed}],
        outputs=[{"path": "/d/out", "url": url}],
    )
    t = run(env, body)
    assert t["state"] == "COMPLETE" and file_of(url) == b"hello from drs\n"
    # after finishing, the query part is removed from URLs in the DB (system.md section 8)
    assert "?" not in t["inputs"][0]["url"]
    assert signed.split("?")[1] not in env.worker.log() + env.tes.log()


def test_https_input_denied_is_system_error(env):
    tampered = signed_url(env).replace("sig=", "sig=0")
    t = run(env, task(sh("cat /d/in"), inputs=[{"path": "/d/in", "url": tampered}]))
    assert t["state"] == "SYSTEM_ERROR"
    assert any("denied (403)" in s for s in t["logs"][0]["system_logs"])


def test_NEG_SSRF(env):
    for url in ("https://evil.example/x", "http://127.0.0.1:1/x", "ftp://x/y", "gs://b/k"):
        body = task(sh("true"), inputs=[{"path": "/d/in", "url": url}])
        assert create(env, body).status_code == 400, url


def test_NEG_FIFO_OUTPUT(env):
    """A FIFO at an output or at the next executor's
    stdin/stdout/stderr does not block the worker; it ends the task (SYSTEM_ERROR or
    EXECUTOR_ERROR) and can run the next task."""
    url = out_url(env)
    t = run(env, task(sh("mkfifo /d/out"), outputs=[{"path": "/d/out", "url": url}]), timeout=20)
    assert t["state"] == "SYSTEM_ERROR"
    assert "not a regular file" in " ".join(t["logs"][0].get("system_logs") or [])
    assert not Path(url.removeprefix("file://")).exists()
    for second in (sh("echo hi", stdout="/d/p"), sh("echo hi", stderr="/d/p"),
                   sh("cat", stdin="/d/p")):  # fmt: skip
        t = run(env, task(sh("mkfifo /d/p"), second), timeout=20)
        assert t["state"] in ("SYSTEM_ERROR", "EXECUTOR_ERROR"), second
    t = run(env, task(sh("rm /d/s; mkfifo /d/s", stdout="/d/s")), timeout=20)
    assert (
        t["state"] == "COMPLETE"
    )  # even if it swaps its own stdout for a FIFO, reading the tail at the end does not block
    assert run(env, task(sh("true")), timeout=20)["state"] == "COMPLETE"


def test_NEG_OUTPUT_EXISTS(env):
    """With a shared files_root, the intake reply does not reveal whether
    another user's file exists. 200 either way; if it exists it is not overwritten, SYSTEM_ERROR,
    with the same reason text as "not a directory" (D-044). Only overlap with one's own outputs
    gets 400 at intake."""
    taken = out_url(env, "bobs")
    t = run(env, task(sh("echo bob > /d/o"), outputs=[{"path": "/d/o", "url": taken}]), who=env.bob)
    assert t["state"] == "COMPLETE"
    blocked = out_url(env, "afile") + "/x"  # a file in the middle of the path
    Path(blocked.removeprefix("file://")).parent.write_text("")
    free = out_url(env, "free")
    reasons = []
    for url in (taken, blocked, free):
        body = task(sh("echo alice > /d/o"), outputs=[{"path": "/d/o", "url": url}])
        r = create(env, body)
        assert r.status_code == 200, r.text  # same reply whether it exists or not
        t = wait(env, r.json()["id"])
        reasons.append((t["state"], [s for s in t["logs"][0]["system_logs"] if "output" in s]))
    failed = ("SYSTEM_ERROR", ["output /d/o: cannot publish to the output url"])
    assert reasons[0] == reasons[1] == failed
    assert reasons[2] == ("COMPLETE", [])
    assert file_of(taken) == b"bob\n"  # not overwritten
    r = create(env, task(sh("true"), outputs=[{"path": "/d/o", "url": free}]))
    assert r.status_code == 400 and "already exists" in r.json()["msg"]  # own output


def read_response(sock: socket.socket) -> tuple[str, str]:
    """Read an HTTP/1.1 response up to Content-Length even if headers and body arrive separately."""
    data = b""
    while b"\r\n\r\n" not in data and (chunk := sock.recv(4096)):
        data += chunk
    head, _, body = data.partition(b"\r\n\r\n")
    length = next(int(v) for k, _, v in (h.partition(b":") for h in head.split(b"\r\n")[1:])
                  if k.strip().lower() == b"content-length")  # fmt: skip
    while len(body) < length and (chunk := sock.recv(4096)):
        body += chunk
    return head.decode(), body.decode()


def test_NEG_BODY_LIMIT(env):
    """The body is counted while read; over 64 MiB gets 400 before
    parsing (after auth, D-056; the unauthenticated case is test_NEG_BODY_AUTH_FIRST).
    Chunked requests without Content-Length are stopped too. Broken JSON gets 400."""
    from yai.tes.app import MAX_BODY

    u = urlsplit(env.url)
    host, port = u.hostname or "", u.port or 80
    auth = env.alice["Authorization"]
    with socket.create_connection((host, port), timeout=10) as sock:  # length only (after auth)
        sock.sendall(f"POST {u.path}/tasks HTTP/1.1\r\nHost: {host}\r\nAuthorization: {auth}\r\n"
                     f"Content-Type: application/json\r\nContent-Length: {MAX_BODY + 1}\r\n"
                     "\r\n{}".encode())  # fmt: skip
        head, body = read_response(sock)
    assert head.startswith("HTTP/1.1 400") and "larger than" in body

    def chunks():
        yield b'{"pad": "'
        for _ in range(MAX_BODY // (1 << 20) + 1):
            yield b"x" * (1 << 20)
        yield b'"}'

    r = httpx.post(
        env.url + "/tasks",
        content=chunks(),
        headers={**env.alice, "content-type": "application/json"},
    )
    assert r.status_code == 400 and "larger than" in r.json()["msg"]
    r = httpx.post(
        env.url + "/tasks", content=b"{", headers={**env.alice, "content-type": "application/json"}
    )
    assert r.status_code == 400 and "not valid JSON" in r.json()["msg"]


def test_NEG_BODY_AUTH_FIRST(env, tmp_path):
    """D-056: CreateTask checks auth and scope before the body. No auth gets 401 without reading
    the body (the reply comes before the 64 MiB body is sent). After a 401, even if the body is
    sent in full, the next request on the connection is handled correctly or the connection is
    closed (h11 discards the rest of the body before reading the next)."""
    from fastapi.testclient import TestClient

    from yai.tes.app import MAX_BODY, create_app

    u = urlsplit(env.url)
    host, port = u.hostname or "", u.port or 80
    with socket.create_connection((host, port), timeout=10) as sock:
        sock.sendall(f"POST {u.path}/tasks HTTP/1.1\r\nHost: {host}\r\n"
                     f"Content-Type: application/json\r\nContent-Length: {MAX_BODY}\r\n"
                     "\r\n".encode())  # fmt: skip
        head, _ = read_response(sock)  # if it waited for the body, this fails on timeout (10 s)
    assert head.startswith("HTTP/1.1 401")

    size = 1 << 20
    with socket.create_connection((host, port), timeout=10) as sock:
        sock.sendall(f"POST {u.path}/tasks HTTP/1.1\r\nHost: {host}\r\n"
                     f"Content-Type: application/json\r\nContent-Length: {size}\r\n"
                     "\r\n".encode())  # fmt: skip
        head, _ = read_response(sock)
        assert head.startswith("HTTP/1.1 401")
        # send failure, reset or EOF means "closed". Hanging without a reply fails on timeout
        # (socket.timeout does not count as "closed")
        try:
            sock.sendall(b" " * size)
            sock.sendall(f"GET {u.path}/service-info HTTP/1.1\r\nHost: {host}\r\n\r\n".encode())
            closed = sock.recv(1, socket.MSG_PEEK) == b""
        except (BrokenPipeError, ConnectionResetError):
            closed = True
        if not closed:
            head, body = read_response(sock)
            assert head.startswith("HTTP/1.1 200") and '"tes"' in body, head

    # same order with static-bearer: broken body without credentials gets 401 (was 400)
    tokens = {"t": {"sub": "a", "scopes": []}}
    cfg = {"data_dir": str(tmp_path / "tes"), "files_root": str(tmp_path / "files"),
           "auth": {"mode": "static-bearer", "tokens": tokens}}  # fmt: skip
    client = TestClient(create_app(cfg))
    r = client.post(
        "/ga4gh/tes/v1/tasks", content=b"{", headers={"content-type": "application/json"}
    )
    assert r.status_code == 401
    headers = {"Authorization": "Bearer t", "content-type": "application/json"}
    r = client.post("/ga4gh/tes/v1/tasks", content=b"{", headers=headers)
    assert r.status_code == 403  # the scope check also comes before the body


def test_NEG_TES_TASK_LIMIT(tmp_path):
    """N-1: unfinished tasks per user are capped at max_active, even with concurrent submits.
    Other users and delegated work (per on_behalf_of) are counted separately. A resubmit (same
    nonce) is not counted and returns
    the earlier id. Once one finishes, the next is accepted."""
    import threading

    from yai.tes.db import TaskLimit, TesDb

    db = TesDb(tmp_path / "tes.sqlite")

    def make(task_id: str, owner=("iss", "a"), obo=None, nonce=None) -> str | None:
        try:
            return db.create(task_id, owner, {}, 0, on_behalf_of=obo, run_id=None,
                             nonce=nonce, max_active=3)  # fmt: skip
        except TaskLimit:
            return None

    made: list[str | None] = []
    threads = [threading.Thread(target=lambda i=i: made.append(make(f"t{i}"))) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(m is not None for m in made) == 3
    assert make("other", ("iss", "b"))  # other users are not blocked
    svc = ("iss", "wes")
    assert make("d1", svc, "iss|a", "n1") == "d1"  # delegated work is separate from direct work
    assert make("d1-again", svc, "iss|a", "n1") == "d1"  # a resubmit returns the earlier id
    assert make("d2", svc, "iss|a") and make("d3", svc, "iss|a")
    assert make("d4", svc, "iss|a") is None and make("d5", svc, "iss|b")
    assert make("d1-retry", svc, "iss|a", "n1") == "d1"  # even at the limit, resubmit: earlier id
    done = next(m for m in made if m)
    assert db.transition(done, "QUEUED", "CANCELING")
    assert db.transition(done, "CANCELING", "CANCELED")
    assert make("next") and make("again") is None


def test_task_limit_is_429(tmp_path):
    """N-1: CreateTask returns 429 at the per-user limit; other users pass. No runner is started,
    so accepted tasks stay QUEUED."""
    from fastapi.testclient import TestClient

    from yai.tes.app import create_app

    cfg = {"data_dir": str(tmp_path / "tes"), "files_root": str(tmp_path / "files"),
           "max_active_tasks_per_user": 2,
           "auth": {"mode": "static-bearer", "tokens": {
               "ta": {"sub": "alice", "scopes": ["tes.submit"]},
               "tb": {"sub": "bob", "scopes": ["tes.submit"]}}}}  # fmt: skip
    client = TestClient(create_app(cfg))
    body = task({"image": "alpine", "command": ["true"]})

    def post(tok: str):
        auth = {"Authorization": f"Bearer {tok}"}
        return client.post("/ga4gh/tes/v1/tasks", json=body, headers=auth)

    assert [post("ta").status_code for _ in range(3)] == [200, 200, 429]
    r = post("ta")
    assert r.json() == {"msg": "too many unfinished tasks (at most 2)", "status_code": 429}
    assert post("tb").status_code == 200


def test_NEG_RAM_MIN(tmp_path):
    """D-060 (1), the tiny-ram_gb finding: ram_gb under 6 MiB (Docker's --memory minimum) gets
    400 at intake (int(ram * 1024) used to give --memory 0m = no limit). Exactly 6 MiB passes."""
    from fastapi.testclient import TestClient

    from yai.tes.app import create_app

    tokens = {"t": {"sub": "alice", "scopes": ["tes.submit"]}}
    cfg = {"data_dir": str(tmp_path / "tes"), "files_root": str(tmp_path / "files"),
           "auth": {"mode": "static-bearer", "tokens": tokens}}  # fmt: skip
    client = TestClient(create_app(cfg))
    auth = {"Authorization": "Bearer t"}
    for ram, code in ((0.0005, 400), (5 / 1024, 400), (6 / 1024, 200)):
        body = task({"image": "alpine", "command": ["true"]})
        body["resources"] = {"ram_gb": ram}
        r = client.post("/ga4gh/tes/v1/tasks", json=body, headers=auth)
        assert r.status_code == code, (ram, r.text)
