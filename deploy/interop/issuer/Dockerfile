# ga4gh/passport-playground-issuer (main bfb0232576d87113421c6300d4af4b88e69e90cf, 2026-07-21), run as its own Dockerfile does
FROM python:3.12.15-slim-bookworm
ARG ISSUER_REPO=https://github.com/ga4gh/passport-playground-issuer.git
ARG ISSUER_COMMIT=bfb0232576d87113421c6300d4af4b88e69e90cf
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
 && git clone -q "${ISSUER_REPO}" /src && cd /src && git checkout -q --detach "${ISSUER_COMMIT}" \
 && git rev-parse HEAD > /src/COMMIT && rm -rf /src/.git \
 && apt-get purge -y git && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*
WORKDIR /src
RUN pip install --no-cache-dir -r requirements.txt
USER 65534:65534
CMD ["sh", "-c", "gunicorn --chdir app --bind 0.0.0.0:${PORT:-8080} main:app"]
