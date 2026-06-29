# syntax=docker/dockerfile:1.7

# Never Play Alone validator image.
#
# Runs the `npa-validator` binary plus a local Chutes proxy. Requires the
# mcbench submodule checked out at ./mcbench (git submodule update --init --recursive).
#
# The container is a Docker *client*: it talks to the host Docker daemon
# (mounted at /var/run/docker.sock) to spawn Minecraft and agent containers,
# so Docker does not need to run inside this image.

FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# System deps:
#   - docker-ce-cli + compose plugin: so the validator can `docker run` the
#     Minecraft/agent containers on the host daemon.
#   - nodejs + npm: the mcbench packet recorder (mineflayer) needs Node.
#   - curl: health/debug; gnupg: Docker repo key import.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        gnupg \
        nodejs \
        npm \
    && rm -rf /var/lib/apt/lists/* \
    && install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc \
    && chmod a+r /etc/apt/keyrings/docker.asc \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
        > /etc/apt/sources.list.d/docker.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends docker-ce-cli docker-compose-plugin \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Subnet package sources (bittensor/httpx/typer).
COPY pyproject.toml README.md ./
COPY miner/ ./miner/
COPY validator/ ./validator/

# mcbench submodule (must be checked out before `docker build`).
# We need its python package, the agent Dockerfile context, and the recorder.
COPY mcbench/pyproject.toml mcbench/README.md ./mcbench/
COPY mcbench/mcbench/ ./mcbench/mcbench/
COPY mcbench/docker/ ./mcbench/docker/
COPY mcbench/tools/ ./mcbench/tools/

# Install recorder Node deps (package-lock.json present → reproducible).
RUN cd mcbench/tools/recorder && npm ci --omit=optional || npm install

# Install Python packages: mcbench first (the validator imports it at runtime),
# then the subnet package itself.
RUN pip install --no-cache-dir -e ./mcbench \
    && pip install --no-cache-dir -e .

# Bittensor wallets live under ~/.bittensor on the host; docker-compose binds
# that host directory into /root/.bittensor inside the container.
ENV NPA_WORKSPACE_ROOT=/workspace \
    NPA_PROXY_BIND_HOST=0.0.0.0

RUN mkdir -p /workspace

# The proxy port must be published on the host (docker-compose.yml) so that
# miner sandboxes can reach it via host.docker.internal:host-gateway.
EXPOSE 18080

# Healthcheck: the validator's APIClient hits /health on the backend on start,
# but for liveness of the container we only need to confirm PID 1 still exists.
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import os; os.kill(1, 0)"

CMD ["npa-validator"]
