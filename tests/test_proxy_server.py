"""Unit tests for the containerized proxy's accounting helpers.

The server module reads config from the environment at import time, so we point
it at a temp pinned pairs file and fund a provider before loading it, and
register it in sys.modules so its dataclasses resolve.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

SERVER_PATH = Path(__file__).resolve().parent.parent / "docker" / "proxy" / "server.py"

_PAIRS = Path(tempfile.mkdtemp()) / "model_pairs.json"
_PAIRS.write_text(json.dumps({"pairs": [
    {"openrouter": "premium", "chutes": "premium-ch",
     "price": {"openrouter": {"input": 10, "output": 20}, "chutes": {"input": 5, "output": 8}}},
]}))
os.environ["NPA_PROXY_MODEL_PAIRS_FILE"] = str(_PAIRS)
os.environ["NPA_PROXY_OPENROUTER_KEY"] = "test"

if importlib.util.find_spec("httpx") is None:  # pragma: no cover
    pytest.skip("httpx not installed", allow_module_level=True)

_spec = importlib.util.spec_from_file_location("npa_proxy_server", SERVER_PATH)
server = importlib.util.module_from_spec(_spec)
sys.modules["npa_proxy_server"] = server
_spec.loader.exec_module(server)


def test_price_for_from_pairs_and_unknown():
    assert server._price_for("premium") == {"input_per_1m_usd": 10.0, "output_per_1m_usd": 20.0}
    assert server._price_for("unknown") == {"input_per_1m_usd": 0.0, "output_per_1m_usd": 0.0}


def test_usage_tokens_openai_and_responses_shapes():
    assert server._usage_tokens({"usage": {"prompt_tokens": 12, "completion_tokens": 5}}) == (12, 5)
    assert server._usage_tokens({"usage": {"input_tokens": 7, "output_tokens": 9}}) == (7, 9)
    assert server._usage_tokens({}) == (0, 0)
    assert server._usage_tokens({"usage": None}) == (0, 0)


def test_estimate_input_tokens_from_messages_and_input():
    messages = {"messages": [{"role": "user", "content": "x" * 400}]}
    assert server._estimate_input_tokens(messages) > 50
    assert server._estimate_input_tokens({"input": "y" * 40}) == 10
    assert server._estimate_input_tokens({}) == 0
