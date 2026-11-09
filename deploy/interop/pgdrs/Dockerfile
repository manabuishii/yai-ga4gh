# ga4gh/passport-playground-drs (main 5eb7745d743cfde837dcb07ef6cc2467120a01d7, 2026-06-12).
# Fetched from the official source at a pinned commit and run inside the container only
FROM python:3.12.15-slim-bookworm
ARG PGDRS_REPO=https://github.com/ga4gh/passport-playground-drs.git
ARG PGDRS_COMMIT=5eb7745d743cfde837dcb07ef6cc2467120a01d7
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
 && git clone -q "${PGDRS_REPO}" /app && cd /app && git checkout -q --detach "${PGDRS_COMMIT}" \
 && git rev-parse HEAD > /app/COMMIT && rm -rf /app/.git \
 && apt-get purge -y git && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*
WORKDIR /app
RUN pip install --no-cache-dir -r requirements.txt
ENV DRS_DATA_DIR=/app/data PYTHONUNBUFFERED=1
USER 65534:65534
CMD ["python", "main.py"]
