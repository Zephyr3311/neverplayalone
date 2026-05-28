"""Bittensor SDK wrappers — subtensor, metagraph, commitments, weights.

Score commitments use a compact JSON payload:
    {"v":1,"e":<epoch>,"k":<king_uid>,"ks":<king_score>,"c":<challenger_uid>,"cs":<challenger_score>}

Miner commitments use the literal string "owner/repo@sha".
"""
from __future__ import annotations

import json
import logging
from typing import Optional

import bittensor as bt

from .config import COMMIT_VERSION, NETUID, NETWORK

log = logging.getLogger(__name__)

_subtensor: Optional["bt.subtensor"] = None


def get_subtensor() -> "bt.subtensor":
    global _subtensor
    if _subtensor is None:
        _subtensor = bt.subtensor(network=NETWORK)
    return _subtensor


def make_wallet(name: str = "default", hotkey: str = "default") -> "bt.wallet":
    return bt.wallet(name=name, hotkey=hotkey)


def current_block() -> int:
    return get_subtensor().get_current_block()


def get_tempo() -> int:
    # tempo() returns the epoch length in blocks for this subnet.
    return int(get_subtensor().tempo(NETUID))


def current_epoch() -> int:
    return current_block() // max(get_tempo(), 1)


def epoch_start_block(epoch_id: int) -> int:
    return epoch_id * get_tempo()


def get_metagraph(block: Optional[int] = None):
    return get_subtensor().metagraph(NETUID, block=block)


def commit_miner_code(wallet: "bt.wallet", repo: str, sha: str) -> None:
    """Miner publishes `owner/repo@sha` on-chain."""
    payload = f"{repo}@{sha}"
    get_subtensor().set_commitment(wallet=wallet, netuid=NETUID, data=payload)


def commit_score(
    wallet: "bt.wallet",
    epoch_id: int,
    king_uid: int,
    king_score: float,
    challenger_uid: int,
    challenger_score: float,
) -> None:
    """Validator publishes a stake-aggregable score record for this epoch."""
    payload = {
        "v": COMMIT_VERSION,
        "e": int(epoch_id),
        "k": int(king_uid),
        "ks": round(float(king_score), 4),
        "c": int(challenger_uid),
        "cs": round(float(challenger_score), 4),
    }
    data = json.dumps(payload, separators=(",", ":"))
    get_subtensor().set_commitment(wallet=wallet, netuid=NETUID, data=data)


def get_all_commitments(block: Optional[int] = None) -> dict[str, str]:
    """Return {hotkey: raw commitment string} for every UID with one set."""
    subtensor = get_subtensor()
    mg = subtensor.metagraph(NETUID, block=block)
    out: dict[str, str] = {}
    for uid, hotkey in enumerate(mg.hotkeys):
        try:
            commit = subtensor.get_commitment(NETUID, uid, block=block)
        except Exception as e:
            log.debug("get_commitment failed for uid=%s: %s", uid, e)
            continue
        if commit:
            out[hotkey] = commit
    return out


def parse_score_commit(raw: str) -> Optional[dict]:
    """Decode a validator's score commitment. Returns None on malformed input."""
    if not raw or not raw.startswith("{"):
        return None
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None
    required = {"v", "e", "k", "ks", "c", "cs"}
    if not required.issubset(data.keys()):
        return None
    return data


def parse_miner_commit(raw: str) -> Optional[tuple[str, str]]:
    """Decode a miner's `owner/repo@sha` commitment."""
    if not raw or "@" not in raw or raw.startswith("{"):
        return None
    repo, sha = raw.rsplit("@", 1)
    if "/" not in repo or not sha:
        return None
    return repo, sha


def set_winner_weights(wallet: "bt.wallet", winner_uid: Optional[int]) -> None:
    """Winner-take-all weight vector. If winner_uid is None, set all zeros."""
    mg = get_metagraph()
    n = len(mg.hotkeys)
    uids = list(range(n))
    weights = [0.0] * n
    if winner_uid is not None and 0 <= winner_uid < n:
        weights[winner_uid] = 1.0
    get_subtensor().set_weights(
        wallet=wallet,
        netuid=NETUID,
        uids=uids,
        weights=weights,
        wait_for_inclusion=False,
    )
