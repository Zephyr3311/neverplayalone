"""HTTP client for api.neverplayalone.ai.

Signs write requests with the validator's hotkey. The API verifies the signature
covers (method, path, body, nonce, timestamp) and rejects stale/reused nonces.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Optional

import httpx

from .config import API_URL

log = logging.getLogger(__name__)


class APIClient:
    def __init__(self, wallet, base_url: str = API_URL, timeout: float = 30.0):
        self.wallet = wallet
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(timeout=timeout)

    def close(self) -> None:
        self._client.close()

    def _sign_headers(self, method: str, path: str, body: bytes) -> dict[str, str]:
        nonce = uuid.uuid4().hex
        timestamp = str(int(time.time()))
        msg = f"{method}\n{path}\n{body.decode('utf-8')}\n{nonce}\n{timestamp}"
        signature = self.wallet.hotkey.sign(msg.encode("utf-8")).hex()
        return {
            "X-Hotkey": self.wallet.hotkey.ss58_address,
            "X-Nonce": nonce,
            "X-Signature": signature,
            "X-Timestamp": timestamp,
        }

    def _get(self, path: str):
        r = self._client.get(self.base_url + path)
        r.raise_for_status()
        return r.json()

    def _post_signed(self, path: str, body: dict):
        data = json.dumps(body).encode("utf-8")
        headers = {"Content-Type": "application/json", **self._sign_headers("POST", path, data)}
        r = self._client.post(self.base_url + path, content=data, headers=headers)
        r.raise_for_status()
        return r.json()

    # ── Public endpoints ──────────────────────────────────────────────

    def health(self) -> dict:
        return self._get("/health")

    def get_current_duel(self) -> dict:
        return self._get("/duel/current")

    def get_queue(self) -> list[dict]:
        return self._get("/queue")

    def get_history(self, limit: int = 50, offset: int = 0) -> list[dict]:
        return self._get(f"/duel/history?limit={limit}&offset={offset}")

    # ── Validator signed endpoints ────────────────────────────────────

    def submit_result(
        self,
        epoch_id: int,
        king_hotkey: Optional[str],
        king_score: Optional[float],
        challenger_hotkey: str,
        challenger_score: float,
    ) -> dict:
        return self._post_signed(
            "/duel/result",
            {
                "epoch_id": epoch_id,
                "king_hotkey": king_hotkey,
                "king_score": king_score,
                "challenger_hotkey": challenger_hotkey,
                "challenger_score": challenger_score,
            },
        )

    # ── Owner-only signed endpoints ───────────────────────────────────

    def enqueue(self, uid: int, hotkey: str, repo: str, sha: str) -> dict:
        return self._post_signed(
            "/queue/enqueue", {"uid": uid, "hotkey": hotkey, "repo": repo, "sha": sha}
        )

    def remove(self, hotkey: str) -> dict:
        return self._post_signed("/queue/remove", {"hotkey": hotkey})

    def advance(self, epoch_id: int, new_king_hotkey: Optional[str] = None) -> dict:
        return self._post_signed(
            "/duel/advance", {"epoch_id": epoch_id, "new_king_hotkey": new_king_hotkey}
        )
