from __future__ import annotations

import pytest

from validator import loop


@pytest.fixture(autouse=True)
def _isolate_last_winner(tmp_path, monkeypatch):
    """Point the validator's persisted last-winner file at a per-test temp path so
    tests never read or write the real cwd file (or leak state between tests)."""
    monkeypatch.setattr(loop, "LAST_WINNER_PATH", str(tmp_path / "last_winner.json"))
