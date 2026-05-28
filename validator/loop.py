"""Main validator loop.

On every detected epoch boundary:
  1. Settle previous epoch — aggregate on-chain score commits with stake weighting,
     decide if the challenger dethroned the king (delta gate), set Yuma weights.
  2. If running as owner — sync new miner commitments to API queue and advance.
  3. Read the current (king, challenger) pair from the API.
  4. Pick K random tasks from mcbench/tasks/, run king + challenger N trials each.
  5. Submit telemetry to API and commit scores on-chain.
"""
from __future__ import annotations

import logging
import time
from collections import Counter
from typing import Optional

from . import chain
from .api_client import APIClient
from .config import DETHRONE_DELTA, LOOP_POLL_SECONDS, OWNER_HOTKEY
from .duel import evaluate_participant, pick_random_tasks
from .owner import owner_epoch_tick

log = logging.getLogger(__name__)


def aggregate_verdict(
    commits: dict[str, dict],
    stakes: dict[str, float],
    king_uid: int,
    challenger_uid: int,
    delta: float,
) -> tuple[int, float, float]:
    """Stake-weighted aggregate of validator score commits.

    Returns (winner_uid, weighted_king_score, weighted_challenger_score).
    On no valid commits, king holds and scores are 0.0.
    """
    valid = [
        (h, c)
        for h, c in commits.items()
        if c.get("k") == king_uid and c.get("c") == challenger_uid
    ]
    if not valid:
        return king_uid, 0.0, 0.0

    total_stake = sum(stakes.get(h, 0.0) for h, _ in valid)
    if total_stake <= 0:
        return king_uid, 0.0, 0.0

    w_king = sum(stakes.get(h, 0.0) * c["ks"] for h, c in valid) / total_stake
    w_chal = sum(stakes.get(h, 0.0) * c["cs"] for h, c in valid) / total_stake

    new_king = challenger_uid if w_chal >= w_king + delta else king_uid
    return new_king, w_king, w_chal


def _majority_pair(score_commits: dict[str, dict]) -> Optional[tuple[int, int]]:
    """Most-committed (king_uid, challenger_uid) pair across validators."""
    pairs = Counter((c["k"], c["c"]) for c in score_commits.values())
    if not pairs:
        return None
    (pair, _), = pairs.most_common(1)
    return pair


def settle_previous_epoch(wallet, previous_epoch: int) -> Optional[int]:
    """Read commits from `previous_epoch`, aggregate, set weights. Returns new-king uid."""
    raw_commits = chain.get_all_commitments()

    # Parse and filter to score commits from the target epoch only.
    epoch_commits: dict[str, dict] = {}
    for hotkey, raw in raw_commits.items():
        parsed = chain.parse_score_commit(raw)
        if parsed is not None and parsed.get("e") == previous_epoch:
            epoch_commits[hotkey] = parsed

    if not epoch_commits:
        log.info("epoch=%s: no score commits → king holds", previous_epoch)
        return None

    pair = _majority_pair(epoch_commits)
    if pair is None:
        return None
    king_uid, challenger_uid = pair

    mg = chain.get_metagraph()
    stakes = {hotkey: float(mg.S[uid]) for uid, hotkey in enumerate(mg.hotkeys)}

    new_king_uid, w_king, w_chal = aggregate_verdict(
        epoch_commits, stakes, king_uid, challenger_uid, DETHRONE_DELTA
    )
    log.info(
        "epoch=%s verdict: pair=(king=%s, chal=%s) scores=(%.3f vs %.3f) winner_uid=%s",
        previous_epoch, king_uid, challenger_uid, w_king, w_chal, new_king_uid,
    )

    try:
        chain.set_winner_weights(wallet, new_king_uid)
    except Exception as e:
        log.warning("set_weights failed: %s", e)

    return new_king_uid


def run_duel_for_epoch(wallet, api: APIClient, epoch_id: int) -> None:
    """Read pair from API, run duel, submit telemetry, commit scores on-chain."""
    try:
        duel = api.get_current_duel()
    except Exception as e:
        log.warning("could not fetch /duel/current: %s", e)
        return

    if duel.get("epoch_id") != epoch_id:
        log.warning(
            "API epoch=%s vs local=%s — running against API's pair anyway",
            duel.get("epoch_id"), epoch_id,
        )

    king = duel.get("king")
    challenger = duel.get("challenger")
    if not challenger:
        log.info("epoch=%s: no challenger this epoch", epoch_id)
        return

    tasks = pick_random_tasks()
    log.info("epoch=%s tasks: %s", epoch_id, [t.name for t in tasks])

    king_score = 0.0
    if king:
        log.info("evaluating king uid=%s %s@%s", king["uid"], king["repo"], king["sha"][:12])
        king_score = evaluate_participant(king["repo"], king["sha"], tasks)

    log.info(
        "evaluating challenger uid=%s %s@%s",
        challenger["uid"], challenger["repo"], challenger["sha"][:12],
    )
    challenger_score = evaluate_participant(challenger["repo"], challenger["sha"], tasks)

    log.info(
        "epoch=%s results: king_score=%.3f challenger_score=%.3f",
        epoch_id, king_score, challenger_score,
    )

    # Telemetry to API (non-authoritative).
    try:
        api.submit_result(
            epoch_id=epoch_id,
            king_hotkey=king["hotkey"] if king else None,
            king_score=king_score,
            challenger_hotkey=challenger["hotkey"],
            challenger_score=challenger_score,
        )
    except Exception as e:
        log.warning("submit_result failed: %s", e)

    # Authoritative: commit scores on-chain for next-epoch consensus.
    try:
        chain.commit_score(
            wallet=wallet,
            epoch_id=epoch_id,
            king_uid=king["uid"] if king else -1,
            king_score=king_score,
            challenger_uid=challenger["uid"],
            challenger_score=challenger_score,
        )
    except Exception as e:
        log.warning("commit_score failed: %s", e)


def main_loop(wallet, api: APIClient) -> None:
    is_owner = wallet.hotkey.ss58_address == OWNER_HOTKEY
    last_handled_epoch: Optional[int] = None
    log.info("loop started (owner=%s)", is_owner)

    while True:
        try:
            current = chain.current_epoch()
        except Exception as e:
            log.warning("current_epoch failed: %s", e)
            time.sleep(LOOP_POLL_SECONDS)
            continue

        if current == last_handled_epoch:
            time.sleep(LOOP_POLL_SECONDS)
            continue

        log.info("new epoch detected: %s", current)

        # 1. Settle previous epoch and set weights.
        if last_handled_epoch is not None:
            try:
                settle_previous_epoch(wallet, last_handled_epoch)
            except Exception as e:
                log.warning("settle_previous_epoch failed: %s", e)

        # 2. Owner: sync queue + advance API to next pair.
        if is_owner:
            try:
                owner_epoch_tick(api, current)
            except Exception as e:
                log.warning("owner_epoch_tick failed: %s", e)

        # 3. Run duel for current epoch (and commit scores).
        try:
            run_duel_for_epoch(wallet, api, current)
        except Exception as e:
            log.warning("run_duel_for_epoch failed: %s", e)

        last_handled_epoch = current
