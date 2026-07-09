"""Validator + CLI shared configuration."""
from __future__ import annotations

import os

from shared.chain import NETUID, NETWORK  # noqa: F401

API_URL = os.environ.get("NPA_API_URL", "https://api.neverplayalone.ai")

MISSION_ID = os.environ.get("NPA_MISSION_ID", "resource_gathering")
LOOP_POLL_SECONDS = int(os.environ.get("NPA_LOOP_POLL_SECONDS", "12"))
WEIGHT_EPOCH_BLOCKS = int(os.environ.get("NPA_WEIGHT_EPOCH_BLOCKS", "360"))
EVALUATION_START_CUTOFF_RATIO = float(
    os.environ.get("NPA_EVALUATION_START_CUTOFF_RATIO", "0.5")
)
WORKSPACE_ROOT = os.environ.get("NPA_WORKSPACE_ROOT", "/tmp/npa_validator")
MAX_PARALLEL_AGENTS = int(os.environ.get("NPA_MAX_PARALLEL_AGENTS", "4"))
BURN_RATE = float(os.environ.get("NPA_BURN_RATE", "0.9"))
BURN_UID = int(os.environ.get("NPA_BURN_UID", "0"))
# Port the proxy container listens on inside the sandbox network. It is never
# published to the host, so this is a container-internal port, not a host port.
PROXY_PORT = int(os.environ.get("NPA_PROXY_PORT", "8080"))
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
CHUTES_API_KEY = os.environ.get("CHUTES_API_KEY", "")
PROXY_MAX_TOTAL_SPEND_USD = float(os.environ.get("NPA_PROXY_MAX_TOTAL_SPEND_USD", "0.05"))
PROXY_UPSTREAM_TIMEOUT_SECONDS = float(
    os.environ.get("NPA_PROXY_UPSTREAM_TIMEOUT_SECONDS", "60")
)
