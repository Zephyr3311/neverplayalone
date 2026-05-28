"""Pydantic request/response schemas."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class Participant(BaseModel):
    uid: int
    hotkey: str
    repo: str
    sha: str


class CurrentDuel(BaseModel):
    epoch_id: Optional[int]
    king: Optional[Participant]
    challenger: Optional[Participant]


class EnqueueRequest(BaseModel):
    uid: int
    hotkey: str
    repo: str
    sha: str


class RemoveRequest(BaseModel):
    hotkey: str


class AdvanceRequest(BaseModel):
    epoch_id: int
    # Optional: owner reports who Yuma crowned. If matches current challenger, throne flips.
    new_king_hotkey: Optional[str] = None


class DuelResultRequest(BaseModel):
    epoch_id: int
    king_hotkey: Optional[str] = None
    king_score: Optional[float] = None
    challenger_hotkey: str
    challenger_score: float
