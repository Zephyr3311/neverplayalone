from __future__ import annotations

import json
from pathlib import Path

from validator.proxy import ProxyContainer


def _container(workspace: Path) -> ProxyContainer:
    return ProxyContainer(
        container_name="npa-proxy-test",
        listen_port=8080,
        workspace=workspace,
        upstream_api_key="secret-key",
        upstream_base_url="https://openrouter.ai/api/v1",
        allowed_models="",
        model_prices_json="",
        default_input_price=0.0,
        default_output_price=0.0,
        default_max_total_spend_usd=1.0,
        upstream_timeout_seconds=60.0,
    )


def test_mint_session_env_points_at_container_dns(tmp_path):
    proxy = _container(tmp_path)
    session = proxy.mint_session("round=1:entry-a")

    expected_url = "http://npa-proxy-test:8080/v1"
    assert session.env["OPENROUTER_BASE_URL"] == expected_url
    assert session.env["OPENAI_BASE_URL"] == expected_url
    # The agent gets the opaque session token, never the real upstream key.
    assert session.env["OPENROUTER_API_KEY"] == session.token
    assert session.env["OPENAI_API_KEY"] == session.token
    assert session.token != "secret-key"
    assert session.max_total_spend_usd == 1.0


def test_sessions_file_maps_tokens_and_hides_upstream_key(tmp_path):
    proxy = _container(tmp_path)
    s1 = proxy.mint_session("m1")
    s2 = proxy.mint_session("m2", max_total_spend_usd=0.5)
    (tmp_path / "proxy").mkdir(parents=True, exist_ok=True)
    proxy._write_sessions_file()
    proxy._write_env_file()

    table = json.loads((tmp_path / "proxy" / "sessions.json").read_text())
    assert set(table) == {s1.token, s2.token}
    assert table[s1.token]["session_id"] == s1.session_id
    assert table[s2.token]["max_total_spend_usd"] == 0.5
    # The real key only appears in the env-file (mounted into the container),
    # never in the sessions table the tokens map through.
    assert "secret-key" not in json.dumps(table)
    assert "NPA_PROXY_UPSTREAM_API_KEY=secret-key" in (tmp_path / "proxy" / "proxy.env").read_text()


def test_read_usage_roundtrip(tmp_path):
    proxy = _container(tmp_path)
    session = proxy.mint_session("m1")
    usage_dir = tmp_path / "proxy" / "usage"
    usage_dir.mkdir(parents=True, exist_ok=True)
    summary = {"session_id": session.session_id, "total_spend_usd": 0.02, "request_count": 3}
    (usage_dir / f"{session.session_id}.summary.json").write_text(json.dumps(summary))

    assert proxy.read_usage(session.session_id) == summary
    assert proxy.read_usage("missing") is None


def test_cannot_mint_after_start_flag(tmp_path):
    proxy = _container(tmp_path)
    proxy._started = True
    try:
        proxy.mint_session("late")
    except RuntimeError as exc:
        assert "after the proxy container has started" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected RuntimeError")
