"""Docker runner (groundwork for stage 2, D-034). The second Runner plug-in point (D-024).

The default stays the local process (LocalProcessRunner).
Selected with the setting `runner: {kind: docker, docker: {...}}`.
Where the Docker daemon lives (rootless, Podman, dedicated VM, dind) is undecided, so this works
with any of them:

- The target follows the docker CLI convention: env var `DOCKER_HOST` (override: `docker_host`).
    The socket location is written neither in this code nor in compose
- Host paths given to `-v` are "paths as seen by the Docker daemon". When they differ from the
    runner container's paths, `host_work_dir` remaps the work dir root (dind, other VM, container)
- `--user` so that created files keep the right owner
    (default: the runner's own uid:gid; "0:0" under rootless)
- No `--privileged`, host network or host pid. The network is disabled
    (the runner fetches the inputs)

Container names are `yai-<task_id>-<attempt>-<executor_index>` (a name can be created only once,
so there is no double start, tes.md section 4). No `--rm`: remove after reading the exit code.
On restart, containers are matched by name.

Rules for handling user-supplied values on the host side (D-041):
- `image` is checked against Docker reference grammar on submit (`yai/common/imageref.py`) and
    placed after `--` in argv (a value starting with `-` is never read as an option)
- Mounted directories come only from the task's declaration (`PathMap.mount_dirs`), never from
    host state (`is_dir`). No start if a mount source is a link. The set is the same for every
    executor, so what an earlier executor left inside cannot change the next mount sources
- `docker logs` writes into the unmounted log_dir, then copies to the declared stdout/stderr
    without following links (removing any existing entry by name first, then O_EXCL)
"""

from __future__ import annotations

import os
import stat
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from yai.common.safefs import UnsafePath, copy_beneath, ensure_dir_beneath
from yai.tes.runner import ExecutorResult, PathMap, read_tail

LABEL = "yai.tes"
DEFAULT_CPUS = 1.0  # tes.md section 2: "a finite default even when omitted"
DEFAULT_RAM_GB = 2.0
PIDS_LIMIT = 256
LOG_MAX = "10m"

Call = Callable[..., subprocess.CompletedProcess]


class DockerError(Exception):
    pass


class DockerRunner:
    """Runner that calls the docker CLI. `call` is shaped like subprocess.run (swapped in tests)."""

    def __init__(self, config: dict[str, Any] | None = None, call: Call | None = None,
                 sleep: Callable[[float], None] = time.sleep):  # fmt: skip
        cfg = dict(config or {})
        self.docker = str(cfg.get("binary", "docker"))
        self.docker_host = cfg.get("docker_host")  # else env DOCKER_HOST (or the CLI default)
        self.work_dir = cfg.get("work_dir")  # work dir root seen by the runner (set by worker)
        # The same place as seen by the Docker daemon. Otherwise the env var (from compose,
        # deploy/compose.yaml)
        self.host_work_dir = cfg.get("host_work_dir") or os.environ.get("YAI_TES_HOST_WORK_DIR")
        user = cfg.get("user", f"{os.getuid()}:{os.getgid()}")
        self.user = str(user) if user else None
        self.network = str(cfg.get("network", "none"))
        self.extra_labels = dict(cfg.get("labels") or {})
        self.poll = float(cfg.get("poll_seconds", 0.5))
        self.call = call or subprocess.run
        self.sleep = sleep

    # --- docker CLI ---
    def _env(self) -> dict[str, str]:
        env = dict(os.environ)
        if self.docker_host:
            env["DOCKER_HOST"] = str(self.docker_host)
        return env

    def _docker(self, *args: str, check: bool = True, **kw: Any) -> subprocess.CompletedProcess:
        kw.setdefault("capture_output", "stdout" not in kw)
        kw.setdefault("text", True)
        proc = self.call([self.docker, *args], env=self._env(), check=False, **kw)
        if check and proc.returncode != 0:
            err = (proc.stderr or "").strip() if isinstance(proc.stderr, str) else ""
            raise DockerError(f"docker {args[0]} failed ({proc.returncode}): {err[:300]}")
        return proc

    # --- paths ---
    def host_path(self, local: Path) -> str:
        """Runner path -> path as seen by the Docker daemon (remapped if host_work_dir is set)."""
        text = str(local)
        if self.host_work_dir and self.work_dir:
            root = str(Path(self.work_dir))
            if text == root or text.startswith(root + "/"):
                return str(self.host_work_dir).rstrip("/") + text[len(root) :]
        return text

    def mounts(self, executor: dict[str, Any], paths: PathMap) -> list[tuple[str, str]]:
        """Directories to mount (host, container). Decided only from the declaration (volumes and
        workdir are directories; inputs, outputs, stdout and stderr are files, so their parents);
        nested ones collapse to the parent (under fs_root the layout matches the container, so the
        parent suffices). Host state is not consulted (D-041). The worker adds the executor workdir
        to the declaration (added here if missing, for direct calls)."""
        if executor.get("workdir") and executor["workdir"] not in paths.dirs:
            paths.add_dir(executor["workdir"])
        top = paths.mount_dirs()
        if "" in top or "/" in top:
            raise DockerError("cannot mount the container root; use a directory below /")
        paths.fs_root.mkdir(parents=True, exist_ok=True)  # made by the worker (for direct calls)
        out = []
        for d in top:
            host = paths.host(d)
            try:
                ensure_dir_beneath(str(paths.fs_root), d.lstrip("/"))
                st = os.lstat(host)
            except (UnsafePath, OSError) as exc:
                raise DockerError(f"mount source {d} is not a plain directory: {exc}") from exc
            if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
                raise DockerError(f"mount source {d} is not a plain directory")
            out.append((self.host_path(host), d))
        return out

    # --- Runner ---
    @staticmethod
    def container_name(log_dir: Path) -> str:
        """Build fixed names from log_dir = <work_dir>/<task_id>/logs/<idx> (worker.log_dir)."""
        return f"yai-{log_dir.parent.parent.name}-0-{log_dir.name}"

    def argv(self, executor: dict[str, Any], paths: PathMap, name: str,
             resources: dict[str, Any] | None) -> list[str]:  # fmt: skip
        if executor.get("stdin"):
            raise DockerError("stdin is not supported by the docker runner yet")  # prov. (D-034)
        res = resources or {}
        cpus = float(res.get("cpu_cores") or DEFAULT_CPUS)
        ram = float(res.get("ram_gb") or DEFAULT_RAM_GB)
        if int(ram * 1024) < 6:  # 0m = no limit in Docker (D-060 (1)); rejected on submit
            raise DockerError(f"ram_gb {ram:g} is below 6 MiB")
        task_id = name.split("-")[1]
        args = ["run", "-d", "--name", name, "--label", f"{LABEL}=1",
                "--label", f"{LABEL}.task={task_id}",
                "--network", self.network, "--cpus", f"{cpus:g}",
                "--memory", f"{int(ram * 1024)}m", "--pids-limit", str(PIDS_LIMIT),
                "--log-opt", f"max-size={LOG_MAX}",
                "--security-opt", "no-new-privileges", "--cap-drop", "ALL"]  # fmt: skip
        for k, v in sorted(self.extra_labels.items()):
            args += ["--label", f"{k}={v}"]
        if self.user:
            args += ["--user", self.user]
        if executor.get("workdir"):
            args += ["--workdir", executor["workdir"]]
        for k, v in sorted((executor.get("env") or {}).items()):
            args += ["--env", f"{k}={v}"]
        for host, container in self.mounts(executor, paths):
            args += ["--volume", f"{host}:{container}"]
        # After `--` come image and command. A value starting with `-` that slipped past the submit
        # check (imageref) is still not an option ("invalid reference format" on docker 20.10/27,
        # D-041)
        args += ["--entrypoint", executor["command"][0], "--", executor["image"]]
        return args + list(executor["command"][1:])

    def run(self, executor: dict[str, Any], paths: PathMap, *, log_dir: Path, record_start,
            should_stop, max_seconds: int, resources: dict[str, Any] | None = None,
            ) -> ExecutorResult:  # fmt: skip
        name = self.container_name(log_dir)
        start = int(time.time())
        try:
            argv = self.argv(executor, paths, name, resources)  # mounts checks the mount points
            self._docker(*argv)
        except DockerError as exc:
            return ExecutorResult(None, start, int(time.time()), "", "", f"cannot start: {exc}")
        record_start(0, f"docker:{name}")
        return self.wait(executor, paths, log_dir, name, start, should_stop, max_seconds)

    def resume(self, executor: dict[str, Any], paths: PathMap, *, log_dir: Path, record_start,
               should_stop, max_seconds: int) -> ExecutorResult | None:  # fmt: skip
        """On restart, watch the container with the fixed name if any. None if not started."""
        name = self.container_name(log_dir)
        state = self.inspect(name)
        if state is None:
            return None
        record_start(0, f"docker:{name}")
        return self.wait(executor, paths, log_dir, name, state[2], should_stop, max_seconds)

    def inspect(self, name: str) -> tuple[str, int, int] | None:
        """(state, exit code, start time in UTC seconds). None if absent."""
        proc = self._docker("inspect", "--format",
                            "{{.State.Status}} {{.State.ExitCode}} {{.State.StartedAt}}", name,
                            check=False)  # fmt: skip
        if proc.returncode != 0:
            return None
        status, code, started = (proc.stdout or "").split()[:3]
        return status, int(code), _epoch(started)

    def wait(self, executor, paths, log_dir, name, start, should_stop, max_seconds):
        error = None
        while True:
            state = self.inspect(name)
            if state is None:
                return ExecutorResult(None, start, int(time.time()), "", "",
                                      "container disappeared")  # fmt: skip
            if state[0] in ("exited", "dead"):
                break
            if should_stop():
                self._docker("kill", name, check=False)
                error = "canceled"
                break
            if time.time() - start > max_seconds:
                self._docker("kill", name, check=False)
                error = "time limit exceeded"
                break
            self.sleep(self.poll)
        final = self.inspect(name)
        code = final[1] if final and final[0] in ("exited", "dead") and error is None else None
        # Logs go to the unmounted log_dir (written only by the runner), then are copied to the
        # declared stdout/stderr without following links (never write through a container's link,
        # D-041)
        out_path, err_path = log_dir / "stdout", log_dir / "stderr"
        log_dir.mkdir(parents=True, exist_ok=True)
        with open(out_path, "wb") as out, open(err_path, "wb") as err:
            self._docker("logs", name, check=False, stdout=out, stderr=err, text=False)
        self._docker("rm", "-f", name, check=False)
        for key, src in (("stdout", out_path), ("stderr", err_path)):
            if executor.get(key) and error is None:
                try:
                    copy_beneath(str(log_dir), src.name, str(paths.fs_root),
                                 executor[key].lstrip("/"), replace=True)  # fmt: skip
                except UnsafePath as exc:
                    error = f"{key} {executor[key]}: {exc}"
        if code is None and error is None:
            error = "container ended without an exit code"
        return ExecutorResult(code, start, int(time.time()), read_tail(out_path),
                              read_tail(err_path), error)  # fmt: skip

    def cleanup(self, live: set[str]) -> list[str]:
        """Remove yai-labelled containers except those of unfinished tasks (orphans)."""
        proc = self._docker("ps", "-a", "--filter", f"label={LABEL}=1", "--format",
                            "{{.Names}}", check=False)  # fmt: skip
        gone = []
        for name in (proc.stdout or "").split():
            parts = name.split("-")
            if len(parts) >= 2 and parts[1] not in live:
                self._docker("rm", "-f", name, check=False)
                gone.append(name)
        return gone


def _epoch(text: str) -> int:
    """docker StartedAt (RFC 3339, down to ns) -> UTC seconds. Now if unreadable."""
    from datetime import datetime

    try:
        head = text.split(".")[0].rstrip("Z")
        return int(datetime.fromisoformat(head + "+00:00").timestamp())
    except ValueError:
        return int(time.time())
