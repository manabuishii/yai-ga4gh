"""The unauthenticated TES and worker started by tools/conformance_tes.py are always stopped
on exit.

Instead of the official tool, pass a tiny tool that writes the received --server and waits.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parent.parent
FAKE = """import sys, time, urllib.request
args = sys.argv[1:]
server = args[args.index("--server") + 1]
urllib.request.urlopen(server + "v1/service-info", timeout=5)  # confirm it was up before writing
open({mark!r}, "w").write(server)
{then}
"""


def ours(tmp: Path) -> list[int]:
    """Processes whose command line contains tmp (the tool's temp dir is under TMPDIR=tmp)."""
    pids = []
    for d in Path("/proc").iterdir():
        if d.name.isdigit():
            try:
                cmd = (d / "cmdline").read_bytes()
            except OSError:
                continue
            if str(tmp).encode() in cmd and b"conformance_tes" not in cmd:
                pids.append(int(d.name))
    return pids


def wait_for(cond, timeout: float = 20) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.1)
    return False


@pytest.mark.parametrize("how", ["sigterm", "suite_fails"])
def test_NEG_CONFORMANCE_STOPS(tmp_path, how):
    mark = tmp_path / "server"
    then = "time.sleep(60)" if how == "sigterm" else "sys.exit(3)"
    pkg = (
        tmp_path / "suite" / "compliance_suite"
    )  # the tool runs -m compliance_suite.cli in suite-dir
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "cli.py").write_text(FAKE.format(mark=str(mark), then=then))
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "tools" / "conformance_tes.py"), "--suite-dir",
         str(tmp_path / "suite"), "--suite-python", sys.executable, "--label", "t",
         "--out", str(tmp_path / "out")],
        env=dict(os.environ, TMPDIR=str(tmp_path)), stdout=subprocess.DEVNULL,
    )  # fmt: skip
    try:
        assert wait_for(mark.exists)
        server = mark.read_text()
        if how == "sigterm":
            assert ours(tmp_path)  # TES and worker are up
            proc.send_signal(signal.SIGTERM)
        assert proc.wait(30) != 0
        with pytest.raises(httpx.TransportError):
            httpx.get(server + "v1/service-info", timeout=2)
        assert wait_for(lambda: not ours(tmp_path), 10), ours(tmp_path)
    finally:
        if proc.poll() is None:
            proc.kill()
        for pid in ours(tmp_path):  # clean up anything left behind after a failure
            os.kill(pid, signal.SIGKILL)
