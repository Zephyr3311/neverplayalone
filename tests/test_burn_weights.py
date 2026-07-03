from __future__ import annotations

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


def test_distinct_burn_uid_and_winner_at_95_percent():
    weights = compute_weight_vector(5, winner_uid=3, burn_rate=0.95, burn_uid=0)
    assert round(weights[0], 6) == 0.95
    assert round(weights[3], 6) == 0.05
    assert round(sum(weights), 6) == 1.0
