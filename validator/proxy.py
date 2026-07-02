"""Validator-local OpenAI-compatible proxy restricted to Chutes upstream."""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Iterator
from urllib.parse import urlparse

import httpx

from validator.config import (
    CHUTES_API_KEY,
    CHUTES_BASE_URL,
    PROXY_ALLOWED_MODELS,
    PROXY_BIND_HOST,
    PROXY_DEFAULT_INPUT_PRICE_PER_1M_USD,
    PROXY_DEFAULT_OUTPUT_PRICE_PER_1M_USD,
    PROXY_ENABLED,
    PROXY_MAX_TOTAL_SPEND_USD,
    PROXY_MODEL_PRICES_JSON,
    PROXY_PORT,
    PROXY_UPSTREAM_TIMEOUT_SECONDS,
)

log = logging.getLogger(__name__)


def _json_response(handler: BaseHTTPRequestHandler, status: int, payload: dict) -> None:
    body = json.dumps(payload).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _error_payload(message: str, *, code: str = "proxy_error", error_type: str = "invalid_request_error") -> dict:
    return {
        "error": {
            "message": message,
            "type": error_type,
            "code": code,
        }
    }


@dataclass(frozen=True)
class SessionEnv:
    token: str
    env: dict[str, str]


@dataclass
class ProxySession:
    session_id: str
    label: str
    max_total_spend_usd: float
    request_count: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_spend_usd: float = 0.0
    last_model: str | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def summary(self) -> dict:
        return {
            "session_id": self.session_id,
            "label": self.label,
            "request_count": self.request_count,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_spend_usd": round(self.total_spend_usd, 8),
            "max_total_spend_usd": self.max_total_spend_usd,
            "last_model": self.last_model,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class LocalChutesProxy:
    def __init__(
        self,
        *,
        bind_host: str,
        port: int,
        upstream_base_url: str,
        api_key: str,
        allowed_models: set[str],
        model_prices: dict[str, dict[str, float]],
        default_input_price_per_1m_usd: float,
        default_output_price_per_1m_usd: float,
        default_max_total_spend_usd: float,
        upstream_timeout_seconds: float,
    ):
        self.bind_host = bind_host
        self.port = port
        self.upstream_base_url = upstream_base_url.rstrip("/")
        self.api_key = api_key
        self.allowed_models = allowed_models
        self.model_prices = model_prices
        self.default_input_price_per_1m_usd = default_input_price_per_1m_usd
        self.default_output_price_per_1m_usd = default_output_price_per_1m_usd
        self.default_max_total_spend_usd = default_max_total_spend_usd
        self.upstream_timeout_seconds = upstream_timeout_seconds
        self._client = httpx.Client(timeout=upstream_timeout_seconds)
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._sessions: dict[str, ProxySession] = {}
        self._lock = threading.Lock()

    @classmethod
    def from_config(cls) -> "LocalChutesProxy":
        if not PROXY_ENABLED:
            raise RuntimeError("proxy is disabled")
        if not CHUTES_API_KEY:
            raise RuntimeError("CHUTES_API_KEY (or NPA_CHUTES_API_KEY) is required when proxy is enabled")
        allowed_models = {
            model.strip() for model in PROXY_ALLOWED_MODELS.split(",") if model.strip()
        }
        prices: dict[str, dict[str, float]] = {}
        if PROXY_MODEL_PRICES_JSON.strip():
            raw = json.loads(PROXY_MODEL_PRICES_JSON)
            if not isinstance(raw, dict):
                raise ValueError("NPA_PROXY_MODEL_PRICES_JSON must be a JSON object")
            for model, row in raw.items():
                if not isinstance(model, str) or not isinstance(row, dict):
                    raise ValueError("model prices must be an object of model -> pricing rows")
                prices[model] = {
                    "input_per_1m_usd": float(row.get("input_per_1m_usd", 0.0)),
                    "output_per_1m_usd": float(row.get("output_per_1m_usd", 0.0)),
                }
        return cls(
            bind_host=PROXY_BIND_HOST,
            port=PROXY_PORT,
            upstream_base_url=CHUTES_BASE_URL,
            api_key=CHUTES_API_KEY,
            allowed_models=allowed_models,
            model_prices=prices,
            default_input_price_per_1m_usd=PROXY_DEFAULT_INPUT_PRICE_PER_1M_USD,
            default_output_price_per_1m_usd=PROXY_DEFAULT_OUTPUT_PRICE_PER_1M_USD,
            default_max_total_spend_usd=PROXY_MAX_TOTAL_SPEND_USD,
            upstream_timeout_seconds=PROXY_UPSTREAM_TIMEOUT_SECONDS,
        )

    def start(self) -> None:
        if self._server is not None:
            return
        proxy = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                proxy.handle(self)

            def do_POST(self) -> None:  # noqa: N802
                proxy.handle(self)

            def log_message(self, fmt: str, *args) -> None:
                log.debug("proxy %s - %s", self.address_string(), fmt % args)

        self._server = ThreadingHTTPServer((self.bind_host, self.port), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        log.info("local proxy listening on http://%s:%s", self.bind_host, self.port)

    def stop(self) -> None:
        if self._server is None:
            return
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._server = None
        self._thread = None
        self._client.close()

    def create_session(self, label: str, *, max_total_spend_usd: float | None = None) -> SessionEnv:
        session_id = uuid.uuid4().hex
        token = uuid.uuid4().hex
        with self._lock:
            self._sessions[token] = ProxySession(
                session_id=session_id,
                label=label,
                max_total_spend_usd=(
                    self.default_max_total_spend_usd
                    if max_total_spend_usd is None
                    else max_total_spend_usd
                ),
            )
        base_url = f"http://host.docker.internal:{self.port}/v1"
        env = {
            "OPENAI_BASE_URL": base_url,
            "OPENAI_API_KEY": token,
            "CHUTES_BASE_URL": base_url,
            "CHUTES_API_KEY": token,
            "NPA_PROXY_SESSION_TOKEN": token,
        }
        return SessionEnv(token=token, env=env)

    def usage_summary(self, token: str) -> dict | None:
        with self._lock:
            session = self._sessions.get(token)
            return None if session is None else session.summary()

    def handle(self, handler: BaseHTTPRequestHandler) -> None:
        parsed = urlparse(handler.path)
        if parsed.path == "/health":
            _json_response(handler, 200, {"status": "ok"})
            return

        auth = handler.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            _json_response(handler, 401, _error_payload("missing bearer token", code="missing_token"))
            return
        token = auth.removeprefix("Bearer ").strip()
        with self._lock:
            session = self._sessions.get(token)
        if session is None:
            _json_response(handler, 401, _error_payload("unknown proxy session", code="bad_token"))
            return

        path = parsed.path
        if path.startswith("/v1/"):
            path = path[3:]
        if not path.startswith("/"):
            path = "/" + path
        if handler.command not in {"GET", "POST"}:
            _json_response(handler, 405, _error_payload("method not allowed", code="method_not_allowed"))
            return

        if handler.command == "GET":
            if path != "/models":
                _json_response(handler, 404, _error_payload("unsupported proxy path", code="unsupported_path"))
                return
            self._forward_models(handler)
            return

        if path not in {"/chat/completions", "/responses"}:
            _json_response(handler, 404, _error_payload("unsupported proxy path", code="unsupported_path"))
            return
        self._forward_json(handler, session, path)

    def _forward_models(self, handler: BaseHTTPRequestHandler) -> None:
        try:
            response = self._client.get(
                f"{self.upstream_base_url}/models",
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
        except Exception as exc:
            _json_response(handler, 502, _error_payload(f"upstream request failed: {exc}", code="upstream_error"))
            return

        handler.send_response(response.status_code)
        content_type = response.headers.get("Content-Type", "application/json")
        body = response.content
        handler.send_header("Content-Type", content_type)
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)

    def _forward_json(self, handler: BaseHTTPRequestHandler, session: ProxySession, path: str) -> None:
        try:
            content_length = int(handler.headers.get("Content-Length", "0"))
        except ValueError:
            _json_response(handler, 400, _error_payload("bad content-length", code="bad_request"))
            return
        raw = handler.rfile.read(content_length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception:
            _json_response(handler, 400, _error_payload("body must be valid JSON", code="bad_json"))
            return
        if not isinstance(payload, dict):
            _json_response(handler, 400, _error_payload("JSON body must be an object", code="bad_json"))
            return
        if payload.get("stream") is True:
            _json_response(handler, 400, _error_payload("streaming is not supported by the validator proxy", code="stream_unsupported"))
            return

        model = str(payload.get("model", "")).strip()
        if not model:
            _json_response(handler, 400, _error_payload("model is required", code="missing_model"))
            return
        if self.allowed_models and model not in self.allowed_models:
            _json_response(handler, 403, _error_payload(f"model not allowed: {model}", code="model_not_allowed"))
            return
        if session.total_spend_usd >= session.max_total_spend_usd:
            _json_response(handler, 403, _error_payload("proxy session spend limit reached", code="budget_exhausted"))
            return

        estimated_cost = self._estimate_cost_usd(payload, model)
        if session.total_spend_usd + estimated_cost > session.max_total_spend_usd:
            _json_response(handler, 403, _error_payload("request exceeds remaining proxy budget", code="budget_exceeded"))
            return

        try:
            response = self._client.post(
                f"{self.upstream_base_url}{path}",
                content=json.dumps(payload).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
            )
        except Exception as exc:
            _json_response(handler, 502, _error_payload(f"upstream request failed: {exc}", code="upstream_error"))
            return

        body = response.content
        content_type = response.headers.get("Content-Type", "application/json")
        if 200 <= response.status_code < 300 and "application/json" in content_type:
            try:
                response_payload = response.json()
                self._record_usage(session, model, response_payload)
            except Exception as exc:
                log.warning("proxy usage accounting failed for %s: %s", session.label, exc)

        handler.send_response(response.status_code)
        handler.send_header("Content-Type", content_type)
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)

    def _record_usage(self, session: ProxySession, model: str, payload: dict) -> None:
        input_tokens, output_tokens = self._extract_usage_tokens(payload)
        price = self._pricing_for_model(model)
        request_cost = (
            (input_tokens * price["input_per_1m_usd"]) + (output_tokens * price["output_per_1m_usd"])
        ) / 1_000_000.0
        with self._lock:
            session.request_count += 1
            session.total_input_tokens += input_tokens
            session.total_output_tokens += output_tokens
            session.total_spend_usd += request_cost
            session.last_model = model
            session.updated_at = time.time()

    def _pricing_for_model(self, model: str) -> dict[str, float]:
        return self.model_prices.get(
            model,
            {
                "input_per_1m_usd": self.default_input_price_per_1m_usd,
                "output_per_1m_usd": self.default_output_price_per_1m_usd,
            },
        )

    def _extract_usage_tokens(self, payload: dict) -> tuple[int, int]:
        usage = payload.get("usage")
        if not isinstance(usage, dict):
            return 0, 0
        input_tokens = usage.get("prompt_tokens", usage.get("input_tokens", 0))
        output_tokens = usage.get("completion_tokens", usage.get("output_tokens", 0))
        return int(input_tokens or 0), int(output_tokens or 0)

    def _estimate_cost_usd(self, payload: dict, model: str) -> float:
        price = self._pricing_for_model(model)
        estimated_input_tokens = self._estimate_input_tokens(payload)
        estimated_output_tokens = int(
            payload.get(
                "max_output_tokens",
                payload.get("max_completion_tokens", payload.get("max_tokens", 0)),
            )
            or 0
        )
        return (
            (estimated_input_tokens * price["input_per_1m_usd"])
            + (estimated_output_tokens * price["output_per_1m_usd"])
        ) / 1_000_000.0

    @staticmethod
    def _estimate_input_tokens(payload: dict) -> int:
        messages = payload.get("messages")
        if isinstance(messages, list):
            chars = sum(len(json.dumps(message, ensure_ascii=False)) for message in messages)
            return max(1, chars // 4)
        input_value = payload.get("input")
        if isinstance(input_value, str):
            return max(1, len(input_value) // 4)
        if isinstance(input_value, list):
            chars = sum(len(json.dumps(item, ensure_ascii=False)) for item in input_value)
            return max(1, chars // 4)
        return 0


@contextmanager
def configure_npabench_proxy(agent_env_by_name: dict[str, dict[str, str]]) -> Iterator[None]:
    if not agent_env_by_name:
        yield
        return

    from npabench.agents import sandboxed_agent

    original = sandboxed_agent.SandboxedAgent.docker_run_cmd

    def patched(self, context, image):
        cmd = list(original(self, context, image))
        extra_env = agent_env_by_name.get(self.spec.name)
        if not extra_env:
            return cmd
        injection = ["--add-host", "host.docker.internal:host-gateway"]
        for key, value in extra_env.items():
            injection += ["-e", f"{key}={value}"]
        return cmd[:-3] + injection + cmd[-3:]

    sandboxed_agent.SandboxedAgent.docker_run_cmd = patched
    try:
        yield
    finally:
        sandboxed_agent.SandboxedAgent.docker_run_cmd = original

