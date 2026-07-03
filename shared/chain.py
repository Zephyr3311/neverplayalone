"""Bittensor SDK helpers shared by miners and validators."""
from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any, Optional

NETUID = int(os.environ.get("NPA_NETUID", "98"))
NETWORK = os.environ.get("NPA_NETWORK", "finney")

log = logging.getLogger(__name__)

if TYPE_CHECKING:
    import bittensor as bt

_subtensor: Optional[Any] = None


def _bt():
    import bittensor as bt

    return bt


def _resolve_ctor(bt, *names: str):
    for name in names:
        ctor = getattr(bt, name, None)
        if ctor is not None:
            return ctor
    raise AttributeError(f"bittensor does not expose any of: {', '.join(names)}")


def get_subtensor() -> "bt.subtensor":
    global _subtensor
    if _subtensor is None:
        bt = _bt()
        subtensor_ctor = _resolve_ctor(bt, "subtensor", "Subtensor")
        _subtensor = subtensor_ctor(network=NETWORK)
    return _subtensor


def make_wallet(name: str = "default", hotkey: str = "default") -> "bt.wallet":
    bt = _bt()
    wallet_ctor = _resolve_ctor(bt, "wallet", "Wallet")
    wallet_path = os.environ.get("NPA_BT_WALLET_DIR")
    kwargs = {"name": name, "hotkey": hotkey}
    if wallet_path:
        kwargs["path"] = wallet_path
    return wallet_ctor(**kwargs)


def current_block() -> int:
    return get_subtensor().get_current_block()


def current_block_hash() -> str:
    substrate = getattr(get_subtensor(), "substrate", None)
    if substrate is None or not hasattr(substrate, "get_block_hash"):
        raise RuntimeError("subtensor substrate does not expose get_block_hash")
    return str(substrate.get_block_hash(current_block()))


def block_number_for_hash(block_hash: str) -> Optional[int]:
    substrate = getattr(get_subtensor(), "substrate", None)
    if substrate is None or not hasattr(substrate, "get_block_number"):
        return None
    try:
        return int(substrate.get_block_number(block_hash))
    except Exception as exc:
        log.warning("could not resolve block number for hash %s: %s", block_hash, exc)
        return None


def get_metagraph(block: Optional[int] = None):
    return get_subtensor().metagraph(NETUID, block=block)


def hotkey_uid(hotkey: str, block: Optional[int] = None) -> int:
    metagraph = get_metagraph(block=block)
    for uid, known_hotkey in enumerate(metagraph.hotkeys):
        if known_hotkey == hotkey:
            return uid
    raise ValueError(f"hotkey not found in metagraph: {hotkey}")


def stake_by_hotkey(block: Optional[int] = None) -> dict[str, float]:
    metagraph = get_metagraph(block=block)
    return {
        hotkey: float(metagraph.S[uid])
        for uid, hotkey in enumerate(metagraph.hotkeys)
    }


def stake_by_hotkey_for_block_hash(block_hash: str) -> dict[str, float]:
    block_number = block_number_for_hash(block_hash)
    if block_number is None:
        raise RuntimeError(f"could not resolve block number for hash {block_hash}")
    return stake_by_hotkey(block=block_number)


def self_stake_for_hotkey(hotkey: str, block_hash: str | None = None) -> float:
    try:
        stakes = stake_by_hotkey_for_block_hash(block_hash) if block_hash else stake_by_hotkey()
        return float(stakes.get(hotkey, 0.0))
    except Exception as exc:
        log.warning("stake lookup fallback for %s: %s", hotkey, exc)
        return float(stake_by_hotkey().get(hotkey, 0.0))


def compute_weight_vector(
    count: int,
    winner_uid: Optional[int],
    burn_rate: float = 0.0,
    burn_uid: int = 0,
) -> list[float]:
    weights = [0.0] * count
    if count == 0:
        return weights

    burn_rate = min(max(float(burn_rate), 0.0), 1.0)
    if burn_rate > 0.0 and not (0 <= burn_uid < count):
        log.warning(
            "burn_rate=%.4f requested but burn_uid=%s is out of range [0,%d); disabling burn",
            burn_rate,
            burn_uid,
            count,
        )
        burn_rate = 0.0

    if burn_rate > 0.0:
        weights[burn_uid] += burn_rate
    winner_share = 1.0 - burn_rate

    if winner_uid is not None and 0 <= winner_uid < count:
        weights[winner_uid] += winner_share
    elif burn_rate > 0.0:
        weights[burn_uid] += winner_share
    return weights


def set_winner_weights(
    wallet: "bt.wallet",
    winner_uid: Optional[int],
    burn_rate: float = 0.0,
    burn_uid: int = 0,
) -> None:
    metagraph = get_metagraph()
    count = len(metagraph.hotkeys)
    raw_uids = list(range(count))
    raw_weights = compute_weight_vector(count, winner_uid, burn_rate, burn_uid)

    try:
        from bittensor.utils.weight_utils import (
            convert_weights_and_uids_for_emit,
            process_weights_for_netuid,
        )

        emit_uids, emit_weights = convert_weights_and_uids_for_emit(
            *process_weights_for_netuid(
                uids=getattr(metagraph, "uids", raw_uids),
                weights=raw_weights,
                netuid=NETUID,
                subtensor=get_subtensor(),
                metagraph=metagraph,
            )
        )
    except Exception as exc:
        log.warning("weight preprocessing unavailable, falling back to raw weights: %s", exc)
        emit_uids, emit_weights = raw_uids, raw_weights

    get_subtensor().set_weights(
        wallet=wallet,
        netuid=NETUID,
        uids=emit_uids,
        weights=emit_weights,
        wait_for_inclusion=False,
        wait_for_finalization=False,
    )
