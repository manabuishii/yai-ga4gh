"""Run the official DRS test suite (ga4gh/drs-compliance-suite) against yai-drs (test-plan.md
CONF-DRS).

Starts AAI and DRS locally, registers one object per policy (public, bearer, dataset), writes
the suite config (one entry per auth type) and runs it. The report is saved under
tests/reports/conf-drs/ and failed cases are listed. Classifying them (version difference /
suite assumption / yai bug) is done by hand.

Usage:
  python tools/conformance_drs.py --suite-dir <clone> --suite-python <python of the venv>
How to install the suite: tests/reports/conf-drs/README.md.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests.servers import Server, free_port  # noqa: E402
from tests.stack import DS1, Creds, drs_config, register, start_aai, write_config  # noqa: E402

OUT = ROOT / "tests" / "reports" / "conf-drs"


def failed_cases(report: dict) -> list[dict]:
    out = []
    for phase in report.get("phases", []):
        for test in phase.get("tests", []):
            for case in test.get("cases", []):
                if case.get("status") not in ("PASS", "SKIP"):
                    out.append(
                        {
                            "test": test.get("test_name"),
                            "case": case.get("case_name"),
                            "status": case.get("status"),
                            "message": case.get("message"),
                        }
                    )
    return out


def counts(report: dict) -> Counter[str]:
    c: Counter[str] = Counter()
    for phase in report.get("phases", []):
        for test in phase.get("tests", []):
            for case in test.get("cases", []):
                c[case.get("status", "?")] += 1
    return c


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite-dir", required=True, type=Path)
    parser.add_argument("--suite-python", required=True)
    parser.add_argument("--label", default="run")
    args = parser.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        aai = start_aai(tmp)
        port = free_port()
        cfg = drs_config(aai, tmp, port)
        cfg_path = write_config(tmp / "drs.yaml", cfg)
        files = {}
        for name in ("pub", "bear", "ds1"):
            f = tmp / f"{name}.txt"
            f.write_bytes(f"{name} bytes\n".encode())
            files[name] = f
        ids = {
            "pub": register(cfg_path, files["pub"], "--policy", "public"),
            "bear": register(cfg_path, files["bear"], "--policy", "bearer"),
            "ds1": register(cfg_path, files["ds1"], "--policy", "dataset", "--dataset", DS1),
        }
        drs = Server("drs", cfg, tmp / "drs", port=port).start()
        creds = Creds(aai)
        at = creds.at("alice", "yai-drs")
        passport = creds.passport("alice")
        objects = [
            {"drs_id": ids["pub"], "auth_type": "none", "auth_token": "", "is_bundle": False},
            {"drs_id": ids["bear"], "auth_type": "bearer", "auth_token": at, "is_bundle": False},
            {"drs_id": ids["ds1"], "auth_type": "passport", "auth_token": [passport],
             "is_bundle": False},
        ]  # fmt: skip
        suite_cfg = {
            "service_info": {"auth_type": "none", "auth_token": ""},
            "drs_object_info": objects,
            "drs_object_access": [{k: o[k] for k in ("drs_id", "auth_type", "auth_token")}
                                  for o in objects],
        }  # fmt: skip
        suite_cfg_path = tmp / "suite.json"
        suite_cfg_path.write_text(json.dumps(suite_cfg), encoding="utf-8")
        report_path = OUT / f"{args.label}-report.json"
        try:
            subprocess.run(  # noqa: S603 runs the suite we were given
                [args.suite_python, "compliance_suite/report_runner.py",
                 "--server_base_url", drs.url, "--platform_name", "yai-drs",
                 "--platform_description", "yai (learning project)", "--drs_version", "1.2.0",
                 "--config_file", str(suite_cfg_path), "--report_path", str(report_path)],
                cwd=args.suite_dir, check=True,
                env={"PYTHONPATH": str(args.suite_dir), "PATH": "/usr/bin:/bin"},
            )  # fmt: skip
        finally:
            drs.stop()
            aai.stop()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    # Do not save a copy of the config, so no credential is left behind
    # (the suite's report itself contains no token)
    for secret in (at, passport):
        if secret in report_path.read_text(encoding="utf-8"):
            raise SystemExit("report contains a credential")
    failed = failed_cases(report)
    summary = {"counts": dict(counts(report)), "failed": failed}
    (OUT / f"{args.label}-failed.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary["counts"]), f"failed={len(failed)}")
    for f in failed:
        print("-", f["test"], "|", f["case"], "|", f["status"], "|", (f["message"] or "")[:160])
    return 0


if __name__ == "__main__":
    sys.exit(main())
