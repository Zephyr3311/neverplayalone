"""Round evaluation helpers backed by mcbench batch execution."""
from __future__ import annotations

import hashlib
import io
import json
import logging
import shutil
import tarfile
from pathlib import Path, PurePosixPath

from . import chain
from .api_client import APIClient
from .config import MAX_PARALLEL_AGENTS, MISSION_ID, PROXY_ENABLED, WORKSPACE_ROOT
from .proxy import LocalChutesProxy, configure_mcbench_proxy

log = logging.getLogger(__name__)


def _safe_dirname(raw: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in raw).strip("_") or "miner"


def _workspace(round_id: int) -> Path:
    root = Path(WORKSPACE_ROOT).resolve() / f"round_{round_id}"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _safe_extract_tar_gz(payload: bytes, dest: Path) -> None:
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
        for member in archive.getmembers():
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError(f"unsafe tar path: {member.name}")
            if member.issym() or member.islnk():
                raise ValueError(f"link entries are not allowed in agent archive: {member.name}")
            if member.isdev():
                raise ValueError(f"device entries are not allowed in agent archive: {member.name}")
        archive.extractall(dest)


def _materialize_agents(api: APIClient, roster: dict, workspace: Path) -> dict[str, dict]:
    agents_root = workspace / "agents"
    agents_root.mkdir(parents=True, exist_ok=True)
    local_entries: dict[str, dict] = {}
    for entry in roster["entries"]:
        agent_dir = agents_root / f"{entry['miner_uid']}_{_safe_dirname(entry['miner_hotkey'])}"
        shutil.rmtree(agent_dir, ignore_errors=True)
        agent_dir.mkdir(parents=True, exist_ok=True)
        payload = api.download_bytes(entry["download_url"])
        _safe_extract_tar_gz(payload, agent_dir)
        local_entries[entry["miner_hotkey"]] = {
            **entry,
            "agent_dir": agent_dir,
        }
    return local_entries


def _ensure_recording_file(report) -> Path:
    if report.recording_path is not None and report.recording_path.exists():
        return report.recording_path
    fallback = report.output_dir / "recording.mcpr"
    fallback.write_bytes(b"")
    return fallback


def _write_proxy_usage(report, usage_summary: dict | None) -> None:
    if not usage_summary:
        return
    payload = json.dumps(usage_summary, indent=2)
    (report.output_dir / "proxy_usage.json").write_text(payload)
    report_path = report.output_dir / "report.json"
    if report_path.exists():
        report_data = json.loads(report_path.read_text())
        report_data["proxy_usage"] = usage_summary
        report_path.write_text(json.dumps(report_data, indent=2))


def _per_validator_seed(round_id: int, mission_id: str, validator_hotkey: str) -> int:
    block_hash = chain.current_block_hash()
    material = f"{mission_id}:{round_id}:{block_hash}:{validator_hotkey}"
    return int(hashlib.sha256(material.encode("utf-8")).hexdigest(), 16)


def run_round_evaluation(wallet, api: APIClient, round_state: dict) -> dict:
    from mcbench import AgentMode, AgentSpec, evaluate_multiple_agents

    round_id = int(round_state["round_id"])
    roster = api.get_round_roster(round_id)
    workspace = _workspace(round_id)
    local_entries = _materialize_agents(api, roster, workspace)
    if not local_entries:
        log.info("round=%s: roster is empty", round_id)
        return {"round_id": round_id, "rows": []}

    validator_hotkey = wallet.hotkey.ss58_address
    seed = _per_validator_seed(
        round_id,
        roster.get("mission_id", MISSION_ID),
        validator_hotkey,
    )
    validator_uid = chain.hotkey_uid(validator_hotkey)
    stake_weight = chain.self_stake_for_hotkey(
        validator_hotkey,
        round_state.get("freeze_block_hash"),
    )

    agent_specs = [
        AgentSpec(name=hotkey, path=entry["agent_dir"])
        for hotkey, entry in local_entries.items()
    ]
    proxy = LocalChutesProxy.from_config() if PROXY_ENABLED else None
    session_tokens: dict[str, str] = {}
    agent_env_by_name: dict[str, dict[str, str]] = {}
    if proxy is not None:
        proxy.start()
        for miner_hotkey in local_entries:
            session = proxy.create_session(
                f"round={round_id}:{miner_hotkey}",
            )
            session_tokens[miner_hotkey] = session.token
            agent_env_by_name[miner_hotkey] = session.env
    try:
        with configure_mcbench_proxy(agent_env_by_name):
            batch_report = evaluate_multiple_agents(
                agent_specs,
                mission_id=roster.get("mission_id", MISSION_ID),
                seed=seed,
                output_dir=workspace / "mcbench_results",
                record=True,
                agent_mode=AgentMode.SANDBOXED,
                max_parallel=MAX_PARALLEL_AGENTS,
            )
    finally:
        if proxy is not None:
            proxy.stop()

    rows: list[dict] = []
    for miner_hotkey, entry in local_entries.items():
        report = batch_report.agents[miner_hotkey]
        if proxy is not None:
            _write_proxy_usage(report, proxy.usage_summary(session_tokens[miner_hotkey]))
        report_path = report.output_dir / "report.json"
        if not report_path.exists():
            raise RuntimeError(f"missing report.json for miner {miner_hotkey}")
        recording_path = _ensure_recording_file(report)

        report_slot = api.request_artifact_slot(
            round_id=round_id,
            validator_uid=validator_uid,
            miner_uid=entry["miner_uid"],
            miner_hotkey=miner_hotkey,
            artifact_kind="report_json",
        )
        recording_slot = api.request_artifact_slot(
            round_id=round_id,
            validator_uid=validator_uid,
            miner_uid=entry["miner_uid"],
            miner_hotkey=miner_hotkey,
            artifact_kind="recording_mcpr",
        )

        api.upload_bytes(report_slot["upload_url"], report_path.read_bytes())
        api.upload_bytes(recording_slot["upload_url"], recording_path.read_bytes())

        rows.append(
            {
                "miner_uid": entry["miner_uid"],
                "miner_hotkey": miner_hotkey,
                "score": float(report.score),
                "status": report.status,
                "report_s3_key": report_slot["storage_key"],
                "recording_s3_key": recording_slot["storage_key"],
            }
        )

    api.upload_scoreboard(
        round_id=round_id,
        validator_uid=validator_uid,
        stake_weight=stake_weight,
        rows=rows,
    )
    return {
        "round_id": round_id,
        "validator_uid": validator_uid,
        "stake_weight": stake_weight,
        "rows": rows,
    }
