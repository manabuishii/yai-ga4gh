# Partner client (py-tes 1.1.4 from PyPI) and the check scripts. pip runs inside the container
FROM python:3.12.15-slim-bookworm
RUN pip install --no-cache-dir "py-tes==1.1.4" "httpx>=0.27" "pyyaml>=6" "PyJWT>=2.9"
COPY *.py /work/
WORKDIR /work
ENV PYTHONUNBUFFERED=1

