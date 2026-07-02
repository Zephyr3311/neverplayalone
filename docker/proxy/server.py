"""Containerized OpenAI-compatible egress proxy for miner sandboxes.

Runs inside each evaluation's isolated Docker network as the sandbox's only
route to the upstream LLM provider (OpenRouter by default). It:

  * authenticates every request against a static per-session token table
    (loaded from a mounted sessions file),
  * restricts traffic to chat/completions-style endpoints and an optional
    model allowlist,
  * enforces a per-session USD spend cap, and
  * records per-request and per-session usage to a mounted volume the
    validator reads back after the round.

The real upstream API key lives only in this container; sandboxes receive an
opaque per-session token instead. This module has no dependency on the rest of
the subnet package so the container image stays small.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import httpx

log = logging.getLogger("npa.proxy")


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


UPSTREAM_API_KEY = _env("NPA_PROXY_UPSTREAM_API_KEY")
UPSTREAM_BASE_URL = _env("NPA_PROXY_UPSTREAM_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
LISTEN_HOST = _env("NPA_PROXY_LISTEN_HOST", "0.0.0.0")
LISTEN_PORT = int(_env("NPA_PROXY_LISTEN_PORT", "8080"))
SESSIONS_FILE = Path(_env("NPA_PROXY_SESSIONS_FILE", "/sessions.json"))
USAGE_DIR = Path(_env("NPA_PROXY_USAGE_DIR", "/usage"))
GLOBAL_ALLOWED_MODELS = {m.strip() for m in _env("NPA_PROXY_ALLOWED_MODELS").split(",") if m.strip()}
DEFAULT_INPUT_PRICE = float(_env("NPA_PROXY_DEFAULT_INPUT_PRICE_PER_1M_USD", "0"))
DEFAULT_OUTPUT_PRICE = float(_env("NPA_PROXY_DEFAULT_OUTPUT_PRICE_PER_1M_USD", "0"))
UPSTREAM_TIMEOUT = float(_env("NPA_PROXY_UPSTREAM_TIMEOUT_SECONDS", "60"))
# OpenRouter attribution headers (optional; shown on the OpenRouter dashboard).
REFERER = _env("NPA_PROXY_REFERER", "https://neverplayalone.ai")
TITLE = _env("NPA_PROXY_TITLE", "Never Play Alone")


def _upstream_headers() -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {UPSTREAM_API_KEY}",
        "Content-Type": "application/json",
    }
    if REFERER:
        headers["HTTP-Referer"] = REFERER
    if TITLE:
        headers["X-Title"] = TITLE
    return headers


def _load_model_prices() -> dict[str, dict[str, float]]:
    raw = _env("NPA_PROXY_MODEL_PRICES_JSON").strip()
    if not raw:
        return {}
    parsed = json.loads(raw)
    prices: dict[str, dict[str, float]] = {}
    for model, row in parsed.items():
        prices[model] = {
            "input_per_1m_usd": float(row.get("input_per_1m_usd", 0.0)),
            "output_per_1m_usd": float(row.get("output_per_1m_usd", 0.0)),
        }
    return prices


MODEL_PRICES = _load_model_prices()


@dataclass
class Session:
    session_id: str
    label: str
    max_total_spend_usd: float
    allowed_models: set[str] = field(default_factory=set)
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


def _load_sessions() -> dict[str, Session]:
    if not SESSIONS_FILE.exists():
        return {}
    raw = json.loads(SESSIONS_FILE.read_text())
    sessions: dict[str, Session] = {}
    for token, row in raw.items():
        sessions[token] = Session(
            session_id=str(row["session_id"]),
            label=str(row.get("label", row["session_id"])),
            max_total_spend_usd=float(row.get("max_total_spend_usd", 0.0)),
            allowed_models={str(m) for m in row.get("allowed_models", [])},
        )
    return sessions


def _json_response(handler: BaseHTTPRequestHandler, status: int, payload: dict) -> None:
    body = json.dumps(payload).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _error(message: str, *, code: str) -> dict:
    return {"error": {"message": message, "type": "invalid_request_error", "code": code}}


class Proxy:
    def __init__(self) -> None:
        if not UPSTREAM_API_KEY:
            raise RuntimeError("NPA_PROXY_UPSTREAM_API_KEY is required")
        self.sessions = _load_sessions()
        self._lock = threading.Lock()
        # Force IPv4: OpenRouter resolves to Cloudflare AAAA records the Docker
        # bridge cannot reach, so an IPv6-first attempt would stall before
        # falling back to IPv4. Binding an IPv4 source address avoids it.
        self._client = httpx.Client(
            timeout=UPSTREAM_TIMEOUT,
            transport=httpx.HTTPTransport(local_address="0.0.0.0"),
        )
        USAGE_DIR.mkdir(parents=True, exist_ok=True)
        log.info("proxy loaded %d session(s), upstream=%s", len(self.sessions), UPSTREAM_BASE_URL)

    # ── request routing ────────────────────────────────────────────────
    def handle(self, handler: BaseHTTPRequestHandler) -> None:
        parsed = urlparse(handler.path)
        if parsed.path == "/health":
            _json_response(handler, 200, {"status": "ok"})
            return

        auth = handler.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            _json_response(handler, 401, _error("missing bearer token", code="missing_token"))
            return
        token = auth.removeprefix("Bearer ").strip()
        with self._lock:
            session = self.sessions.get(token)
        if session is None:
            _json_response(handler, 401, _error("unknown proxy session", code="bad_token"))
            return

        path = parsed.path
        if path.startswith("/v1/"):
            path = path[3:]
        if not path.startswith("/"):
            path = "/" + path

        if handler.command == "GET":
            if path != "/models":
                _json_response(handler, 404, _error("unsupported path", code="unsupported_path"))
                return
            self._forward_models(handler)
            return
        if handler.command == "POST":
            if path not in {"/chat/completions", "/responses"}:
                _json_response(handler, 404, _error("unsupported path", code="unsupported_path"))
                return
            self._forward_json(handler, session, path)
            return
        _json_response(handler, 405, _error("method not allowed", code="method_not_allowed"))

    def _forward_models(self, handler: BaseHTTPRequestHandler) -> None:
        try:
            response = self._client.get(
                f"{UPSTREAM_BASE_URL}/models",
                headers=_upstream_headers(),
            )
        except Exception as exc:
            _json_response(handler, 502, _error(f"upstream failed: {exc}", code="upstream_error"))
            return
        self._relay(handler, response)

    def _forward_json(self, handler: BaseHTTPRequestHandler, session: Session, path: str) -> None:
        t0 = time.monotonic()
        try:
            length = int(handler.headers.get("Content-Length", "0"))
        except ValueError:
            _json_response(handler, 400, _error("bad content-length", code="bad_request"))
            return
        if length > 10 * 1024 * 1024:
            self._log_request(session, "", 413, 0, 0, 0.0, 0.0, "oversize_rejected")
            _json_response(handler, 413, _error("request body too large", code="too_large"))
            return
        raw = handler.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
            assert isinstance(payload, dict)
        except Exception:
            _json_response(handler, 400, _error("body must be a JSON object", code="bad_json"))
            return
        if payload.get("stream") is True:
            _json_response(handler, 400, _error("streaming is not supported", code="stream_unsupported"))
            return

        model = str(payload.get("model", "")).strip()
        if not model:
            _json_response(handler, 400, _error("model is required", code="missing_model"))
            return
        allowed = session.allowed_models or GLOBAL_ALLOWED_MODELS
        if allowed and model not in allowed:
            self._log_request(session, model, 403, 0, 0, 0.0, _ms(t0), "model_rejected")
            _json_response(handler, 403, _error(f"model not allowed: {model}", code="model_not_allowed"))
            return

        with self._lock:
            remaining = session.max_total_spend_usd - session.total_spend_usd
        if remaining <= 0:
            self._log_request(session, model, 403, 0, 0, 0.0, _ms(t0), "budget_exhausted")
            _json_response(handler, 403, _error("session spend limit reached", code="budget_exhausted"))
            return
        estimated = self._estimate_cost(payload, model)
        if estimated > remaining:
            self._log_request(session, model, 403, 0, 0, 0.0, _ms(t0), "budget_exceeded")
            _json_response(handler, 403, _error("request exceeds remaining budget", code="budget_exceeded"))
            return

        try:
            response = self._client.post(
                f"{UPSTREAM_BASE_URL}{path}",
                content=json.dumps(payload).encode("utf-8"),
                headers=_upstream_headers(),
            )
        except Exception as exc:
            self._log_request(session, model, 502, 0, 0, 0.0, _ms(t0), "upstream_error")
            _json_response(handler, 502, _error(f"upstream failed: {exc}", code="upstream_error"))
            return

        in_tok, out_tok, cost = self._account(session, model, response, payload)
        self._log_request(session, model, response.status_code, in_tok, out_tok, cost, _ms(t0), "ok")
        self._relay(handler, response)

    # ── accounting ─────────────────────────────────────────────────────
    def _account(self, session: Session, model: str, response, payload: dict) -> tuple[int, int, float]:
        if not (200 <= response.status_code < 300):
            return 0, 0, 0.0
        content_type = response.headers.get("Content-Type", "")
        in_tok = out_tok = 0
        if "application/json" in content_type:
            try:
                body = response.json()
                in_tok, out_tok = _usage_tokens(body)
            except Exception:
                body = None
            if in_tok == 0 and out_tok == 0:
                # Upstream omitted usage; fall back to estimates so the spend
                # cap still depletes and a miner cannot get unmetered calls.
                in_tok = _estimate_input_tokens(payload)
                out_tok = max(1, len(response.content) // 4)
        price = _price_for(model)
        cost = ((in_tok * price["input_per_1m_usd"]) + (out_tok * price["output_per_1m_usd"])) / 1_000_000.0
        with self._lock:
            session.request_count += 1
            session.total_input_tokens += in_tok
            session.total_output_tokens += out_tok
            session.total_spend_usd += cost
            session.last_model = model
            session.updated_at = time.time()
            self._write_summary(session)
        return in_tok, out_tok, cost

    def _estimate_cost(self, payload: dict, model: str) -> float:
        price = _price_for(model)
        in_tok = _estimate_input_tokens(payload)
        out_tok = int(
            payload.get("max_output_tokens", payload.get("max_completion_tokens", payload.get("max_tokens", 0)))
            or 0
        )
        return ((in_tok * price["input_per_1m_usd"]) + (out_tok * price["output_per_1m_usd"])) / 1_000_000.0

    def _write_summary(self, session: Session) -> None:
        try:
            (USAGE_DIR / f"{session.session_id}.summary.json").write_text(
                json.dumps(session.summary(), indent=2)
            )
        except OSError as exc:
            log.warning("failed to write usage summary for %s: %s", session.session_id, exc)

    def _log_request(
        self,
        session: Session,
        model: str,
        status: int,
        in_tok: int,
        out_tok: int,
        cost: float,
        duration_ms: float,
        outcome: str,
    ) -> None:
        entry = {
            "timestamp": int(time.time() * 1000),
            "session_id": session.session_id,
            "model": model,
            "status": status,
            "input_tokens": in_tok,
            "output_tokens": out_tok,
            "cost_usd": round(cost, 8),
            "duration_ms": round(duration_ms, 1),
            "outcome": outcome,
        }
        try:
            with (USAGE_DIR / f"{session.session_id}.jsonl").open("a") as fh:
                fh.write(json.dumps(entry) + "\n")
        except OSError as exc:
            log.warning("failed to log request for %s: %s", session.session_id, exc)

    @staticmethod
    def _relay(handler: BaseHTTPRequestHandler, response) -> None:
        body = response.content
        content_type = response.headers.get("Content-Type", "application/json")
        handler.send_response(response.status_code)
        handler.send_header("Content-Type", content_type)
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)


def _price_for(model: str) -> dict[str, float]:
    return MODEL_PRICES.get(
        model,
        {"input_per_1m_usd": DEFAULT_INPUT_PRICE, "output_per_1m_usd": DEFAULT_OUTPUT_PRICE},
    )


def _usage_tokens(payload: dict) -> tuple[int, int]:
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return 0, 0
    in_tok = usage.get("prompt_tokens", usage.get("input_tokens", 0))
    out_tok = usage.get("completion_tokens", usage.get("output_tokens", 0))
    return int(in_tok or 0), int(out_tok or 0)


def _estimate_input_tokens(payload: dict) -> int:
    messages = payload.get("messages")
    if isinstance(messages, list):
        chars = sum(len(json.dumps(m, ensure_ascii=False)) for m in messages)
        return max(1, chars // 4)
    value = payload.get("input")
    if isinstance(value, str):
        return max(1, len(value) // 4)
    if isinstance(value, list):
        chars = sum(len(json.dumps(item, ensure_ascii=False)) for item in value)
        return max(1, chars // 4)
    return 0


def _ms(t0: float) -> float:
    return (time.monotonic() - t0) * 1000


def main() -> None:
    logging.basicConfig(level=os.environ.get("NPA_PROXY_LOG_LEVEL", "INFO").upper())
    proxy = Proxy()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            proxy.handle(self)

        def do_POST(self) -> None:  # noqa: N802
            proxy.handle(self)

        def log_message(self, fmt: str, *args) -> None:
            log.debug("proxy %s - %s", self.address_string(), fmt % args)

    server = ThreadingHTTPServer((LISTEN_HOST, LISTEN_PORT), Handler)
    log.info("proxy listening on %s:%d", LISTEN_HOST, LISTEN_PORT)
    server.serve_forever()


if __name__ == "__main__":
    main()
