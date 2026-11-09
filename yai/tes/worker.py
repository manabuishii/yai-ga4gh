"""The yai-tes worker process (tes.md sections 2 and 4).

- Only one runs (takes a lock file at startup; exits if it cannot)
- Picks up QUEUED, INITIALIZING (fetch inputs) -> RUNNING (executors in order) -> upload outputs
  -> done
- Saves the intent before each external action. On restart, resumes from the saved intent
- Fault injection points (test settings only): faults: {<point>: exit}
"""

from __future__ import annotations

import contextlib
import fcntl
import logging
import os
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from yai.common import startup
from yai.common.fetch import Fetcher, FetchError, redact
from yai.common.logsetup import setup_logging
from yai.common.safefs import (
    CannotPublish,
    MissingSource,
    TooLarge,
    UnsafePath,
    copy_beneath,
    discard_part,
    publish_beneath,
)
from yai.tes.db import TERMINAL, TaskRow, TesDb
from yai.tes.model import file_url_path
from yai.tes.runner import (
    ExecutorResult,
    LocalProcessRunner,
    PathMap,
    Runner,
    container_dirs,
    kill_group,
    read_exit,
    read_started,
)

log = logging.getLogger("yai.tes.worker")
ATTEMPT = 0  # the minimal version does not retry


def runner_kind(config: dict[str, Any]) -> str:
    kind = str((config.get("runner") or {}).get("kind", "local-process"))
    if kind not in ("local-process", "docker"):
        raise SystemExit(f"runner.kind must be local-process or docker, not {kind}")
    return kind


def make_runner(config: dict[str, Any], work_dir: Path) -> Runner:
    """Pick the runner from settings. Default: local process (D-024). Docker: D-034 (prov.)."""
    if runner_kind(config) == "docker":
        from yai.tes.docker_runner import DockerRunner

        docker = dict((config.get("runner") or {}).get("docker") or {})
        docker.setdefault("work_dir", str(work_dir))
        return DockerRunner(docker)
    return LocalProcessRunner()


class Stop(Exception):
    """Noticed a cancel."""


class TesWorker:
    def __init__(self, config: dict[str, Any], db: TesDb, runner: Runner | None = None):
        self.config = config
        self.db = db
        self.work_dir = Path(config.get("work_dir", "var/tes/work")).resolve()
        self.runner = runner or make_runner(config, self.work_dir)
        self.files_root = str(Path(config.get("files_root", "var/tes/files")).resolve())
        Path(self.files_root).mkdir(parents=True, exist_ok=True)
        self.work_dir.mkdir(parents=True, exist_ok=True)
        fetch = config.get("fetch", {})
        self.fetcher = Fetcher(
            allow=list(fetch.get("allow", ["drs.yai.test:443"])),
            insecure_http_hosts=list(fetch.get("insecure_http_hosts", [])),
            retries=int(fetch.get("retries", 3)),
            backoff=float(fetch.get("backoff_seconds", 0.5)),
        )
        self.max_input = int(config.get("max_input_bytes", 10 * 1024**3))
        self.max_output = int(config.get("max_output_bytes", 10 * 1024**3))  # total per task
        self.input_timeout = float(config.get("input_timeout_seconds", 3600))
        self.max_seconds = int(config.get("max_runtime_seconds", 86400))
        self.concurrency = int(config.get("concurrency", 2))
        self.faults: dict[str, str] = dict(config.get("faults", {}))
        self.busy: set[str] = set()
        self.lock = threading.Lock()

    # --- fault injection points (test-plan.md section 3) ---
    def fault(self, point: str) -> None:
        if self.faults.get(point) == "exit":
            log.warning("fault injected at %s", point)
            os._exit(17)

    # --- main loop ---
    def serve_forever(self, poll: float = 0.3) -> None:
        lock_path = self.work_dir / "worker.lock"
        lock = open(lock_path, "w")  # noqa: SIM115 held for the life of the process
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("another yai-tes worker is running") from None
        log.info("worker started (lock held)")
        self.recover()
        with ThreadPoolExecutor(self.concurrency) as pool:
            while True:
                self.tick(pool)
                time.sleep(poll)

    def tick(self, pool: ThreadPoolExecutor) -> None:
        for row in self.db.tasks_in(("CANCELING",)):
            if self._claim(row.id):
                pool.submit(self._guard, self.finish_cancel, row)
        with self.lock:
            free = self.concurrency - len(self.busy)
        if free <= 0:
            return
        for row in self.db.tasks_in(("QUEUED",))[:free]:
            if self._claim(row.id):
                pool.submit(self._guard, self.start, row)

    def _claim(self, task_id: str) -> bool:
        with self.lock:
            if task_id in self.busy:
                return False
            self.busy.add(task_id)
            return True

    def _guard(self, fn, row: TaskRow) -> None:
        try:
            fn(row)
        except Exception:
            log.exception("worker error on %s", row.id)
            self.system_error(row.id, "internal error in worker")
        finally:
            with self.lock:
                self.busy.discard(row.id)

    # --- on restart (tes.md section 4) ---
    def recover(self) -> None:
        for row in self.db.tasks_in(("INITIALIZING", "RUNNING")):
            self.busy.add(row.id)
            threading.Thread(target=self._guard, args=(self.resume, row), daemon=True).start()
        live = {r.id for r in self.db.tasks_in(("INITIALIZING", "RUNNING", "CANCELING"))}
        for name in self.runner.cleanup(live):
            log.warning("removed orphan container %s", name)
        # tasks that crashed after writing the final state but before cleanup
        for d in self.work_dir.iterdir():
            row = self.db.get(d.name) if d.is_dir() else None
            if row is not None and row.state in TERMINAL:
                self.finish(row.id)

    def resume(self, row: TaskRow) -> None:
        self.db.add_system_log(row.id, ATTEMPT, "worker restarted; resuming")
        if row.state == "INITIALIZING":
            shutil.rmtree(self.task_dir(row.id), ignore_errors=True)  # fetch inputs from scratch
            self.prepare_and_run(row, state="INITIALIZING")
        else:
            self.run_executors(row, self.path_map(row), resume=True)

    # --- one task ---
    def task_dir(self, task_id: str) -> Path:
        return self.work_dir / task_id

    def path_map(self, row: TaskRow) -> PathMap:
        pm = PathMap(self.task_dir(row.id) / "fs")
        task = row.task
        for inp in task.get("inputs") or []:
            pm.add(inp["path"])
        for out in task.get("outputs") or []:
            pm.add(out["path"])
        for v in task.get("volumes") or []:
            pm.add_dir(v)
        for ex in task.get("executors") or []:
            if ex.get("workdir"):
                pm.add_dir(ex["workdir"])
            for key in ("stdin", "stdout", "stderr"):
                if ex.get(key):
                    pm.add(ex[key])
        return pm

    def start(self, row: TaskRow) -> None:
        if not self.db.transition(row.id, "QUEUED", "INITIALIZING"):
            return  # lost to a cancel
        self.db.start_log(row.id, ATTEMPT, int(time.time()))
        self.prepare_and_run(row, state="INITIALIZING")

    def cancelled(self, task_id: str) -> bool:
        row = self.db.get(task_id)
        return row is None or row.state == "CANCELING"

    def prepare_and_run(self, row: TaskRow, state: str) -> None:
        pm = self.path_map(row)
        pm.fs_root.mkdir(parents=True, exist_ok=True)
        try:
            for d in container_dirs(row.task):
                pm.host(d).mkdir(parents=True, exist_ok=True)
            for inp in row.task.get("inputs") or []:
                if self.cancelled(row.id):
                    raise Stop
                self.fetch_input(row, inp, pm)
        except Stop:
            self.finish_cancel(row)
            return
        except (FetchError, UnsafePath, OSError) as exc:
            reason = getattr(exc, "reason", None) or type(exc).__name__
            self.fail(row.id, state, "SYSTEM_ERROR", f"input fetch failed: {reason}")
            return
        self.run_executors(row, pm, resume=False)

    def fetch_input(self, row: TaskRow, inp: dict[str, Any], pm: PathMap) -> None:
        dest = pm.host(inp["path"])
        dest.parent.mkdir(parents=True, exist_ok=True)
        if inp.get("content") is not None:
            with open(dest, "x", encoding="utf-8") as f:
                f.write(inp["content"])
            return
        url = inp["url"]
        if url.startswith("file:"):
            # ownership was checked on submit. Copy without following links
            copy_beneath(self.files_root, inp["_rel"], str(pm.fs_root), inp["path"].lstrip("/"))
            return
        size = self.fetcher.download(
            url, dest, max_bytes=self.max_input, total_timeout=self.input_timeout
        )
        self.db.add_system_log(row.id, ATTEMPT, f"input {redact(url)} ({size} bytes)")

    def run_executors(self, row: TaskRow, pm: PathMap, *, resume: bool) -> None:
        state = self.db.get(row.id).state  # type: ignore[union-attr]
        known = self.db.executions(row.id, ATTEMPT) if resume else {}
        for idx, ex in enumerate(row.task["executors"]):
            if self.cancelled(row.id):
                self.finish_cancel(row)
                return
            log_dir = self.log_dir(row.id, idx)
            log_dir.mkdir(parents=True, exist_ok=True)
            done = known.get(idx)
            if done and done.phase == "finished":
                # judge from the saved result. An unknown exit code is not success
                code = done.exit_code
                if code is None and done.error is None and done.exit_file:
                    code = read_exit(Path(done.exit_file))  # rows from before the column was added
                result = ExecutorResult(code, 0, 0, "", "", done.error)
                if code is None and result.error is None:
                    result.error = "executor exit code unknown after restart"
                if not self.after_executor(row, idx, ex, result, state):
                    return
                continue
            resumed = None
            if done:
                # crashed after "start": watch what already started (wrap marker, container with
                # the fixed name) (no double start)

                def mark(p: int, ef: str, idx: int = idx, phase: str = done.phase) -> None:
                    if phase == "starting":
                        self.db.set_execution(row.id, ATTEMPT, idx, "started", int(time.time()),
                                              p, ef)  # fmt: skip

                resumed = self.runner.resume(
                    ex, pm, log_dir=log_dir, record_start=mark,
                    should_stop=lambda: self.cancelled(row.id), max_seconds=self.max_seconds,
                )  # fmt: skip
            if resumed is not None:
                result = resumed
            elif done and done.phase == "started":
                result = ExecutorResult(None, 0, 0, "", "", "executor lost on restart")
            else:
                self.db.set_execution(row.id, ATTEMPT, idx, "starting", int(time.time()))
                self.fault("before_executor_start")
                if state == "INITIALIZING":
                    if not self.db.transition(row.id, "INITIALIZING", "RUNNING"):
                        self.finish_cancel(row)
                        return
                    state = "RUNNING"

                def record_start(p: int, ef: str, idx: int = idx) -> None:
                    self.fault("before_executor_record")  # after Popen, before saving the PID
                    self.db.set_execution(row.id, ATTEMPT, idx, "started", int(time.time()), p, ef)
                    self.fault("after_executor_start")

                result = self.runner.run(
                    ex, pm, log_dir=log_dir, record_start=record_start,
                    should_stop=lambda: self.cancelled(row.id), max_seconds=self.max_seconds,
                    resources=row.task.get("resources"),
                )  # fmt: skip
            if state == "INITIALIZING":
                self.db.transition(row.id, "INITIALIZING", "RUNNING")
                state = "RUNNING"
            self.db.set_executor_log(
                row.id, ATTEMPT, idx, exit_code=result.exit_code, start=result.start,
                end=result.end, stdout=result.stdout_tail, stderr=result.stderr_tail,
            )  # fmt: skip
            self.db.set_execution(row.id, ATTEMPT, idx, "finished", int(time.time()),
                                  exit_code=result.exit_code, error=result.error)  # fmt: skip
            self.fault("after_executor_finished")
            if not self.after_executor(row, idx, ex, result, state):
                return
        self.upload_outputs(row, pm)

    def after_executor(
        self, row: TaskRow, idx: int, ex: dict[str, Any], result: ExecutorResult, state: str
    ) -> bool:
        """Advance on one executor's result. True if it is fine to go to the next executor."""
        if result.error == "canceled" or self.cancelled(row.id):
            self.finish_cancel(row)
            return False
        if result.error:
            self.fail(row.id, state, "SYSTEM_ERROR", f"executor {idx}: {result.error}")
            return False
        if result.exit_code != 0 and not ex.get("ignore_error"):
            msg = f"executor {idx} exited {result.exit_code}"
            self.fail(row.id, state, "EXECUTOR_ERROR", msg)  # TES-RUN-01, ST-02
            return False
        return True

    def log_dir(self, task_id: str, idx: int) -> Path:
        return self.task_dir(task_id) / "logs" / str(idx)

    def upload_outputs(self, row: TaskRow, pm: PathMap) -> None:
        """Copy outputs to storage. No link following, no overwrite (TES-ST-01, 02)."""
        outputs = row.task.get("outputs") or []
        self.db.plan_transfers(row.id, [(o["path"], o["url"]) for o in outputs])
        used = self.db.output_bytes(row.id)  # also count what earlier attempts recorded (#30)
        for path, url, done in self.db.transfers(row.id):
            if done:
                with contextlib.suppress(Exception):  # crashed before removing the temp name
                    discard_part(self.files_root, file_url_path(url, self.files_root), row.id)
                continue
            if self.cancelled(row.id):
                self.finish_cancel(row)
                return
            try:
                rel = file_url_path(url, self.files_root)
                # copy to a temporary name and publish with link. Safe to call again after a crash
                room = max(0, self.max_output - used)  # negative means "no limit"; clamp at 0
                size = publish_beneath(str(pm.fs_root), path.lstrip("/"), self.files_root, rel,
                                       row.id, max_bytes=room)  # fmt: skip
            except Exception as exc:  # link, missing file, existing output, unwritable storage
                detail = getattr(exc, "msg", None) or str(exc)
                if isinstance(exc, CannotPublish):  # one reason for users (#6); logged
                    log.warning("output %s of %s: %s", path, row.id, exc.__cause__)
                if isinstance(exc, TooLarge):
                    detail = f"outputs are larger than {self.max_output} bytes in total"  # D-044
                if isinstance(exc, MissingSource):  # stays TES-ST-02; WES tells (D-026)
                    self.db.add_log_metadata(row.id, ATTEMPT, "yai.missing_outputs", path)
                self.fail(row.id, "RUNNING", "SYSTEM_ERROR", f"output {path}: {detail}")
                return
            self.fault("before_output_record")  # after publishing, before recording
            self.db.record_output(row, path, url, size)
            used += size
            discard_part(self.files_root, rel, row.id)
            self.fault("after_output_copy")
        self.fault("before_complete_write")
        if self.db.transition(row.id, "RUNNING", "COMPLETE"):
            self.finish(row.id)
        else:
            self.finish_cancel(row)

    # --- finish ---
    def fail(self, task_id: str, frm: str, to: str, reason: str) -> None:
        self.db.add_system_log(task_id, ATTEMPT, reason)
        if self.db.transition(task_id, frm, to):
            self.finish(task_id)
        else:
            row = self.db.get(task_id)
            if row and row.state == "CANCELING":
                self.finish_cancel(row)

    def system_error(self, task_id: str, reason: str) -> None:
        row = self.db.get(task_id)
        if row and row.state in ("INITIALIZING", "RUNNING"):
            self.fail(task_id, row.state, "SYSTEM_ERROR", reason)

    def finish_cancel(self, row: TaskRow) -> None:
        # stop only the process matching the start marker (not a reused PID)
        for idx, done in self.db.executions(row.id, ATTEMPT).items():
            started = read_started(self.log_dir(row.id, idx)) if done.phase != "finished" else None
            if started is not None and started.running():
                kill_group(started.pid)
        if self.db.transition(row.id, "CANCELING", "CANCELED"):
            self.db.add_system_log(row.id, ATTEMPT, "canceled")
            self.finish(row.id)

    def finish(self, task_id: str) -> None:
        """Cleanup after writing the final state: remove the work dir. Idempotent.
        Signed URL query parts were removed in the same write as the final state (TesDb.transition).
        If this crashes, recover on restart picks up finished tasks whose work dir remains."""
        self.fault("before_cleanup")
        self.db.end_log(task_id, ATTEMPT, int(time.time()))
        shutil.rmtree(self.task_dir(task_id), ignore_errors=True)


def main(config: dict[str, Any], config_path: str | None = None) -> None:
    setup_logging()
    startup.check("tes-worker", config)
    startup.log_start("tes-worker", config, config_path)
    data_dir = Path(config.get("data_dir", "var/tes"))
    TesWorker(config, TesDb(data_dir / "tes.sqlite")).serve_forever()
