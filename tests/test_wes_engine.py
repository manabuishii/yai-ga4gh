"""WES built-in engine: "unknown" vs "absent", and finish races.

A fake TES that can drop responses, be unreachable, return UNKNOWN, etc. replaces TES, and
the engine runs in-process. The DB is real (WesDb). "Restart" rebuilds Engine on the same DB.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from yai.common.timeutil import Clock
from yai.wes.db import WesDb
from yai.wes.engine import Engine, EngineConfig
from yai.wes.resolver import DrsResolver
from yai.wes.tes_client import TesError

TOOL = """\
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


class FakeTes:
    """Fake TES. tasks: id -> {"nonce", "state", "hidden"} (hidden ones are not found)."""

    def __init__(self, clock: Clock):
        self.clock = clock
        self.tasks: dict[str, dict[str, Any]] = {}
        self.nonces_sent: list[str] = []
        self.create_mode = "ok"  # ok | lost (created, response lost) | busy (not created, 503)
        self.reachable = True  # if False, GetTask, ListTasks and CancelTask are unreachable
        self.find_fails = 0  # ListTasks is unreachable this many times
        self.reveal_after_finds = 0  # ListTasks calls until a hidden task appears (late submission)
        self.state_override: str | None = None  # state returned by GetTask (UNKNOWN etc.)
        self.tick = 0  # seconds the clock advances per call
        self.cancels: list[str] = []

    def _advance(self) -> None:
        self.clock.offset += self.tick

    def create(self, task: dict[str, Any]) -> str:
        self._advance()
        nonce = task["tags"]["yai.submit_nonce"]
        self.nonces_sent.append(nonce)
        if self.create_mode == "busy":
            raise TesError("CreateTask answered 503", ambiguous=True, status=503)
        existing = [k for k, v in self.tasks.items() if v["nonce"] == nonce]
        task_id = existing[0] if existing else f"t{len(self.tasks) + 1}"
        if not existing:
            self.tasks[task_id] = {"nonce": nonce, "state": "RUNNING", "hidden": False}
        if self.create_mode == "lost":
            raise TesError("timeout", ambiguous=True)
        return task_id

    def get(self, task_id: str) -> dict[str, Any]:
        self._advance()
        if not self.reachable:
            raise TesError("unreachable")
        state = self.tasks[task_id]["state"]
        if self.state_override and state not in ("CANCELED", "COMPLETE"):
            state = self.state_override
        return {"id": task_id, "state": state}

    def cancel(self, task_id: str) -> None:
        self._advance()
        if not self.reachable:
            raise TesError("unreachable")
        self.cancels.append(task_id)
        if self.tasks[task_id]["state"] not in ("COMPLETE", "EXECUTOR_ERROR", "SYSTEM_ERROR"):
            self.tasks[task_id]["state"] = "CANCELED"

    def find_by_nonce(self, nonce: str) -> str | None:
        self._advance()
        if not self.reachable or self.find_fails > 0:
            self.find_fails = max(0, self.find_fails - 1)
            raise TesError("ListTasks unreachable")
        if self.reveal_after_finds > 0:
            self.reveal_after_finds -= 1
        else:
            for v in self.tasks.values():
                v["hidden"] = False
        for k, v in self.tasks.items():
            if v["nonce"] == nonce and not v["hidden"]:
                return k
        return None


class Rig:
    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.db = WesDb(tmp / "wes.sqlite")
        self.clock = Clock()
        self.tes = FakeTes(self.clock)
        (tmp / "tool.cwl").write_text(TOOL)
        self.count = 0
        self.engine = self.new_engine()

    def new_engine(self) -> Engine:
        cfg = EngineConfig(poll_seconds=0.01, nonce_search_seconds=0.05,
                           nonce_search_interval=0.01, cancel_retry_seconds=0.01,
                           unreachable_limit_seconds=30)  # fmt: skip
        return Engine(self.db, self.tes, DrsResolver(), cfg, self.clock,  # type: ignore[arg-type]
                      lambda _run_id: self.tmp / "tool.cwl", lambda *_: "")  # fmt: skip

    def restart(self) -> Engine:
        self.engine = self.new_engine()
        return self.engine

    def run(self, state: str = "INITIALIZING", **step: Any) -> str:
        self.count += 1
        run_id = f"r{self.count}"
        req = {"workflow_params": {"script": "true"}}
        self.db.create_run(run_id, ("iss", "sub"), req, self.clock.now(), False, [("tool", [])])
        path = {"INITIALIZING": ["INITIALIZING"], "RUNNING": ["INITIALIZING", "RUNNING"],
                "CANCELING": ["INITIALIZING", "CANCELING"],
                "SYSTEM_ERROR": ["INITIALIZING", "SYSTEM_ERROR"]}[state]  # fmt: skip
        frm = "QUEUED"
        for to in path:
            assert self.db.transition(run_id, frm, to, self.clock.now())
            frm = to
        if step:
            self.db.update_step(run_id, 1, **step)
        return run_id

    def state(self, run_id: str) -> str:
        run = self.db.get_run(run_id)
        assert run is not None
        return run.state

    def step(self, run_id: str):
        return self.db.steps(run_id)[0]


def in_thread(fn, *args) -> threading.Thread:
    t = threading.Thread(target=fn, args=args, daemon=True)
    t.start()
    return t


def until(cond, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not cond():
        if time.monotonic() > deadline:
            raise AssertionError("condition not met")
        time.sleep(0.01)


@pytest.fixture
def rig(tmp_path) -> Rig:
    return Rig(tmp_path)


# --- 3: resubmit after restart uses the stored nonce; never resubmit on "unknown" ---


def test_restart_waits_while_search_unknown_and_finds_the_first_task(rig):
    """The first CreateTask response is lost and ListTasks is unreachable right after restart
    -> no resubmit."""
    run_id = rig.run(submit_nonce="nA", phase="submitting")
    rig.tes.tasks["t1"] = {"nonce": "nA", "state": "COMPLETE", "hidden": False}
    rig.tes.find_fails = 3
    rig.restart().drive(run_id)
    assert rig.tes.nonces_sent == []  # not resubmitted
    assert rig.state(run_id) == "COMPLETE"
    assert rig.step(run_id).tes_task_id == "t1"


def test_restart_resubmits_with_the_saved_nonce(rig):
    """Resubmit once search confirms "absent". The nonce is the stored one (yai-tes dedupes)."""
    run_id = rig.run(submit_nonce="nB", phase="submitting")
    rig.tes.find_fails = 2
    engine = rig.restart()
    t = in_thread(engine.drive, run_id)
    until(lambda: rig.tes.nonces_sent)
    assert rig.tes.nonces_sent == ["nB"]
    rig.tes.tasks["t1"]["state"] = "COMPLETE"
    t.join(5)
    assert rig.state(run_id) == "COMPLETE"
    assert len(rig.tes.tasks) == 1


def test_lost_response_then_restart_makes_one_task(rig):
    """Lost CreateTask response (task exists in TES) shows up late in search -> no duplicate."""
    run_id = rig.run()
    rig.tes.create_mode = "lost"
    rig.tes.reveal_after_finds = 2
    t = in_thread(rig.engine.drive, run_id)
    until(lambda: rig.state(run_id) == "RUNNING")
    rig.tes.tasks["t1"]["state"] = "COMPLETE"
    t.join(5)
    assert rig.state(run_id) == "COMPLETE"
    assert len(rig.tes.tasks) == 1


# --- 4: no CANCELED while the submission result is unknown ---


def test_cancel_stays_canceling_while_submission_unknown(rig):
    run_id = rig.run("CANCELING", submit_nonce="nC", phase="submitting")
    rig.tes.tasks["t1"] = {"nonce": "nC", "state": "RUNNING", "hidden": True}
    rig.tes.reachable = False
    t = in_thread(rig.restart().drive, run_id)
    time.sleep(0.2)
    assert rig.state(run_id) == "CANCELING"  # not finished while unreachable
    rig.tes.reachable = True
    rig.tes.reveal_after_finds = 2  # the first CreateTask arrived late
    t.join(5)
    assert rig.state(run_id) == "CANCELED"
    assert rig.tes.cancels == ["t1"]
    assert rig.tes.tasks["t1"]["state"] == "CANCELED"


def test_cancel_with_no_task_ends_after_search_window(rig):
    run_id = rig.run("CANCELING", submit_nonce="nD", phase="submitting")
    rig.restart().drive(run_id)
    assert rig.state(run_id) == "CANCELED"
    assert rig.tes.cancels == []


# --- 12: give up after repeated submit failures / UNKNOWN (the fake advances the clock) ---


def test_create_always_503_ends_in_system_error(rig):
    run_id = rig.run()
    rig.tes.create_mode = "busy"
    rig.tes.tick = 5
    rig.engine.drive(run_id)
    assert rig.state(run_id) == "SYSTEM_ERROR"
    run = rig.db.get_run(run_id)
    assert run and any("TES unreachable" in s for s in run.system_logs)
    assert len(set(rig.tes.nonces_sent)) == 1  # still a single nonce across resubmits


def test_task_always_unknown_ends_in_system_error_and_is_canceled(rig):
    run_id = rig.run()
    rig.tes.state_override = "UNKNOWN"
    rig.tes.tick = 5
    rig.engine.drive(run_id)
    assert rig.state(run_id) == "SYSTEM_ERROR"
    assert rig.tes.cancels == ["t1"]
    assert not rig.step(run_id).cancel_pending


def test_unreachable_count_survives_restart(rig):
    """The start of the unreachable period is stored; a restart does not reset the count."""
    run_id = rig.run("RUNNING", phase="submitted", tes_task_id="t1")
    rig.tes.tasks["t1"] = {"nonce": "nE", "state": "RUNNING", "hidden": False}
    rig.db.update_step(run_id, 1, unreachable_since=rig.clock.now() - 31)
    rig.tes.reachable = False
    t = in_thread(rig.restart().drive, run_id)
    until(lambda: rig.state(run_id) == "SYSTEM_ERROR")
    rig.tes.reachable = True
    t.join(5)
    assert rig.tes.cancels == ["t1"]


def test_stop_ends_watchers_without_changing_state(rig):
    """D-062: stop wakes watchers mid-wait so they exit. Run state is unchanged (the next startup's
    recover continues it), and no new watchers start after stop. Previously a watcher kept polling
    an unreachable TES after tests, logging "token endpoint unreachable" at exit."""
    run_id = rig.run("RUNNING", phase="submitted", tes_task_id="t1")
    rig.tes.tasks["t1"] = {"nonce": "nS", "state": "RUNNING", "hidden": False}
    rig.tes.reachable = False
    rig.engine.cfg.poll_seconds = 60  # stop exits promptly even with a long wait
    rig.engine.start(run_id, [])
    until(lambda: rig.step(run_id).unreachable_since is not None)
    watcher = rig.engine.threads[run_id]
    started = time.monotonic()
    assert rig.engine.stop(timeout=5)
    assert time.monotonic() - started < 2
    assert not watcher.is_alive() and rig.engine.threads == {}
    assert rig.state(run_id) == "RUNNING"
    rig.engine.start(run_id)
    assert rig.engine.threads == {}


# --- 11: CancelTask after an error finish is stored and sent again after restart ---


def test_stop_after_error_is_persisted_and_resumed_on_restart(rig):
    run_id = rig.run("RUNNING", phase="submitted", tes_task_id="t1")
    rig.tes.tasks["t1"] = {"nonce": "nF", "state": "RUNNING", "hidden": False}
    rig.tes.reachable = False
    rig.tes.tick = 20
    in_thread(rig.engine.drive, run_id)  # this watcher stays unreachable (treated as crashed)
    until(lambda: rig.state(run_id) == "SYSTEM_ERROR")
    assert rig.step(run_id).cancel_pending  # the cancel marker remains even for a finished run
    old = rig.engine
    old.cancel_all = lambda *a, **k: None  # type: ignore[method-assign]  # the old one has stopped
    rig.tes.tick = 0
    rig.tes.reachable = True
    engine = rig.restart()
    engine.recover()
    until(lambda: not rig.step(run_id).cancel_pending)
    assert rig.tes.tasks["t1"]["state"] == "CANCELED"
    assert rig.state(run_id) == "SYSTEM_ERROR"


def test_engine_exception_still_cancels_the_task(rig):
    run_id = rig.run("RUNNING", phase="submitted", tes_task_id="t1")
    rig.tes.tasks["t1"] = {"nonce": "nG", "state": "RUNNING", "hidden": False}

    real, calls = rig.engine.record_step, []

    def broken(run_id_: str, task: dict, n: int = 1) -> None:
        calls.append(run_id_)
        if len(calls) == 1:
            raise RuntimeError("bug")  # exception inside the watcher (once)
        real(run_id_, task, n)

    rig.engine.record_step = broken  # type: ignore[method-assign]
    rig.engine.start(run_id)
    until(lambda: rig.state(run_id) == "SYSTEM_ERROR" and not rig.step(run_id).cancel_pending)
    assert rig.tes.cancels == ["t1"]


# --- 5: completion/error racing with cancel still ends CANCELED ---


@pytest.mark.parametrize("to", ["COMPLETE", "SYSTEM_ERROR"])
def test_finish_while_canceling_ends_canceled(rig, to):
    run_id = rig.run("CANCELING", phase="submitted", tes_task_id="t1")
    rig.tes.tasks["t1"] = {"nonce": "nH", "state": "COMPLETE", "hidden": False}
    if to == "COMPLETE":
        rig.engine.finish(run_id, to)  # CancelRun arrived right after the watcher read completion
    else:
        rig.engine.fail(run_id, to, "input resolution failed: x")
    assert rig.state(run_id) == "CANCELED"


def test_old_db_gets_new_columns(tmp_path):
    """Columns added later are also added to old DB files."""
    path = tmp_path / "old.sqlite"
    with sqlite3.connect(path) as db:
        db.executescript(
            "CREATE TABLE steps (run_id TEXT NOT NULL, n INTEGER NOT NULL, name TEXT NOT NULL,"
            " phase TEXT NOT NULL, submit_nonce TEXT, tes_task_id TEXT, state TEXT,"
            " cmd_json TEXT NOT NULL DEFAULT '[]', exit_code INTEGER, start_time INTEGER,"
            " end_time INTEGER, cancel_sent_at INTEGER, PRIMARY KEY (run_id, n));"
        )
    WesDb(path)
    with sqlite3.connect(path) as db:
        cols = {r[1] for r in db.execute("PRAGMA table_info(steps)")}
    assert {"unreachable_since", "cancel_pending"} <= cols


# --- D-030 option A: no signed-URL query in request and records of finished runs ---

FILE_TOOL = """\
cwlVersion: v1.2
class: CommandLineTool
baseCommand: cat
inputs:
  a:
    type: File
    inputBinding: {position: 1}
  b:
    type: File
    inputBinding: {position: 2}
outputs: []
requirements:
  DockerRequirement: {dockerPull: "alpine:3.20"}
"""


def signed_request() -> dict[str, Any]:
    return {
        "workflow_url": "https://h.test/tool.cwl?sig=CANARY-wf",
        "workflow_params": {
            "a": {"class": "File", "location": "https://h.test/a.txt?X-Amz-Signature=CANARY-a"},
            "b": {"class": "File", "location": "drs://drs.yai.test/obj1?CANARY-b"},
            "many": [{"class": "File", "path": "https://h.test/c.txt?CANARY-c#frag"}],
            "text": "a string?CANARY-kept",  # non-File strings are unchanged
        },
        "tags": {"k": "v"},
    }


@pytest.mark.parametrize(
    "path",
    [
        ["INITIALIZING", "RUNNING", "COMPLETE"],
        ["INITIALIZING", "RUNNING", "EXECUTOR_ERROR"],
        ["INITIALIZING", "SYSTEM_ERROR"],
        ["INITIALIZING", "CANCELING", "CANCELED"],
        ["CANCELED"],  # cancel while QUEUED
    ],
)
def test_request_keeps_signed_urls_until_the_run_ends(rig, path):
    run_id = "rs"
    rig.db.create_run(run_id, ("iss", "sub"), signed_request(), rig.clock.now(), False, [("t", [])])
    frm = "QUEUED"
    for to in path:
        run = rig.db.get_run(run_id)
        assert run is not None and run.request == signed_request()  # unchanged until finished
        assert rig.db.transition(run_id, frm, to, rig.clock.now())
        frm = to
    run = rig.db.get_run(run_id)
    assert run is not None and run.state == path[-1]
    req = run.request
    assert "CANARY-wf" not in json.dumps(req) and "CANARY-a" not in json.dumps(req)
    assert "CANARY-b" not in json.dumps(req) and "CANARY-c" not in json.dumps(req)
    assert req["workflow_url"] == "https://h.test/tool.cwl"
    params = req["workflow_params"]
    assert params["a"]["location"] == "https://h.test/a.txt"
    assert params["b"]["location"] == "drs://drs.yai.test/obj1"
    assert params["many"][0]["path"] == "https://h.test/c.txt"
    assert params["text"] == "a string?CANARY-kept" and req["tags"] == {"k": "v"}
    with sqlite3.connect(rig.db.path) as db:
        raw = db.execute("SELECT request_json FROM runs WHERE id=?", (run_id,)).fetchone()[0]
    assert "CANARY-a" not in raw and "CANARY-wf" not in raw


def test_lost_race_does_not_scrub(rig):
    """A lost conditional update (state differs) changes nothing."""
    run_id = "rr"
    rig.db.create_run(run_id, ("iss", "sub"), signed_request(), rig.clock.now(), False, [("t", [])])
    assert rig.db.transition(run_id, "QUEUED", "INITIALIZING", rig.clock.now())
    assert not rig.db.transition(run_id, "RUNNING", "COMPLETE", rig.clock.now())
    run = rig.db.get_run(run_id)
    assert run is not None and run.state == "INITIALIZING" and run.request == signed_request()


def test_system_logs_never_hold_signed_urls(rig):
    run_id = rig.run()
    rig.db.add_log(run_id, "fetch https://h.test/x?sig=CANARY-log failed")
    run = rig.db.get_run(run_id)
    assert run is not None and "CANARY-log" not in json.dumps(run.system_logs)
    assert "https://h.test/x" in run.system_logs[-1]


class SignedResolver:
    def resolve(self, uri: str, passports: list[str] | None) -> str:
        return "https://store.test/obj1?X-Amz-Signature=CANARY-resolved"


def test_drs_resolution_log_hides_both_queries_but_task_gets_the_url(rig):
    """DRS resolution records redact the query of both the original drs:// and the resolved URL.
    Tasks sent to TES keep the signed URL (needed to run; unchanged until finish)."""
    (rig.tmp / "tool.cwl").write_text(FILE_TOOL)
    engine = rig.engine
    engine.resolver = SignedResolver()  # type: ignore[assignment]
    run_id = "rd"
    req = signed_request()
    req["workflow_params"] = {k: req["workflow_params"][k] for k in ("a", "b")}
    rig.db.create_run(run_id, ("iss", "sub"), req, rig.clock.now(), False, [("t", [])])
    run = rig.db.get_run(run_id)
    assert run is not None
    task = engine.build_task(run)
    urls = {i["name"]: i["url"] for i in task["inputs"]}
    assert urls["a"].endswith("CANARY-a")
    assert urls["b"].endswith("CANARY-resolved")
    logs = json.dumps(rig.db.get_run(run_id).system_logs)  # type: ignore[union-attr]
    assert "drs://drs.yai.test/obj1" in logs
    assert "CANARY" not in logs


def test_old_finished_runs_are_scrubbed_on_start(tmp_path):
    """Also removed from requests of runs finished by an older version (cleared at startup)."""
    path = tmp_path / "wes.sqlite"
    db = WesDb(path)
    db.create_run("old", ("iss", "sub"), signed_request(), 0, False, [("t", [])])
    db.create_run("live", ("iss", "sub"), signed_request(), 0, False, [("t", [])])
    with sqlite3.connect(path) as raw:
        raw.execute("UPDATE runs SET state='COMPLETE' WHERE id='old'")
    db = WesDb(path)
    old, live = db.get_run("old"), db.get_run("live")
    assert old is not None and "CANARY-a" not in json.dumps(old.request)
    assert live is not None and live.request == signed_request()  # unfinished runs are unchanged


# --- D-030 for runs/<id>/ (2026-10-17): finished runs keep no documents or attachments ---


def run_files(runs: Path, run_id: str) -> Path:
    d = runs / run_id
    (d / "workflow").mkdir(parents=True)
    (d / "workflow" / "tool.cwl").write_text("location: https://h.test/x?sig=CANARY-doc\n")
    (d / "attachments").mkdir()
    (d / "attachments" / "in.txt").write_text("CANARY-att")
    return d


@pytest.mark.parametrize(
    "path",
    [
        ["INITIALIZING", "RUNNING", "COMPLETE"],
        ["INITIALIZING", "RUNNING", "EXECUTOR_ERROR"],
        ["INITIALIZING", "SYSTEM_ERROR"],
        ["INITIALIZING", "CANCELING", "CANCELED"],
        ["CANCELED"],  # cancel while QUEUED (the API writes without going through engine)
    ],
)
def test_run_files_stay_until_the_run_ends(tmp_path, path):
    runs = tmp_path / "runs"
    db = WesDb(tmp_path / "wes.sqlite", runs)
    db.create_run("rf", ("iss", "sub"), signed_request(), 0, False, [("t", [])])
    d = run_files(runs, "rf")
    frm = "QUEUED"
    for to in path:
        assert d.is_dir()  # kept until finished (needed for execution and restart)
        assert db.transition("rf", frm, to, 0)
        frm = to
    assert not d.exists()


def test_lost_race_keeps_run_files(tmp_path):
    runs = tmp_path / "runs"
    db = WesDb(tmp_path / "wes.sqlite", runs)
    db.create_run("rf", ("iss", "sub"), signed_request(), 0, False, [("t", [])])
    d = run_files(runs, "rf")
    assert not db.transition("rf", "RUNNING", "COMPLETE", 0)  # state differs
    assert d.is_dir()


def test_engine_endings_drop_run_files(tmp_path):
    """Engine finishes (error, cancel, complete) go through the same path (WesDb.transition)."""
    rig = Rig(tmp_path)
    runs = tmp_path / "runs"
    rig.db.runs_dir = runs
    failed, canceled, done = rig.run(), rig.run("CANCELING"), rig.run("RUNNING")
    dirs = [run_files(runs, r) for r in (failed, canceled, done)]
    rig.engine.fail(failed, "SYSTEM_ERROR", "x", stop=False)
    rig.engine.cancel_all(canceled)
    rig.db.update_step(done, 1, outputs_json="{}")
    rig.engine.finish_complete(done)
    states = [rig.state(r) for r in (failed, canceled, done)]
    assert states == ["SYSTEM_ERROR", "CANCELED", "COMPLETE"]
    assert not any(d.exists() for d in dirs)


def test_run_files_are_swept_on_start(tmp_path):
    """At startup, remove runs/<id>/ for runs finished by an older version, runs that crashed after
    recording the end but before removal, and runs absent from the DB (leftovers of a crashed
    submission). Unfinished runs keep theirs (they continue after restart)."""
    path, runs = tmp_path / "wes.sqlite", tmp_path / "runs"
    db = WesDb(path)  # older version (no removal)
    for run_id in ("old", "live"):
        db.create_run(run_id, ("iss", "sub"), signed_request(), 0, False, [("t", [])])
    with sqlite3.connect(path) as raw:
        raw.execute("UPDATE runs SET state='EXECUTOR_ERROR' WHERE id='old'")
        raw.execute("UPDATE runs SET state='RUNNING' WHERE id='live'")
    old, live, orphan = (run_files(runs, r) for r in ("old", "live", "orphan"))
    WesDb(path, runs)
    assert not old.exists() and not orphan.exists()
    assert live.is_dir() and (live / "attachments" / "in.txt").read_text() == "CANARY-att"


# --- watcher threads and per-user unfinished-run limit ---


def test_finished_watchers_leave_threads(rig):
    """Watchers of finished runs leave threads (previously one stayed per run)."""
    ids = [rig.run("SYSTEM_ERROR") for _ in range(5)]
    for run_id in ids:
        rig.engine.start(run_id)
    until(lambda: not rig.engine.threads)


def test_NEG_WES_RUN_LIMIT(tmp_path):
    """Unfinished runs per owner are capped at max_active, even with concurrent submits (D-044).
    Other owners are not counted. Once one finishes, the next is accepted."""
    from yai.wes.db import RunLimit

    db = WesDb(tmp_path / "wes.sqlite")

    def make(run_id: str, owner=("iss", "a")) -> bool:
        try:
            db.create_run(run_id, owner, {}, 0, False, [("t", [])], max_active=3)
        except RunLimit:
            return False
        return True

    made: list[bool] = []
    threads = [threading.Thread(target=lambda i=i: made.append(make(f"r{i}"))) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(made) == [False] * 7 + [True] * 3
    assert make("other", ("iss", "b"))
    done = next(r.id for r in db.runs_in(("QUEUED",)) if r.owner == ("iss", "a"))
    assert db.transition(done, "QUEUED", "CANCELED", 0)
    assert make("next") and not make("again")
