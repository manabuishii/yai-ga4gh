# Official Funnel v0.11.14 image (quay.io, the image of release v0.11.14, 2026-06-11) plus docker CLI 27.3.1.
# The official image has only nerdctl (for containerd) and no docker CLI, while Funnel's worker execs `docker run ...`.
# 27.x talks to daemon 20.10.13 (API 1.41), same version as the yai image
FROM quay.io/ohsu-comp-bio/funnel:v0.11.14@sha256:8e10ba4de436b058bed95427110d9fe947142162078274a6a91cfe3b3eb8d15a
ARG DOCKER_CLI_VERSION=27.3.1
RUN apk add --no-cache ca-certificates \
 && wget -qO- "https://download.docker.com/linux/static/stable/x86_64/docker-${DOCKER_CLI_VERSION}.tgz" \
    | tar -xz -C /usr/local/bin --strip-components=1 docker/docker
ENTRYPOINT ["/app/funnel"]
