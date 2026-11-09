"""Unit tests for the Docker runner (D-034). The docker CLI is not called; calls are replaced.

Checks: the docker run arguments (limits, network off, name, labels, --user, mounted paths,
how DOCKER_HOST is passed), reading the exit code and stdout, cancel and timeout, reconciling
after restart, and orphan cleanup. Running real containers (NEG-LIMITS limits apply, nothing is
left in E2E-06) is covered once the Docker setup is fixed (the docker-marked tests in test_e2e.py).
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from yai.tes.docker_runner import DockerError, DockerRunner
from yai.tes.runner import PathMap
from yai.tes.worker import make_runner


class FakeDocker:
    """A fake docker CLI. Records calls and holds container state."""

    def __init__(self):
        self.calls: list[list[str]] = []
        self.envs: list[dict[str, str]] = []
        self.containers: dict[str, dict[str, Any]] = {}
        self.polls_until_exit = 1
        self.exit_code = 0
        self.fail_run = False

    def __call__(self, argv: list[str], env=None, check=False, **kw) -> subprocess.CompletedProcess:
        self.calls.append(argv)
        self.envs.append(dict(env or {}))
        cmd, args = argv[1], argv[2:]
        out, code = "", 0
        if cmd == "run":
            name = args[args.index("--name") + 1]
            if self.fail_run or name in self.containers:
                return subprocess.CompletedProcess(argv, 125, "", "Conflict. name in use")
            self.containers[name] = {"status": "running", "polls": 0, "code": 0}
            out = "cid\n"
        elif cmd == "inspect":
            c = self.containers.get(args[-1])
            if c is None:
                return subprocess.CompletedProcess(argv, 1, "", "No such object")
            if c["status"] == "running":
                c["polls"] += 1
                if c["polls"] > self.polls_until_exit:
                    c["status"], c["code"] = "exited", self.exit_code
            out = f"{c['status']} {c['code']} 2026-10-17T00:00:00.123456789Z\n"
        elif cmd == "kill":
            c = self.containers[args[-1]]
            c["status"], c["code"] = "exited", 137
        elif cmd == "logs":
            kw["stdout"].write(b"hello\n")
            kw["stderr"].write(b"warn\n")
        elif cmd == "rm":
            self.containers.pop(args[-1], None)
        elif cmd == "ps":
            out = "\n".join(self.containers) + "\n"
        return subprocess.CompletedProcess(argv, code, out, "")

    def run_args(self) -> list[str]:
        return next(c for c in self.calls if c[1] == "run")


@pytest.fixture
def fake():
    return FakeDocker()


def task_paths(tmp: Path) -> tuple[PathMap, Path]:
    work = tmp / "work"
    log_dir = work / "t01abc" / "logs" / "0"
    log_dir.mkdir(parents=True)
    pm = PathMap(work / "t01abc" / "fs")
    for p in ("/yai/inputs/infile/in.txt", "/yai/work/out.txt"):
        pm.add(p)
    pm.add_dir("/yai/work")
    pm.host("/yai/inputs/infile").mkdir(parents=True)
    pm.host("/yai/inputs/infile/in.txt").write_text("x")
    return pm, log_dir


EXECUTOR = {"image": "alpine:3.20", "command": ["cat", "/yai/inputs/infile/in.txt"],
            "workdir": "/yai/work", "stdout": "/yai/work/out.txt", "env": {"A": "1"}}  # fmt: skip


def runner(fake: FakeDocker, tmp: Path, **cfg: Any) -> DockerRunner:
    return DockerRunner({"work_dir": str(tmp / "work"), "poll_seconds": 0, **cfg}, call=fake,
                        sleep=lambda _s: None)  # fmt: skip


def test_run_args_have_limits_no_network_and_fixed_name(fake, tmp_path):
    pm, log_dir = task_paths(tmp_path)
    started: list[tuple[int, str]] = []
    res = runner(fake, tmp_path).run(
        EXECUTOR, pm, log_dir=log_dir, record_start=lambda p, e: started.append((p, e)),
        should_stop=lambda: False, max_seconds=60, resources={"cpu_cores": 2},
    )  # fmt: skip
    args = fake.run_args()
    assert res.exit_code == 0 and res.error is None
    assert started == [(0, "docker:yai-t01abc-0-0")]
    name = args[args.index("--name") + 1]
    assert name == "yai-t01abc-0-0"  # fixed name from tes.md section 4 (no double start)
    joined = " ".join(args)
    assert "--network none" in joined
    assert "--cpus 2 " in joined and "--memory 2048m" in joined  # omitted ram defaults to 2 GB
    assert "--pids-limit 256" in joined and "--log-opt max-size=10m" in joined
    assert "--label yai.tes=1" in joined and "--label yai.tes.task=t01abc" in joined
    assert "--privileged" not in joined and "--network host" not in joined
    assert "--pid" not in args and "/var/run/docker.sock" not in joined
    assert args[args.index("--entrypoint") + 1:] == ["cat", "--", "alpine:3.20",
                                                     "/yai/inputs/infile/in.txt"]  # fmt: skip
    assert "--env" in args and "A=1" in args
    # when finished, stdout goes to the file and the container is removed
    assert (pm.host("/yai/work/out.txt")).read_text() == "hello\n"
    assert res.stdout_tail == "hello\n" and res.stderr_tail == "warn\n"
    assert fake.containers == {}
    assert ["docker", "rm", "-f", "yai-t01abc-0-0"] in fake.calls


def test_mounts_are_directories_merged_and_translated_for_the_daemon(fake, tmp_path):
    """The host side of -v is the path seen by the Docker daemon (via host_work_dir, D-034)."""
    pm, _ = task_paths(tmp_path)
    r = runner(fake, tmp_path, host_work_dir="/srv/yai/tes-work")
    mounts = r.mounts(EXECUTOR, pm)
    assert mounts == [
        ("/srv/yai/tes-work/t01abc/fs/yai/inputs/infile", "/yai/inputs/infile"),
        ("/srv/yai/tes-work/t01abc/fs/yai/work", "/yai/work"),  # out.txt is under /yai/work
    ]
    without = runner(fake, tmp_path).mounts(EXECUTOR, pm)
    assert without[0][0] == str(pm.host("/yai/inputs/infile"))


def test_docker_host_comes_from_env_or_config_not_hardcoded(fake, tmp_path, monkeypatch):
    pm, log_dir = task_paths(tmp_path)
    monkeypatch.setenv("DOCKER_HOST", "tcp://docker.yai.test:2376")
    runner(fake, tmp_path).run(EXECUTOR, pm, log_dir=log_dir, record_start=lambda *_: None,
                               should_stop=lambda: False, max_seconds=60)  # fmt: skip
    assert fake.envs[0]["DOCKER_HOST"] == "tcp://docker.yai.test:2376"
    fake2 = FakeDocker()
    r = DockerRunner({"docker_host": "unix:///run/user/1000/docker.sock"}, call=fake2)
    r.inspect("x")
    assert fake2.envs[0]["DOCKER_HOST"] == "unix:///run/user/1000/docker.sock"


def test_user_defaults_to_worker_ids_and_can_be_root_for_rootless(fake, tmp_path):
    import os

    pm, _ = task_paths(tmp_path)
    args = runner(fake, tmp_path).argv(EXECUTOR, pm, "yai-t01abc-0-0", None)
    assert args[args.index("--user") + 1] == f"{os.getuid()}:{os.getgid()}"
    args = runner(fake, tmp_path, user="0:0").argv(EXECUTOR, pm, "yai-t01abc-0-0", None)
    assert args[args.index("--user") + 1] == "0:0"
    args = runner(fake, tmp_path, user="").argv(EXECUTOR, pm, "yai-t01abc-0-0", None)
    assert "--user" not in args


def test_nonzero_exit_code_is_returned(fake, tmp_path):
    pm, log_dir = task_paths(tmp_path)
    fake.exit_code = 3
    res = runner(fake, tmp_path).run(EXECUTOR, pm, log_dir=log_dir, record_start=lambda *_: None,
                                     should_stop=lambda: False, max_seconds=60)  # fmt: skip
    assert res.exit_code == 3 and res.error is None


def test_cancel_and_time_limit_kill_the_container(fake, tmp_path):
    pm, log_dir = task_paths(tmp_path)
    fake.polls_until_exit = 99
    res = runner(fake, tmp_path).run(EXECUTOR, pm, log_dir=log_dir, record_start=lambda *_: None,
                                     should_stop=lambda: True, max_seconds=60)  # fmt: skip
    assert res.error == "canceled" and res.exit_code is None
    assert ["docker", "kill", "yai-t01abc-0-0"] in fake.calls
    assert fake.containers == {}  # removed after stopping too (E2E-06 "no container left")
    res = runner(fake, tmp_path).run(EXECUTOR, pm, log_dir=log_dir, record_start=lambda *_: None,
                                     should_stop=lambda: False, max_seconds=-1)  # fmt: skip
    assert res.error == "time limit exceeded"


@pytest.mark.parametrize("how", ["canceled", "time limit exceeded"])
def test_cancel_and_time_limit_do_not_copy_logs_to_the_declared_place(fake, tmp_path, how):
    """NEG-CANCEL-NO-COPY (D-041 (2)): on cancel or timeout, do not copy to the declared stdout
    and stderr locations. Logs go to the unmounted log_dir; the tail is returned in the result
    (needed for the state record)."""
    pm, log_dir = task_paths(tmp_path)
    pm.add("/yai/work/err.txt")
    fake.polls_until_exit = 99
    ex = {**EXECUTOR, "stderr": "/yai/work/err.txt"}
    res = runner(fake, tmp_path).run(
        ex, pm, log_dir=log_dir, record_start=lambda *_: None,
        should_stop=lambda: how == "canceled", max_seconds=60 if how == "canceled" else -1,
    )  # fmt: skip
    assert res.error == how
    assert not pm.host("/yai/work/out.txt").exists()
    assert not pm.host("/yai/work/err.txt").exists()
    assert (log_dir / "stdout").read_bytes() == b"hello\n" and res.stdout_tail == "hello\n"
    assert res.stderr_tail == "warn\n"
    # compare: a normal finish copies
    fake.polls_until_exit = 1
    ok = runner(fake, tmp_path).run(ex, pm, log_dir=log_dir, record_start=lambda *_: None,
                                    should_stop=lambda: False, max_seconds=60)  # fmt: skip
    assert ok.error is None and pm.host("/yai/work/out.txt").read_bytes() == b"hello\n"
    assert pm.host("/yai/work/err.txt").read_bytes() == b"warn\n"


def test_start_failure_is_a_system_error_reason(fake, tmp_path):
    pm, log_dir = task_paths(tmp_path)
    fake.fail_run = True
    res = runner(fake, tmp_path).run(EXECUTOR, pm, log_dir=log_dir, record_start=lambda *_: None,
                                     should_stop=lambda: False, max_seconds=60)  # fmt: skip
    assert res.exit_code is None and res.error and res.error.startswith("cannot start")


def test_stdin_and_root_mount_are_refused(fake, tmp_path):
    pm, _ = task_paths(tmp_path)
    with pytest.raises(DockerError, match="stdin"):
        runner(fake, tmp_path).argv({**EXECUTOR, "stdin": "/yai/work/in"}, pm, "n-t-0-0", None)
    pm.add("/top.txt")
    with pytest.raises(DockerError, match="root"):
        runner(fake, tmp_path).mounts(EXECUTOR, pm)


def test_resume_watches_the_named_container_or_reports_not_started(fake, tmp_path):
    pm, log_dir = task_paths(tmp_path)
    r = runner(fake, tmp_path)
    kw: dict[str, Any] = dict(log_dir=log_dir, record_start=lambda *_: None,
                              should_stop=lambda: False, max_seconds=60)  # fmt: skip
    assert r.resume(EXECUTOR, pm, **kw) is None  # not started yet -> the runner starts it
    fake.containers["yai-t01abc-0-0"] = {"status": "running", "polls": 0, "code": 0}
    res = r.resume(EXECUTOR, pm, **kw)
    assert res is not None and res.exit_code == 0
    assert not any(c[1] == "run" for c in fake.calls)  # no double start


# --- D-041 (security review): where user values are handled on the host side


def test_image_comes_after_double_dash_even_if_it_starts_with_a_dash(fake, tmp_path):
    """Image names starting with `-` (D-041): even if a value slips past the intake check
    (imageref), it comes after `--`, so it does not become a docker option."""
    pm, _ = task_paths(tmp_path)
    ex = {**EXECUTOR, "image": "--privileged", "command": ["-v", "/:/host", "sh"]}
    args = runner(fake, tmp_path).argv(ex, pm, "yai-t01abc-0-0", None)
    i = args.index("--")
    assert args[i + 1:] == ["--privileged", "/:/host", "sh"]  # fmt: skip
    assert args[i - 2 : i] == ["--entrypoint", "-v"]  # command[0] is the --entrypoint value
    assert "--privileged" not in args[:i]


def test_stdout_symlink_planted_by_the_container_is_not_followed(fake, tmp_path):
    """Links at the stdout location (D-041): do not write through a link the container placed
    at the stdout location (to another file the worker can write). Logs go to the unmounted
    log_dir; at the declared location the link is removed by name and a regular file is
    placed."""
    pm, log_dir = task_paths(tmp_path)
    victim = tmp_path / "victim.txt"
    victim.write_text("keep me")
    pm.host("/yai/work").mkdir(parents=True, exist_ok=True)
    pm.host("/yai/work/out.txt").symlink_to(victim)  # link "placed by the container"
    res = runner(fake, tmp_path).run(EXECUTOR, pm, log_dir=log_dir, record_start=lambda *_: None,
                                     should_stop=lambda: False, max_seconds=60)  # fmt: skip
    assert victim.read_text() == "keep me"
    assert res.error is None and res.exit_code == 0
    out = pm.host("/yai/work/out.txt")
    assert not out.is_symlink() and out.read_text() == "hello\n"
    assert (log_dir / "stdout").read_bytes() == b"hello\n" and res.stdout_tail == "hello\n"
    assert fake.containers == {}


def test_stdout_fifo_or_file_left_by_the_container_is_replaced_not_opened(fake, tmp_path):
    """Links at the stdout location (D-041): an existing entry is removed by name before
    creating (do not open a FIFO and wait for a reader; a regular file is replaced)."""
    import os

    pm, log_dir = task_paths(tmp_path)
    pm.host("/yai/work").mkdir(parents=True, exist_ok=True)
    os.mkfifo(pm.host("/yai/work/out.txt"))
    kw: dict[str, Any] = dict(log_dir=log_dir, record_start=lambda *_: None,
                              should_stop=lambda: False, max_seconds=60)  # fmt: skip
    res = runner(fake, tmp_path).run(EXECUTOR, pm, **kw)
    assert res.error is None and pm.host("/yai/work/out.txt").read_text() == "hello\n"
    pm.host("/yai/work/out.txt").write_text("old")
    res = runner(FakeDocker(), tmp_path).run(EXECUTOR, pm, **kw)
    assert res.error is None and pm.host("/yai/work/out.txt").read_text() == "hello\n"
    pm.host("/yai/work/out.txt").unlink()
    pm.host("/yai/work/out.txt").mkdir()  # a directory is not replaced
    res = runner(FakeDocker(), tmp_path).run(EXECUTOR, pm, **kw)
    assert res.error and "cannot replace" in res.error


def test_mount_set_is_fixed_by_the_declaration_not_by_what_the_container_left(fake, tmp_path):
    """Only output /safe/item is declared -> mount the parent /safe. Even if the first
    executor turns item into a link (../../.. = the work_dir of all tasks), the second does not
    mount item itself.
    If the mount source itself is a link, do not start."""
    work = tmp_path / "work"
    pm = PathMap(work / "t01abc" / "fs")
    pm.add("/safe/item")
    ex = {"image": "alpine:3.20", "command": ["true"]}
    r = runner(fake, tmp_path)
    assert r.mounts(ex, pm) == [(str(pm.host("/safe")), "/safe")]
    pm.host("/safe/item").symlink_to("../../..")  # placed by the first executor
    assert pm.host("/safe/item").is_dir()  # before the fix, this made /safe/item the choice
    assert r.mounts(ex, pm) == [(str(pm.host("/safe")), "/safe")]
    other = {**ex, "workdir": "/safe/item/deep"}  # the second workdir is under the link
    log_dir = work / "t01abc" / "logs" / "1"
    log_dir.mkdir(parents=True)
    res = r.run(other, pm, log_dir=log_dir, record_start=lambda *_: None,
                should_stop=lambda: False, max_seconds=60)  # fmt: skip
    assert res.error is None
    assert r.mounts(other, pm) == [(str(pm.host("/safe")), "/safe")]  # not /safe/item/deep
    assert not (work / "deep").exists()  # before the fix, mkdir followed the link into work_dir
    pm2 = PathMap(work / "t02abc" / "fs")
    pm2.add_dir("/data")
    pm2.fs_root.mkdir(parents=True)
    pm2.host("/data").symlink_to(tmp_path)
    with pytest.raises(DockerError, match="not a plain directory"):
        r.mounts(ex, pm2)


def test_cleanup_removes_only_orphans(fake, tmp_path):
    fake.containers = {"yai-tlive-0-0": {}, "yai-tgone-0-0": {}, "yai-tgone-0-1": {}}
    gone = runner(fake, tmp_path).cleanup({"tlive"})
    assert sorted(gone) == ["yai-tgone-0-0", "yai-tgone-0-1"]
    assert list(fake.containers) == ["yai-tlive-0-0"]


def test_worker_default_runner_is_still_local_process(tmp_path):
    from yai.tes.runner import LocalProcessRunner

    assert isinstance(make_runner({}, tmp_path), LocalProcessRunner)
    r = make_runner({"runner": {"kind": "docker", "docker": {"host_work_dir": "/h"}}}, tmp_path)
    assert isinstance(r, DockerRunner) and r.work_dir == str(tmp_path)
    with pytest.raises(SystemExit):
        make_runner({"runner": {"kind": "podman"}}, tmp_path)


def test_NEG_RAM_MIN_argv(fake, tmp_path):
    """D-060 (1): the runner also never builds --memory 0m from under 6 MiB
    (even if a value that would fail intake arrives)."""
    pm, _ = task_paths(tmp_path)
    with pytest.raises(DockerError, match="ram_gb"):
        runner(fake, tmp_path).argv(EXECUTOR, pm, "yai-t01abc-0-0", {"ram_gb": 0.0005})
    args = runner(fake, tmp_path).argv(EXECUTOR, pm, "yai-t01abc-0-0", {"ram_gb": 6 / 1024})
    assert args[args.index("--memory") + 1] == "6m"
