# Environment of the combined tests (docker compose)

Built in stage 2 and run on a local rootless Docker (a daemon of a dedicated host user). Decisions:
D-034 (Docker runner of TES), D-035 (compose layout), D-037 (layout on a real machine) and D-038
(how results are counted) in `docs/decisions.md`.

## Layout

| Service | What | Inside the network | Host |
| --- | --- | --- | --- |
| proxy | Caddy. Serves `aai/drs/tes/wes.yai.test:443` with a certificate of the local CA and forwards to each service | 443 (network aliases `*.yai.test`) | only `${YAI_BIND:-127.0.0.1}:${YAI_HTTPS_PORT:-8443}` |
| aai, drs, tes, wes | `yai serve <name>` (one image, `base` of `deploy/Dockerfile`) | 8401-8404 | not published |
| tes-worker | `yai tes worker`. With `runner.kind: docker` it starts containers **at `DOCKER_HOST` (`YAI_DOCKER_HOST`)**. For a unix socket, the directory `YAI_DOCKER_SOCK_DIR` is mounted read-only at `/run/yai-docker` | — | — |
| seed (profile `seed`) | Registers the test data in DRS and writes the IDs to the `e2e-state` volume (observer) | — | — |
| e2e (separate project, `compose.e2e.yaml`) | Test container. Joins the same network from outside and calls `https://*.yai.test` (D-023). Writes junit to `local/results/` | — | — |

- Not used: `--privileged`, `network_mode: host`, `pid: host`, mounting the rootful daemon's `/var/run/docker.sock`. Every service is `read_only`, `cap_drop: [ALL]` and `no-new-privileges`
- **Every bind mount source is under `YAI_LOCAL_DIR`** (D-037). It must be where the Docker daemon sees it; a checkout inside a development container may be invisible to the daemon, so `setup.sh` also puts copies of the Caddyfile, seed.py and testdata under `local/`
- Only **the TES working directory** is a bind mount (the daemon mounts the same place into job containers with `-v`). The path as compose sees it, `YAI_TES_WORK_DIR`, and as the daemon sees it, `YAI_TES_WORK_DIR_FOR_DOCKER`, are passed separately (they differ between a development container, the host and dind)
- Owners: services run as `YAI_UID:YAI_GID`, jobs as `runner.docker.user` (the same by default). With rootless Docker or Podman, uid 0 in a container is the daemon's user on the host, so use `0:0` for both (checked on a real machine: only 0:0 can write to the working directory, D-034)
- Secrets: `setup.sh` creates them under `local/` (not in git). Config templates: `config/*.yaml.in`

## Steps

```sh
cp deploy/env/rootless.env.example deploy/env/rootless.env   # once; adjust the paths
sh tools/compose_e2e.sh            # setup -> build -> up -> seed -> e2e -> build/junit-compose.xml -> clean up (down -v)
YAI_KEEP=1 sh tools/compose_e2e.sh # keep the stack (for debugging); later sh tools/compose_e2e.sh down
make e2e-compose                   # the same
```

Building the test image (`yai-test`) needs network: its build downloads the pinned specification files
(`tools/ext_specs.py`, checked by sha256) and bundles them. To run a second stack next to an existing
one, also set `YAI_PROJECT`, `YAI_NETWORK`, `YAI_IMAGE`, `YAI_TEST_IMAGE`, `YAI_HTTPS_PORT` and a
separate `YAI_LOCAL_DIR` in the env file.

The values come from `deploy/env/rootless.env` (`YAI_LOCAL_DIR`, `YAI_UID=0`, `YAI_DOCKER_SOCK_DIR` and
others; see `deploy/env/rootless.env.example`). Another env file can be given as the first argument.

Step by step:

```sh
set -a; . deploy/env/rootless.env; set +a
sh deploy/setup.sh                                                # CA, certificate, secrets, configs, copies, local/compose.env
E=$YAI_LOCAL_DIR/compose.env
docker compose -f deploy/compose.yaml --env-file $E config -q     # shape only (no daemon needed)
docker compose -f deploy/compose.yaml --env-file $E build
docker compose -f deploy/compose.yaml --env-file $E up -d
docker compose -f deploy/compose.yaml --env-file $E --profile seed run --rm seed
docker compose -f deploy/compose.e2e.yaml --env-file $E run --rm e2e   # the e2e and docker marks of tests/test_e2e.py
docker compose -f deploy/compose.yaml --env-file $E --profile seed down -v
```

- `make check` needs no Docker. `tools/test_status.py` also counts `build/junit-compose.xml` if it exists (D-038)
- **From inside a development container, `127.0.0.1:8443` may be unreachable** (a rootless daemon publishes on the host's loopback). To call one endpoint, use a container on the same network: `docker run --rm --network yai-net -v $YAI_LOCAL_DIR/certs:/certs:ro curlimages/curl:8.10.1 --cacert /certs/ca.pem https://wes.yai.test/ga4gh/wes/v1/service-info`. On the host: `curl --cacert <local>/certs/ca.pem --resolve wes.yai.test:8443:127.0.0.1 https://wes.yai.test:8443/ga4gh/wes/v1/service-info`
- Checking the clean-up: `docker ps -a --filter label=yai.tes=1` is empty (E2E-06) and `docker volume ls | grep yai` is empty. The images (`yai:dev`, `yai-test:dev`, about 250 MB each) are kept

## Interoperability partners (stage 3)

`deploy/interop/` (separate project `yai-interop`, joins the same network from outside). Partners, versions and steps: `deploy/interop/README.md`; results: `tests/reports/interop/`. `sh tools/interop.sh up` -> scripts -> `sh tools/interop.sh down`.

## What to change per daemon layout

| Layout | `YAI_DOCKER_SOCK_DIR`, `YAI_DOCKER_HOST` | `YAI_LOCAL_DIR`, `YAI_TES_WORK_DIR_FOR_DOCKER` | `YAI_UID`, job user |
| --- | --- | --- | --- |
| **rootless Docker (dedicated user, the current layout)** | the socket directory as the daemon sees it (e.g. `/run/user/1001/docker`) and `unix:///run/yai-docker/docker.sock` | a shared directory that the daemon sees at the same path | `0`, `0:0` |
| Podman (compatible API) | the `/run/user/<uid>/podman` directory and `unix:///run/yai-docker/podman.sock` | same as above | `0`, `0:0` |
| dedicated VM | leave the directory empty; `ssh://user@vm` or `tcp://vm:2376` (TLS) | a place shared with the VM (path on the VM) | the VM's user |
| dind | leave the directory empty; `tcp://dind:2376` (dind on the same network) | the path inside dind (mount the same volume into dind) | any |
