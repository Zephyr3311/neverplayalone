# Validator Guide

Validators run every miner's agent against the same deterministic
[npabench](https://github.com/neverplayalone/neverplayalone_bench) mission,
upload the resulting artifacts and scoreboards, then compute the round winner
from all validators' scoreboards and set winner-take-all weights on chain.
See the [README](../README.md#incentive-mechanism) for the full mechanism.

## Requirements

- Linux host with Python 3.10+, git, and Docker (npabench launches the
  Minecraft server and miner sandboxes in containers)
- Node.js + npm (the npabench recorder is a Node tool)
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

1. creates `.venv` and installs the subnet package
2. clones npabench into `vendor/neverplayalone_bench` at the pinned
   `BENCH_REF` (override with `NPA_BENCH_REF`) and installs it editable —
   npabench must run from a full repo checkout, so do not replace this with a
   plain `pip install git+URL`
3. runs `npm install` for the npabench recorder
4. creates `.env` from `.env.example` if missing

Re-running the script is safe; it updates the npabench checkout to the pin.
All validators must run the same npabench version or scores diverge.

## Configure

Edit `.env`:

- `NPA_BT_WALLET_DIR` — your `~/.bittensor` path if not using the default
  wallet root
- `NPA_WALLET` / `NPA_HOTKEY` — the wallet registered above
- `OPENROUTER_API_KEY` and `NPA_PROXY_ENABLED=1` — only when miners need LLM
  access through the proxy

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
| `OPENROUTER_API_KEY` | unset | OpenRouter API key used by the validator proxy |
| `NPA_OPENROUTER_BASE_URL` | `https://openrouter.ai/api/v1` | OpenRouter API base URL |
| `NPA_PROXY_ENABLED` | `1` | Run the per-round egress proxy container |
| `NPA_PROXY_PORT` | `8080` | Container-internal port the proxy listens on (not published to the host) |
| `NPA_PROXY_ALLOWED_MODELS` | empty | Optional comma-separated model allowlist |
| `NPA_PROXY_DEFAULT_INPUT_PRICE_PER_1M_USD` | `0` | Fallback input token price used for spend control |
| `NPA_PROXY_DEFAULT_OUTPUT_PRICE_PER_1M_USD` | `0` | Fallback output token price used for spend control |
| `NPA_PROXY_MODEL_PRICES_JSON` | empty | Optional per-model pricing JSON |
| `NPA_PROXY_MAX_TOTAL_SPEND_USD` | `1.0` | Max total proxy spend per miner run |
| `NPA_LOG_LEVEL` | `INFO` | Log level |

## Run

```bash
set -a; source .env; set +a
.venv/bin/npa-validator
```

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
access. When `NPA_PROXY_ENABLED=1`, each round starts an **egress-proxy
container** that npabench attaches to those networks, so the sandbox reaches it
by container DNS (`http://npa-proxy-round-<id>:8080/v1`) and never touches the
host. The proxy is the sandbox's only route to OpenRouter:

- your real `OPENROUTER_API_KEY` lives only inside the proxy container — each
  agent gets a per-session token, injected as `OPENROUTER_BASE_URL`/`OPENAI_BASE_URL`
  plus a matching key (any OpenAI-compatible client picks these up from env)
- requests are restricted to chat/completions-style endpoints, optionally to
  an allowlisted model set, with a request body size cap
- each run has a hard spend cap (`NPA_PROXY_MAX_TOTAL_SPEND_USD`); the cap
  still depletes even if the upstream omits a `usage` field
- the proxy records per-request usage to a shared volume; the summary is folded
  into the uploaded `report.json` as `proxy_usage`, so LLM consumption is
  tracked per miner

Because the proxy is reached only over the internal Docker network, it binds no
host port and is not reachable from the host LAN or the internet.

## Updating

```bash
git pull
./scripts/validator_setup.sh   # refreshes deps and the pinned npabench checkout
```
