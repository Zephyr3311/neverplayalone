#!/usr/bin/env python3
"""Round-13 submission readiness check.

Exercises every step of the real submission path that can be reached WITHOUT
spending anything: no registration extrinsic, and no POST to
/miner/submissions/slot (that would consume a `max_uses` slot). Everything here
is a local check or a GET.

usage: .venv/bin/python preflight.py [--wallet NAME] [--hotkey NAME] [--archive PATH]
"""
from __future__ import annotations
import argparse, hashlib, os, pathlib, sys, tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

ap = argparse.ArgumentParser()
ap.add_argument("--wallet", default="cricketminercoldkey")
ap.add_argument("--hotkey", default="cricketminerhotkey")
ap.add_argument("--archive", default="../npa_agent/agent_v02.tar.gz")
a = ap.parse_args()

OK, WARN, BAD = "  OK  ", " WARN ", " BLOCK"
rows: list[tuple[str, str, str]] = []
def add(state, label, detail=""): rows.append((state, label, detail))

# 1. CLI + config defaults -------------------------------------------------
from miner.config import API_URL, NPA_NETWORK
from shared import chain
add(OK if API_URL == "https://api.neverplayalone.ai" else BAD, "API_URL", API_URL)
add(OK if NPA_NETWORK == "finney" else BAD, "network", NPA_NETWORK)
add(OK if chain.NETUID == 98 else BAD, "netuid", str(chain.NETUID))
overrides = {k: v for k, v in os.environ.items()
             if k.startswith(("NPA_", "BT_")) }
add(OK if not overrides else WARN, "env overrides",
    "none — hardcoded defaults apply" if not overrides else str(overrides))

# 2. Archive ---------------------------------------------------------------
arch = pathlib.Path(a.archive)
if not arch.is_file():
    add(BAD, "archive", f"missing: {arch}")
else:
    blob = arch.read_bytes()
    sha = hashlib.sha256(blob).hexdigest()
    add(OK if arch.name.endswith(".tar.gz") else BAD, "archive name", arch.name)
    try:
        from validator.round_evaluation import _safe_extract_tar_gz
        with tempfile.TemporaryDirectory() as d:
            _safe_extract_tar_gz(blob, pathlib.Path(d))
            files = sorted(p.relative_to(d).as_posix()
                           for p in pathlib.Path(d).rglob("*") if p.is_file())
        add(OK if "index.js" in files else BAD, "archive preflight",
            f"{len(blob):,} bytes, {len(files)} files, index.js at root")
        add(OK, "archive sha256", sha)
    except Exception as e:
        add(BAD, "archive preflight", f"{type(e).__name__}: {e}")

# 3. Round window ----------------------------------------------------------
import httpx
try:
    r = httpx.get(f"{API_URL}/miner/rounds/current", timeout=20.0)
    r.raise_for_status()
    body = r.json()
    # /miner/rounds/current wraps the round in "submission_round".
    rd = body.get("submission_round") or body.get("data") or body
    open_now = rd.get("status") == "submission_open"
    add(OK if open_now else WARN, "round window",
        f"{rd.get('round_id')} status={rd.get('status')} "
        f"closes_at_block={rd.get('evaluation_start_block')}")
except Exception as e:
    add(BAD, "round window", f"{type(e).__name__}: {e}")

# 4. Wallet + hotkey signing against the LIVE backend ----------------------
try:
    wallet = chain.make_wallet(a.wallet, a.hotkey)
    hk = wallet.hotkey.ss58_address
    add(OK, "wallet/hotkey", f"{a.wallet}/{a.hotkey} -> {hk}")
except Exception as e:
    add(BAD, "wallet/hotkey", f"{type(e).__name__}: {e}")
    wallet = None

if wallet is not None:
    from shared.api_client import APIClient
    api = APIClient(wallet, base_url=API_URL)
    try:
        usage = api.get_hotkey_usage()
        add(OK, "signed auth + quota",
            f"use_count={usage.get('use_count')}/{usage.get('max_uses')} "
            f"remaining={usage.get('remaining')} can_submit={usage.get('can_submit')} "
            f"banned={usage.get('banned')}")
    except httpx.HTTPStatusError as e:
        detail = ""
        try: detail = e.response.json().get("detail", "")
        except Exception: detail = (e.response.text or "")[:120]
        if "not registered" in str(detail):
            # The backend authenticated the signature and rejected only on
            # registration. That is the furthest this check can go pre-burn.
            add(OK, "signed auth", "signature ACCEPTED; rejected only for "
                                   "'hotkey not registered' — expected pre-registration")
            add(BLOCK := " AFTER", "quota (max_uses)",
                "unknown until registered — run `npacli usage` BEFORE submitting")
        else:
            add(BAD, "signed auth", f"HTTP {e.response.status_code}: {detail}")
    except Exception as e:
        add(BAD, "signed auth", f"{type(e).__name__}: {e}")
    finally:
        api.close()

    # 5. Chain: is the hotkey in the metagraph yet? -----------------------
    try:
        uid = chain.hotkey_uid(hk)
        add(OK, "registered UID", str(uid))
    except Exception as e:
        add(" AFTER", "registration",
            f"not in metagraph yet ({type(e).__name__}) — this is the one blocking step")
    finally:
        chain.close_subtensor()

w = max(len(l) for _, l, _ in rows)
print("\nRound-13 submission readiness\n" + "-" * (w + 40))
for state, label, detail in rows:
    print(f"[{state}] {label:<{w}}  {detail}")
print("-" * (w + 40))
print("[ AFTER] = only resolvable after the hotkey is registered.\n")
