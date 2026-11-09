"""Derive interop configs from yai's (local/config/*.yaml by deploy/setup.sh) (stage 3, R-09).

- tes.yaml, wes.yaml: add the partners' https hosts to fetch.allow. The playground DRS access
  URL is always http, so only pgdrs.yai.test:8645 goes into insecure_http_hosts (test-only
  setting; reason in the INTEROP-DRS-1 report)
- wes.yaml: resolver.passport_hosts unchanged (no Passports sent to partners; pgdrs
  ignores Passports)
- wes-funnel.yaml: point tes.url at Funnel (INTEROP-TES-2)
- drs.yaml: add the Playground Broker (Hydra) to trust.brokers (INTEROP-AAI-1, to observe
  Passport shape differences)
- aai.yaml: copied as-is
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

PGDRS_API = "pgdrs.yai.test:443"
PGDRS_FILES = "pgdrs.yai.test:8645"
FUNNEL = "https://funnel.yai.test/ga4gh/tes/v1"
HYDRA_ISS = "https://broker.yai.test/"


def main(src: Path, dst: Path) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    cfg = {n: yaml.safe_load((src / f"{n}.yaml").read_text()) for n in ("aai", "drs", "tes", "wes")}
    for n in ("tes", "wes"):
        fetch = cfg[n].setdefault("fetch", {})
        allow = fetch.setdefault("allow", [])
        for host in (PGDRS_API, "funnel.yai.test:443"):
            if host not in allow:
                allow.append(host)
        insecure = fetch.setdefault("insecure_http_hosts", [])
        if PGDRS_FILES not in insecure:
            insecure.append(PGDRS_FILES)
    brokers = cfg["drs"]["trust"].setdefault("brokers", [])
    if not any(b.get("iss") == HYDRA_ISS for b in brokers):
        brokers.append({"iss": HYDRA_ISS, "jwks_uri": HYDRA_ISS + ".well-known/jwks.json"})
    for n, doc in cfg.items():
        (dst / f"{n}.yaml").write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False))
    funnel = dict(cfg["wes"])
    funnel["tes"] = {**cfg["wes"]["tes"], "url": FUNNEL}
    (dst / "wes-funnel.yaml").write_text(
        yaml.safe_dump(funnel, allow_unicode=True, sort_keys=False)
    )


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
