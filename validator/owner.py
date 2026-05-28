"""Owner-validator extension: sync miner commitments to API queue, advance API.

The owner is the only validator that mutates API queue state. It does NOT
have any special authority over duel outcomes — those are decided by Yuma
consensus on validator-committed scores.
"""
from __future__ import annotations

import logging
from typing import Optional

from . import chain
from .api_client import APIClient

log = logging.getLogger(__name__)

# uid -> last-seen raw miner commitment ("owner/repo@sha"). In-memory only; on restart
# we re-enqueue everything once, which is harmless (enqueue dedupes by hotkey).
_seen_miner_commits: dict[int, str] = {}


def sync_miner_commitments(api: APIClient) -> None:
    """Read all on-chain miner commitments. Enqueue any new/changed ones."""
    mg = chain.get_metagraph()
    subtensor = chain.get_subtensor()

    for uid, hotkey in enumerate(mg.hotkeys):
        try:
            raw = subtensor.get_commitment(chain.NETUID, uid)
        except Exception as e:
            log.debug("get_commitment uid=%s: %s", uid, e)
            continue
        if not raw:
            continue
        parsed = chain.parse_miner_commit(raw)
        if not parsed:
            continue
        if _seen_miner_commits.get(uid) == raw:
            continue
        repo, sha = parsed
        try:
            api.enqueue(uid=uid, hotkey=hotkey, repo=repo, sha=sha)
            _seen_miner_commits[uid] = raw
            log.info("enqueued uid=%s %s@%s", uid, repo, sha[:12])
        except Exception as e:
            log.warning("enqueue failed for uid=%s: %s", uid, e)


def derive_new_king_from_chain() -> Optional[str]:
    """Owner peeks at on-chain incentive after Yuma to know who other validators crowned."""
    mg = chain.get_metagraph()
    incentives = list(mg.I)
    if not incentives or max(incentives) <= 0:
        return None
    max_uid = max(range(len(incentives)), key=lambda i: incentives[i])
    return mg.hotkeys[max_uid]


def advance_api(api: APIClient, epoch_id: int) -> None:
    """Push API state forward: maybe crown new king, pop next challenger."""
    new_king = derive_new_king_from_chain()
    try:
        api.advance(epoch_id=epoch_id, new_king_hotkey=new_king)
        log.info("advanced API to epoch %s (new_king_hotkey=%s)", epoch_id, new_king)
    except Exception as e:
        log.warning("advance failed: %s", e)


def owner_epoch_tick(api: APIClient, epoch_id: int) -> None:
    """One-shot per epoch: sync queue, then advance."""
    sync_miner_commitments(api)
    advance_api(api, epoch_id)
