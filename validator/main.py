"""Validator entrypoint."""
from __future__ import annotations

import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    env_file = _ROOT / ".env"
    if not env_file.exists():
        return
    for raw in env_file.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()

import logging  # noqa: E402

from shared import chain  # noqa: E402
from shared.api_client import APIClient  # noqa: E402
from validator.config import API_URL, NETUID, NETWORK, PROXY_ENABLED, PROXY_PORT  # noqa: E402
from validator.loop import main_loop  # noqa: E402


def _setup_logging() -> None:
    level = os.environ.get("NPA_LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def main() -> int:
    _setup_logging()
    log = logging.getLogger("npa.validator")

    wallet_name = os.environ.get("NPA_WALLET", "default")
    wallet_hotkey = os.environ.get("NPA_HOTKEY", "default")
    wallet = chain.make_wallet(wallet_name, wallet_hotkey)

    log.info("hotkey=%s", wallet.hotkey.ss58_address)
    log.info("netuid=%s network=%s api=%s", NETUID, NETWORK, API_URL)
    log.info("proxy_enabled=%s proxy_port=%s", PROXY_ENABLED, PROXY_PORT)

    api = APIClient(wallet, base_url=API_URL)
    try:
        api.health()
    except Exception as exc:
        log.error("backend unreachable at %s: %s", API_URL, exc)
        return 1

    try:
        main_loop(wallet, api)
    except KeyboardInterrupt:
        log.info("interrupted")
        return 0
    finally:
        api.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
