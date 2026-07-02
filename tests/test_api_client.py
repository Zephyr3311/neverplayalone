from __future__ import annotations

import importlib.util
import json
import sys
import types
import unittest

if importlib.util.find_spec("httpx") is None:
    sys.modules["httpx"] = types.SimpleNamespace(Client=lambda timeout=None: object())

from shared.api_client import APIClient


class _FakeHotkey:
    ss58_address = "hotkey-1"

    def sign(self, payload: bytes) -> bytes:
        return b"signed:" + payload


class _FakeWallet:
    hotkey = _FakeHotkey()


class _FakeResponse:
    def __init__(self, payload=None, *, content: bytes = b"", headers: dict[str, str] | None = None):
        self._payload = payload
        self.content = content
        self.headers = headers or {}

    def raise_for_status(self) -> None:
        return None

    def json(self):
        if self._payload is not None:
            return self._payload
        return json.loads(self.content)


class _RecordingHTTPClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, str] | None, bytes | None]] = []

    def get(self, url: str, headers: dict[str, str] | None = None):
        self.calls.append(("GET", url, headers, None))
        return _FakeResponse({"ok": True})

    def post(self, url: str, content: bytes | None = None, headers: dict[str, str] | None = None):
        self.calls.append(("POST", url, headers, content))
        return _FakeResponse({"ok": True})

    def put(self, url: str, content: bytes | None = None, headers: dict[str, str] | None = None):
        self.calls.append(("PUT", url, headers, content))
        return _FakeResponse(content=b"", headers={})

    def close(self) -> None:
        return None


class APIClientTests(unittest.TestCase):
    def test_validator_roster_get_is_signed(self) -> None:
        client = APIClient(_FakeWallet(), base_url="http://127.0.0.1:8000")
        http_client = _RecordingHTTPClient()
        client._client = http_client

        client.get_round_roster(7)

        method, url, headers, body = http_client.calls[0]
        self.assertEqual(method, "GET")
        self.assertEqual(url, "http://127.0.0.1:8000/validator/rounds/7/roster")
        self.assertIsNone(body)
        self.assertIsNotNone(headers)
        assert headers is not None
        self.assertEqual(headers["X-Hotkey"], "hotkey-1")
        self.assertIn("X-Nonce", headers)
        self.assertIn("X-Timestamp", headers)
        self.assertIn("X-Signature", headers)

    def test_public_validator_current_round_get_remains_unsigned(self) -> None:
        client = APIClient(_FakeWallet(), base_url="http://127.0.0.1:8000")
        http_client = _RecordingHTTPClient()
        client._client = http_client

        client.get_current_rounds()

        method, url, headers, body = http_client.calls[0]
        self.assertEqual(method, "GET")
        self.assertEqual(url, "http://127.0.0.1:8000/validator/rounds/current")
        self.assertIsNone(body)
        self.assertIsNone(headers)

    def test_upload_put_accepts_empty_non_json_response(self) -> None:
        client = APIClient(_FakeWallet(), base_url="http://127.0.0.1:8000")
        http_client = _RecordingHTTPClient()
        client._client = http_client

        result = client.upload_bytes("https://storage.example/upload", b"abc")

        method, url, headers, body = http_client.calls[0]
        self.assertEqual(method, "PUT")
        self.assertEqual(url, "https://storage.example/upload")
        self.assertEqual(body, b"abc")
        self.assertIsNone(headers)
        self.assertEqual(result, {})


if __name__ == "__main__":
    unittest.main()
