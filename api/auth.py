"""Hotkey signature verification for write endpoints.

Validators sign requests with their bittensor hotkey. The signature covers
method, path, request body, and a unique nonce. Replay protection comes from
both the timestamp window and the nonce table in storage.
"""
from __future__ import annotations

import time
from typing import Awaitable, Callable

import bittensor as bt
from fastapi import HTTPException, Request

from .config import OWNER_HOTKEY, SIGNATURE_MAX_AGE
from .store import consume_nonce


async def _verify(request: Request) -> str:
    hotkey = request.headers.get("X-Hotkey")
    nonce = request.headers.get("X-Nonce")
    signature = request.headers.get("X-Signature")
    timestamp = request.headers.get("X-Timestamp")

    if not (hotkey and nonce and signature and timestamp):
        raise HTTPException(401, "missing auth headers")

    try:
        ts = int(timestamp)
    except ValueError:
        raise HTTPException(401, "bad timestamp")
    if abs(int(time.time()) - ts) > SIGNATURE_MAX_AGE:
        raise HTTPException(401, "stale request")

    body = await request.body()
    msg = f"{request.method}\n{request.url.path}\n{body.decode('utf-8')}\n{nonce}\n{timestamp}"

    try:
        keypair = bt.Keypair(ss58_address=hotkey)
        ok = keypair.verify(msg.encode("utf-8"), bytes.fromhex(signature))
    except Exception:
        raise HTTPException(401, "signature verification error")
    if not ok:
        raise HTTPException(401, "invalid signature")

    if not consume_nonce(nonce, hotkey):
        raise HTTPException(401, "nonce reused")

    return hotkey


async def verify_signed(request: Request) -> str:
    return await _verify(request)


async def verify_owner(request: Request) -> str:
    hotkey = await _verify(request)
    if hotkey != OWNER_HOTKEY:
        raise HTTPException(403, "owner-only endpoint")
    return hotkey
