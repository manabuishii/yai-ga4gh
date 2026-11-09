#!/bin/sh
# Prepare the interoperability environment (stage 3). Run after deploy/setup.sh (same
# environment variables; writes under interop/ in YAI_LOCAL_DIR):
#   - copies of what compose bind-mounts: Caddyfile, the Funnel config, Sapporo's run.sh
#     (rewritten for a rootless Docker socket)
#   - interop versions of the yai configs, interop/config/*.yaml: partner hosts added to the
#     https allow lists (R-09), Hydra added as a broker, and wes-funnel.yaml with Funnel as the
#     TES of WES (INTEROP-TES-2)
#   - partner working directories (funnel-work, sapporo-runs, 775), script output out/, a copy
#     of the test data cases/
# Sapporo's run.sh is not copied into this repository: tools/ext_specs.py downloads it from a
# pinned commit into build/specs-ext/interop/sapporo/run.sh and checks its sha256.
# Uses no Docker. Safe to run again.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
root=$(cd "$here/../.." && pwd)
local_dir=${YAI_LOCAL_DIR:-$here/../local}
local_dir=$(cd "$local_dir" && pwd)
io=$local_dir/interop
[ -f "$local_dir/config/wes.yaml" ] || { echo "setup-interop: run deploy/setup.sh first" >&2; exit 1; }
run_sh=$root/build/specs-ext/interop/sapporo/run.sh
# Always check the cache against specs/EXTERNAL.SHA256SUMS (no network if it is complete and right)
"$root/.venv/bin/python" "$root/tools/ext_specs.py"
umask 022
mkdir -p "$io/caddy" "$io/funnel" "$io/sapporo" "$io/config" "$io/funnel-work" "$io/sapporo-runs" "$io/out" "$io/cases"
chmod 775 "$io/funnel-work" "$io/sapporo-runs" "$io/out"
cp "$here/Caddyfile" "$io/caddy/Caddyfile"
cp "$root"/tests/cases/cwl/cat_file.cwl "$root"/tests/cases/cwl/cat_file.job.json "$root"/tests/cases/cwl/input.txt "$io/cases/"
# Funnel config: the working directory as the daemon sees it (= the same path as here, D-037 (2))
sed -e "s|@WORK@|$io/funnel-work|g" "$here/funnel/funnel.yaml.in" > "$io/funnel/funnel.yaml"
# Sapporo's run.sh (sapporo-wes/sapporo-service 2.4.0, Apache-2.0): replace the rootful socket
# that it passes to cwltool with the rootless socket directory (read-only) and API 1.41
sock_dir=${YAI_DOCKER_SOCK_DIR:-$local_dir/docker-sock}
sed -e "s|-v /var/run/docker.sock:/var/run/docker.sock|-v $sock_dir:/run/yai-docker:ro|g" \
    -e "s|-e DOCKER_HOST=unix:///var/run/docker.sock|-e DOCKER_HOST=unix:///run/yai-docker/docker.sock|g" \
    -e "s|-e DOCKER_API_VERSION=1.44|-e DOCKER_API_VERSION=1.41|g" \
    "$run_sh" > "$io/sapporo/run.sh"
chmod 644 "$io/caddy/Caddyfile" "$io/funnel/funnel.yaml" "$io/sapporo/run.sh" "$io"/cases/*
# Interop versions of the yai configs
"$root/.venv/bin/python" "$here/make_configs.py" "$local_dir/config" "$io/config"
chmod 644 "$io"/config/*.yaml
echo "setup-interop: $io"
