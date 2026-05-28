"""SQLite-backed state for the queue and duel history."""
from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from typing import Iterator, Optional

from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS queue (
    position INTEGER PRIMARY KEY AUTOINCREMENT,
    uid INTEGER NOT NULL,
    hotkey TEXT NOT NULL UNIQUE,
    repo TEXT NOT NULL,
    sha TEXT NOT NULL,
    enqueued_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS duels (
    epoch_id INTEGER PRIMARY KEY,
    king_hotkey TEXT,
    king_uid INTEGER,
    king_repo TEXT,
    king_sha TEXT,
    challenger_hotkey TEXT,
    challenger_uid INTEGER,
    challenger_repo TEXT,
    challenger_sha TEXT,
    started_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS duel_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    epoch_id INTEGER NOT NULL,
    validator_hotkey TEXT NOT NULL,
    king_score REAL,
    challenger_score REAL,
    submitted_at INTEGER NOT NULL,
    UNIQUE (epoch_id, validator_hotkey)
);

CREATE TABLE IF NOT EXISTS king (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    hotkey TEXT,
    uid INTEGER,
    repo TEXT,
    sha TEXT,
    coronated_at_epoch INTEGER
);

CREATE TABLE IF NOT EXISTS nonces (
    nonce TEXT PRIMARY KEY,
    hotkey TEXT NOT NULL,
    seen_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_nonces_seen ON nonces (seen_at);
"""


@contextmanager
def db() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with db() as conn:
        conn.executescript(SCHEMA)
        conn.execute("INSERT OR IGNORE INTO king (id) VALUES (1)")


def consume_nonce(nonce: str, hotkey: str) -> bool:
    """Returns True if the nonce was unseen and is now recorded. False if already seen."""
    try:
        with db() as conn:
            conn.execute(
                "INSERT INTO nonces (nonce, hotkey, seen_at) VALUES (?, ?, ?)",
                (nonce, hotkey, int(time.time())),
            )
        return True
    except sqlite3.IntegrityError:
        return False


def gc_nonces(older_than_seconds: int = 3600) -> None:
    cutoff = int(time.time()) - older_than_seconds
    with db() as conn:
        conn.execute("DELETE FROM nonces WHERE seen_at < ?", (cutoff,))


def get_king() -> Optional[dict]:
    with db() as conn:
        row = conn.execute("SELECT * FROM king WHERE id = 1").fetchone()
        if not row or not row["hotkey"]:
            return None
        return {"uid": row["uid"], "hotkey": row["hotkey"], "repo": row["repo"], "sha": row["sha"]}


def set_king(uid: int, hotkey: str, repo: str, sha: str, epoch: int) -> None:
    with db() as conn:
        conn.execute(
            "UPDATE king SET uid = ?, hotkey = ?, repo = ?, sha = ?, coronated_at_epoch = ? WHERE id = 1",
            (uid, hotkey, repo, sha, epoch),
        )


def enqueue(uid: int, hotkey: str, repo: str, sha: str) -> None:
    now = int(time.time())
    with db() as conn:
        existing = conn.execute("SELECT 1 FROM queue WHERE hotkey = ?", (hotkey,)).fetchone()
        if existing:
            conn.execute(
                "UPDATE queue SET uid = ?, repo = ?, sha = ?, enqueued_at = ? WHERE hotkey = ?",
                (uid, repo, sha, now, hotkey),
            )
        else:
            conn.execute(
                "INSERT INTO queue (uid, hotkey, repo, sha, enqueued_at) VALUES (?, ?, ?, ?, ?)",
                (uid, hotkey, repo, sha, now),
            )


def remove(hotkey: str) -> None:
    with db() as conn:
        conn.execute("DELETE FROM queue WHERE hotkey = ?", (hotkey,))


def list_queue() -> list[dict]:
    with db() as conn:
        rows = conn.execute("SELECT * FROM queue ORDER BY position ASC").fetchall()
        return [dict(r) for r in rows]


def pop_front() -> Optional[dict]:
    with db() as conn:
        row = conn.execute("SELECT * FROM queue ORDER BY position ASC LIMIT 1").fetchone()
        if not row:
            return None
        conn.execute("DELETE FROM queue WHERE position = ?", (row["position"],))
        return {"uid": row["uid"], "hotkey": row["hotkey"], "repo": row["repo"], "sha": row["sha"]}


def latest_duel() -> Optional[dict]:
    with db() as conn:
        row = conn.execute("SELECT * FROM duels ORDER BY epoch_id DESC LIMIT 1").fetchone()
        return dict(row) if row else None


def insert_duel(
    epoch_id: int,
    king: Optional[dict],
    challenger: Optional[dict],
) -> None:
    with db() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO duels
               (epoch_id, king_hotkey, king_uid, king_repo, king_sha,
                challenger_hotkey, challenger_uid, challenger_repo, challenger_sha,
                started_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                epoch_id,
                king["hotkey"] if king else None,
                king["uid"] if king else None,
                king["repo"] if king else None,
                king["sha"] if king else None,
                challenger["hotkey"] if challenger else None,
                challenger["uid"] if challenger else None,
                challenger["repo"] if challenger else None,
                challenger["sha"] if challenger else None,
                int(time.time()),
            ),
        )


def list_duels(limit: int, offset: int) -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM duels ORDER BY epoch_id DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        return [dict(r) for r in rows]


def insert_result(epoch_id: int, validator_hotkey: str, king_score, challenger_score) -> None:
    with db() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO duel_results
               (epoch_id, validator_hotkey, king_score, challenger_score, submitted_at)
               VALUES (?, ?, ?, ?, ?)""",
            (epoch_id, validator_hotkey, king_score, challenger_score, int(time.time())),
        )
