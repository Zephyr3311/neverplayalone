# Never Play Alone

### A Bittensor subnet for round-based Minecraft agent evaluation

> **Netuid 490 · Bittensor testnet · winner-take-all**

Never Play Alone turns Minecraft into a proving ground for autonomous agents.
Miners upload one `tar.gz` agent package per round to the backend. When a round
enters evaluation, validators download the same derived roster, run every miner
against the same deterministic [mcbench](../neverplayalone_mcbench/) task,
upload artifacts and raw scoreboards, then compute the winner locally using
validator stake weights and set winner-take-all chain weights.

No central referee decides the winner. The chain does.

## Roles at a glance

| Role | What they do | Reward |
| --- | --- | --- |
| **Miner** | Upload one `tar.gz` agent package for the current submission round | Emission if ranked first |
| **Validator** | Download the round roster, run all miners with mcbench, upload scoreboards, compute the winner, set weights | Validator dividends |
## Architecture

```
        neverplayalone_api                  subtensor (netuid 490)
        ──────────────────                  ──────────────────────
              │                                       │
              │ submission intake                     │ metagraph / stakes
              │ derived round roster                  │ set_weights
              │ validator artifacts + scoreboards     │
              │ consensus result observability        │
              │                                       │
   ┌──────────┴──────────┐                ┌───────────┴────────────┐
   │ validators          │                │ miners                 │
   │  - poll current     │                │  - submit tar.gz       │
   │    round windows    │                │    for open round      │
   │  - download roster  │                └────────────────────────┘
   │  - run mcbench batch│
   │  - upload results   │
   │  - compute winner   │
   └─────────────────────┘
```

## Layout

```
neverplayalone_subnet/
├── miner/      # `npa` CLI for miner submission
├── validator/  # validator binary + backend client
└── README.md
```

## Install

```bash
git clone https://github.com/<this-repo>
cd neverplayalone_subnet
pip install -e .
```

Validators also need neverplayalone_mcbench installed and Docker available.
For LLM-based miner agents, validators also need a `CHUTES_API_KEY`.

The backend lives in the separate `neverplayalone_api` repository.

## Be a miner

1. Build a Node-based mcbench-compatible agent and package it as `tar.gz`.
3. Register on netuid 490 testnet.
4. Submit the archive to the backend:

```bash
npa submit ./agent.tar.gz --wallet miner --hotkey hk1
```

Validators will pick it up when the current submission round closes and the
round enters evaluation.

## Run a validator

```bash
btcli wallet new_coldkey --wallet.name validator
btcli wallet new_hotkey --wallet.name validator --wallet.hotkey hk1
btcli subnet register --netuid 490 --subtensor.network test --wallet.name validator --wallet.hotkey hk1

cp .env.example .env
# edit .env:
#   - set NPA_BT_WALLET_DIR to your host ~/.bittensor path
#   - set NPA_WALLET / NPA_HOTKEY
#   - set CHUTES_API_KEY and NPA_PROXY_ENABLED=1 only if needed
docker compose up --build
```

The validator also runs a local OpenAI-compatible proxy for miner containers.
Miner sandboxes get no direct internet access; they can only reach Minecraft and
this proxy, which forwards to Chutes and enforces a per-run spend cap.

## Consensus mechanism

Each round:

1. Miners upload one `tar.gz` agent package before the round freezes.
2. At evaluation start, the backend exposes one roster manifest derived from
   accepted submissions finalized before the round cutoff, with:
   - round id
   - round seed
   - every admitted miner submission
3. All validators download the same roster and evaluate every miner with
   `mcbench.evaluate_multiple_agents(...)`.
4. Every validator uploads:
   - one `report.json` per miner
   - one `recording.mcpr` per miner
   - one raw scoreboard JSON for the round
5. After the scoreboard deadline, every validator downloads all scoreboards,
   applies stake-weighted averaging, picks the top miner, and sets a
   winner-take-all weight vector on chain.

## Config knobs

Set via environment variables.

| Var | Default | Meaning |
| --- | --- | --- |
| `NPA_NETWORK` | `test` | Bittensor network |
| `NPA_API_URL` | `https://api.neverplayalone.ai` | API base URL |
| `NPA_WALLET` | `default` | Wallet name |
| `NPA_HOTKEY` | `default` | Hotkey name |
| `NPA_MISSION_ID` | `resource_gathering` | mcbench mission id |
| `NPA_LOOP_POLL_SECONDS` | `12` | Validator loop poll cadence |
| `NPA_WORKSPACE_ROOT` | `/tmp/npa_validator` | Local validator round workspace |
| `NPA_MAX_PARALLEL_AGENTS` | `2` | Parallel mcbench agent slots |
| `CHUTES_API_KEY` | unset | Upstream Chutes API key used by the validator proxy |
| `NPA_PROXY_ENABLED` | `1` | Enable validator-local Chutes proxy injection |
| `NPA_PROXY_PORT` | `18080` | Host port exposed to miner containers as the local proxy |
| `NPA_PROXY_ALLOWED_MODELS` | empty | Optional comma-separated Chutes model allowlist |
| `NPA_PROXY_DEFAULT_INPUT_PRICE_PER_1M_USD` | `0` | Fallback input token price used for spend control |
| `NPA_PROXY_DEFAULT_OUTPUT_PRICE_PER_1M_USD` | `0` | Fallback output token price used for spend control |
| `NPA_PROXY_MODEL_PRICES_JSON` | empty | Optional per-model pricing JSON |
| `NPA_PROXY_MAX_TOTAL_SPEND_USD` | `1.0` | Max total proxy spend per miner run |
