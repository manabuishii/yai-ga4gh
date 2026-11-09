"""Runner plug-in point (D-024). Stage 1 is the local process (LocalProcessRunner).

There is one plug-in point, Runner. The Docker runner turns the same PathMap into
`-v <host>:<container>` behind the same interface (the work dir must be visible to the host, D-024).
"""

from __future__ import annotations

import errno
import os
import posixpath
import re
import signal
import stat
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

TAIL = 64 * 1024  # tail of stdout/stderr (provisional, tes.md section 5)
ALREADY_STARTED = 75  # wrap: a start marker already existed, so nothing was run


@dataclass
class PathMap:
    """Container path -> host path. Placed under the per-task root <work_dir>/<task_id>/fs."""

    fs_root: Path
    declared: list[str] = field(default_factory=list)
    dirs: list[str] = field(default_factory=list)  # declared dirs (volumes, workdir)

    def host(self, container_path: str) -> Path:
        return self.fs_root / container_path.lstrip("/")

    def add(self, container_path: str) -> None:
        if container_path not in self.declared:
            self.declared.append(container_path)

    def add_dir(self, container_path: str) -> None:
        """Declared directories (volumes, executor workdir). Whether a path is a file is not judged
        from host state (not affected by what a container placed there, D-041)."""
        self.add(container_path)
        if container_path not in self.dirs:
            self.dirs.append(container_path)

    def mount_dirs(self) -> list[str]:
        """Directories to mount (container paths): declared directories plus parents of files,
        nested ones collapsed to the parent. Fixed for the whole task (not per executor)."""
        dirs = {p.rstrip("/") if p in self.dirs else posixpath.dirname(p) for p in self.declared}
        return sorted(d for d in dirs if not any(d.startswith(o + "/") for o in dirs))

    def rewrite(self, value: str) -> str:
        """Replace declared path strings with host paths (D-024). Scans the original string once and
        at each position replaces the longest matching declaration. Replaced host paths are not
        rescanned (no double replacement for nested declarations or a work_dir containing one)."""
        if not self.declared:
            return value
        longest_first = sorted(self.declared, key=len, reverse=True)
        pattern = re.compile("|".join(re.escape(p) for p in longest_first))
        return pattern.sub(lambda m: str(self.host(m.group(0))), value)


@dataclass
class ExecutorResult:
    exit_code: int | None
    start: int
    end: int
    stdout_tail: str
    stderr_tail: str
    error: str | None = None  # cannot start or limit exceeded (reason for SYSTEM_ERROR)


class Runner(Protocol):
    """Run one executor and wait until it ends. Stop it when should_stop returns True.

    Two implementations: local process (LocalProcessRunner, default) and Docker (DockerRunner)."""

    def run(
        self,
        executor: dict[str, Any],
        paths: PathMap,
        *,
        log_dir: Path,
        record_start,
        should_stop,
        max_seconds: int,
        resources: dict[str, Any] | None = None,
    ) -> ExecutorResult: ...

    def resume(
        self,
        executor: dict[str, Any],
        paths: PathMap,
        *,
        log_dir: Path,
        record_start,
        should_stop,
        max_seconds: int,
    ) -> ExecutorResult | None:
        """On restart: if already started, watch it and return the result. None if not started."""
        ...

    def cleanup(self, live: set[str]) -> list[str]:
        """At startup: remove leftovers (orphans) other than those of unfinished (live) tasks."""
        ...


def open_regular(path: Path, flags: int) -> int:
    """Open regular files only (D-042). O_NONBLOCK, so a FIFO left by an earlier executor cannot
    block the slot (opening a FIFO with no reader for write gives ENXIO). Checked with fstat."""
    fd = os.open(path, flags | os.O_NONBLOCK, 0o644)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError(errno.EINVAL, "not a regular file", str(path))
        os.set_blocking(fd, True)
    except BaseException:
        os.close(fd)
        raise
    return fd


def read_tail(path: Path) -> str:
    try:
        fd = open_regular(path, os.O_RDONLY)  # no wait even if swapped for a FIFO
    except FileNotFoundError:
        return ""
    except OSError:
        return "[not a regular file]"
    with open(fd, "rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        f.seek(max(0, size - TAIL))
        return f.read().decode("utf-8", errors="replace")


@dataclass(frozen=True)
class Started:
    """Start marker (log_dir/started) written by wrap before the child runs. Detects PID reuse."""

    pid: int
    ticks: str  # starttime in /proc/<pid>/stat (since boot; differs for another process, same PID)
    at: int  # start time (UTC seconds). The run-time limit counts from here across restarts

    def running(self) -> bool:
        return proc_start(self.pid) == self.ticks


def proc_start(pid: int) -> str | None:
    try:
        with open(f"/proc/{pid}/stat") as f:
            stat = f.read()
    except OSError:
        return None
    return stat.rpartition(")")[2].split()[19]  # field 22 (counted as the 3rd after comm)


def read_started(log_dir: Path) -> Started | None:
    try:
        pid, ticks, at = (log_dir / "started").read_text().split()
        return Started(int(pid), ticks, int(at))
    except (OSError, ValueError):
        return None


def output_paths(executor: dict[str, Any], paths: PathMap, log_dir: Path) -> tuple[Path, Path]:
    out = paths.host(executor["stdout"]) if executor.get("stdout") else log_dir / "stdout"
    err = paths.host(executor["stderr"]) if executor.get("stderr") else log_dir / "stderr"
    return out, err


class LocalProcessRunner:
    """Run as a local process (D-024). No shell. image is not used."""

    def run(
        self,
        executor: dict[str, Any],
        paths: PathMap,
        *,
        log_dir: Path,
        record_start,
        should_stop,
        max_seconds: int,
        resources: dict[str, Any] | None = None,  # no limits for local processes (D-024)
    ) -> ExecutorResult:
        argv = [paths.rewrite(a) for a in executor["command"]]
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(paths.fs_root)}
        env.update({k: paths.rewrite(v) for k, v in (executor.get("env") or {}).items()})
        cwd = paths.host(executor["workdir"]) if executor.get("workdir") else paths.fs_root
        cwd.mkdir(parents=True, exist_ok=True)
        out_path, err_path = output_paths(executor, paths, log_dir)
        exit_file = log_dir / "exit_code"
        for p in (out_path, err_path):
            p.parent.mkdir(parents=True, exist_ok=True)
        start = int(time.time())
        stdin = None
        try:
            if executor.get("stdin"):
                fd = open_regular(paths.host(executor["stdin"]), os.O_RDONLY)
                stdin = open(fd, "rb")  # noqa: SIM115
            write = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
            with open(open_regular(out_path, write), "wb") as out, \
                    open(open_regular(err_path, write), "wb") as err:  # fmt: skip
                proc = subprocess.Popen(  # noqa: S603 argv passed directly (no shell)
                    [sys.executable, "-m", "yai.tes.wrap", str(exit_file), "--", *argv],
                    cwd=cwd, env=env, stdin=stdin or subprocess.DEVNULL, stdout=out, stderr=err,
                    start_new_session=True,
                )  # fmt: skip
        except OSError as exc:
            return ExecutorResult(None, start, int(time.time()), "", "", f"cannot start: {exc}")
        finally:
            if stdin:
                stdin.close()
        while (started := read_started(log_dir)) is None and proc.poll() is None:
            time.sleep(0.01)  # until wrap writes the start marker
        if started is None:
            proc.wait()
            return ExecutorResult(None, start, int(time.time()), "", "",
                                  "executor wrapper exited before starting")  # fmt: skip
        if started.pid != proc.pid:  # a wrap from before a crash came first: watch it
            proc.wait()
            proc = None
        record_start(started.pid, str(exit_file))
        return self.wait(started, exit_file, out_path, err_path, should_stop, max_seconds, proc)

    def resume(
        self,
        executor: dict[str, Any],
        paths: PathMap,
        *,
        log_dir: Path,
        record_start,
        should_stop,
        max_seconds: int,
    ) -> ExecutorResult | None:
        """Watch the process in the start marker (from wrap). No marker means not started (None)."""
        started = read_started(log_dir)
        if started is None:
            return None
        record_start(started.pid, str(log_dir / "exit_code"))
        out_path, err_path = output_paths(executor, paths, log_dir)
        return self.wait(started, log_dir / "exit_code", out_path, err_path, should_stop,
                         max_seconds)  # fmt: skip

    def cleanup(self, live: set[str]) -> list[str]:
        return []  # orphans are matched by wrap's marker and the exit code file (D-024)

    @staticmethod
    def wait(started, exit_file, out_path, err_path, should_stop, max_seconds, proc=None):
        """Wait until the marker's process ends. A different process (PID reuse) is neither
        awaited nor stopped."""
        error = None
        while True:
            # An adopted wrap is not our child, so it may remain unreaped after exit (zombie).
            # The exit code file (written last by wrap) means it has ended
            done = (proc.poll() is not None if proc is not None
                    else not started.running() or read_exit(exit_file) is not None)  # fmt: skip
            if done:
                break
            if should_stop():
                kill_group(started.pid)
                error = "canceled"
                break
            if time.time() - started.at > max_seconds:
                kill_group(started.pid)
                error = "time limit exceeded"
                break
            time.sleep(0.2)
        if proc is not None:
            proc.wait()
        code = read_exit(exit_file)
        if code is None and error is None:
            error = "executor exited without an exit code"
        return ExecutorResult(code, started.at, int(time.time()), read_tail(out_path),
                              read_tail(err_path), error)  # fmt: skip


def read_exit(exit_file: Path) -> int | None:
    try:
        return int(Path(exit_file).read_text().strip())
    except (FileNotFoundError, ValueError):
        return None


def kill_group(pid: int) -> None:
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pid, sig)
        except ProcessLookupError:
            return
        time.sleep(0.3)


def container_dirs(task: dict[str, Any]) -> list[str]:
    """Directories created in advance (volumes, each executor's workdir, parents of outputs, stdout
    and stderr). Every place the runner mounts is created before any executor runs (D-041)."""
    out = list(task.get("volumes") or [])
    out += [posixpath.dirname(o["path"]) for o in task.get("outputs") or []]
    for ex in task.get("executors") or []:
        if ex.get("workdir"):
            out.append(ex["workdir"])
        out += [posixpath.dirname(ex[k]) for k in ("stdout", "stderr") if ex.get(k)]
    return out
