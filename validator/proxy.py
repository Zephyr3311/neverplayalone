"""Validator-side manager for the sandbox egress proxy container.

The proxy itself runs as a Docker container (see ``docker/proxy/server.py``)
attached to each evaluation slot's internal network, so sandboxed agents reach
it by container DNS and never touch the host. This module owns that container's
lifecycle plus the per-session tokens: it mints an opaque token per agent,
hands the agent the proxy's internal base URL via environment variables, and
reads back the usage the container records to a shared volume.

The real upstream API key lives only inside the container. Sandboxes only ever
see their own session token.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from validator.config import (
    OPENROUTER_API_KEY,
    OPENROUTER_BASE_URL,
    PROXY_ALLOWED_MODELS,
    PROXY_DEFAULT_INPUT_PRICE_PER_1M_USD,
    PROXY_DEFAULT_OUTPUT_PRICE_PER_1M_USD,
    PROXY_ENABLED,
    PROXY_MAX_TOTAL_SPEND_USD,
    PROXY_MODEL_PRICES_JSON,
    PROXY_PORT,
    PROXY_UPSTREAM_TIMEOUT_SECONDS,
)

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
PROXY_BUILD_DIR = REPO_ROOT / "docker" / "proxy"
PROXY_IMAGE_REPO = "npa-proxy"


@dataclass
class ProxySession:
    session_id: str
    token: str
    label: str
    max_total_spend_usd: float
    env: dict[str, str] = field(default_factory=dict)


def _image_tag() -> str:
    recipe = b"".join(
        (PROXY_BUILD_DIR / name).read_bytes() for name in ("Dockerfile", "server.py")
    )
    return f"{PROXY_IMAGE_REPO}:{hashlib.sha256(recipe).hexdigest()[:12]}"


def ensure_proxy_image() -> str:
    tag = _image_tag()
    present = subprocess.run(
        ["docker", "image", "inspect", tag], capture_output=True, text=True
    ).returncode == 0
    if present:
        return tag
    log.info("building proxy image %s (one-time)", tag)
    result = subprocess.run(
        ["docker", "build", "-t", tag, str(PROXY_BUILD_DIR)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"building proxy image failed (exit {result.returncode})\n"
            f"--- stderr ---\n{result.stderr}\n--- stdout ---\n{result.stdout}"
        )
    return tag


class ProxyContainer:
    """Lifecycle + session table for one round's egress proxy container."""

    def __init__(
        self,
        *,
        container_name: str,
        listen_port: int,
        workspace: Path,
        upstream_api_key: str,
        upstream_base_url: str,
        allowed_models: str,
        model_prices_json: str,
        default_input_price: float,
        default_output_price: float,
        default_max_total_spend_usd: float,
        upstream_timeout_seconds: float,
    ) -> None:
        self.container_name = container_name
        self.listen_port = listen_port
        self.workspace = workspace
        self.upstream_api_key = upstream_api_key
        self.upstream_base_url = upstream_base_url
        self.allowed_models = allowed_models
        self.model_prices_json = model_prices_json
        self.default_input_price = default_input_price
        self.default_output_price = default_output_price
        self.default_max_total_spend_usd = default_max_total_spend_usd
        self.upstream_timeout_seconds = upstream_timeout_seconds
        self._sessions: dict[str, ProxySession] = {}
        self._proxy_dir = workspace / "proxy"
        self._usage_dir = self._proxy_dir / "usage"
        self._sessions_file = self._proxy_dir / "sessions.json"
        self._env_file = self._proxy_dir / "proxy.env"
        self._started = False

    @classmethod
    def from_config(cls, *, container_name: str, workspace: Path) -> "ProxyContainer":
        if not PROXY_ENABLED:
            raise RuntimeError("proxy is disabled")
        if not OPENROUTER_API_KEY:
            raise RuntimeError("OPENROUTER_API_KEY is required when proxy is enabled")
        return cls(
            container_name=container_name,
            listen_port=PROXY_PORT,
            workspace=workspace,
            upstream_api_key=OPENROUTER_API_KEY,
            upstream_base_url=OPENROUTER_BASE_URL,
            allowed_models=PROXY_ALLOWED_MODELS,
            model_prices_json=PROXY_MODEL_PRICES_JSON,
            default_input_price=PROXY_DEFAULT_INPUT_PRICE_PER_1M_USD,
            default_output_price=PROXY_DEFAULT_OUTPUT_PRICE_PER_1M_USD,
            default_max_total_spend_usd=PROXY_MAX_TOTAL_SPEND_USD,
            upstream_timeout_seconds=PROXY_UPSTREAM_TIMEOUT_SECONDS,
        )

    @property
    def name(self) -> str:
        return self.container_name

    def mint_session(self, label: str, *, max_total_spend_usd: float | None = None) -> ProxySession:
        """Create a session token + the agent env that routes it through the proxy.

        Must be called before ``start()``; the full table is baked into the
        container at launch.
        """
        if self._started:
            raise RuntimeError("cannot mint sessions after the proxy container has started")
        session_id = uuid.uuid4().hex
        token = uuid.uuid4().hex
        base_url = f"http://{self.container_name}:{self.listen_port}/v1"
        session = ProxySession(
            session_id=session_id,
            token=token,
            label=label,
            max_total_spend_usd=(
                self.default_max_total_spend_usd
                if max_total_spend_usd is None
                else max_total_spend_usd
            ),
            env={
                "OPENAI_BASE_URL": base_url,
                "OPENAI_API_KEY": token,
                "OPENROUTER_BASE_URL": base_url,
                "OPENROUTER_API_KEY": token,
                "NPA_PROXY_SESSION_TOKEN": token,
            },
        )
        self._sessions[session_id] = session
        return session

    def start(self) -> None:
        if self._started:
            return
        ensure_proxy_image()
        self._usage_dir.mkdir(parents=True, exist_ok=True)
        self._write_sessions_file()
        self._write_env_file()
        subprocess.run(
            ["docker", "rm", "-f", self.container_name], capture_output=True, text=True
        )
        command = [
            "docker", "run", "-d",
            "--name", self.container_name,
            "--user", f"{os.getuid()}:{os.getgid()}",
            "--env-file", str(self._env_file),
            "-v", f"{self._sessions_file}:/sessions.json:ro",
            "-v", f"{self._usage_dir}:/usage",
            _image_tag(),
        ]
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(
                f"starting proxy container failed (exit {result.returncode})\n"
                f"--- stderr ---\n{result.stderr}\n--- stdout ---\n{result.stdout}"
            )
        self._started = True
        log.info(
            "proxy container %s started with %d session(s)",
            self.container_name,
            len(self._sessions),
        )

    def stop(self) -> None:
        subprocess.run(
            ["docker", "rm", "-f", self.container_name], capture_output=True, text=True
        )
        self._started = False

    def read_usage(self, session_id: str) -> dict | None:
        summary_path = self._usage_dir / f"{session_id}.summary.json"
        if not summary_path.exists():
            return None
        try:
            return json.loads(summary_path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("could not read usage summary for %s: %s", session_id, exc)
            return None

    # ── internal ───────────────────────────────────────────────────────
    def _write_sessions_file(self) -> None:
        table = {
            session.token: {
                "session_id": session.session_id,
                "label": session.label,
                "max_total_spend_usd": session.max_total_spend_usd,
            }
            for session in self._sessions.values()
        }
        self._sessions_file.write_text(json.dumps(table, indent=2))
        self._sessions_file.chmod(0o600)

    def _write_env_file(self) -> None:
        env = {
            "NPA_PROXY_UPSTREAM_API_KEY": self.upstream_api_key,
            "NPA_PROXY_UPSTREAM_BASE_URL": self.upstream_base_url,
            "NPA_PROXY_LISTEN_PORT": str(self.listen_port),
            "NPA_PROXY_ALLOWED_MODELS": self.allowed_models,
            "NPA_PROXY_MODEL_PRICES_JSON": self.model_prices_json,
            "NPA_PROXY_DEFAULT_INPUT_PRICE_PER_1M_USD": str(self.default_input_price),
            "NPA_PROXY_DEFAULT_OUTPUT_PRICE_PER_1M_USD": str(self.default_output_price),
            "NPA_PROXY_UPSTREAM_TIMEOUT_SECONDS": str(self.upstream_timeout_seconds),
        }
        self._env_file.write_text("".join(f"{key}={value}\n" for key, value in env.items()))
        self._env_file.chmod(0o600)
