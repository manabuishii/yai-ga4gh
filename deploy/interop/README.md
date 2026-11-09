# Interoperability partners (stage 3, test-plan.md section 6)

The partners run as a project `yai-interop`, separate from the main stack (`deploy/compose.yaml`), and join the same network `yai-net` from outside. They get the local CA, and a second Caddy (`proxy`) serves their `*.yai.test` names. Same rules as the main stack (`tests/test_deploy.py` checks both): no `--privileged`, host network or pid, never mount the rootful daemon's socket, every bind mount source under `YAI_LOCAL_DIR`, nothing published to the host. Results: `tests/reports/interop/`.

## Partners and versions (checked at the distribution sites and pinned on 2026-10-17)

| Pair | Partner | Source and version | Image | Name |
| --- | --- | --- | --- | --- |
| INTEROP-TES-1 | py-tes 1.1.4 (client) | PyPI `py-tes==1.1.4` (git tag 1.1.4 = 880dc5a) | `clients/Dockerfile` (python 3.12.15-slim + pip) | — |
| INTEROP-TES-2 | Funnel v0.11.14 | quay.io/ohsu-comp-bio/funnel:v0.11.14 `@sha256:8e10ba4d…` (image of release v0.11.14, 2026-06-11; git tag v0.11.14 = 2e54ad7) | `funnel/Dockerfile`: the official image + docker CLI 27.3.1 (the official image has only nerdctl, no docker) | funnel.yai.test |
| INTEROP-WES-1 | Sapporo 2.4.0 | ghcr.io/sapporo-wes/sapporo-service:2.4.0 `@sha256:0e0a1537…` (git tag 2.4.0 = 14e6b5c) | as is. Its `sapporo/run.sh` (Apache-2.0) is not copied here: `tools/ext_specs.py` downloads it from the pinned commit (sha256 checked), and setup.sh rewrites it for the rootless socket and passes it as `SAPPORO_RUN_SH` | sapporo.yai.test |
| INTEROP-DRS-1 | ga4gh/passport-playground-drs | git main 5eb7745 (2026-06-12) | `pgdrs/Dockerfile` (clone and pin the commit) | pgdrs.yai.test (443 = API, 8645 = plain-http access URLs) |
| INTEROP-AAI-1 | Broker and Issuer of the GA4GH Passport Playground | Broker = image oryd/hydra:v26.2.0 (steps of the playground-broker README 1a29660), Issuer = git main bfb0232 (2026-07-21) | hydra as is (`serve all --dev`, DSN=memory), `issuer/Dockerfile` | broker.yai.test |

## Steps

```sh
sh tools/interop.sh up                 # setup (main + interop) -> build -> main up (interop configs) -> seed -> partners up
sh tools/interop.sh client pytes_check.py                                   # INTEROP-TES-1
sh tools/interop.sh wes-funnel && sh tools/interop.sh e2e -k "E2E_01 and compose"   # INTEROP-TES-2 (Funnel as the TES of WES)
sh tools/interop.sh wes-yai                                                  # switch back
sh tools/interop.sh client wes_common_check.py --url https://sapporo.yai.test --name sapporo --engine cwltool --out INTEROP-WES-1.sapporo.json
sh tools/interop.sh client drs_compare.py                                   # INTEROP-DRS-1
sh tools/interop.sh client aai_check.py                                     # INTEROP-AAI-1
sh tools/interop.sh down               # remove partner and main containers, network, volumes (keeps images)
```

The scripts write JSON to `$YAI_LOCAL_DIR/interop/out/`; copies for the reports go to `tests/reports/interop/data/`.

## Interop configs (`make_configs.py`, R-09)

Made from the `local/config/*.yaml` written by `deploy/setup.sh` into `local/interop/config/`: `pgdrs.yai.test:443` and `funnel.yai.test:443` added to `fetch.allow` of tes and wes, `pgdrs.yai.test:8645` to `insecure_http_hosts` (the partner's access URLs are always http; a test setting), Hydra added to `trust.brokers` of drs, and `wes-funnel.yaml` with `tes.url` set to Funnel. The main compose stack gets `compose.yai-interop.yaml` as an overlay that switches the config locations (done by `tools/interop.sh`).
