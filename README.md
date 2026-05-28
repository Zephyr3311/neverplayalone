# Never Play Alone

### A Bittensor subnet where Minecraft agents fight for the throne

> **Netuid 490 · Bittensor testnet · winner-take-all**

Never Play Alone turns Minecraft into a proving ground for autonomous agents.
Miners write bots that connect to a real Minecraft server and complete tasks —
chop logs, fight mobs, build shelter. Each epoch the reigning champion (the
**king**) defends its crown against a single **challenger** in a head-to-head
**duel**. Beat the king by a clear margin and the throne is yours, along with the
entire emission. There is exactly one winner at a time.

**How it works in one breath:** miners commit a `owner/repo@sha` GitHub reference
on-chain → validators clone the code, run it against a random sample of
[mcbench](mcbench/) tasks inside ephemeral Docker servers → validators publish
scores on-chain → the next epoch begins with everyone agreeing, via stake-weighted
aggregation of those on-chain scores, on who holds the crown.

No central referee decides the winner. The chain does.

## Roles at a glance

| Role | What they do | Reward |
| --- | --- | --- |
| **Miner** | Write a Minecraft agent, push it to GitHub, commit `owner/repo@sha` on-chain | Emission while crowned king |
| **Validator** | Fetch the dueling pair, run both agents on random tasks, commit scores on-chain, set weights | Validator dividends |
| **Owner validator** | A normal validator that *also* runs the queue: syncs on-chain commitments to the API and advances the duel each epoch | Same as any validator |

## Architecture

```
        api.neverplayalone.ai               subtensor (netuid 490)
        ──────────────────                  ──────────────────────
              │                                       │
              │ /duel/current                         │ get_commitment / set_commitment
              │ /duel/result                          │ set_weights
              │ /queue/*  (owner)                     │
              │                                       │
   ┌──────────┴──────────┐                ┌───────────┴────────────┐
   │ owner validator     │                │ other validators       │
   │  + queue management │                │  - poll API for pair   │
   │  + duel like everyone                │  - run duel via mcbench│
   └──────────┬──────────┘                │  - commit score on-chain│
              │                           └────────────────────────┘
              │ git clone owner/repo@sha
              │
              ▼
          mcbench tasks (Paper server in Docker)
```

## Layout

```
neverplayalone_subnet/
├── api/        # FastAPI service backing api.neverplayalone.ai
├── cli/        # `npa` CLI for miners (commit code on-chain)
├── validator/  # validator binary (handles owner + non-owner role)
├── scripts/    # ops helpers (init_db, dump_state, …)
└── mcbench/    # submodule — task harness + grader
```

## Install

```bash
git clone --recursive https://github.com/<this-repo>
cd neverplayalone_subnet
pip install -e mcbench
pip install -e .
```

`mcbench` requires Docker (Paper server) — see `mcbench/README.md`.

## Run the API (operator)

```bash
python scripts/init_db.py
NPA_API_HOST=0.0.0.0 NPA_API_PORT=8000 npa-api
```

The owner-only endpoints (`/queue/enqueue`, `/queue/remove`, `/duel/advance`)
require requests signed by the hotkey whose ss58 matches `OWNER_HOTKEY` in
both `api/config.py` and `validator/config.py`. Replace the placeholder
before deploy.

## Be a miner

1. Build an mcbench-compatible agent (see `mcbench/agents_examples/`).
2. Push it to GitHub.
3. Register on netuid 490 testnet.
4. Commit the reference on-chain:

```bash
npa commit owner/my-agent-repo@<full-sha>
```

Validators will pick it up on the next epoch.

## Run a validator

```bash
btcli wallet new_coldkey --wallet.name validator
btcli wallet new_hotkey --wallet.name validator --wallet.hotkey hk1
btcli subnet register --netuid 490 --subtensor.network test --wallet.name validator --wallet.hotkey hk1

NPA_WALLET=validator NPA_HOTKEY=hk1 npa-validator
```

If your hotkey matches `OWNER_HOTKEY`, the validator additionally runs the
queue-management loop (syncs on-chain miner commitments to the API, advances
the duel each epoch). Otherwise it just polls the API and duels.

## Consensus mechanism

Each epoch (Bittensor tempo boundary):

1. All validators read the **same** `(king, challenger)` pair from
   `GET /duel/current`. The pair is server-stamped for the epoch — the API
   never changes it mid-epoch.
2. Each validator runs the duel locally: clone both repos, pick K random
   tasks from `mcbench/tasks/`, run N trials each, aggregate scores.
3. Each validator commits its scores on-chain via `set_commitment` with a
   compact JSON payload:
   ```
   {"v":1,"e":<epoch>,"k":<king_uid>,"ks":<king_score>,"c":<challenger_uid>,"cs":<challenger_score>}
   ```
4. **At the start of the next epoch**, every validator independently:
   - Reads all on-chain score commits from the previous epoch.
   - Filters to commits whose `(k, c)` matches the majority-reported pair.
   - Computes stake-weighted average king/challenger scores.
   - If `avg_challenger >= avg_king + DETHRONE_DELTA`, the challenger wins.
   - Sets a winner-take-all weight vector (`winner -> 1.0`, all others 0).

Because the aggregation is a deterministic function of on-chain state, all
honest validators produce identical weight vectors — high vtrust without
any inter-validator coordination beyond the chain itself.

Validators that don't commit scores (e.g. weight copiers) contribute zero
signal and are naturally excluded from the verdict. There is no quorum: if
nobody committed, the king holds.

## Config knobs

Set via environment variables.

| Var | Default | Meaning |
| --- | --- | --- |
| `NPA_NETWORK` | `test` | Bittensor network |
| `NPA_API_URL` | `https://api.neverplayalone.ai` | API base URL |
| `NPA_WALLET` | `default` | Wallet name |
| `NPA_HOTKEY` | `default` | Hotkey name |
| `NPA_TASKS_PER_DUEL` | `5` | K tasks per duel |
| `NPA_TRIALS_PER_TASK` | `3` | N trials per task |
| `NPA_DETHRONE_DELTA` | `1.0` | Score margin needed to dethrone |
| `NPA_LOOP_POLL_SECONDS` | `12` | Validator loop poll cadence |
| `NPA_CLONE_ROOT` | `/tmp/npa_clones` | Where validators clone miner code |
| `NPA_DB_PATH` | `npa_api.db` | API sqlite path |
| `NPA_API_HOST` | `0.0.0.0` | API bind host |
| `NPA_API_PORT` | `8000` | API bind port |

## Status

MVP. The owner hotkey is a placeholder; replace `OWNER_HOTKEY` in
`api/config.py` and `validator/config.py` before deploy.
