from __future__ import annotations

from validator import loop

# LAST_WINNER_PATH is isolated to a per-test temp file by the autouse fixture in
# conftest.py, so these tests never touch the real cwd file.


def test_save_load_last_winner_roundtrip():
    assert loop._load_last_winner() is None  # nothing saved yet
    loop._save_last_winner(7, "hkA")
    assert loop._load_last_winner() == (7, "hkA")
    loop._save_last_winner(12, "hkB")  # overwrites
    assert loop._load_last_winner() == (12, "hkB")


def _capture_set_weights(monkeypatch):
    calls = {}
    monkeypatch.setattr(
        loop.chain,
        "set_winner_weights",
        lambda wallet, uid, burn_rate=0.0, burn_uid=0: calls.update(uid=uid),
    )
    return calls


def test_fallback_weights_last_winner_when_eligible(monkeypatch):
    loop._save_last_winner(5, "hk5")
    monkeypatch.setattr(loop, "_hotkey_is_eligible", lambda api, hk: True)
    calls = _capture_set_weights(monkeypatch)

    result = loop._set_fallback_weights(
        object(), object(), "2026-08-07", source="no_consensus", epoch_index=0
    )

    assert result is True
    assert calls.get("uid") == 5


def test_fallback_skips_when_no_saved_winner(monkeypatch):
    calls = _capture_set_weights(monkeypatch)

    result = loop._set_fallback_weights(
        object(), object(), "r", source="no_consensus", epoch_index=0
    )

    assert result is False  # nothing to fall back to
    assert calls == {}  # no weight set, and no burn


def test_fallback_skips_when_last_winner_ineligible(monkeypatch):
    loop._save_last_winner(9, "hk9")
    monkeypatch.setattr(loop, "_hotkey_is_eligible", lambda api, hk: False)  # banned since
    calls = _capture_set_weights(monkeypatch)

    result = loop._set_fallback_weights(
        object(), object(), "r", source="no_consensus", epoch_index=0
    )

    assert result is False  # do not weight a banned winner
    assert calls == {}


def test_offline_fallback_weights_without_eligibility(monkeypatch):
    loop._save_last_winner(3, "hk3")

    def _boom(api, hotkey):
        raise AssertionError("eligibility must not be checked on the offline path")

    monkeypatch.setattr(loop, "_hotkey_is_eligible", _boom)
    calls = _capture_set_weights(monkeypatch)

    # Offline path: api is None, eligibility skipped, still weights the saved uid.
    result = loop._set_fallback_weights(
        object(), None, "(offline)", source="offline_api_down", check_eligibility=False
    )

    assert result is True
    assert calls.get("uid") == 3
