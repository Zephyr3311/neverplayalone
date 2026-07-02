"""HTTP client for the Never Play Alone backend."""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

import httpx

API_URL = "https://api.neverplayalone.ai"


class APIClient:
    def __init__(self, wallet, base_url: str = API_URL, timeout: float = 60.0):
        self.wallet = wallet
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(timeout=timeout)

    def close(self) -> None:
        self._client.close()

    def _resolve_url(self, path_or_url: str) -> str:
        if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
            return path_or_url
        return self.base_url + path_or_url

    def _sign_headers(self, method: str, path: str, body: bytes) -> dict[str, str]:
        nonce = uuid.uuid4().hex
        timestamp = str(int(time.time()))
        message = f"{method}\n{path}\n{body.decode('utf-8')}\n{nonce}\n{timestamp}"
        signature = self.wallet.hotkey.sign(message.encode("utf-8")).hex()
        return {
            "X-Hotkey": self.wallet.hotkey.ss58_address,
            "X-Nonce": nonce,
            "X-Signature": signature,
            "X-Timestamp": timestamp,
        }

    def _get(self, path_or_url: str):
        response = self._client.get(self._resolve_url(path_or_url))
        response.raise_for_status()
        return response.json()

    def _get_signed(self, path: str):
        headers = self._sign_headers("GET", path, b"")
        response = self._client.get(self.base_url + path, headers=headers)
        response.raise_for_status()
        return response.json()

    def _post_signed(self, path: str, body: dict):
        payload = json.dumps(body).encode("utf-8")
        headers = {"Content-Type": "application/json", **self._sign_headers("POST", path, payload)}
        response = self._client.post(self.base_url + path, content=payload, headers=headers)
        response.raise_for_status()
        return response.json()

    def _put_bytes(self, url: str, data: bytes) -> dict:
        response = self._client.put(url, content=data)
        response.raise_for_status()
        content_type = response.headers.get("Content-Type", "")
        if not response.content:
            return {}
        if "application/json" not in content_type.lower():
            return {}
        return response.json()

    def health(self) -> dict:
        return self._get("/health")

    def get_miner_current_round(self) -> dict:
        return self._get("/miner/rounds/current")

    def create_submission_slot(self, miner_uid: int, filename: str) -> dict:
        return self._post_signed(
            "/miner/submissions/slot",
            {"miner_uid": miner_uid, "filename": filename},
        )

    def upload_submission_file(self, upload_url: str, archive_path: str | Path) -> dict:
        return self._put_bytes(upload_url, Path(archive_path).read_bytes())

    def finalize_submission(self, submission_id: str) -> dict:
        return self._post_signed(
            "/miner/submissions/finalize",
            {"submission_id": submission_id},
        )

    def get_current_rounds(self) -> dict:
        return self._get("/validator/rounds/current")

    def get_round_roster(self, round_id: int) -> dict:
        return self._get_signed(f"/validator/rounds/{round_id}/roster")

    def download_bytes(self, url: str) -> bytes:
        response = self._client.get(url)
        response.raise_for_status()
        return response.content

    def request_artifact_slot(
        self,
        *,
        round_id: int,
        validator_uid: int,
        entry_id: str,
        entry_kind: str,
        miner_uid: int,
        miner_hotkey: str,
        artifact_kind: str,
    ) -> dict:
        return self._post_signed(
            "/validator/artifacts/slot",
            {
                "round_id": round_id,
                "validator_uid": validator_uid,
                "entry_id": entry_id,
                "entry_kind": entry_kind,
                "miner_uid": miner_uid,
                "miner_hotkey": miner_hotkey,
                "artifact_kind": artifact_kind,
            },
        )

    def upload_bytes(self, upload_url: str, data: bytes) -> dict:
        return self._put_bytes(upload_url, data)

    def upload_scoreboard(
        self,
        *,
        round_id: int,
        validator_uid: int,
        stake_weight: float,
        rows: list[dict],
    ) -> dict:
        return self._post_signed(
            "/validator/scoreboards",
            {
                "round_id": round_id,
                "validator_uid": validator_uid,
                "stake_weight": stake_weight,
                "rows": rows,
            },
        )

    def list_round_scoreboards(self, round_id: int) -> list[dict]:
        return self._get_signed(f"/validator/rounds/{round_id}/scoreboards")

    def upload_consensus_result(
        self,
        *,
        round_id: int,
        validator_uid: int,
        top_miner_uid: int,
        top_miner_hotkey: str,
        winner_entry_id: str | None = None,
        winner_entry_kind: str | None = None,
        source_submission_id: str | None = None,
        source_round_id: int | None = None,
        champion_kept: bool = False,
    ) -> dict:
        return self._post_signed(
            "/validator/consensus-results",
            {
                "round_id": round_id,
                "validator_uid": validator_uid,
                "top_miner_uid": top_miner_uid,
                "top_miner_hotkey": top_miner_hotkey,
                "winner_entry_id": winner_entry_id,
                "winner_entry_kind": winner_entry_kind,
                "source_submission_id": source_submission_id,
                "source_round_id": source_round_id,
                "champion_kept": champion_kept,
            },
        )
