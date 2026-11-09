"""Small built-in yai engine (wes.md section 4, D-014). Runs inside the API process.

One step = one TES task. A CommandLineTool is one step; a serial Workflow runs one step at a time.
Flow: QUEUED -> INITIALIZING -> (per step: resolve drs:// inputs -> save intent before submitting
to TES -> watch in RUNNING -> record outputs) -> COMPLETE when all steps are COMPLETE; stop at
the first failure.
Passports live only in memory (run_id -> list of Passports) and are dropped when the run ends.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from yai.common.fetch import redact
from yai.common.timeutil import Clock, parse_rfc3339
from yai.wes import cwl
from yai.wes.db import TERMINAL, RunRow, StepRow, WesDb
from yai.wes.resolver import DrsResolver, ResolveError
from yai.wes.tes_client import TesClient, TesError

log = logging.getLogger("yai.wes.engine")

# run_event of docs/data/state-map-tes-to-wes.yaml (a one-task run, so it maps straight to the run)
# TES states that no longer move (PREEMPTED is also final in TES)
TES_TERMINAL = frozenset({"COMPLETE", "EXECUTOR_ERROR", "SYSTEM_ERROR", "CANCELED", "PREEMPTED"})
TES_STATES = TES_TERMINAL | {"QUEUED", "INITIALIZING", "RUNNING", "PAUSED", "CANCELING"}
TES_TO_RUN = {
    "COMPLETE": "COMPLETE",
    "EXECUTOR_ERROR": "EXECUTOR_ERROR",
    "SYSTEM_ERROR": "SYSTEM_ERROR",
    "CANCELED": "SYSTEM_ERROR",  # CANCELED although WES did not cancel
    "PREEMPTED": "SYSTEM_ERROR",
}


class LostCredentials(Exception):
    """The step has drs:// inputs but no stored Passport (lost on restart)."""


class Unreachable(Exception):
    """TES unreachable (or UNKNOWN) for longer than unreachable_limit_seconds."""


class Stopping(BaseException):
    """Engine.stop was called. Leave the watch without changing run state (recover resumes it).
    A BaseException so that an intermediate `except Exception` does not catch it."""


@dataclass
class EngineConfig:
    poll_seconds: float = 2.0
    nonce_search_seconds: float = 60.0
    nonce_search_interval: float = 10.0
    cancel_retry_seconds: float = 10.0
    unreachable_limit_seconds: float = 1800.0
    output_root: str = "/var/tes/files"  # root of TES output storage (same as the TES files_root)
    faults: dict[str, str] = field(default_factory=dict)


class Engine:
    def __init__(
        self, db: WesDb, tes: TesClient, resolver: DrsResolver, cfg: EngineConfig, clock: Clock,
        workdir_of, attachment_of,
    ):  # fmt: skip
        self.db, self.tes, self.resolver, self.cfg, self.clock = db, tes, resolver, cfg, clock
        self.workdir_of = workdir_of  # run_id -> location of the saved workflow document
        self.attachment_of = attachment_of  # (run_id, attachment name) -> content string
        self.passports: dict[str, list[str]] = {}
        self.threads: dict[str, threading.Thread] = {}
        self.lock = threading.Lock()
        self.stopping = threading.Event()

    # --- start ---
    def start(self, run_id: str, passports: list[str] | None = None) -> None:
        """RunWorkflow always passes passports (even empty). None means restart or cancel."""
        if passports is not None:
            self.passports[run_id] = list(passports)
        with self.lock:
            if self.stopping.is_set():
                return  # no new watches after stop; the run continues in the next startup's recover
            if run_id in self.threads and self.threads[run_id].is_alive():
                return
            t = threading.Thread(target=self._guard, args=(run_id,), daemon=True)
            self.threads[run_id] = t
            t.start()

    def recover(self) -> None:
        """On restart: resume unfinished runs from their intent (Passports are lost).
        Finished runs whose TES tasks are not yet fully stopped keep sending CancelTask."""
        for run in self.db.runs_in(("QUEUED", "INITIALIZING", "RUNNING", "CANCELING")):
            self.start(run.id)
        for run_id in self.db.pending_stops():
            self.start(run_id)

    def stop(self, timeout: float = 10.0) -> bool:
        """Stop all watches (D-062). They wake from waits and exit without changing run state.
        True if all stopped within timeout."""
        self.stopping.set()
        deadline = time.monotonic() + timeout
        with self.lock:
            threads = list(self.threads.values())
        for t in threads:
            t.join(max(0.0, deadline - time.monotonic()))
        return not any(t.is_alive() for t in threads)

    def _pause(self, seconds: float) -> None:
        """Replacement for time.sleep. Ends the wait and raises Stopping once stop is called."""
        if self.stopping.wait(seconds):
            raise Stopping

    def _guard(self, run_id: str) -> None:
        try:
            self.drive(run_id)
        except Stopping:
            log.info("engine stopped while driving %s", run_id)
        except Exception:
            log.exception("engine error on %s", run_id)
            try:
                self.fail(run_id, "SYSTEM_ERROR", "internal error in engine")
            except Exception:
                log.exception("engine could not end %s", run_id)
        finally:
            run = self.db.get_run(run_id)
            if run is None or run.state in TERMINAL:
                self.passports.pop(run_id, None)  # drop when the run ends (C-18)
            with self.lock:  # drop finished watches, not a replacing one (#32)
                if self.threads.get(run_id) is threading.current_thread():
                    del self.threads[run_id]

    # --- advance ---
    def drive(self, run_id: str) -> None:
        """Advance steps in order from 1 (serial Workflow; a CommandLineTool is one step).
        Steps with recorded outputs are skipped as done (also after a restart)."""
        run = self.db.get_run(run_id)
        if run is None:
            return
        if run.state in TERMINAL:
            if any(s.cancel_pending for s in self.db.steps(run_id)):
                self.cancel_all(run_id, final=False)  # keep stopping the tasks of a finished run
            return
        if run.state == "QUEUED":
            if not self.db.transition(run_id, "QUEUED", "INITIALIZING", self.clock.now()):
                return  # lost to a cancel (QUEUED -> CANCELED)
            run = self.db.get_run(run_id)
            assert run is not None
        if run.state == "CANCELING":
            self.cancel_all(run_id)
            return
        for step in self.db.steps(run_id):
            if step.outputs is not None:
                continue
            if not self.drive_step(run, step):
                return
        self.finish_complete(run_id)

    def drive_step(self, run: RunRow, step: StepRow) -> bool:
        """Submit one step and watch until done. True if the step is COMPLETE (go to the next)."""
        run_id, n = run.id, step.n
        if step.phase == "submitted" and step.tes_task_id:
            return self.watch(run_id, step.tes_task_id, n)
        nonce = None
        if step.phase == "submitting" and step.submit_nonce:
            # previous submit result unknown: no resubmit until surely "absent"
            nonce = step.submit_nonce
            try:
                found = self._search(run_id, nonce, bounded=True, n=n)
            except Unreachable:
                self.fail(run_id, "SYSTEM_ERROR", "TES unreachable")
                return False
            if found:
                self._submitted(run_id, found, n)
                return self.watch(run_id, found, n)
        try:
            task = self.build_task(run, n)
        except LostCredentials:
            self.fail(run_id, "SYSTEM_ERROR", "credentials were lost on restart", stop=False)
            return False
        except (ResolveError, cwl.CWLError) as exc:
            self.fail(run_id, "SYSTEM_ERROR", f"input resolution failed: {exc}", stop=False)
            return False
        task_id = self.submit(run_id, task, nonce, n=n)
        if task_id is None:
            return False
        return self.watch(run_id, task_id, n)

    def plan_of(self, run: RunRow) -> cwl.Plan:
        return cwl.parse_plan(self.workdir_of(run.id).read_bytes())

    def build_task(self, run: RunRow, n: int = 1) -> dict[str, Any]:
        """Build the TES task for step n from the saved document and inputs.

        drs:// is resolved just before submitting (short signed URL lifetimes, system.md section 5).
        Outputs of the previous step are passed as TES output file:// URLs unchanged (C-25)."""
        req = run.request
        plan = self.plan_of(run)
        tool = plan.steps[n - 1].tool
        done = {s.n: s.outputs for s in self.db.steps(run.id) if s.outputs is not None}
        params = cwl.step_params(plan, n, req.get("workflow_params") or {}, done)
        argv, files = cwl.bind(tool, params)
        needs = any(loc.startswith("drs://") for loc, _ in files.values())
        if needs and run.needs_passport and run.id not in self.passports:
            raise LostCredentials  # Passports are memory only; lost on restart (wes.md section 4)
        inputs = []
        for name, (loc, path) in sorted(files.items()):
            if loc.startswith("attachment:"):
                content = self.attachment_of(run.id, loc.removeprefix("attachment:"))
                inputs.append({"name": name, "path": path, "content": content})
                continue
            if loc.startswith("drs://"):
                url = self.resolver.resolve(loc, self.passports.get(run.id))
                self.db.add_log(run.id, f"input {name}: {redact(loc)} resolved to {redact(url)}")
            elif loc.startswith(("https://", "http://", "file://")):
                # file:// only for earlier-step outputs (step_params); user file:// is 400 on submit
                url = loc
            else:
                raise cwl.CWLError(f"input {name}: unsupported location")
            inputs.append({"name": name, "path": path, "url": url})
        executor: dict[str, Any] = {"image": tool.image, "command": argv, "workdir": cwl.WORKDIR}
        if tool.stdout:
            executor["stdout"] = f"{cwl.WORKDIR}/{tool.stdout}"
        if tool.stderr:
            executor["stderr"] = f"{cwl.WORKDIR}/{tool.stderr}"
        # step number in the storage name (same output names across steps do not collide, D-033)
        outputs = [
            {"name": o.name, "path": f"{cwl.WORKDIR}/{o.glob}",
             "url": f"file://{self.cfg.output_root}/wes/{run.id}/{n}/{o.name}/{o.glob}"}
            for o in tool.outputs
        ]  # fmt: skip
        self.db.update_step(run.id, n, cmd_json=_dumps(argv))
        return {
            "name": f"{run.id}.{n}",
            "executors": [executor],
            "inputs": inputs,
            "outputs": outputs,
            "tags": {
                "yai.run_id": run.id,
                "yai.on_behalf_of": f"{run.owner[0]}|{run.owner[1]}",
            },
        }

    def submit(
        self, run_id: str, task: dict[str, Any], nonce: str | None = None, *, n: int = 1
    ) -> str | None:
        """Save the intent (phase=submitting and nonce) before submitting.
        Resubmits reuse the saved nonce (yai-tes's unique constraint prevents duplicates)."""
        nonce = nonce or "n" + uuid.uuid4().hex
        task["tags"]["yai.submit_nonce"] = nonce
        self.db.update_step(run_id, n, phase="submitting", submit_nonce=nonce)
        self.fault("before_create_task")
        while True:
            if self._canceling(run_id):
                self.cancel_all(run_id)  # look up by nonce, then cancel
                return None
            try:
                task_id = self.tes.create(task)
                self.fault("after_create_task")
                break
            except TesError as exc:
                if not exc.ambiguous:
                    msg = f"task submission failed: {exc.reason}"
                    self.fail(run_id, "SYSTEM_ERROR", msg, stop=False)
                    return None
                try:
                    if self._overdue(run_id, n):
                        raise Unreachable
                    found = self._search(run_id, nonce, bounded=True, n=n)
                except Unreachable:
                    self.fail(run_id, "SYSTEM_ERROR", "TES unreachable")
                    return None
                if found:
                    task_id = found
                    break
        self._reached(run_id, n)
        self._submitted(run_id, task_id, n)
        return task_id

    def _search(self, run_id: str, nonce: str, *, bounded: bool, n: int = 1) -> str | None:
        """Look up the task by nonce. The first request may still be in progress, so None only
        after "absent" has lasted nonce_search_seconds (counted from the first "absent").
        While unreachable the answer is "unknown", never None.
        If bounded, unreachable time is capped at unreachable_limit_seconds."""
        deadline: float | None = None
        while True:
            try:
                found = self.tes.find_by_nonce(nonce)
            except TesError as exc:
                log.warning("search for %s failed: %s", run_id, exc.reason)
                if bounded and self._overdue(run_id, n):
                    raise Unreachable from exc
            else:
                if found:
                    return found
                deadline = deadline or time.monotonic() + self.cfg.nonce_search_seconds
                if time.monotonic() >= deadline:
                    return None
            self._pause(self.cfg.nonce_search_interval)

    def _overdue(self, run_id: str, n: int = 1) -> bool:
        """Count time TES is unreachable (or UNKNOWN). True once past the limit.
        The start is saved in steps so the count survives restarts."""
        since = self._step(run_id, n).unreachable_since
        now = self.clock.now()
        if since is None:
            self.db.update_step(run_id, n, unreachable_since=now)
            return False
        return now - since > self.cfg.unreachable_limit_seconds

    def _reached(self, run_id: str, n: int = 1) -> None:
        if self._step(run_id, n).unreachable_since is not None:
            self.db.update_step(run_id, n, unreachable_since=None)

    def _step(self, run_id: str, n: int) -> StepRow:
        return next(s for s in self.db.steps(run_id) if s.n == n)

    def _submitted(self, run_id: str, task_id: str, n: int = 1) -> None:
        self.db.update_step(run_id, n, phase="submitted", tes_task_id=task_id)
        # from step 2 on the run is already RUNNING, so nothing to do
        self.db.transition(run_id, "INITIALIZING", "RUNNING", self.clock.now())

    def watch(self, run_id: str, task_id: str, n: int = 1) -> bool:
        """Watch until done. If the step is COMPLETE, record outputs and return True."""
        while True:
            if self._canceling(run_id):
                self.cancel_all(run_id)
                return False
            try:
                task = self.tes.get(task_id)
            except TesError:
                if self._overdue(run_id, n):
                    self.fail(run_id, "SYSTEM_ERROR", "TES unreachable")  # keeps sending CancelTask
                    return False
                self._pause(self.cfg.poll_seconds)
                continue
            self.record_step(run_id, task, n)
            state = task.get("state", "UNKNOWN")
            if state not in TES_STATES:
                # a lasting UNKNOWN is treated like unreachable (state-map-tes-to-wes.yaml)
                if self._overdue(run_id, n):
                    self.fail(run_id, "SYSTEM_ERROR", f"TES task {task_id} stayed {state}")
                    return False
                self._pause(self.cfg.poll_seconds)
                continue
            self._reached(run_id, n)
            target = TES_TO_RUN.get(state)
            if target == "COMPLETE":
                self.db.update_step(run_id, n, outputs_json=_dumps(self.outputs_of(task)))
                return True
            if target:
                last = (task.get("logs") or [{}])[-1].get("system_logs") or []
                reason = f"TES task {task_id} ended {state}: {'; '.join(last)}"
                missing = self.missing_outputs(task) if state == "SYSTEM_ERROR" else []
                if missing:
                    # executors exited 0 but CWL outputs are missing (wes.md section 1, D-026)
                    target = "EXECUTOR_ERROR"
                    reason = f"CWL output not produced: {', '.join(missing)}; {reason}"
                self.fail(run_id, target, reason)
                return False
            self._pause(self.cfg.poll_seconds)

    def finish_complete(self, run_id: str) -> None:
        """All steps COMPLETE: pick run outputs by outputSource, then COMPLETE."""
        run = self.db.get_run(run_id)
        if run is None:
            return
        done = {s.n: s.outputs or {} for s in self.db.steps(run_id)}
        self.db.set_outputs(run_id, cwl.run_outputs(self.plan_of(run), done))
        self.finish(run_id, "COMPLETE")

    def record_step(self, run_id: str, task: dict[str, Any], n: int = 1) -> None:
        logs = (task.get("logs") or [{}])[-1]
        ex = (logs.get("logs") or [{}])[-1]

        def ts(v: Any) -> int | None:
            # C-10. Others return missing times as "" (Funnel v0.11.14, INTEROP-TES-2), so empty or
            # unreadable strings count as "absent" and do not fail the run
            if not isinstance(v, str) or not v.strip():
                return None
            try:
                return parse_rfc3339(v)
            except ValueError:
                return None

        self.db.update_step(
            run_id, n, state=task.get("state"), exit_code=ex.get("exit_code"),
            start_time=ts(logs.get("start_time")), end_time=ts(logs.get("end_time")),
        )  # fmt: skip

    @staticmethod
    def missing_outputs(task: dict[str, Any]) -> list[str]:
        """From the TES report, return outputs "not produced although all executors exited 0".

        The TES work dir is not visible, so judge from executor exit codes and the "outputs whose
        source file is missing" that yai-tes writes to `yai.missing_outputs` in tesTaskLog.metadata.
        Failures at start or input fetch (no exit code), transfer failures (unwritable storage or
        links) and TES servers that do not write this marker return empty (stays SYSTEM_ERROR).
        """
        logs = (task.get("logs") or [{}])[-1]
        executors = task.get("executors") or []
        codes = [ex.get("exit_code") for ex in logs.get("logs") or []]
        if not executors or len(codes) != len(executors) or any(c != 0 for c in codes):
            return []
        mark = (logs.get("metadata") or {}).get("yai.missing_outputs") or ""
        marked = set(filter(None, mark.split(",")))
        return [o["path"] for o in task.get("outputs") or [] if o.get("path") in marked]

    @staticmethod
    def outputs_of(task: dict[str, Any]) -> dict[str, Any]:
        logs = (task.get("logs") or [{}])[-1]
        by_path = {o["path"]: o for o in logs.get("outputs") or []}
        out = {}
        for o in task.get("outputs") or []:
            rec = by_path.get(o["path"], {})
            out[o.get("name") or o["path"]] = {
                "class": "File",
                "location": o["url"],
                "size": int(rec.get("size_bytes", 0) or 0),
            }
        return out

    # --- cancel / finish ---
    def _canceling(self, run_id: str) -> bool:
        run = self.db.get_run(run_id)
        return run is not None and run.state == "CANCELING"

    def cancel_all(self, run_id: str, final: bool = True) -> None:
        """CancelTask for every step task submitted (or possibly submitted). Resend if unreachable.
        CANCELED once all have ended.

        Tasks not known to be submitted (nonce only) are looked up until "absent" is certain
        (stays CANCELING while unreachable)."""
        for step in self.db.steps(run_id):
            task_id = step.tes_task_id
            if task_id is None and step.submit_nonce:
                task_id = self._search(run_id, step.submit_nonce, bounded=False, n=step.n)
                if task_id:
                    self.db.update_step(run_id, step.n, phase="submitted", tes_task_id=task_id)
            if step.state in TES_TERMINAL:
                task_id = None  # steps read as finished need no stop
            while task_id is not None:
                try:
                    self.tes.cancel(task_id)
                    self.db.update_step(run_id, step.n, cancel_sent_at=self.clock.now())
                    task = self.tes.get(task_id)
                    self.record_step(run_id, task, step.n)
                    if task.get("state") in TES_TERMINAL:
                        break
                except TesError as exc:
                    log.warning("cancel of %s not delivered: %s", task_id, exc.reason)
                self._pause(min(self.cfg.poll_seconds, self.cfg.cancel_retry_seconds))
            if step.cancel_pending:
                self.db.update_step(run_id, step.n, cancel_pending=0)
        if final:
            self.finish(run_id, "CANCELED", frm="CANCELING")

    def fail(self, run_id: str, to: str, reason: str, *, stop: bool = True) -> None:
        """Finish with an error. On stop, keep sending CancelTask to tasks that may be running
        (after saving the marker; continues after restart, wes.md section 4)."""
        self.db.add_log(run_id, reason)
        live = [
            s for s in self.db.steps(run_id)
            if (s.tes_task_id is not None or s.submit_nonce is not None)
            and s.state not in TES_TERMINAL
        ] if stop else []  # fmt: skip
        for s in live:
            self.db.update_step(run_id, s.n, cancel_pending=1)
        self.finish(run_id, to)
        if any(s.cancel_pending for s in self.db.steps(run_id)):  # cancel path not done
            self.cancel_all(run_id, final=False)

    def finish(self, run_id: str, to: str, frm: str | None = None) -> None:
        run = self.db.get_run(run_id)
        if run is None or run.state in TERMINAL:
            return
        if run.state == "CANCELING" and to != "CANCELED":
            self.cancel_all(run_id)  # the cancel came first
            return
        if not self.db.transition(run_id, frm or run.state, to, self.clock.now()):
            again = self.db.get_run(run_id)
            if again and again.state == "CANCELING" and to != "CANCELED":
                self.cancel_all(run_id)
        self.passports.pop(run_id, None)

    def fault(self, point: str) -> None:
        if self.cfg.faults.get(point) == "exit":
            log.warning("fault injected at %s", point)
            os._exit(17)


def _dumps(value: Any) -> str:
    return json.dumps(value)
