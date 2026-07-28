from __future__ import annotations

import os

from validator import round_evaluation


def _make_round(root, name: str, mtime: int):
    """Create a round_<name> workspace with a scratch reference_world inside."""
    ref_world = root / f"round_{name}" / "task_0" / "reference_world"
    ref_world.mkdir(parents=True)
    (ref_world / "level.dat").write_bytes(b"world")
    round_dir = root / f"round_{name}"
    os.utime(round_dir, (mtime, mtime))
    return round_dir


def test_prune_keeps_only_active_round(monkeypatch, tmp_path):
    monkeypatch.setattr(round_evaluation, "WORKSPACE_ROOT", str(tmp_path))
    old_a = _make_round(tmp_path, "2026-07-01-AM", 1000)
    old_b = _make_round(tmp_path, "2026-07-01-PM", 2000)
    current = _make_round(tmp_path, "2026-07-02-AM", 3000)

    round_evaluation._prune_round_workspaces("2026-07-02-AM", retain=1)

    assert not old_a.exists()
    assert not old_b.exists()
    assert current.exists()


def test_prune_retains_recent_rounds(monkeypatch, tmp_path):
    monkeypatch.setattr(round_evaluation, "WORKSPACE_ROOT", str(tmp_path))
    oldest = _make_round(tmp_path, "2026-07-01-AM", 1000)
    previous = _make_round(tmp_path, "2026-07-01-PM", 2000)
    current = _make_round(tmp_path, "2026-07-02-AM", 3000)

    # retain=2 keeps the active round plus the single most-recent other round.
    round_evaluation._prune_round_workspaces("2026-07-02-AM", retain=2)

    assert not oldest.exists()
    assert previous.exists()
    assert current.exists()


def test_prune_keeps_active_round_even_if_not_newest(monkeypatch, tmp_path):
    # A stale leftover with a newer mtime must not evict the active round.
    monkeypatch.setattr(round_evaluation, "WORKSPACE_ROOT", str(tmp_path))
    current = _make_round(tmp_path, "2026-07-02-AM", 1000)
    leftover = _make_round(tmp_path, "2026-07-09-AM", 5000)

    round_evaluation._prune_round_workspaces("2026-07-02-AM", retain=1)

    assert current.exists()
    assert not leftover.exists()


def test_prune_is_noop_when_root_missing(monkeypatch, tmp_path):
    missing = tmp_path / "does_not_exist"
    monkeypatch.setattr(round_evaluation, "WORKSPACE_ROOT", str(missing))
    # Must not raise when the workspace root has never been created.
    round_evaluation._prune_round_workspaces("2026-07-02-AM", retain=1)


def test_prune_ignores_non_round_entries(monkeypatch, tmp_path):
    monkeypatch.setattr(round_evaluation, "WORKSPACE_ROOT", str(tmp_path))
    unrelated = tmp_path / "keep_me.txt"
    unrelated.write_text("not a round dir")
    old = _make_round(tmp_path, "2026-07-01-AM", 1000)
    current = _make_round(tmp_path, "2026-07-02-AM", 3000)

    round_evaluation._prune_round_workspaces("2026-07-02-AM", retain=1)

    assert unrelated.exists()  # only round_* dirs are touched
    assert not old.exists()
    assert current.exists()
