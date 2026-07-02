# Egress proxy for miner sandboxes. Small, single-file HTTP server that
# forwards allowlisted LLM traffic to the upstream provider and meters usage.
FROM python:3.12-slim

RUN pip install --no-cache-dir "httpx>=0.27"

COPY server.py /app/server.py
WORKDIR /app

# The validator runs this container with `--user <uid>:<gid>` so the mounted
# usage volume is written as the host user; no build-time USER needed.
ENTRYPOINT ["python", "-u", "server.py"]
