FROM python:3.12.12-slim-bookworm@sha256:2986c55feb36e6cae00fa1fefb454283e4b33f35e75ff8bdd123b134130be301
RUN apt-get update && apt-get install -y --no-install-recommends \
    bash coreutils curl jq iptables \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 1000 --create-home solver
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /work
