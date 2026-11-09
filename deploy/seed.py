"""Register the four test data objects in yai-drs and write their IDs for e2e (observer, D-023).

Run once by the compose seed service (deploy/compose.yaml). Registers via the yai CLI path.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

CONFIG = "/yai/local/config/drs.yaml"
DATA = Path("/yai/testdata")
OUT = Path("/yai/e2e/ids.json")
ITEMS = {  # name -> (file, policy args)
    "ds1": ("ds1.txt", ["--policy", "dataset", "--dataset", "https://yai.test/datasets/ds1"]),
    "public": ("public.txt", ["--policy", "public"]),
}


def main() -> None:
    ids = {}
    for key, (name, args) in ITEMS.items():
        out = subprocess.run(  # noqa: S603 fixed arguments
            ["yai", "drs", "register", str(DATA / name), "--config", CONFIG, *args],  # noqa: S607
            check=True, capture_output=True, text=True,
        )  # fmt: skip
        ids[key] = out.stdout.strip()
    OUT.write_text(json.dumps(ids, indent=2), encoding="utf-8")
    print(json.dumps(ids))


if __name__ == "__main__":
    main()
