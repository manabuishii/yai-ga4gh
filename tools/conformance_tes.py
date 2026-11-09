"""Run the official TES test suite (ga4gh/openapi-test-runner) against yai-tes (test-plan.md
CONF-TES).

Starts yai-tes with the provisional "everything open" config (auth.mode: anonymous) and a
worker locally, and runs the suite with `--version 1.1.0`. The report is saved under
tests/reports/conf-tes/.

**Test use only**: the yai-tes started here has no auth and a
local-process executor (runs as the worker's user, D-024), and listens on loopback (127.0.0.1)
only. TES and the worker are always stopped on exit, including suite failure, timeout, Ctrl-C,
SIGTERM and SIGHUP. Not for deployment. Classifying failures (version difference / suite
assumption / yai bug) is written by hand in the README.

Usage:
  python tools/conformance_tes.py --suite-dir <clone> --suite-python <python of the venv> \
      --label <date>
"""

from __future__ import annotations

import argparse
import contextlib
import json
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests.servers import Server, Worker  # noqa: E402

OUT = ROOT / "tests" / "reports" / "conf-tes"


def _exit_on_signal(signum: int, _frame: object) -> None:
    raise SystemExit(128 + signum)  # stop via finally / ExitStack


def main() -> int:
    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, _exit_on_signal)
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite-dir", required=True, type=Path)
    parser.add_argument("--suite-python", required=True)
    parser.add_argument("--label", default="run")
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args()

    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    report_dir = out / args.label  # the suite writes <dir>/report.json
    report_dir.mkdir(exist_ok=True)
    report_path = report_dir / "report.json"
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        cfg = {
            "data_dir": str(tmp / "tes-data"),
            "files_root": str(tmp / "tes-files"),
            "work_dir": str(tmp / "tes-work"),
            "auth": {"mode": "anonymous"},
        }
        with contextlib.ExitStack() as stack:  # always stop in reverse order (even on failure)
            tes = Server("tes", cfg, tmp / "tes")
            stack.callback(tes.stop)
            tes.start()
            worker = Worker(cfg, tmp / "worker")
            stack.callback(worker.stop)
            worker.start()
            try:
                proc = subprocess.run(  # noqa: S603 runs the suite we were given
                    [args.suite_python, "-m", "compliance_suite.cli", "report",
                     # the suite appends v1/ to --server, so pass up to /ga4gh/tes/
                     "--server", tes.root + "/ga4gh/tes/", "--version", "1.1.0",
                     "--output_path", str(report_dir)],
                    cwd=args.suite_dir, capture_output=True, text=True, timeout=3600,
                )  # fmt: skip
            except subprocess.TimeoutExpired as exc:
                print("runner timed out")
                print(str(exc.stderr or "")[-3000:])
                return 1
    (out / f"{args.label}-runner.log").write_text(proc.stdout + proc.stderr, encoding="utf-8")
    print("runner exit", proc.returncode)
    if not report_path.exists():
        print(proc.stderr[-3000:])
        return 1
    report = json.loads(report_path.read_text(encoding="utf-8"))
    summary = report.get("summary", {})
    print(json.dumps(summary))
    for phase in report.get("phases", []):
        for test in phase.get("tests", []):
            for case in test.get("cases", []):
                if case.get("status") not in ("PASS", "SKIP"):
                    msg = (case.get("message") or "")[:200]
                    print("-", test.get("test_name"), "|", case.get("case_name"), "|", msg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
