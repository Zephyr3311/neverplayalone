from __future__ import annotations

import validator.loop as loop
from validator.loop import (
    _epoch_end_block,
    _evaluation_cutoff_block,
    _previous_round_replacement,
    _process_consensus,
    _select_winner,
    _weight_epoch_index,
    _weighted_entry_scores,
)


def _entry(entry_id: str, kind: str, uid: int, score: float, hotkey: str | None = None) -> dict:
    return {
        "entry_id": entry_id,
        "entry_kind": kind,
        "miner_uid": uid,
        "miner_hotkey": hotkey or f"hk{uid}",
        "submission_id": entry_id,
        "source_round_id": 1,
        "score": score,
    }


def _round_state(start=1000, deadline=2500, end=2800):
    return {
        "round_id": "2026-07-06-AM",
        "evaluation_start_block": start,
        "scoreboard_deadline_block": deadline,
        "round_end_block": end,
    }


def test_evaluation_cutoff_defaults_to_half_round():
    assert _evaluation_cutoff_block(_round_state(start=1000, end=2800)) == 1900


def test_weight_epoch_index_is_round_relative():
    state = _round_state(start=1000, end=2800)
    assert _weight_epoch_index(state, 999) is None
    assert _weight_epoch_index(state, 1000) == 0
    assert _weight_epoch_index(state, 1359) == 0
    assert _weight_epoch_index(state, 1360) == 1
    assert _weight_epoch_index(state, 2800) is None


def test_deadline_containing_epoch_can_be_reserved_for_current_consensus():
    state = _round_state(start=1000, deadline=2500, end=2800)
    epoch = _weight_epoch_index(state, 2440)
    assert epoch == 4
    assert _epoch_end_block(state, epoch) > state["scoreboard_deadline_block"]


def test_champion_kept_within_margin():
    entries = {
        "champ": _entry("champ", "champion_defense", 1, 10.0),
        "chal": _entry("chal", "submission", 2, 12.0),
    }
    winner, kept = _select_winner(entries, margin=5.0)
    assert kept is True
    assert winner["entry_id"] == "champ"


def test_challenger_wins_beyond_margin():
    entries = {
        "champ": _entry("champ", "champion_defense", 1, 10.0),
        "chal": _entry("chal", "submission", 2, 16.0),
    }
    winner, kept = _select_winner(entries, margin=5.0)
    assert kept is False
    assert winner["entry_id"] == "chal"


def test_no_champion_falls_back_to_top_score():
    entries = {
        "a": _entry("a", "submission", 1, 5.0),
        "b": _entry("b", "submission", 2, 9.0),
    }
    winner, kept = _select_winner(entries, margin=5.0)
    assert kept is False
    assert winner["entry_id"] == "b"


def test_champion_only_keeps_title():
    entries = {"champ": _entry("champ", "champion_defense", 1, 3.0)}
    winner, kept = _select_winner(entries, margin=0.0)
    assert kept is True
    assert winner["entry_id"] == "champ"


def test_champion_and_same_hotkey_resubmission_ranked_separately(monkeypatch):
    monkeypatch.setattr(loop.chain, "stake_by_hotkey", lambda block=None: {})
    scoreboards = [
        {
            "validator_hotkey": "val1",
            "stake_weight": 1.0,
            "rows": [
                {
                    "entry_id": "champion:1:s1",
                    "entry_kind": "champion_defense",
                    "miner_uid": 7,
                    "miner_hotkey": "X",
                    "submission_id": "s1",
                    "source_round_id": 1,
                    "score": 10.0,
                    "status": "ok",
                },
                {
                    "entry_id": "s2",
                    "entry_kind": "submission",
                    "miner_uid": 7,
                    "miner_hotkey": "X",
                    "submission_id": "s2",
                    "source_round_id": 2,
                    "score": 20.0,
                    "status": "ok",
                },
            ],
        }
    ]
    entries = _weighted_entry_scores(scoreboards, None)
    assert set(entries) == {"champion:1:s1", "s2"}
    assert entries["champion:1:s1"]["entry_kind"] == "champion_defense"
    assert entries["s2"]["score"] == 20.0


def test_weighted_entry_scores_uses_stake_weights(monkeypatch):
    monkeypatch.setattr(
        loop.chain, "stake_by_hotkey", lambda block=None: {"val_a": 3.0, "val_b": 1.0}
    )
    row = lambda score: {  # noqa: E731
        "entry_id": "e1",
        "entry_kind": "submission",
        "miner_uid": 1,
        "miner_hotkey": "m1",
        "submission_id": "e1",
        "source_round_id": 2,
        "score": score,
        "status": "ok",
    }
    scoreboards = [
        {"validator_hotkey": "val_a", "stake_weight": 0.0, "rows": [row(10.0)]},
        {"validator_hotkey": "val_b", "stake_weight": 0.0, "rows": [row(2.0)]},
    ]
    entries = _weighted_entry_scores(scoreboards, None)
    assert entries["e1"]["score"] == 8.0


class _FakeHotkey:
    ss58_address = "val-self"


class _FakeWallet:
    hotkey = _FakeHotkey()


class _FakeAPI:
    def __init__(self, scoreboards, margin):
        self._scoreboards = scoreboards
        self._margin = margin
        self.consensus = None
        self.banned_hotkeys: set[str] = set()
        self.scoreboards_by_round: dict[str, list[dict]] = {}
        self.rosters: dict[str, dict] = {}

    def list_round_scoreboards(self, round_id):
        return self.scoreboards_by_round.get(round_id, self._scoreboards)

    def get_round_roster(self, round_id):
        return {"champion_margin": self._margin, **self.rosters.get(round_id, {})}

    def upload_consensus_result(self, **kwargs):
        self.consensus = kwargs
        return {}

    def hotkey_eligibility(self, hotkeys):
        return {
            "policy_hash": "test-policy",
            "hotkeys": {
                hotkey: {
                    "banned": hotkey in self.banned_hotkeys,
                    "reason": "cheating" if hotkey in self.banned_hotkeys else None,
                }
                for hotkey in hotkeys
            },
        }


def _champion_scoreboards(champion_score, challenger_score):
    return [
        {
            "validator_hotkey": "val1",
            "stake_weight": 1.0,
            "rows": [
                {
                    "entry_id": "champion:1:s1",
                    "entry_kind": "champion_defense",
                    "miner_uid": 7,
                    "miner_hotkey": "X",
                    "submission_id": "s1",
                    "source_round_id": 1,
                    "score": champion_score,
                    "status": "ok",
                },
                {
                    "entry_id": "s2",
                    "entry_kind": "submission",
                    "miner_uid": 8,
                    "miner_hotkey": "Y",
                    "submission_id": "s2",
                    "source_round_id": 2,
                    "score": challenger_score,
                    "status": "ok",
                },
            ],
        }
    ]


def _patch_chain(monkeypatch, weights_sink):
    monkeypatch.setattr(loop.chain, "stake_by_hotkey", lambda block=None: {})
    monkeypatch.setattr(loop.chain, "hotkey_uid", lambda hotkey: 99)
    monkeypatch.setattr(
        loop.chain,
        "set_winner_weights",
        lambda wallet, uid, burn_rate=0.0, burn_uid=0: weights_sink.append(uid),
    )


def test_process_consensus_reaffirms_champion_uid_when_kept(monkeypatch):
    weights = []
    _patch_chain(monkeypatch, weights)
    api = _FakeAPI(_champion_scoreboards(champion_score=10.0, challenger_score=12.0), margin=5.0)

    result = _process_consensus(_FakeWallet(), api, {"round_id": "2026-07-06-AM"})

    assert api.consensus["round_id"] == "2026-07-06-AM"  # date-based string id flows through
    assert api.consensus["champion_kept"] is True
    assert api.consensus["winner_entry_kind"] == "champion_defense"
    assert api.consensus["source_submission_id"] == "s1"
    assert api.consensus["source_round_id"] == 1
    assert weights == [7]
    assert result == (7, "X")


def test_process_consensus_sets_challenger_uid_when_dethroned(monkeypatch):
    weights = []
    _patch_chain(monkeypatch, weights)
    api = _FakeAPI(_champion_scoreboards(champion_score=10.0, challenger_score=20.0), margin=5.0)

    result = _process_consensus(_FakeWallet(), api, {"round_id": "2026-07-06-AM"})

    assert api.consensus["champion_kept"] is False
    assert api.consensus["winner_entry_kind"] == "submission"
    assert weights == [8]
    assert result == (8, "Y")


def test_process_consensus_excludes_banned_winner(monkeypatch):
    weights = []
    _patch_chain(monkeypatch, weights)
    api = _FakeAPI(_champion_scoreboards(champion_score=10.0, challenger_score=20.0), margin=0.0)
    api.banned_hotkeys.add("Y")

    result = _process_consensus(_FakeWallet(), api, {"round_id": "2026-07-06-AM"})

    assert result == (7, "X")
    assert api.consensus["top_miner_hotkey"] == "X"
    assert weights == [7]


def test_banned_reigning_champion_falls_back_to_previous_round_runner_up(monkeypatch):
    monkeypatch.setattr(loop.chain, "stake_by_hotkey", lambda block=None: {})
    api = _FakeAPI(_champion_scoreboards(champion_score=20.0, challenger_score=10.0), margin=0.0)
    api.banned_hotkeys.add("X")
    api.rosters = {
        "current": {"previous_round_id": "previous"},
        "previous": {"freeze_block_hash": None},
    }
    api.scoreboards_by_round["previous"] = _champion_scoreboards(champion_score=20.0, challenger_score=10.0)

    assert _previous_round_replacement(api, "current") == (8, "Y")


def _capture_weight_call(monkeypatch, captured):
    monkeypatch.setattr(loop.chain, "stake_by_hotkey", lambda block=None: {})
    monkeypatch.setattr(loop.chain, "hotkey_uid", lambda hotkey: 99)
    monkeypatch.setattr(
        loop.chain,
        "set_winner_weights",
        lambda wallet, uid, burn_rate=0.0, burn_uid=0: captured.update(
            uid=uid, burn_rate=burn_rate, burn_uid=burn_uid
        ),
    )


def test_process_consensus_applies_validator_burn_config(monkeypatch):
    monkeypatch.setattr(loop, "BURN_RATE", 0.9)
    monkeypatch.setattr(loop, "BURN_UID", 0)
    captured = {}
    _capture_weight_call(monkeypatch, captured)

    api = _FakeAPI(_champion_scoreboards(champion_score=10.0, challenger_score=20.0), margin=5.0)
    _process_consensus(_FakeWallet(), api, {"round_id": "2026-07-06-AM"})

    assert captured == {"uid": 8, "burn_rate": 0.9, "burn_uid": 0}


def test_process_consensus_defaults_to_no_burn_when_unconfigured(monkeypatch):
    monkeypatch.setattr(loop, "BURN_RATE", 0.0)
    monkeypatch.setattr(loop, "BURN_UID", 0)
    captured = {}
    _capture_weight_call(monkeypatch, captured)

    api = _FakeAPI(_champion_scoreboards(champion_score=10.0, challenger_score=20.0), margin=5.0)
    _process_consensus(_FakeWallet(), api, {"round_id": "2026-07-06-AM"})

    assert captured == {"uid": 8, "burn_rate": 0.0, "burn_uid": 0}
