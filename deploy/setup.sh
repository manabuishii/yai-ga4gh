#!/bin/sh
# Prepare the environment of the combined tests (D-035, D-037). Uses no Docker (openssl only):
#   - a local CA and a certificate for *.yai.test (D-017)
#   - secrets (two AAI client secrets) and the configs that contain them
#   - the TES working directory (where the Docker daemon also sees it, D-034)
#   - copies of the files that compose bind-mounts (Caddyfile, seed.py, testdata), placed where
#     the Docker daemon sees them (a checkout inside a development container may be invisible
#     to the daemon, D-037)
#   - the variables for compose, local/compose.env (passed with docker compose --env-file)
# Writes to YAI_LOCAL_DIR (default deploy/local, not in git). Safe to run again: existing keys
# and secrets are kept; copies, configs and compose.env are rewritten every time.
# Usage: sh deploy/setup.sh   with environment variables (all optional; for a rootless daemon
# see deploy/env/rootless.env.example):
#   YAI_LOCAL_DIR             where to write (somewhere the Docker daemon sees)
#   YAI_DOCKER_SOCK_DIR       directory with the daemon's socket (host path); default: the empty
#                             directory local/docker-sock
#   YAI_DOCKER_HOST           where the runner connects; default: that directory mounted in the
#                             container, unix:///run/yai-docker/docker.sock
#   YAI_UID, YAI_GID          container user (default: the current uid/gid, i.e. the owner of the
#                             working directory); 0 and 0 with rootless Docker or Podman
#   YAI_TES_JOB_USER          --user of the containers that TES starts (default YAI_UID:YAI_GID, D-034)
#   YAI_TES_RUNNER            docker (default) or local-process
#   YAI_TES_WORK_DIR_FOR_DOCKER  the working directory as the daemon sees it (default: same path)
#   YAI_CERT_DAYS
set -eu
here=$(cd "$(dirname "$0")" && pwd)
local_dir=${YAI_LOCAL_DIR:-$here/local}
mkdir -p "$local_dir"
local_dir=$(cd "$local_dir" && pwd)
runner=${YAI_TES_RUNNER:-docker}
uid=${YAI_UID:-$(id -u)}
gid=${YAI_GID:-$(id -g)}
job_user=${YAI_TES_JOB_USER:-$uid:$gid}
work_for_docker=${YAI_TES_WORK_DIR_FOR_DOCKER:-$local_dir/tes-work}
days=${YAI_CERT_DAYS:-825}
umask 077
mkdir -p "$local_dir/certs" "$local_dir/secrets" "$local_dir/config" "$local_dir/tes-work" \
  "$local_dir/results" "$local_dir/docker-sock" "$local_dir/caddy" "$local_dir/seed/testdata"
chmod 755 "$local_dir" "$local_dir/config" "$local_dir/certs" "$local_dir/docker-sock" \
  "$local_dir/caddy" "$local_dir/seed" "$local_dir/seed/testdata"
# Containers (another host user with rootless Docker) write these two, so the group may write too.
# In a directory with a default ACL, chmod also sets the ACL mask: without 775 the ACL's rwx
# would be cut down to r-x (D-037)
chmod 775 "$local_dir/tes-work" "$local_dir/results"
sock_dir=${YAI_DOCKER_SOCK_DIR:-$local_dir/docker-sock}
docker_host=${YAI_DOCKER_HOST:-unix:///run/yai-docker/docker.sock}

# --- Local CA and certificate (P-256) ---
certs=$local_dir/certs
if [ ! -f "$certs/ca.pem" ]; then
  openssl ecparam -name prime256v1 -genkey -noout -out "$certs/ca-key.pem"
  openssl req -x509 -new -key "$certs/ca-key.pem" -sha256 -days 3650 \
    -subj "/CN=yai test CA (local only)" \
    -addext "basicConstraints=critical,CA:TRUE,pathlen:0" \
    -addext "keyUsage=critical,keyCertSign,cRLSign" -out "$certs/ca.pem"
fi
if [ ! -f "$certs/server.pem" ]; then
  openssl ecparam -name prime256v1 -genkey -noout -out "$certs/server-key.pem"
  openssl req -new -key "$certs/server-key.pem" -subj "/CN=*.yai.test" -out "$certs/server.csr"
  ext=$(mktemp)
  cat > "$ext" <<EXT
basicConstraints=critical,CA:FALSE
keyUsage=critical,digitalSignature
extendedKeyUsage=serverAuth
subjectAltName=DNS:*.yai.test,DNS:aai.yai.test,DNS:drs.yai.test,DNS:tes.yai.test,DNS:wes.yai.test
EXT
  openssl x509 -req -in "$certs/server.csr" -CA "$certs/ca.pem" -CAkey "$certs/ca-key.pem" \
    -CAcreateserial -days "$days" -sha256 -extfile "$ext" -out "$certs/server.pem"
  rm -f "$ext" "$certs/server.csr"
fi
# Another container user (proxy) reads the certificate and key, hence 644. Only the CA key stays
# 600, moved where no container sees it
chmod 644 "$certs/ca.pem" "$certs/server.pem" "$certs/server-key.pem"
mkdir -p "$local_dir/ca-private" && chmod 700 "$local_dir/ca-private"
[ -f "$certs/ca-key.pem" ] && mv "$certs/ca-key.pem" "$local_dir/ca-private/ca-key.pem"
[ -f "$certs/ca.srl" ] && mv "$certs/ca.srl" "$local_dir/ca-private/ca.srl"

# --- Secrets ---
for name in wes-client-secret test-client-secret; do
  [ -f "$local_dir/secrets/$name" ] || openssl rand -hex 24 > "$local_dir/secrets/$name"
done
chmod 755 "$local_dir/secrets"
chmod 644 "$local_dir/secrets/"*   # read by the container user (YAI_UID); a local test setup (D-002)

# --- Configs (replace @...@) ---
wes_secret=$(cat "$local_dir/secrets/wes-client-secret")
test_secret=$(cat "$local_dir/secrets/test-client-secret")
for svc in aai drs tes wes; do
  sed -e "s|@WES_CLIENT_SECRET@|$wes_secret|g" -e "s|@TEST_CLIENT_SECRET@|$test_secret|g" \
      -e "s|@TES_RUNNER@|$runner|g" -e "s|@TES_JOB_USER@|$job_user|g" \
      "$here/config/$svc.yaml.in" > "$local_dir/config/$svc.yaml"
  chmod 644 "$local_dir/config/$svc.yaml"
done
# --- Copies of what compose bind-mounts (where the daemon sees them) ---
cp "$here/caddy/Caddyfile" "$local_dir/caddy/Caddyfile"
cp "$here/seed.py" "$local_dir/seed/seed.py"
cp "$here"/testdata/* "$local_dir/seed/testdata/"
chmod 644 "$local_dir/caddy/Caddyfile" "$local_dir/seed/seed.py" "$local_dir"/seed/testdata/*
# --- Variables for compose ---
cat > "$local_dir/compose.env" <<ENV
# Written by setup.sh (you may edit it). Pass with docker compose --env-file "$local_dir/compose.env"
YAI_LOCAL_DIR=$local_dir
YAI_UID=$uid
YAI_GID=$gid
YAI_TES_WORK_DIR=$local_dir/tes-work
YAI_TES_WORK_DIR_FOR_DOCKER=$work_for_docker
YAI_DOCKER_SOCK_DIR=$sock_dir
YAI_DOCKER_HOST=$docker_host
YAI_BIND=${YAI_BIND:-127.0.0.1}
YAI_HTTPS_PORT=${YAI_HTTPS_PORT:-8443}
ENV
chmod 644 "$local_dir/compose.env"
echo "setup: $local_dir (runner=$runner, uid:gid=$uid:$gid, job user=$job_user, docker=$docker_host)"
echo "setup: CA is $certs/ca.pem; from the host use curl --cacert and --resolve (README)"
