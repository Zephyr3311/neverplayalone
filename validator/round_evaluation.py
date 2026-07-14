"""Round evaluation helpers backed by npabench batch execution."""
from __future__ import annotations

import hashlib
import io
import json
import logging
import shutil
import tarfile
from pathlib import Path, PurePosixPath

from shared import chain
from shared.api_client import APIClient

from validator.config import MAX_PARALLEL_AGENTS, MISSION_ID, TASKS_PER_ROUND, WORKSPACE_ROOT
from validator.proxy import ProxyContainer

log = logging.getLogger(__name__)


def _safe_dirname(raw: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in raw).strip("_") or "miner"


def _workspace(round_id: str) -> Path:
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
        entry_id = entry.get("entry_id") or entry["submission_id"]
        entry_kind = entry.get("entry_kind", "submission")
        spec_name = _safe_dirname(f"{entry_kind}_{entry_id}")
        agent_dir = agents_root / spec_name
        shutil.rmtree(agent_dir, ignore_errors=True)
        agent_dir.mkdir(parents=True, exist_ok=True)
        payload = api.download_bytes(entry["download_url"])
        _safe_extract_tar_gz(payload, agent_dir)
        local_entries[entry_id] = {
            "entry_id": entry_id,
            "entry_kind": entry_kind,
            "miner_uid": entry["miner_uid"],
            "miner_hotkey": entry["miner_hotkey"],
            "submission_id": entry.get("submission_id"),
            "source_round_id": entry.get("source_round_id"),
            "spec_name": spec_name,
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


def _per_validator_seed(round_id: str, mission_id: str, validator_hotkey: str) -> int:
    block_hash = chain.current_block_hash()
    material = f"{mission_id}:{round_id}:{block_hash}:{validator_hotkey}"
    return int(hashlib.sha256(material.encode("utf-8")).hexdigest(), 16)


def _aggregate_status(statuses: list[str]) -> str:
    """Combine per-task statuses into one scoreboard status: ``ok`` if the agent
    produced a good run in any task, otherwise the first non-ok status seen."""
    if "ok" in statuses:
        return "ok"
    return statuses[0] if statuses else "error"


def run_round_evaluation(wallet, api: APIClient, round_state: dict) -> dict:
    from npabench import AgentMode, AgentSpec, evaluate_multiple_agents

    round_id = round_state["round_id"]  # date-based string id, e.g. "2026-07-06-AM"
    log.info("round=%s: fetching roster", round_id)
    roster = api.get_round_roster(round_id)
    workspace = _workspace(round_id)
    log.info("round=%s: workspace=%s", round_id, workspace)
    local_entries = _materialize_agents(api, roster, workspace)
    log.info("round=%s: materialized %s roster entries", round_id, len(local_entries))
    if not local_entries:
        log.info("round=%s: roster is empty", round_id)
        return {"round_id": round_id, "rows": []}

    validator_hotkey = wallet.hotkey.ss58_address
    mission_id = roster.get("mission_id", MISSION_ID)
    validator_uid = chain.hotkey_uid(validator_hotkey)
    stake_weight = chain.self_stake_for_hotkey(
        validator_hotkey,
        round_state.get("freeze_block_hash"),
    )

    proxy = ProxyContainer.from_config(
        container_name=f"npa-proxy-round-{round_id}",
        workspace=workspace,
    )
    # One proxy session per (entry, task): each task gets its own spend budget, so
    # a miner's total budget for the round is TASKS_PER_ROUND x the per-run cap.
    # All sessions must be minted before the proxy starts.
    sessions: dict[tuple[str, int], object] = {}
    for entry_id in local_entries:
        for task_index in range(TASKS_PER_ROUND):
            sessions[(entry_id, task_index)] = proxy.mint_session(
                f"round={round_id}:{entry_id}:task={task_index}"
            )
    sidecar_containers = (proxy.name,)

    # Per-entry accumulators across the TASKS_PER_ROUND evaluations. Each task uses
    # a fresh chain-block-hash seed (the block advances between the multi-minute
    # evals), so every task is a distinct, unpredictable mission instance.
    task_scores: dict[str, list[float]] = {entry_id: [] for entry_id in local_entries}
    task_statuses: dict[str, list[str]] = {entry_id: [] for entry_id in local_entries}
    primary_keys: dict[str, dict[str, str]] = {}

    proxy.start()
    try:
        for task_index in range(TASKS_PER_ROUND):
            seed = _per_validator_seed(round_id, mission_id, validator_hotkey)
            agent_specs = [
                AgentSpec(
                    name=entry["spec_name"],
                    path=entry["agent_dir"],
                    env=sessions[(entry_id, task_index)].env,
                )
                for entry_id, entry in local_entries.items()
            ]
            log.info(
                "round=%s task=%s/%s: starting npabench mission=%s seed=%s entries=%s",
                round_id,
                task_index + 1,
                TASKS_PER_ROUND,
                mission_id,
                seed,
                len(agent_specs),
            )
            batch_report = evaluate_multiple_agents(
                agent_specs,
                mission_id=mission_id,
                seed=seed,
                output_dir=workspace / f"task_{task_index}",
                record=True,
                agent_mode=AgentMode.SANDBOXED,
                max_parallel=MAX_PARALLEL_AGENTS,
                sidecar_containers=sidecar_containers,
            )

            for entry_id, entry in local_entries.items():
                report = batch_report.agents[entry["spec_name"]]
                _write_proxy_usage(
                    report, proxy.read_usage(sessions[(entry_id, task_index)].session_id)
                )
                report_path = report.output_dir / "report.json"
                if not report_path.exists():
                    raise RuntimeError(
                        f"missing report.json for entry {entry_id} task {task_index}"
                    )
                recording_path = _ensure_recording_file(report)

                # Per-task artifacts get their own storage path via a task-suffixed
                # entry_id (the backend derives the key from entry_id, so this needs
                # no API change). The scoreboard row references task 0's keys.
                artifact_entry_id = f"{entry_id}__t{task_index}"
                report_slot = api.request_artifact_slot(
                    round_id=round_id,
                    validator_uid=validator_uid,
                    entry_id=artifact_entry_id,
                    entry_kind=entry["entry_kind"],
                    miner_uid=entry["miner_uid"],
                    miner_hotkey=entry["miner_hotkey"],
                    artifact_kind="report_json",
                )
                recording_slot = api.request_artifact_slot(
                    round_id=round_id,
                    validator_uid=validator_uid,
                    entry_id=artifact_entry_id,
                    entry_kind=entry["entry_kind"],
                    miner_uid=entry["miner_uid"],
                    miner_hotkey=entry["miner_hotkey"],
                    artifact_kind="recording_mcpr",
                )
                api.upload_bytes(report_slot["upload_url"], report_path.read_bytes())
                api.upload_bytes(recording_slot["upload_url"], recording_path.read_bytes())

                task_scores[entry_id].append(float(report.score))
                task_statuses[entry_id].append(report.status)
                if task_index == 0:
                    primary_keys[entry_id] = {
                        "report_s3_key": report_slot["storage_key"],
                        "recording_s3_key": recording_slot["storage_key"],
                    }
                log.info(
                    "round=%s task=%s entry=%s score=%s status=%s",
                    round_id,
                    task_index,
                    entry_id,
                    float(report.score),
                    report.status,
                )
    finally:
        proxy.stop()

    # Aggregate the per-task scores into one scoreboard row per entry. The mean
    # smooths per-seed luck; consensus then stake-averages these across validators
    # exactly as before (it still sees a single score per entry).
    rows: list[dict] = []
    for entry_id, entry in local_entries.items():
        scores = task_scores[entry_id]
        final_score = sum(scores) / len(scores) if scores else 0.0
        keys = primary_keys[entry_id]
        rows.append(
            {
                "entry_id": entry_id,
                "entry_kind": entry["entry_kind"],
                "miner_uid": entry["miner_uid"],
                "miner_hotkey": entry["miner_hotkey"],
                "submission_id": entry.get("submission_id"),
                "source_round_id": entry.get("source_round_id"),
                "score": final_score,
                "status": _aggregate_status(task_statuses[entry_id]),
                "report_s3_key": keys["report_s3_key"],
                "recording_s3_key": keys["recording_s3_key"],
            }
        )
        log.info(
            "round=%s entry=%s task_scores=%s final_score=%s",
            round_id,
            entry_id,
            scores,
            final_score,
        )

    log.info("round=%s: uploading scoreboard rows=%s", round_id, len(rows))
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
