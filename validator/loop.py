"""Main validator loop for round-based backend coordination."""
from __future__ import annotations

import logging
import time
from typing import Optional

from shared import chain
from shared.api_client import APIClient
from validator.config import BURN_RATE, BURN_UID, LOOP_POLL_SECONDS
from validator.round_evaluation import run_round_evaluation

log = logging.getLogger(__name__)


def _weighted_entry_scores(scoreboards: list[dict], freeze_block_hash: str | None) -> dict[str, dict]:
    if not scoreboards:
        return {}

    try:
        stakes = (
            chain.stake_by_hotkey_for_block_hash(freeze_block_hash)
            if freeze_block_hash
            else chain.stake_by_hotkey()
        )
    except Exception as exc:
        log.warning("stake lookup by freeze block failed, using uploaded stake weights: %s", exc)
        stakes = {}

    weighted_totals: dict[str, float] = {}
    weight_totals: dict[str, float] = {}
    meta: dict[str, dict] = {}

    for scoreboard in scoreboards:
        validator_hotkey = scoreboard["validator_hotkey"]
        weight = float(stakes.get(validator_hotkey, scoreboard.get("stake_weight", 0.0)))
        if weight <= 0:
            continue
        for row in scoreboard["rows"]:
            entry_id = row.get("entry_id") or row["miner_hotkey"]
            weighted_totals[entry_id] = weighted_totals.get(entry_id, 0.0) + weight * float(row["score"])
            weight_totals[entry_id] = weight_totals.get(entry_id, 0.0) + weight
            if entry_id not in meta:
                meta[entry_id] = {
                    "entry_id": entry_id,
                    "entry_kind": row.get("entry_kind", "submission"),
                    "miner_uid": int(row["miner_uid"]),
                    "miner_hotkey": row["miner_hotkey"],
                    "submission_id": row.get("submission_id"),
                    "source_round_id": row.get("source_round_id"),
                }

    entries: dict[str, dict] = {}
    for entry_id, total_weight in weight_totals.items():
        if total_weight <= 0:
            continue
        entries[entry_id] = {**meta[entry_id], "score": weighted_totals[entry_id] / total_weight}
    return entries


def _select_winner(entries: dict[str, dict], margin: float) -> tuple[Optional[dict], bool]:
    if not entries:
        return None, False
    ranked = sorted(
        entries.values(),
        key=lambda e: (-e["score"], e["miner_uid"], e["entry_id"]),
    )
    champion = next((e for e in ranked if e["entry_kind"] == "champion_defense"), None)
    if champion is None:
        return ranked[0], False
    challengers = [e for e in ranked if e["entry_kind"] != "champion_defense"]
    best_challenger = challengers[0] if challengers else None
    if best_challenger is None:
        return champion, True
    if best_challenger["score"] > champion["score"] + margin:
        return best_challenger, False
    return champion, True


def _round_margin(api: APIClient, round_id: int) -> float:
    try:
        roster = api.get_round_roster(round_id)
        value = roster.get("champion_margin")
        if value is not None:
            return float(value)
    except Exception as exc:
        log.warning("round=%s: could not read champion margin from roster: %s", round_id, exc)
    return 0.0


def _process_consensus(wallet, api: APIClient, round_state: dict) -> Optional[tuple[int, str]]:
    round_id = int(round_state["round_id"])
    scoreboards = api.list_round_scoreboards(round_id)
    entries = _weighted_entry_scores(scoreboards, round_state.get("freeze_block_hash"))
    if not entries:
        log.info("round=%s: no valid scoreboards yet for consensus", round_id)
        return None

    winner, champion_kept = _select_winner(entries, _round_margin(api, round_id))
    if winner is None:
        return None

    winner_uid = int(winner["miner_uid"])
    winner_hotkey = winner["miner_hotkey"]
    validator_uid = chain.hotkey_uid(wallet.hotkey.ss58_address)
    api.upload_consensus_result(
        round_id=round_id,
        validator_uid=validator_uid,
        top_miner_uid=winner_uid,
        top_miner_hotkey=winner_hotkey,
        winner_entry_id=winner["entry_id"],
        winner_entry_kind=winner["entry_kind"],
        source_submission_id=winner.get("submission_id"),
        source_round_id=winner.get("source_round_id"),
        champion_kept=champion_kept,
    )
    chain.set_winner_weights(wallet, winner_uid, burn_rate=BURN_RATE, burn_uid=BURN_UID)
    log.info(
        "round=%s consensus winner uid=%s hotkey=%s entry=%s champion_kept=%s burn_rate=%.4f burn_uid=%s",
        round_id,
        winner_uid,
        winner_hotkey,
        winner["entry_id"],
        champion_kept,
        BURN_RATE,
        BURN_UID,
    )
    return winner_uid, winner_hotkey


def main_loop(wallet, api: APIClient) -> None:
    evaluated_rounds: set[int] = set()
    consensus_rounds: set[int] = set()
    log.info("loop started")

    while True:
        try:
            rounds = api.get_current_rounds()
        except Exception as exc:
            log.warning("get_current_rounds failed: %s", exc)
            time.sleep(LOOP_POLL_SECONDS)
            continue

        evaluating_round = rounds.get("evaluating_round")
        if evaluating_round:
            round_id = int(evaluating_round["round_id"])
            if round_id not in evaluated_rounds:
                try:
                    run_round_evaluation(wallet, api, evaluating_round)
                    evaluated_rounds.add(round_id)
                    log.info("round=%s evaluation uploaded", round_id)
                except Exception as exc:
                    log.exception("round=%s evaluation failed: %s", round_id, exc)

            if time.time() >= float(evaluating_round["scoreboard_deadline_at"]) and round_id not in consensus_rounds:
                try:
                    winner = _process_consensus(wallet, api, evaluating_round)
                    if winner is not None:
                        consensus_rounds.add(round_id)
                except Exception as exc:
                    log.exception("round=%s consensus failed and will retry: %s", round_id, exc)

        time.sleep(LOOP_POLL_SECONDS)
