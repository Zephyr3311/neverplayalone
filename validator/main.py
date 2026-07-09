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
from validator.config import API_URL, NETUID, NETWORK, PROXY_PORT  # noqa: E402
from validator.loop import main_loop  # noqa: E402


def _setup_logging() -> None:
    level = os.environ.get("NPA_LOG_LEVEL", "INFO").upper()
    level_value = getattr(logging, level, logging.INFO)
    logging.basicConfig(
        level=level_value,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        force=True,
    )
    prefixes = ("npa", "validator", "shared", "npabench")
    for logger_name in ("npa", "npa.validator", "validator", "shared", "npabench"):
        logging.getLogger(logger_name).setLevel(level_value)
    for logger_name, logger_obj in logging.root.manager.loggerDict.items():
        if isinstance(logger_obj, logging.Logger) and logger_name.startswith(prefixes):
            logger_obj.setLevel(level_value)


def main() -> int:
    _setup_logging()
    log = logging.getLogger("npa.validator")

    wallet_name = os.environ.get("NPA_WALLET", "default")
    wallet_hotkey = os.environ.get("NPA_HOTKEY", "default")
    wallet = chain.make_wallet(wallet_name, wallet_hotkey)
    # bittensor mutates logging during wallet construction; restore our config
    # so validator progress logs remain visible afterwards.
    _setup_logging()

    log.info("hotkey=%s", wallet.hotkey.ss58_address)
    log.info(
        "wallet_name=%s wallet_hotkey=%s wallet_path=%s",
        wallet_name,
        wallet_hotkey,
        os.environ.get("NPA_BT_WALLET_DIR", ""),
    )
    log.info("netuid=%s network=%s api=%s", NETUID, NETWORK, API_URL)
    log.info("proxy_port=%s", PROXY_PORT)

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
