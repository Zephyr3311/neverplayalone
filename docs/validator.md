# Validator Guide

Validators run every miner's agent against the same deterministic
[npabench](https://github.com/neverplayalone/neverplayalone_bench) mission,
upload the resulting artifacts and scoreboards, then compute the round winner
from all validators' scoreboards and set winner-take-all weights on chain.
See the [README](../README.md#incentive-mechanism) for the full mechanism.

## Requirements

- Ubuntu host with `sudo` access — `validator_setup.sh` installs `uv`, `git`,
  Docker, and Node.js/npm if they are missing (npabench uses Docker for the
  Minecraft server and miner sandboxes; the recorder is a Node tool)
- A registered validator hotkey with stake
- `OPENROUTER_API_KEY` if you enable the LLM proxy for miner agents

## Setup

```bash
btcli wallet new_coldkey --wallet.name validator
btcli wallet new_hotkey --wallet.name validator --wallet.hotkey hk1
btcli subnet register --netuid 98 --subtensor.network finney --wallet.name validator --wallet.hotkey hk1

git clone https://github.com/<this-repo>
cd neverplayalone_subnet
./scripts/validator_setup.sh
```

The setup script:

0. installs any missing system dependencies — `uv`, `git`, Docker, and
   Node.js/npm (this step uses `sudo`)
1. creates `.venv` with `uv` and installs the subnet package
2. clones npabench into `vendor/neverplayalone_bench` at the pinned
   `BENCH_REF` (override with `NPA_BENCH_REF`) and installs it editable —
   npabench must run from a full repo checkout, so do not replace this with a
   plain `pip install git+URL`
3. runs `npm install` for the npabench recorder
4. installs `pm2` (global, or repo-local if the global install lacks permission)
5. creates `.env` from `.env.example` if missing

After installing Docker it adds you to the `docker` group; log out and back in
(or run `newgrp docker`) so the validator can reach the daemon without `sudo`.

Re-running the script is safe; it updates the npabench checkout to the pin.
All validators must run the same npabench version or scores diverge.

## Configure

Edit `.env`:

- `NPA_BT_WALLET_DIR` — your `~/.bittensor` path if not using the default
  wallet root
- `NPA_WALLET` / `NPA_HOTKEY` — the wallet registered above
- `OPENROUTER_API_KEY` (or `CHUTES_API_KEY`) — **required**; the LLM proxy runs
  every round and needs a provider key

All knobs:

| Var | Default | Meaning |
| --- | --- | --- |
| `NPA_NETWORK` | `finney` | Bittensor network |
| `NPA_API_URL` | `https://api.neverplayalone.ai` | API base URL |
| `NPA_WALLET` | `default` | Wallet name |
| `NPA_HOTKEY` | `default` | Hotkey name |
| `NPA_MISSION_ID` | `resource_gathering` | npabench mission id |
| `NPA_LOOP_POLL_SECONDS` | `12` | Validator loop poll cadence |
| `NPA_WORKSPACE_ROOT` | `/tmp/npa_validator` | Local validator round workspace |
| `NPA_MAX_PARALLEL_AGENTS` | `2` | Parallel npabench agent slots |
| `OPENROUTER_API_KEY` / `CHUTES_API_KEY` | unset | Provider keys — fund one (that provider only) or both (miners pick per request); ≥1 required |
| `NPA_PROXY_PORT` | `8080` | Container-internal port the proxy listens on (not published to the host) |
| _(allowlist)_ | — | Pinned in `docker/proxy/model_pairs.json` (also cross-provider model map) |
| `NPA_PROXY_MAX_TOTAL_SPEND_USD` | `1.0` | Max total proxy spend per miner run |
| _(model prices)_ | — | Pinned per-provider in `docker/proxy/model_pairs.json` |
| `NPA_LOG_LEVEL` | `INFO` | Log level |

## Run

```bash
source .venv/bin/activate
pm2 start validator/main.py
```

`main.py` loads `.env` on startup, so you don't need to `source .env` — just
activate the venv (so `pm2` launches under the right Python) and start it.

The validator runs directly on the host; Docker is used by npabench for the
Minecraft server and the sandboxed miner agents.

## What happens each round

1. The loop polls the backend for the current round windows.
2. When a round enters evaluation, the validator downloads the round roster —
   the same derived manifest every validator sees, containing every admitted
   entry (miner submissions plus the reigning champion's defense entry).
3. Each agent archive is safety-checked, extracted, and evaluated with
   `npabench.evaluate_multiple_agents(...)` using a per-validator seed derived
   from the chain block hash, so runs are deterministic per validator but not
   predictable by miners in advance.
4. Per entry, the validator uploads a `report.json` and a `recording.mcpr`,
   then one raw scoreboard for the round.
5. After the scoreboard deadline, the validator downloads all scoreboards,
   applies stake-weighted averaging, applies the champion-defense margin rule,
   uploads its consensus result, and sets a winner-take-all weight vector on
   chain.

Round workspaces live under `NPA_WORKSPACE_ROOT` (one directory per round)
and can be deleted after a round completes.

## The LLM proxy

Miner sandboxes run on per-slot `--internal` Docker networks with no internet
access. Each round starts an **egress-proxy
container** that npabench attaches to those networks, so the sandbox reaches it
by container DNS (`http://npa-proxy-round-<id>:8080/v1`) and never touches the
host. The proxy is the sandbox's only route to OpenRouter:

- your real `OPENROUTER_API_KEY` lives only inside the proxy container — each
  agent gets a per-session token, injected as `OPENROUTER_BASE_URL`/`OPENAI_BASE_URL`
  plus a matching key (any OpenAI-compatible client picks these up from env)
- requests are restricted to chat/completions-style endpoints and to the models
  pinned in `docker/proxy/model_pairs.json`; the proxy routes each request to the
  provider whose id the agent used (when you fund both keys, the miner picks the
  provider; with one key it falls back to that provider), with a request body cap
- each run has a hard spend cap (`NPA_PROXY_MAX_TOTAL_SPEND_USD`); the cap
  still depletes even if the upstream omits a `usage` field
- the proxy records per-request usage to a shared volume; the summary is folded
  into the uploaded `report.json` as `proxy_usage`, so LLM consumption is
  tracked per miner

Because the proxy is reached only over the internal Docker network, it binds no
host port and is not reachable from the host LAN or the internet.

## Updating

```bash
./scripts/validator_update.sh
```

`validator_update.sh`:

1. fast-forwards the current branch from `origin`
2. re-runs `validator_setup.sh` to refresh Python deps, the pinned
   `vendor/neverplayalone_bench` checkout, and recorder dependencies
3. optionally restarts your PM2 app if you pass `--pm2-name <name>` (or set
   `NPA_PM2_NAME`)

Examples:

```bash
./scripts/validator_update.sh --pm2-name validator
NPA_PM2_NAME=validator ./scripts/validator_update.sh
./scripts/validator_update.sh --no-restart
```

The script refuses to update a dirty worktree by default so it does not
overwrite local changes. Override only when you know what you are doing:

```bash
NPA_ALLOW_DIRTY=1 ./scripts/validator_update.sh
```

## Auto-updating

```bash
source .venv/bin/activate
pm2 start ./scripts/validator_autoupdate.sh --name validator-updater --interpreter bash
```

`validator_autoupdate.sh` is a permanent loop. Every
`NPA_UPDATE_INTERVAL_SECONDS` (default `600`, i.e. 10 minutes) it:

1. checks whether the local subnet repo differs from `origin/main`
2. checks whether `vendor/neverplayalone_bench` differs from its tracked ref
3. only proceeds when the validator is in the **pre-start window**:
   within `NPA_UPDATE_EARLY_WINDOW_BLOCKS` blocks (default `50`) before the
   current submission round's `evaluation_start_block`
4. runs `validator_update.sh --pm2-name <name>`

This is intentionally simple: no validator runtime coordination, just a time
gate based on the next round start block.

Useful env vars:

| Var | Default | Meaning |
| --- | --- | --- |
| `NPA_PM2_NAME` | `validator` | PM2 app restarted after update |
| `NPA_UPDATE_SUBNET_BRANCH` | `main` | Subnet branch to track |
| `NPA_BENCH_REF` | `main` | Bench ref to track |
| `NPA_UPDATE_INTERVAL_SECONDS` | `600` | Drift check cadence |
| `NPA_UPDATE_EARLY_WINDOW_BLOCKS` | `50` | Only restart within this many blocks before the next round start |
