#!/bin/sh
# Run the combined tests on compose (stage 2, D-037): setup -> build -> up -> seed -> e2e ->
# junit into build/ -> clean up.
# Usage: sh tools/compose_e2e.sh [env file]   default: deploy/env/rootless.env
#          (copy deploy/env/rootless.env.example and adjust it)
#   YAI_KEEP=1 keeps the stack (for debugging; clean up later with sh tools/compose_e2e.sh down)
#   sh tools/compose_e2e.sh down [env file]   only clean up (containers, network, volumes; keeps images)
# Results: build/junit-compose.xml (tools/test_status.py in make check counts it with build/junit.xml)
set -eu
root=$(cd "$(dirname "$0")/.." && pwd)
cmd=run
if [ "${1:-}" = down ]; then cmd=down; shift; fi
envfile=${1:-$root/deploy/env/rootless.env}
[ -f "$envfile" ] || { echo "compose_e2e: no env file $envfile (see deploy/env/rootless.env.example)" >&2; exit 2; }
set -a; . "$envfile"; set +a
E=$YAI_LOCAL_DIR/compose.env
main() { docker compose -f "$root/deploy/compose.yaml" --env-file "$E" "$@"; }
e2e() { docker compose -f "$root/deploy/compose.e2e.yaml" --env-file "$E" "$@"; }
down() {
  e2e down --remove-orphans >/dev/null 2>&1 || true
  main --profile seed down -v --remove-orphans
  # No job container started by the runner may be left (E2E-06)
  left=$(docker ps -a --filter label=yai.tes=1 --format '{{.Names}}')
  [ -z "$left" ] || { echo "compose_e2e: job containers left: $left" >&2; exit 1; }
}
if [ "$cmd" = down ]; then down; exit 0; fi
sh "$root/deploy/setup.sh"
main config -q && e2e config -q
main build
e2e build   # rebuild the test image every time (to pick up changes in tests/)
main up -d
main --profile seed run --rm seed
rc=0
e2e run --rm e2e || rc=$?
mkdir -p "$root/build"
cp "$YAI_LOCAL_DIR/results/junit-compose.xml" "$root/build/junit-compose.xml"
echo "compose_e2e: junit -> build/junit-compose.xml (pytest rc=$rc)"
if [ "${YAI_KEEP:-}" = 1 ]; then echo "compose_e2e: YAI_KEEP=1, not cleaning up"; else down; fi
exit $rc
