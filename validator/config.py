"""Validator + CLI shared configuration."""
from __future__ import annotations

import os

NETUID = 490
NETWORK = os.environ.get("NPA_NETWORK", "test")
API_URL = os.environ.get("NPA_API_URL", "https://api.neverplayalone.ai")

MISSION_ID = os.environ.get("NPA_MISSION_ID", "resource_gathering")
LOOP_POLL_SECONDS = int(os.environ.get("NPA_LOOP_POLL_SECONDS", "12"))
WORKSPACE_ROOT = os.environ.get("NPA_WORKSPACE_ROOT", "/tmp/npa_validator")
MAX_PARALLEL_AGENTS = int(os.environ.get("NPA_MAX_PARALLEL_AGENTS", "2"))
PROXY_ENABLED = os.environ.get("NPA_PROXY_ENABLED", "1").lower() not in {"0", "false", "no"}
PROXY_BIND_HOST = os.environ.get("NPA_PROXY_BIND_HOST", "127.0.0.1")
PROXY_PORT = int(os.environ.get("NPA_PROXY_PORT", "18080"))
CHUTES_API_KEY = os.environ.get("CHUTES_API_KEY", os.environ.get("NPA_CHUTES_API_KEY", ""))
CHUTES_BASE_URL = os.environ.get("NPA_CHUTES_BASE_URL", "https://llm.chutes.ai/v1")
PROXY_ALLOWED_MODELS = os.environ.get("NPA_PROXY_ALLOWED_MODELS", "")
PROXY_MODEL_PRICES_JSON = os.environ.get("NPA_PROXY_MODEL_PRICES_JSON", "")
PROXY_DEFAULT_INPUT_PRICE_PER_1M_USD = float(
    os.environ.get("NPA_PROXY_DEFAULT_INPUT_PRICE_PER_1M_USD", "0")
)
PROXY_DEFAULT_OUTPUT_PRICE_PER_1M_USD = float(
    os.environ.get("NPA_PROXY_DEFAULT_OUTPUT_PRICE_PER_1M_USD", "0")
)
PROXY_MAX_TOTAL_SPEND_USD = float(os.environ.get("NPA_PROXY_MAX_TOTAL_SPEND_USD", "1.0"))
PROXY_UPSTREAM_TIMEOUT_SECONDS = float(
    os.environ.get("NPA_PROXY_UPSTREAM_TIMEOUT_SECONDS", "60")
)

# Keep committed payload versioned if validator-side chain payloads change later.
COMMIT_VERSION = 1
