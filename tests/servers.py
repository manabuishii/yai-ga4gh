"""Start services in separate processes (tests call them over HTTP, test-plan.md intro)."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import yaml

ROOT = Path(__file__).resolve().parent.parent
BASES = {
    "aai": "",
    "drs": "/ga4gh/drs/v1",
    "tes": "/ga4gh/tes/v1",
    "wes": "/ga4gh/wes/v1",
}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Server:
    def __init__(self, service: str, config: dict, workdir: Path, port: int | None = None):
        self.service = service
        self.config = config
        self.workdir = workdir
        self.port = port or free_port()
        self.proc: subprocess.Popen | None = None
        self.log_path = workdir / f"{service}-{self.port}.log"

    @property
    def root(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def url(self) -> str:
        return self.root + BASES[self.service]

    def start(self) -> Server:
        self.workdir.mkdir(parents=True, exist_ok=True)
        cfg = self.workdir / f"{self.service}-{self.port}.yaml"
        cfg.write_text(yaml.safe_dump(self.config, allow_unicode=True), encoding="utf-8")
        env = dict(os.environ, PYTHONUNBUFFERED="1")
        args = [sys.executable, "-m", "yai", "serve", self.service]
        args += ["--config", str(cfg), "--port", str(self.port)]
        with open(self.log_path, "ab") as log:
            self.proc = subprocess.Popen(
                args, cwd=self.workdir, env=env, stdout=log, stderr=subprocess.STDOUT
            )
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"{self.service} exited: {self.log_path.read_text()[-2000:]}")
            try:
                httpx.get(self.root + "/", timeout=0.5)
                return self
            except httpx.TransportError:
                time.sleep(0.1)
        self.stop()  # do not leave it half-started
        raise RuntimeError(f"{self.service} did not start")

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    def log(self) -> str:
        return self.log_path.read_text(encoding="utf-8", errors="replace")


class Worker:
    """Start yai tes worker in a separate process (using the same config file as the API)."""

    def __init__(self, config: dict, workdir: Path, name: str = "worker"):
        self.config = config
        self.workdir = workdir
        self.name = name
        self.proc: subprocess.Popen | None = None
        self.log_path = workdir / f"{name}.log"

    def start(self) -> Worker:
        self.workdir.mkdir(parents=True, exist_ok=True)
        cfg = self.workdir / f"{self.name}.yaml"
        cfg.write_text(yaml.safe_dump(self.config, allow_unicode=True), encoding="utf-8")
        env = dict(os.environ, PYTHONUNBUFFERED="1")
        args = [sys.executable, "-m", "yai", "tes", "worker", "--config", str(cfg)]
        with open(self.log_path, "ab") as log:
            self.proc = subprocess.Popen(
                args, cwd=self.workdir, env=env, stdout=log, stderr=subprocess.STDOUT
            )
        return self

    def wait_exit(self, timeout: float = 30) -> int:
        assert self.proc is not None
        return self.proc.wait(timeout)

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    def log(self) -> str:
        return self.log_path.read_text(encoding="utf-8", errors="replace")
