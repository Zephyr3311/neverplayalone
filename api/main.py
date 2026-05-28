"""FastAPI service: queue authority + duel state coordinator.

Owner-only endpoints mutate queue/duel state. Public endpoints serve the
current epoch pair to validators. The API is intentionally dumb — it stores
queue state but never decides duel outcomes. Yuma consensus on validator-
committed scores is the real authority.
"""
from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException

from .auth import verify_owner, verify_signed
from .config import NETUID, OWNER_HOTKEY
from .models import (
    AdvanceRequest,
    CurrentDuel,
    DuelResultRequest,
    EnqueueRequest,
    Participant,
    RemoveRequest,
)
from . import store

app = FastAPI(title="Never Play Alone Subnet API")


@app.on_event("startup")
def _startup() -> None:
    store.init_db()


@app.get("/health")
def health():
    return {"status": "ok", "netuid": NETUID, "owner_hotkey": OWNER_HOTKEY}


@app.get("/queue")
def get_queue():
    return store.list_queue()


@app.post("/queue/enqueue")
def queue_enqueue(req: EnqueueRequest, _: str = Depends(verify_owner)):
    store.enqueue(req.uid, req.hotkey, req.repo, req.sha)
    return {"ok": True}


@app.post("/queue/remove")
def queue_remove(req: RemoveRequest, _: str = Depends(verify_owner)):
    store.remove(req.hotkey)
    return {"ok": True}


def _participant_from_duel_row(row: dict, prefix: str):
    hotkey = row.get(f"{prefix}_hotkey")
    if not hotkey:
        return None
    return Participant(
        uid=row[f"{prefix}_uid"],
        hotkey=hotkey,
        repo=row[f"{prefix}_repo"],
        sha=row[f"{prefix}_sha"],
    )


@app.get("/duel/current", response_model=CurrentDuel)
def duel_current():
    row = store.latest_duel()
    if not row:
        return CurrentDuel(epoch_id=None, king=None, challenger=None)
    return CurrentDuel(
        epoch_id=row["epoch_id"],
        king=_participant_from_duel_row(row, "king"),
        challenger=_participant_from_duel_row(row, "challenger"),
    )


@app.post("/duel/advance")
def duel_advance(req: AdvanceRequest, _: str = Depends(verify_owner)):
    # 1. If the previous duel's challenger was crowned by Yuma, promote them.
    prev = store.latest_duel()
    if prev and req.new_king_hotkey and prev["challenger_hotkey"] == req.new_king_hotkey:
        store.set_king(
            uid=prev["challenger_uid"],
            hotkey=prev["challenger_hotkey"],
            repo=prev["challenger_repo"],
            sha=prev["challenger_sha"],
            epoch=req.epoch_id,
        )

    # 2. Read current king (after potential promotion).
    king = store.get_king()

    # 3. Pop next challenger.
    challenger = store.pop_front()

    # 4. Record new duel for this epoch.
    store.insert_duel(req.epoch_id, king, challenger)
    return {"ok": True, "king": king, "challenger": challenger}


@app.post("/duel/result")
def duel_result(req: DuelResultRequest, signer: str = Depends(verify_signed)):
    store.insert_result(req.epoch_id, signer, req.king_score, req.challenger_score)
    return {"ok": True}


@app.get("/duel/history")
def duel_history(limit: int = 50, offset: int = 0):
    if limit < 1 or limit > 500:
        raise HTTPException(400, "limit out of range")
    return store.list_duels(limit, offset)


def run() -> None:
    """Console-script entrypoint: `npa-api`."""
    import os
    import uvicorn

    host = os.environ.get("NPA_API_HOST", "0.0.0.0")
    port = int(os.environ.get("NPA_API_PORT", "8000"))
    uvicorn.run("api.main:app", host=host, port=port, log_level="info")
