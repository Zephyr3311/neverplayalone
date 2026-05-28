"""Validator entrypoint. Same binary for owner and non-owner — role is detected
from the wallet hotkey at startup.
"""
from __future__ import annotations

import logging
import os
import sys

from . import chain
from .api_client import APIClient
from .config import API_URL, NETUID, NETWORK, OWNER_HOTKEY
from .loop import main_loop


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

    is_owner = wallet.hotkey.ss58_address == OWNER_HOTKEY
    log.info("hotkey=%s owner=%s", wallet.hotkey.ss58_address, is_owner)
    log.info("netuid=%s network=%s api=%s", NETUID, NETWORK, API_URL)

    api = APIClient(wallet)
    try:
        api.health()
    except Exception as e:
        log.error("API unreachable at %s: %s", API_URL, e)
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
