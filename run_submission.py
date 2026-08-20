#!/usr/bin/env python3
"""Gated submission flow for the currently open round.

Every step is checked before the next is allowed, and the only irreversible
actions are refused unless you ask for them explicitly:

  * registration  -- NEVER run by this script. It needs your coldkey password and
                     burns TAO. The script verifies the balance and prints the
                     exact command for you to run.
  * submission    -- DRY RUN by default. Requires --submit, and even then refuses
                     unless funds/UID/quota/archive/window all pass.

usage:
  run_submission.py                  # check everything, change nothing
  run_submission.py --submit         # actually upload, after all gates pass
"""
from __future__ import annotations
import argparse, hashlib, os, pathlib, subprocess, sys, tempfile

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# The released archive. Anything else is refused, so a rebuilt or swapped file
# cannot be submitted unnoticed. `agent/` is byte-identical to this archive's
# contents, so a faithful rebuild differs only in tar mtimes.
KNOWN_ARCHIVES = {
    "56a1a2d52bc929b149ec66bd7ffe930ce5c407a66ee80ff7c134233886f63832":
        ("agent_v02.tar.gz", "v0.1+v0.2", "RELEASED"),
}

ap = argparse.ArgumentParser()
ap.add_argument("--wallet", default="cricketminercoldkey")
ap.add_argument("--hotkey", default="cricketminerhotkey1")
ap.add_argument("--archive", default="../npa_agent/agent_v02.tar.gz")
ap.add_argument("--submit", action="store_true",
                help="actually upload (irreversible; consumes a max_uses slot)")
ap.add_argument("--allow-unknown-archive", action="store_true",
                help="permit an archive whose sha256 is not one of the reviewed builds")
a = ap.parse_args()

G, W, B, S = "\033[32m OK \033[0m", "\033[33mWARN\033[0m", "\033[31mSTOP\033[0m", "  · "
blockers: list[str] = []
def line(state, label, detail=""): print(f"[{state}] {label:<22} {detail}")
def finish(code):
    """bittensor's websocket thread can keep the interpreter alive after we are
    done; flush and exit hard so the caller always gets a prompt status."""
    sys.stdout.flush(); sys.stderr.flush()
    try: chain.close_subtensor()
    except Exception: pass
    os._exit(code)
def stop(label, detail):
    line(B, label, detail); blockers.append(f"{label}: {detail}")

# The round label is read live, never hardcoded -- a stale round number in a
# safety script invites submitting into the wrong window.
print("\n=== Never Play Alone submission flow ===")
print(f"    mode: {'SUBMIT (irreversible)' if a.submit else 'DRY RUN (nothing will be uploaded)'}")

import httpx as _httpx
try:
    _r = _httpx.get("https://api.neverplayalone.ai/miner/rounds/current", timeout=15.0)
    _rd = _r.json().get("submission_round") or {}
    print(f"    round: {_rd.get('round_id')} ({_rd.get('status')})\n")
except Exception:
    print("    round: (could not read the round label)\n")

# ---- STEP 1: funds -------------------------------------------------------
import bittensor as bt
from shared import chain
from miner.config import API_URL

# ValueError from chain.hotkey_uid() means "not in the metagraph" -- a definitive
# answer, not a flake. Retrying it burns chain calls and makes a clean STOP look
# unreliable, so only transport-level faults are retried.
DEFINITIVE = (ValueError, KeyError)

def with_retry(what, fn, tries=3):
    """The finney websocket handshake times out intermittently. A safety check
    must not crash on a flake, and must not silently pass either."""
    last = None
    for n in range(1, tries + 1):
        try:
            return fn(), None
        except DEFINITIVE as e:
            return None, e                      # definitive: report immediately
        except Exception as e:
            last = e
            if n < tries:
                print(f"{S}{what}: attempt {n}/{tries} failed ({type(e).__name__}), retrying")
    return None, last

print("STEP 1  funds")
st, err = with_retry("chain connect", lambda: bt.Subtensor(network="finney"))
bal_tao = burn = need = None
if st is None:
    stop("chain", f"finney unreachable after retries: {type(err).__name__}: {str(err)[:80]}")
    print(f"{S}Cannot verify funds or registration without the chain. Re-run when it responds.")
else:
    burn_rao, e1 = with_retry("burn", lambda: st.get_hyperparameter("Burn", netuid=chain.NETUID))
    wallet_obj = bt.Wallet(name=a.wallet)
    bal, e2 = with_retry("balance", lambda: st.get_balance(wallet_obj.coldkeypub.ss58_address))
    if burn_rao is None or bal is None:
        stop("chain query", f"could not read burn/balance ({e1 or e2})")
    else:
        burn = burn_rao / 1e9
        bal_tao = float(bal.tao if hasattr(bal, "tao") else bal)
        need = burn * 1.05  # burn can rise between check and signature
        line(G if bal_tao >= need else B, "coldkey balance",
             f"τ{bal_tao:.6f} available, burn is τ{burn:.4f} (need ~τ{need:.4f})")
        if bal_tao < need:
            blockers.append(f"insufficient funds: send at least τ{need - bal_tao:.4f} more "
                            f"(recommend τ0.25 total for headroom)")

# ---- STEP 2: registration (never performed here) ------------------------
print("\nSTEP 2  registration")
wallet = chain.make_wallet(a.wallet, a.hotkey)
hk = wallet.hotkey.ss58_address
line(G, "hotkey", f"{a.wallet}/{a.hotkey} -> {hk}")
uid = None
if st is None:
    stop("registration", "not checked -- chain unreachable")
else:
    uid, uerr = with_retry("hotkey uid", lambda: chain.hotkey_uid(hk))
    if uid is not None:
        line(G, "registered UID", str(uid))
    else:
        stop("registration", "hotkey is not in the metagraph")
    print(f"{S}This script never registers -- it needs your coldkey password and burns TAO.")
    print(f"{S}Run this yourself, and read the cost btcli prints before confirming:\n")
    print(f"      btcli subnet register --netuid {chain.NETUID} --subtensor.network finney \\")
    print(f"        --wallet.name {a.wallet} --wallet.hotkey {a.hotkey}\n")

# ---- STEP 3: quota -------------------------------------------------------
print("STEP 3  submission quota")
from shared.api_client import APIClient
import httpx
usage = None
api = APIClient(wallet, base_url=API_URL)
try:
    usage = api.get_hotkey_usage()
    banned = bool(usage.get("banned"))
    can = bool(usage.get("can_submit"))
    rem = usage.get("remaining")
    line(B if banned else G, "ban state",
         f"banned={banned}" + (f" reason={usage.get('ban_reason')}" if banned else ""))
    line(G if can else B, "quota",
         f"use_count={usage.get('use_count')}/{usage.get('max_uses')} remaining={rem} "
         f"can_submit={can} used_rounds={usage.get('used_rounds')}")
    if banned: blockers.append("hotkey is banned")
    if not can: blockers.append(f"backend says can_submit=False (remaining={rem})")
except httpx.HTTPStatusError as e:
    detail = ""
    try: detail = e.response.json().get("detail", "")
    except Exception: detail = (e.response.text or "")[:120]
    if "not registered" in str(detail):
        line(G, "signed auth", "signature accepted; quota unreadable until registered")
        blockers.append("quota unknown until the hotkey is registered")
    else:
        stop("quota", f"HTTP {e.response.status_code}: {detail}")
finally:
    api.close()

# ---- STEP 4: archive + round window ------------------------------------
print("\nSTEP 4  archive and round window")
arch = (ROOT / a.archive).resolve() if not os.path.isabs(a.archive) else pathlib.Path(a.archive)
if not arch.is_file():
    stop("archive", f"missing: {arch}")
else:
    blob = arch.read_bytes()
    sha = hashlib.sha256(blob).hexdigest()
    if sha in KNOWN_ARCHIVES:
        name, contents, note = KNOWN_ARCHIVES[sha]
        line(G if note == "RELEASED" else W, "archive identity",
             f"{name} ({contents}) - {note}")
    elif a.allow_unknown_archive:
        line(W, "archive identity", f"UNRECOGNISED {sha[:16]}… (allowed by flag)")
    else:
        stop("archive identity",
             f"UNRECOGNISED sha256 {sha[:16]}… -- not one of the reviewed builds. "
             "A rebuild from agent/ ships the v0.3 planner change. "
             "See npa_agent/SUBMISSION.md, or pass --allow-unknown-archive.")
    line(G, "archive sha256", sha)
    try:
        from validator.round_evaluation import _safe_extract_tar_gz
        with tempfile.TemporaryDirectory() as d:
            _safe_extract_tar_gz(blob, pathlib.Path(d))
            files = sorted(p.relative_to(d).as_posix()
                           for p in pathlib.Path(d).rglob("*") if p.is_file())
        if "index.js" not in files: stop("archive layout", "index.js is not at the root")
        else: line(G, "archive layout", f"{len(blob):,} bytes, {len(files)} files, index.js at root")
    except Exception as e:
        stop("archive safety", f"{type(e).__name__}: {e}")

try:
    r = httpx.get(f"{API_URL}/miner/rounds/current", timeout=20.0); r.raise_for_status()
    body = r.json(); rd = body.get("submission_round") or body.get("data") or body
    close = rd.get("evaluation_start_block")
    open_now = rd.get("status") == "submission_open"
    cur, _ = with_retry("current block", lambda: st.get_current_block()) if st else (None, None)
    hrs = (close - cur) * 12 / 3600 if (close and cur) else None
    line(G if open_now else B, "round window",
         f"{rd.get('round_id')} status={rd.get('status')} closes at block {close}"
         + (f" (~{hrs:.1f} h)" if hrs is not None else " (block height unavailable)"))
    if not open_now: blockers.append(f"round is not accepting submissions (status={rd.get('status')})")
    elif hrs is not None and hrs < 0.25: line(W, "time remaining", f"only ~{hrs*60:.0f} min left")
except Exception as e:
    stop("round window", f"{type(e).__name__}: {e}")
finally:
    try: chain.close_subtensor()
    except Exception: pass

# ---- STEP 5: submit ----------------------------------------------------
print("\nSTEP 5  submit")
if blockers:
    print(f"[{B}] refusing to submit -- {len(blockers)} blocker(s):")
    for b_ in blockers: print(f"{S}{b_}")
    print("\nFix the blockers and re-run. Nothing was uploaded.")
    finish(1)

cmd = [str(ROOT / ".venv/bin/npacli"), "submit", str(arch),
       "--wallet", a.wallet, "--hotkey", a.hotkey]
if not a.submit:
    line(G, "all gates passed", "dry run -- nothing uploaded")
    print(f"{S}To submit for real, re-run with --submit. It will execute:")
    print(f"      {' '.join(cmd)}")
    finish(0)

print(f"{S}All gates passed. Uploading -- this consumes a max_uses slot and cannot be undone.")
rc = subprocess.call(cmd)
print(f"\n{S}npacli exited {rc}")
if rc == 0:
    print(f"{S}Verify the printed sha256 matches: {hashlib.sha256(arch.read_bytes()).hexdigest()}")
finish(rc)
