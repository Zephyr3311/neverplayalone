"""Main validator loop for round-based backend coordination."""
from __future__ import annotations

import logging
import time
from typing import Optional

from . import chain
from .api_client import APIClient
from .config import LOOP_POLL_SECONDS
from .duel import run_round_evaluation

log = logging.getLogger(__name__)


def _weighted_top_miner(scoreboards: list[dict], freeze_block_hash: str | None) -> tuple[int, str] | None:
    if not scoreboards:
        return None

    try:
        stakes = (
            chain.stake_by_hotkey_for_block_hash(freeze_block_hash)
            if freeze_block_hash
            else chain.stake_by_hotkey()
        )
    except Exception as exc:
        log.warning("stake lookup by freeze block failed, using uploaded stake weights: %s", exc)
        stakes = {}

    weighted_totals: dict[tuple[int, str], float] = {}
    weight_totals: dict[tuple[int, str], float] = {}

    for scoreboard in scoreboards:
        validator_hotkey = scoreboard["validator_hotkey"]
        weight = float(stakes.get(validator_hotkey, scoreboard.get("stake_weight", 0.0)))
        if weight <= 0:
            continue
        for row in scoreboard["rows"]:
            key = (int(row["miner_uid"]), row["miner_hotkey"])
            weighted_totals[key] = weighted_totals.get(key, 0.0) + weight * float(row["score"])
            weight_totals[key] = weight_totals.get(key, 0.0) + weight

    if not weighted_totals:
        return None

    ranking = sorted(
        (
            (uid, hotkey, weighted_totals[(uid, hotkey)] / weight_totals[(uid, hotkey)])
            for uid, hotkey in weighted_totals
            if weight_totals[(uid, hotkey)] > 0
        ),
        key=lambda item: (-item[2], item[0], item[1]),
    )
    winner_uid, winner_hotkey, _ = ranking[0]
    return winner_uid, winner_hotkey


def _process_consensus(wallet, api: APIClient, round_state: dict) -> Optional[tuple[int, str]]:
    scoreboards = api.list_round_scoreboards(round_state["round_id"])
    winner = _weighted_top_miner(scoreboards, round_state.get("freeze_block_hash"))
    if winner is None:
        log.info("round=%s: no valid scoreboards yet for consensus", round_state["round_id"])
        return None

    winner_uid, winner_hotkey = winner
    validator_uid = chain.hotkey_uid(wallet.hotkey.ss58_address)
    chain.set_winner_weights(wallet, winner_uid)
    api.upload_consensus_result(
        round_id=round_state["round_id"],
        validator_uid=validator_uid,
        top_miner_uid=winner_uid,
        top_miner_hotkey=winner_hotkey,
    )
    log.info(
        "round=%s consensus winner uid=%s hotkey=%s",
        round_state["round_id"],
        winner_uid,
        winner_hotkey,
    )
    return winner


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
                    log.warning("round=%s evaluation failed: %s", round_id, exc)

            if time.time() >= float(evaluating_round["scoreboard_deadline_at"]) and round_id not in consensus_rounds:
                try:
                    winner = _process_consensus(wallet, api, evaluating_round)
                    if winner is not None:
                        consensus_rounds.add(round_id)
                except Exception as exc:
                    log.warning("round=%s consensus failed: %s", round_id, exc)

        time.sleep(LOOP_POLL_SECONDS)
