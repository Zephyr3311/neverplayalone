"""Validator entrypoint."""
from __future__ import annotations

import logging
import os
import sys

from shared import chain
from shared.api_client import APIClient
from validator.config import API_URL, NETUID, NETWORK, PROXY_ENABLED, PROXY_PORT
from validator.loop import main_loop


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
