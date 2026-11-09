# Common image of the four yai services and the test container (D-035; built on a local rootless
# Docker since stage 2, D-037).
# Versions are pinned (checked at the distribution sites on 2026-10-17; the docker CLI stays on 27.x,
# which talks to daemon 20.10.13)
FROM python:3.12.15-slim-bookworm AS base
ARG YAI_UID=10001
ARG YAI_GID=10001
# docker CLI (the Docker runner of TES connects to DOCKER_HOST; no daemon in the image)
ARG DOCKER_CLI_VERSION=27.3.1
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl \
 && curl -fsSL "https://download.docker.com/linux/static/stable/x86_64/docker-${DOCKER_CLI_VERSION}.tgz" \
    | tar -xz -C /usr/local/bin --strip-components=1 docker/docker \
 && apt-get purge -y curl && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*
# uv: copy the binary from the official image (docs.astral.sh/uv/guides/integration/docker/,
# version and digest pinned, checked on 2026-10-24).
# 0.13.4 removes the emSign roots from its bundled root certificates (GHSA-hq85-5cpj-mr62, the
# version recommended by the Python advisory).
# Remove the image's pip, which is unused (pip 25.0.1 of the base image bundles an old certifi
# 2024.08.30)
COPY --from=ghcr.io/astral-sh/uv:0.13.4@sha256:9f130421e930bfbdc563c00740466603f8723158e90297d07957c2294640262b /uv /uvx /bin/
RUN python -m pip uninstall -y pip
WORKDIR /yai/app
COPY pyproject.toml uv.lock README.md ./
COPY yai ./yai
COPY schema ./schema
# Dependencies exactly as uv.lock pins them, with hashes (no resolution at build time); then yai
# itself without dependencies.
# Editable install: the code reads schema/*.sql from the repository root (next to yai/), which
# would be missing if copied into site-packages
RUN uv export --frozen --no-dev --no-emit-project -o /tmp/req.txt \
 && uv pip install --system --no-cache --require-hashes -r /tmp/req.txt \
 && uv pip install --system --no-cache --no-deps -e . \
 && rm /tmp/req.txt
# A named volume copies the owner of the image directory when first created, so create them here.
# With rootless Docker the user is 0:0 (D-034); 0 (root) already exists, so it is not created
RUN if [ "${YAI_UID}" != 0 ]; then groupadd -g "${YAI_GID}" yai \
      && useradd -u "${YAI_UID}" -g "${YAI_GID}" -M -s /usr/sbin/nologin yai; fi \
 && mkdir -p /yai/var/aai /yai/var/drs /yai/var/tes/db /yai/var/tes/files /yai/var/tes/work \
    /yai/var/wes /yai/e2e /yai/results \
 && chown -R "${YAI_UID}:${YAI_GID}" /yai/var /yai/e2e /yai/results
USER ${YAI_UID}:${YAI_GID}
# where the docker CLI wants to write its config (the image runs read_only, so under a tmpfs)
ENV PYTHONUNBUFFERED=1 DOCKER_CONFIG=/tmp/docker

# Test container (outside compose, D-023): adds the test tools, tests/ and the specifications
FROM base AS test
USER root
COPY tests ./tests
COPY specs ./specs
COPY docs/data ./docs/data
COPY deploy ./deploy
COPY tools ./tools
RUN uv export --frozen --only-dev --no-emit-project -o /tmp/req-dev.txt \
 && uv pip install --system --no-cache --require-hashes -r /tmp/req-dev.txt && rm /tmp/req-dev.txt \
 && python tools/ext_specs.py && python tools/bundle_specs.py   # build/*.openapi.json for the shape checks (made at build time: the image runs read_only)
USER ${YAI_UID}:${YAI_GID}
