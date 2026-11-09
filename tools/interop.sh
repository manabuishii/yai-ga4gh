#!/bin/sh
# Start and stop the interoperability environment (stage 3): the main compose stack (with the
# interop configs) plus the partners (deploy/interop/compose.interop.yaml).
# Usage: sh tools/interop.sh up [env file]          setup -> build -> main up -> seed -> partners up
#        sh tools/interop.sh wes-funnel|wes-yai      recreate only WES, switching its TES (INTEROP-TES-2)
#        sh tools/interop.sh client <script.py> [args]  run a script in a one-off partner client container
#        sh tools/interop.sh e2e [pytest args]       run test_e2e.py in the yai test container (compose)
#        sh tools/interop.sh down [env file]         remove partner and main containers, network, volumes
#                                                    (keeps images)
# The env file defaults to deploy/env/rootless.env (copy deploy/env/rootless.env.example).
set -eu
root=$(cd "$(dirname "$0")/.." && pwd)
cmd=${1:-up}; shift || true
envfile=$root/deploy/env/rootless.env
case "$cmd" in up|down) [ $# -eq 0 ] || { envfile=$1; shift; } ;; esac
[ -f "$envfile" ] || { echo "interop: no env file $envfile (see deploy/env/rootless.env.example)" >&2; exit 2; }
set -a; . "$envfile"; set +a
E=$YAI_LOCAL_DIR/compose.env
main() { docker compose -f "$root/deploy/compose.yaml" -f "$root/deploy/interop/compose.yai-interop.yaml" --env-file "$E" "$@"; }
io() { docker compose -f "$root/deploy/interop/compose.interop.yaml" --env-file "$E" "$@"; }
e2e() { docker compose -f "$root/deploy/compose.e2e.yaml" --env-file "$E" "$@"; }
case "$cmd" in
  up)
    sh "$root/deploy/setup.sh"
    sh "$root/deploy/interop/setup.sh"
    main config -q && io config -q
    main build && io build
    main up -d
    main --profile seed run --rm seed
    io up -d
    ;;
  wes-funnel) YAI_WES_CONFIG=/yai/local/interop/config/wes-funnel.yaml main up -d --force-recreate wes ;;
  wes-yai) main up -d --force-recreate wes ;;
  client) io build -q client && io run --rm client python "$@" ;;
  e2e) e2e run --rm e2e python -m pytest -q -p no:cacheprovider -m "e2e or docker" tests/test_e2e.py "$@" ;;
  down)
    io --profile client down -v --remove-orphans || true
    e2e down --remove-orphans >/dev/null 2>&1 || true
    main --profile seed down -v --remove-orphans
    left=$(docker ps -a --filter label=yai.tes=1 --format '{{.Names}}')
    [ -z "$left" ] || { echo "interop: job containers left: $left" >&2; exit 1; }
    ;;
  *) echo "interop: unknown command $cmd" >&2; exit 2 ;;
esac
