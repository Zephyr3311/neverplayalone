"""Main validator loop for round-based backend coordination."""
from __future__ import annotations

import logging
import time
from typing import Optional

from shared import chain
from shared.api_client import APIClient
from validator.config import (
    BURN_RATE,
    BURN_UID,
    EVALUATION_START_CUTOFF_RATIO,
    LOOP_POLL_SECONDS,
    WEIGHT_EPOCH_BLOCKS,
)
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


def _round_margin(api: APIClient, round_id: str) -> float:
    try:
        roster = api.get_round_roster(round_id)
        value = roster.get("champion_margin")
        if value is not None:
            return float(value)
    except Exception as exc:
        log.warning("round=%s: could not read champion margin from roster: %s", round_id, exc)
    return 0.0


def _process_consensus(
    wallet,
    api: APIClient,
    round_state: dict,
    *,
    set_weights: bool = True,
) -> Optional[tuple[int, str]]:
    round_id = round_state["round_id"]  # date-based string id, e.g. "2026-07-06-AM"
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
    if set_weights:
        chain.set_winner_weights(wallet, winner_uid, burn_rate=BURN_RATE, burn_uid=BURN_UID)
    log.info(
        (
            "round=%s consensus winner uid=%s hotkey=%s entry=%s champion_kept=%s "
            "weights_set=%s burn_rate=%.4f burn_uid=%s"
        ),
        round_id,
        winner_uid,
        winner_hotkey,
        winner["entry_id"],
        champion_kept,
        set_weights,
        BURN_RATE,
        BURN_UID,
    )
    return winner_uid, winner_hotkey


def _round_blocks(round_state: dict) -> tuple[int, int, int]:
    return (
        int(round_state["evaluation_start_block"]),
        int(round_state["scoreboard_deadline_block"]),
        int(round_state["round_end_block"]),
    )


def _evaluation_cutoff_block(round_state: dict) -> int:
    start_block, _, end_block = _round_blocks(round_state)
    ratio = min(max(EVALUATION_START_CUTOFF_RATIO, 0.0), 1.0)
    return start_block + int((end_block - start_block) * ratio)


def _weight_epoch_index(round_state: dict, current_block: int) -> int | None:
    if WEIGHT_EPOCH_BLOCKS <= 0:
        return None
    start_block, _, end_block = _round_blocks(round_state)
    if current_block < start_block or current_block >= end_block:
        return None
    return (current_block - start_block) // WEIGHT_EPOCH_BLOCKS


def _epoch_end_block(round_state: dict, epoch_index: int) -> int:
    start_block, _, _ = _round_blocks(round_state)
    return start_block + (epoch_index + 1) * WEIGHT_EPOCH_BLOCKS


def _previous_winner_from_roster(api: APIClient, round_id: str) -> Optional[tuple[int, str]]:
    roster = api.get_round_roster(round_id)
    for entry in roster.get("entries", []):
        if entry.get("entry_kind") == "champion_defense":
            return int(entry["miner_uid"]), entry["miner_hotkey"]
    return None


def _set_round_weights(
    wallet,
    *,
    round_id: str,
    winner: tuple[int, str],
    source: str,
    epoch_index: int | None = None,
) -> None:
    winner_uid, winner_hotkey = winner
    chain.set_winner_weights(wallet, winner_uid, burn_rate=BURN_RATE, burn_uid=BURN_UID)
    log.info(
        "round=%s weights set source=%s epoch=%s winner_uid=%s winner_hotkey=%s burn_rate=%.4f burn_uid=%s",
        round_id,
        source,
        epoch_index,
        winner_uid,
        winner_hotkey,
        BURN_RATE,
        BURN_UID,
    )


def main_loop(wallet, api: APIClient) -> None:
    evaluated_rounds: set[str] = set()
    skipped_evaluation_rounds: set[str] = set()
    consensus_rounds: set[str] = set()
    round_winners: dict[str, tuple[int, str]] = {}
    weight_epochs: set[tuple[str, int]] = set()
    log.info("loop started")

    while True:
        current_block = chain.current_block()
        try:
            rounds = api.get_current_rounds()
        except Exception as exc:
            log.warning("get_current_rounds failed: %s", exc)
            time.sleep(LOOP_POLL_SECONDS)
            continue

        evaluating_round = rounds.get("evaluating_round")
        submission_round = rounds.get("submission_round")
        log.info(
            "round_state submission=%s evaluating=%s",
            submission_round["round_id"] if submission_round else None,
            evaluating_round["round_id"] if evaluating_round else None,
        )
        if evaluating_round:
            round_id = evaluating_round["round_id"]
            start_block, deadline_block, end_block = _round_blocks(evaluating_round)
            cutoff_block = _evaluation_cutoff_block(evaluating_round)
            if round_id not in evaluated_rounds and round_id not in skipped_evaluation_rounds:
                try:
                    if current_block < cutoff_block and current_block < deadline_block:
                        log.info(
                            (
                                "round=%s: starting evaluation current_block=%s "
                                "start_block=%s cutoff_block=%s deadline_block=%s"
                            ),
                            round_id,
                            current_block,
                            start_block,
                            cutoff_block,
                            deadline_block,
                        )
                        run_round_evaluation(wallet, api, evaluating_round)
                        evaluated_rounds.add(round_id)
                        log.info("round=%s evaluation uploaded", round_id)
                    else:
                        skipped_evaluation_rounds.add(round_id)
                        log.info(
                            (
                                "round=%s: skipping evaluation because validator started late "
                                "current_block=%s cutoff_block=%s deadline_block=%s end_block=%s"
                            ),
                            round_id,
                            current_block,
                            cutoff_block,
                            deadline_block,
                            end_block,
                        )
                except Exception as exc:
                    log.exception("round=%s evaluation failed: %s", round_id, exc)

            current_block = chain.current_block()
            if current_block >= deadline_block and round_id not in consensus_rounds:
                try:
                    log.info(
                        "round=%s: starting consensus current_block=%s deadline_block=%s",
                        round_id,
                        current_block,
                        deadline_block,
                    )
                    winner = _process_consensus(wallet, api, evaluating_round, set_weights=False)
                    if winner is not None:
                        consensus_rounds.add(round_id)
                        round_winners[round_id] = winner
                        epoch_index = _weight_epoch_index(evaluating_round, current_block)
                        _set_round_weights(
                            wallet,
                            round_id=round_id,
                            winner=winner,
                            source="current_consensus",
                            epoch_index=epoch_index,
                        )
                        if epoch_index is not None:
                            weight_epochs.add((round_id, epoch_index))
                except Exception as exc:
                    log.exception("round=%s consensus failed and will retry: %s", round_id, exc)

            epoch_index = _weight_epoch_index(evaluating_round, current_block)
            if epoch_index is not None and (round_id, epoch_index) not in weight_epochs:
                try:
                    if current_block < deadline_block:
                        if _epoch_end_block(evaluating_round, epoch_index) > deadline_block:
                            log.info(
                                (
                                    "round=%s: reserving epoch=%s for post-deadline consensus "
                                    "current_block=%s deadline_block=%s"
                                ),
                                round_id,
                                epoch_index,
                                current_block,
                                deadline_block,
                            )
                        else:
                            previous_winner = _previous_winner_from_roster(api, round_id)
                            if previous_winner is None:
                                log.info(
                                    "round=%s: no previous champion for epoch weight update epoch=%s",
                                    round_id,
                                    epoch_index,
                                )
                            else:
                                _set_round_weights(
                                    wallet,
                                    round_id=round_id,
                                    winner=previous_winner,
                                    source="previous_consensus",
                                    epoch_index=epoch_index,
                                )
                                weight_epochs.add((round_id, epoch_index))
                    else:
                        winner = round_winners.get(round_id)
                        if winner is not None:
                            _set_round_weights(
                                wallet,
                                round_id=round_id,
                                winner=winner,
                                source="current_consensus",
                                epoch_index=epoch_index,
                            )
                            weight_epochs.add((round_id, epoch_index))
                except Exception as exc:
                    log.exception(
                        "round=%s epoch weight update failed epoch=%s: %s",
                        round_id,
                        epoch_index,
                        exc,
                    )

        time.sleep(LOOP_POLL_SECONDS)
