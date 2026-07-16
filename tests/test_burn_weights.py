from __future__ import annotations

import threading
from types import SimpleNamespace

from shared import chain
from shared.chain import compute_weight_vector


def _as_map(weights: list[float]) -> dict[int, float]:
    return {uid: w for uid, w in enumerate(weights) if w}


def test_no_burn_is_winner_take_all():
    weights = compute_weight_vector(4, winner_uid=2, burn_rate=0.0, burn_uid=0)
    assert _as_map(weights) == {2: 1.0}
    assert sum(weights) == 1.0


def test_burn_splits_between_burn_uid_and_winner():
    weights = compute_weight_vector(4, winner_uid=2, burn_rate=0.9, burn_uid=0)
    assert weights[0] == 0.9
    assert round(weights[2], 6) == 0.1
    assert round(sum(weights), 6) == 1.0


def test_winner_equal_to_burn_uid_combines_to_full_weight():
    weights = compute_weight_vector(4, winner_uid=0, burn_rate=0.9, burn_uid=0)
    assert round(weights[0], 6) == 1.0
    assert round(sum(weights), 6) == 1.0


def test_no_winner_burns_the_full_vector():
    weights = compute_weight_vector(4, winner_uid=None, burn_rate=0.9, burn_uid=0)
    assert round(weights[0], 6) == 1.0
    assert round(sum(weights), 6) == 1.0


def test_no_winner_and_no_burn_emits_nothing():
    weights = compute_weight_vector(4, winner_uid=None, burn_rate=0.0, burn_uid=0)
    assert sum(weights) == 0.0


def test_burn_rate_is_clamped_to_one():
    weights = compute_weight_vector(4, winner_uid=2, burn_rate=1.5, burn_uid=0)
    assert weights[0] == 1.0
    assert weights[2] == 0.0


def test_negative_burn_rate_is_clamped_to_zero():
    weights = compute_weight_vector(4, winner_uid=2, burn_rate=-0.5, burn_uid=0)
    assert _as_map(weights) == {2: 1.0}


def test_out_of_range_burn_uid_disables_burn():
    weights = compute_weight_vector(4, winner_uid=2, burn_rate=0.9, burn_uid=99)
    assert _as_map(weights) == {2: 1.0}


def test_weight_rpc_does_not_block_concurrent_chain_reads(monkeypatch):
    weight_started = threading.Event()
    release_weight_rpc = threading.Event()
    block_read_finished = threading.Event()
    clients = []

    class FakeSubtensor:
        def __init__(self, *, blocks_weights: bool):
            self.blocks_weights = blocks_weights
            self.closed = False

        def metagraph(self, netuid, block=None):
            del netuid, block
            return SimpleNamespace(hotkeys=["winner"])

        def set_weights(self, **kwargs):
            del kwargs
            if self.blocks_weights:
                weight_started.set()
                assert release_weight_rpc.wait(timeout=1)

        def get_current_block(self):
            return 123

        def close(self):
            self.closed = True

    def subtensor_ctor(*, network):
        del network
        client = FakeSubtensor(blocks_weights=not clients)
        clients.append(client)
        return client

    chain.close_subtensor()
    monkeypatch.setattr(chain, "_bt", lambda: SimpleNamespace(subtensor=subtensor_ctor))

    weight_thread = threading.Thread(target=chain.set_winner_weights, args=(object(), 0))
    weight_thread.start()
    assert weight_started.wait(timeout=1)

    def read_current_block():
        assert chain.current_block() == 123
        block_read_finished.set()

    read_thread = threading.Thread(target=read_current_block)
    read_thread.start()
    assert block_read_finished.wait(timeout=0.1)
    assert len(clients) == 2

    release_weight_rpc.set()
    weight_thread.join(timeout=1)
    read_thread.join(timeout=1)
    assert not weight_thread.is_alive()
    assert not read_thread.is_alive()
    assert block_read_finished.is_set()


def test_close_subtensor_closes_only_the_current_thread_client(monkeypatch):
    clients = []

    class FakeSubtensor:
        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

    def subtensor_ctor(*, network):
        del network
        client = FakeSubtensor()
        clients.append(client)
        return client

    chain.close_subtensor()
    monkeypatch.setattr(chain, "_bt", lambda: SimpleNamespace(subtensor=subtensor_ctor))

    first = chain.get_subtensor()
    chain.close_subtensor()
    second = chain.get_subtensor()
    chain.close_subtensor()

    assert first is clients[0]
    assert first.closed is True
    assert second is clients[1]
    assert second.closed is True


def test_distinct_burn_uid_and_winner_at_95_percent():
    weights = compute_weight_vector(5, winner_uid=3, burn_rate=0.95, burn_uid=0)
    assert round(weights[0], 6) == 0.95
    assert round(weights[3], 6) == 0.05
    assert round(sum(weights), 6) == 1.0
