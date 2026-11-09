"""yai-tes restart and cancel races (TES part of NEG-RESTART and NEG-CANCEL-RACE,
test-plan.md section 4).

Stop the runner at a fault-injection point (the faults setting) and restart it without one.
Checks that tasks are not lost, do not run twice, and end in exactly one final state.
Auth uses the placeholder implementation (all public); the runner behavior is what matters here.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from tests.servers import Server, Worker
from tests.test_tes import file_of, sh, task, wait
from yai.common.safefs import CannotPublish, publish_beneath
from yai.tes.db import TesDb
from yai.tes.runner import LocalProcessRunner, Started, proc_start
from yai.tes.worker import ATTEMPT, TesWorker

POINTS = ("before_executor_start", "before_executor_record", "after_executor_start",
          "after_executor_finished", "before_output_record", "after_output_copy",
          "before_complete_write")  # fmt: skip


def make_env(tmp, fault: str | None, **extra) -> SimpleNamespace:
    cfg = {
        "data_dir": str(tmp / "tes-data"),
        "files_root": str(tmp / "tes-files"),
        "work_dir": str(tmp / "tes-work"),
        "auth": {"mode": "anonymous"},
        **extra,
    }
    tes = Server("tes", cfg, tmp / "tes").start()
    faulty = dict(cfg, faults={fault: "exit"}) if fault else cfg
    worker = Worker(faulty, tmp / "worker", name="worker-faulty").start()
    files = (tmp / "tes-files").resolve()
    return SimpleNamespace(tes=tes, worker=worker, cfg=cfg, url=tes.url, alice={}, files=files,
                           tmp=tmp)  # fmt: skip


def stop_all(env) -> None:
    env.worker.stop()
    env.tes.stop()


def submit(env, body) -> str:
    r = httpx.post(env.url + "/tasks", json=body)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def restart_worker(env) -> None:
    env.worker = Worker(env.cfg, env.tmp / "worker", name="worker-clean").start()


@pytest.mark.parametrize("point", POINTS)
def test_NEG_RESTART_tes(tmp_path, point):
    env = make_env(tmp_path, point)
    try:
        url = f"file://{env.files}/out.txt"
        # count runs outside the work directory (undeclared paths are not replaced;
        # survives the work directory being removed on restart)
        count = tmp_path / "count"
        script = f"sleep 1; echo once >> {count}; echo once >> /d/out"
        tid = submit(env, task(sh(script), outputs=[{"path": "/d/out", "url": url}]))
        assert env.worker.wait_exit(30) == 17  # crashed at the fault point
        restart_worker(env)
        t = wait(env, tid)
        assert t["state"] == "COMPLETE", t
        assert count.read_text() == "once\n"  # did not run twice
        assert file_of(url) == b"once\n"
        assert any("resuming" in s for s in t["logs"][0]["system_logs"])
    finally:
        stop_all(env)


@pytest.mark.parametrize("how", ["time_limit", "cannot_start"])
def test_restart_after_failed_executor_is_not_complete(tmp_path, how):
    """Executor failures (timeout, failed to start) are saved together with finished. After a
    restart, a missing exit code is not treated as success."""
    env = make_env(tmp_path, "after_executor_finished", max_runtime_seconds=1)
    try:
        ex = sh("sleep 5") if how == "time_limit" else sh("true", stdin="/d/missing")
        tid = submit(env, task(ex))
        assert env.worker.wait_exit(30) == 17
        restart_worker(env)
        t = wait(env, tid)
        assert t["state"] == "SYSTEM_ERROR", t
    finally:
        stop_all(env)


@pytest.mark.parametrize("how", ["cancel", "time_limit"])
def test_cancel_and_time_limit_publish_nothing(tmp_path, how):
    """NEG-CANCEL-NO-COPY: on cancel or timeout, outputs the executor wrote are not copied to
    storage (no leftover temporary names either). The work directory (including input files
    passed via content) is removed,
    and content is not kept (D-043)."""
    extra = {"max_runtime_seconds": 2} if how == "time_limit" else {}
    env = make_env(tmp_path, None, **extra)
    try:
        url = f"file://{env.files}/out.txt"
        body = task(sh("cp /d/in /d/out; sleep 30"), outputs=[{"path": "/d/out", "url": url}],
                    inputs=[{"path": "/d/in", "content": "CANARY-in\n"}])  # fmt: skip
        tid = submit(env, body)
        work = Path(env.cfg["work_dir"]) / tid
        wait(env, tid, states={"RUNNING"})
        deadline = time.monotonic() + 10
        while not (work / "fs" / "d" / "out").exists() and time.monotonic() < deadline:
            time.sleep(0.1)  # stop after the executor finishes writing its output
        assert (work / "fs" / "d" / "out").exists()
        if how == "cancel":
            assert httpx.post(env.url + f"/tasks/{tid}:cancel").status_code == 200
        t = wait(env, tid)
        assert t["state"] == ("CANCELED" if how == "cancel" else "SYSTEM_ERROR"), t
        deadline = time.monotonic() + 10
        while work.exists() and time.monotonic() < deadline:
            time.sleep(0.1)  # remove right after the final state is written (finish)
        assert not work.exists()
        assert list(env.files.iterdir()) == []  # neither out.txt nor a temporary name
        assert t["inputs"] == [{"path": "/d/in", "type": "FILE"}]
    finally:
        stop_all(env)


def test_restart_after_terminal_write_cleans_up(tmp_path):
    """Crash after writing the final state, before cleanup -> restart removes the work dir (10)."""
    env = make_env(tmp_path, "before_cleanup")
    try:
        tid = submit(env, task(sh("echo x")))
        assert env.worker.wait_exit(30) == 17
        work = Path(env.cfg["work_dir"]) / tid
        assert work.exists()
        assert httpx.get(env.url + f"/tasks/{tid}").json()["state"] == "COMPLETE"
        restart_worker(env)
        deadline = time.monotonic() + 20
        while work.exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        assert not work.exists()
    finally:
        stop_all(env)


def test_terminal_write_redacts_input_urls(tmp_path):
    """The same write as the final state removes the query part of input URLs (no window where
    it remains after finishing)."""
    db = TesDb(tmp_path / "tes.sqlite")
    body = {"inputs": [{"path": "/d/in", "url": "https://h.test/x?sig=CANARY"}]}
    db.create("t1", ("i", "s"), body, 0, on_behalf_of=None, run_id=None, nonce=None)
    assert db.transition("t1", "QUEUED", "INITIALIZING")
    assert "CANARY" in db.get("t1").task["inputs"][0]["url"]  # type: ignore[union-attr]
    assert db.transition("t1", "INITIALIZING", "SYSTEM_ERROR")
    assert db.get("t1").task["inputs"][0]["url"] == "https://h.test/x"  # type: ignore[union-attr]


ENDS = {"COMPLETE": ["INITIALIZING", "RUNNING", "COMPLETE"],
        "EXECUTOR_ERROR": ["INITIALIZING", "RUNNING", "EXECUTOR_ERROR"],
        "SYSTEM_ERROR": ["INITIALIZING", "SYSTEM_ERROR"],
        "CANCELED": ["INITIALIZING", "RUNNING", "CANCELING", "CANCELED"]}  # fmt: skip


@pytest.mark.parametrize("end", sorted(ENDS))
def test_terminal_write_drops_input_content(tmp_path, end):
    """The same write as the final state removes each inputs[].content item (D-043). Kept until
    then. Other fields (path, name, type, url) are kept."""
    db = TesDb(tmp_path / "tes.sqlite")
    body = {"inputs": [{"name": "a", "path": "/d/a", "type": "FILE", "content": "CANARY"},
                       {"name": "b", "path": "/d/b", "url": "https://h.test/b"}]}  # fmt: skip
    db.create("t1", ("i", "s"), body, 0, on_behalf_of=None, run_id=None, nonce=None)
    frm = "QUEUED"
    for to in ENDS[end]:
        assert db.get("t1").task["inputs"][0]["content"] == "CANARY"  # type: ignore[union-attr]
        assert db.transition("t1", frm, to)
        frm = to
    a, b = db.get("t1").task["inputs"]  # type: ignore[union-attr]
    assert a == {"name": "a", "path": "/d/a", "type": "FILE"}
    assert b == {"name": "b", "path": "/d/b", "url": "https://h.test/b"}


def test_running_to_running_keeps_input_content(tmp_path):
    db = TesDb(tmp_path / "tes.sqlite")
    body = {"inputs": [{"path": "/d/a", "content": "CANARY"}]}
    db.create("t1", ("i", "s"), body, 0, on_behalf_of=None, run_id=None, nonce=None)
    steps = (("QUEUED", "INITIALIZING"), ("INITIALIZING", "RUNNING"), ("RUNNING", "RUNNING"))
    for frm, to in steps:
        assert db.transition("t1", frm, to)
    assert db.get("t1").task["inputs"][0]["content"] == "CANARY"  # type: ignore[union-attr]


def test_startup_scrubs_tasks_that_ended_before(tmp_path):
    """At startup, clean tasks finished by an earlier version (content, URL query parts and
    userinfo left in place).
    Unfinished tasks are not touched (they need it to run)."""
    import json
    import sqlite3

    path = tmp_path / "tes.sqlite"
    db = TesDb(path)
    body = {"inputs": [{"path": "/d/a", "content": "CANARY"},
                       {"path": "/d/b", "url": "https://h.test/b?sig=CANARY"},
                       {"path": "/d/c", "url": "https://u:CANARY@h.test/c"}]}  # fmt: skip
    for tid in ("old", "live"):
        db.create(tid, ("i", "s"), body, 0, on_behalf_of=None, run_id=None, nonce=None)
    with sqlite3.connect(path) as c:  # reproduce how the earlier version ended (task_json as is)
        c.execute("UPDATE tasks SET state='COMPLETE', task_json=? WHERE id='old'",
                  (json.dumps(body),))  # fmt: skip
    db = TesDb(path)
    old, live = db.get("old"), db.get("live")
    assert old and live
    assert old.task["inputs"] == [{"path": "/d/a"}, {"path": "/d/b", "url": "https://h.test/b"},
                                  {"path": "/d/c", "url": "https://h.test/c"}]  # fmt: skip
    assert live.task == body
    assert b"CANARY" not in b"".join(r[0].encode() for r in sqlite3.connect(path).execute(
        "SELECT task_json FROM tasks WHERE state='COMPLETE'"))  # fmt: skip


def test_pid_reuse_is_not_killed_or_waited(tmp_path):
    """If a saved PID was reused by another process, do not wait or kill."""
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                             start_new_session=True)  # fmt: skip
    try:
        ticks = proc_start(other.pid)
        assert ticks is not None
        assert Started(other.pid, ticks, 0).running()
        reused = Started(other.pid, ticks + "0", int(time.time()))  # same PID, other start time
        assert not reused.running()
        r = LocalProcessRunner.wait(reused, tmp_path / "exit_code", tmp_path / "o", tmp_path / "e",
                                    lambda: True, 60)  # fmt: skip
        assert r.error == "executor exited without an exit code"  # finished without waiting
        # cancel does not kill it either
        cfg = {"work_dir": str(tmp_path / "work"), "files_root": str(tmp_path / "files")}
        db = TesDb(tmp_path / "tes.sqlite")
        worker = TesWorker(cfg, db)
        db.create("t1", ("i", "s"), {"executors": [sh("true")]}, 0, on_behalf_of=None,
                  run_id=None, nonce=None)  # fmt: skip
        db.transition("t1", "QUEUED", "CANCELING")
        log_dir = worker.log_dir("t1", 0)
        log_dir.mkdir(parents=True)
        (log_dir / "started").write_text(f"{reused.pid} {reused.ticks} {reused.at}\n")
        db.set_execution("t1", ATTEMPT, 0, "started", 0, other.pid, str(log_dir / "exit_code"))
        worker.finish_cancel(db.get("t1"))  # type: ignore[arg-type]
        assert db.get("t1").state == "CANCELED"  # type: ignore[union-attr]
        assert other.poll() is None  # still alive
    finally:
        other.kill()
        other.wait()


def test_NEG_CANCEL_RACE_tes(tmp_path):
    """Stop right after output transfer, before writing the state, and cancel meanwhile ->
    exactly one final state."""
    env = make_env(tmp_path, "before_complete_write")
    try:
        url = f"file://{env.files}/out.txt"
        tid = submit(env, task(sh("echo x > /d/out"), outputs=[{"path": "/d/out", "url": url}]))
        assert env.worker.wait_exit(30) == 17
        assert httpx.post(env.url + f"/tasks/{tid}:cancel").status_code == 200
        assert httpx.get(env.url + f"/tasks/{tid}").json()["state"] == "CANCELING"
        restart_worker(env)
        t = wait(env, tid)
        assert t["state"] == "CANCELED"
        time.sleep(1)  # no rollback
        assert httpx.get(env.url + f"/tasks/{tid}").json()["state"] == "CANCELED"
    finally:
        stop_all(env)


def test_single_worker_lock(tmp_path):
    env = make_env(tmp_path, None)
    try:
        deadline = time.monotonic() + 20
        while "lock held" not in env.worker.log() and time.monotonic() < deadline:
            time.sleep(0.1)
        second = Worker(env.cfg, tmp_path / "worker", name="worker-second").start()
        assert second.wait_exit(20) != 0
        assert "another yai-tes worker is running" in second.log()
    finally:
        stop_all(env)


def test_publish_after_partial_copy_and_after_link(tmp_path):
    """A temporary file left by a crash mid-copy is removed and copied again. A crash after
    publishing but before recording counts as published. Partial files are never visible under
    the published name."""
    src, dst = tmp_path / "src", tmp_path / "dst"
    src.mkdir()
    dst.mkdir()
    (src / "out").write_bytes(b"full content\n")
    (dst / ".out.t1.part").write_bytes(b"ful")  # crashed mid-copy
    assert publish_beneath(str(src), "out", str(dst), "out", "t1") == 13
    assert (dst / "out").read_bytes() == b"full content\n"
    # crashed after publishing, before recording: calling again does not fail as an overwrite
    assert publish_beneath(str(src), "out", str(dst), "out", "t1") == 13
    # another task's temporary file is not treated as ours (existing outputs are not overwritten)
    with pytest.raises(CannotPublish):
        publish_beneath(str(src), "out", str(dst), "out", "t2")
    assert not (dst / ".out.t2.part").exists()


def test_executor_time_limit_counts_across_restart(tmp_path):
    """The run time limit counts from the start time; a restart does not extend it."""
    env = make_env(tmp_path, "after_executor_start", max_runtime_seconds=3)
    try:
        started = time.monotonic()
        tid = submit(env, task(sh("sleep 30")))
        assert env.worker.wait_exit(30) == 17
        time.sleep(2)  # the clock keeps running while it is down
        restart_worker(env)
        t = wait(env, tid)
        assert t["state"] == "SYSTEM_ERROR", t
        assert time.monotonic() - started < 4.5  # counting from the restart would exceed 5 s
    finally:
        stop_all(env)


def test_NEG_DISK_LIMITS(tmp_path):
    """Input/output counts over the limit get 400 at intake; over
    the per-task total output limit -> SYSTEM_ERROR (excess outputs are not published and no
    temporary files remain, D-044)."""
    env = make_env(tmp_path, None, max_output_bytes=10, limits={"max_inputs": 2, "max_outputs": 2})
    try:
        ins = [{"path": f"/d/i{i}", "content": "x"} for i in range(3)]
        outs = [{"path": f"/d/o{i}", "url": f"file://{env.files}/o{i}"} for i in range(3)]
        for body, msg in ((task(sh("true"), inputs=ins), "at most 2 inputs"),
                          (task(sh("true"), outputs=outs), "at most 2 outputs")):  # fmt: skip
            r = httpx.post(env.url + "/tasks", json=body)
            assert r.status_code == 400 and r.json()["msg"] == msg
        assert submit(env, task(sh("true"), inputs=ins[:2], outputs=outs[:2]))  # at the limit: ok
        a, b = (f"file://{env.files}/sum/{n}" for n in "ab")
        outputs = [{"path": "/d/a", "url": a}, {"path": "/d/b", "url": b}]
        tid = submit(env, task(sh("printf 123456 > /d/a; printf 123456 > /d/b"), outputs=outputs))
        t = wait(env, tid)
        assert t["state"] == "SYSTEM_ERROR", t
        assert any("larger than 10 bytes in total" in s for s in t["logs"][0]["system_logs"])
        assert file_of(a) == b"123456"  # output within the limit is published
        left = sorted(p.name for p in (env.files / "sum").iterdir())
        assert left == ["a"]  # neither b nor b's .part
    finally:
        stop_all(env)
