# Subnet 98 — "Never Play Alone" (NPA)
## Full miner-oriented analysis from source code, harness, live API, and on-chain state

**Analysis date:** 2026-08-18
**Repo analysed:** `neverplayalone_subnet` @ `643fe96` (branch `main`)
**External harness analysed:** `github.com/neverplayalone/neverplayalone_bench` @ `a15b4e9`
**Backend:** `https://api.neverplayalone.ai` (closed source — probed live, not read)
**Chain:** finney, netuid **98**, queried live at block ≈ 8,874,800

### How to read this report
Everything below is grounded in code I actually read, or in live API / chain
responses I actually made. Where the answer depends on the closed-source backend
(`neverplayalone_api`), I say so explicitly and mark it **UNVERIFIABLE**. Where I
am inferring, I say **INFERRED**. Prices/emissions are a snapshot and will move.

---

# 1. Subnet Purpose

## What it is designed to do

Never Play Alone is a **round-based agent competition**, not a request/response
inference subnet. Miners write autonomous **Minecraft agents in Node.js**, package
them as a `tar.gz`, and upload one per round. Validators run *every* submitted
agent under identical sandboxed conditions against the same procedurally generated
Minecraft mission, score the outcome, and put a **single winner** on chain.

- `README.md` — "A Bittensor subnet for round-based Minecraft agent evaluation… winner-take-all."
- `shared/chain.py:9` — `NETUID = int(os.environ.get("NPA_NETUID", "98"))`

## Problem it solves

Producing embodied, low-latency, *open-ended* game agents that can survive and
complete objectives in a live Minecraft world, with the stated commercial goal of
selling "AI companions" to Minecraft server operators (`README.md`, "Why Minecraft?").
Emission funds the R&D; the best agent becomes the product.

## What miners produce

A **sealed agent package**: a `.tar.gz` of a directory containing `index.js`
(Node entrypoint) plus any vendored code. Not model weights, not inference
responses, not files served over the network. `docs/miner.md`, "Build an agent".

## What validators check

They *execute* the agent. Per entry, per task, a validator:
1. downloads the archive from the backend roster,
2. safety-checks and extracts it (`validator/round_evaluation.py:59` `_safe_extract_tar_gz`),
3. runs `node index.js` inside a locked-down Docker container attached to an
   `--internal` (no-internet) network with a private Minecraft server
   (`npabench/agents/sandboxed_agent.py:86-112`, `npabench/evaluation/reference_world.py:176`),
4. reads the agent's **final in-game inventory and position over RCON**
   (`npabench/missions/resource_gathering/final_state.py:16-41`),
5. scores it, uploads `report.json` + `recording.mcpr`, and a scoreboard.

The score is derived from **game state**, not from anything the agent claims.
That is the single most important structural property of this subnet.

## Who uses the final result

- The chain: the winner UID receives 100 % of miner emission.
- The subnet operators: the winning agent is intended to power the commercial
  "AI companion deployment" product (`README.md`, "product thesis").
- Miners: winning agents are open source, so the next round's challengers build
  on them (`README.md`, "Fully open-source competition").

## Complete process

```
                                   ONE ROUND = 1 CALENDAR DAY (7,200 blocks)
 ┌────────────────────────── SUBMISSION WINDOW (7,200 blocks ≈ 24 h) ──────────────────────────┐
 │ miner: npacli submit ./agent.tar.gz                                                          │
 │   → POST /miner/submissions/slot  (hotkey-signed)   shared/api_client.py:79                  │
 │   → PUT  <presigned upload_url>   (raw bytes)       shared/api_client.py:85                  │
 │   → POST /miner/submissions/finalize (hotkey-signed) shared/api_client.py:88                 │
 └──────────────────────────────────────────────────────────────────────────────────────────────┘
                                          │ freeze at evaluation_start_block
                                          ▼
   backend derives ONE roster = all accepted entries + reigning champion_defense entry
                                          │
 ┌───────────────────── EVALUATION WINDOW (6,480 blocks ≈ 21.6 h) ─────────────────────┐
 │ every validator, independently:                                                      │
 │   GET /validator/rounds/<id>/roster   (signed)                                       │
 │   for task in range(TASKS_PER_ROUND):        validator/round_evaluation.py:179       │
 │       seed = sha256(mission:round:CURRENT_BLOCK_HASH:validator_hotkey)   :118        │
 │       npabench.evaluate_multiple_agents(all entries, same seed)          :198        │
 │       per entry: upload report.json + recording.mcpr                     :243-244    │
 │   score(entry) = mean(report.score over tasks)                           :278        │
 │   POST /validator/scoreboards  (signed)                                  :303        │
 └──────────────────────────────────────────────────────────────────────────────────────┘
                                          │ scoreboard_deadline_block
                                          ▼
 ┌───────────────── CONSENSUS + WEIGHTS (720 blocks ≈ 2.4 h) ─────────────────┐
 │ every validator, independently:                                             │
 │   GET  /validator/rounds/<id>/scoreboards                                   │
 │   score(e) = Σ stake_v·score_v(e) / Σ stake_v   validator/loop.py:51-93     │
 │   drop banned hotkeys                          validator/loop.py:126-154   │
 │   winner = champion unless challenger > champion + margin   loop.py:96-112 │
 │   POST /validator/consensus-results                                         │
 │   subtensor.set_weights([winner]=1.0)          shared/chain.py:164-205     │
 └─────────────────────────────────────────────────────────────────────────────┘
                                          │ Yuma consensus, every tempo (360 blocks)
                                          ▼
                     winner UID incentive = 1.0 → 41 % of subnet alpha emission
```

**Live confirmation of winner-take-all** (queried on chain, netuid 98):
`Incentive` vector has exactly **one** non-zero entry — UID 236 at 65535/65535.
`Consensus` likewise. Every validator agrees on one UID.

---

# 2. Miner Requirements

## Wallet, registration, chain

| Item | Value | Source |
| --- | --- | --- |
| Netuid | **98** (finney mainnet) | `shared/chain.py:9-10` |
| Coldkey + hotkey | Required. Standard `btcli` wallet | `docs/miner.md` |
| Registration | Required — `chain.hotkey_uid()` raises `ValueError` if the hotkey is not in the metagraph, so `npacli submit` fails hard without it | `miner/cli.py:59`, `shared/chain.py:101-106` |
| Registration cost (burn) | **0.1 α** (`Burn = 100,000,000 rao`), sitting exactly at `MinBurn` | live chain query |
| Burn in TAO / USD | ≈ **0.000292 TAO ≈ $0.06** at α = 0.0029174 TAO, TAO = $190.71 | live chain + CoinGecko |
| PoW registration | **Disabled** — `Difficulty = 18446744073709551615` (u64::MAX). Burn registration only | live chain query |
| Subnet occupancy | **256 / 256 UIDs — FULL.** `SubnetworkN = MaxAllowedUids = 256`. Registering **deregisters** the lowest-pruning-score neuron | live chain query |
| Immunity period | 5,000 blocks ≈ 16.7 h | live chain query |
| Stake requirement for miners | **None in code.** No stake check anywhere in the miner path | grep of repo |
| Minimum TAO balance | Only enough to cover burn + tx fee. Practically < 0.01 TAO | — |

> **Caveat on the burn figure.** In dTAO, `Burn[netuid]` is denominated in the
> subnet's own alpha token. I am confident but not certain of the denomination.
> If it were TAO, registration would be 0.1 TAO ≈ $19. **Run
> `btcli subnet burn-cost --netuid 98` before registering** — that is the
> authoritative number.

> **This matters more than the price:** the subnet is full and there is no
> immunity for a non-performing miner beyond 5,000 blocks. Since only the champion
> earns anything, every non-champion UID has near-zero incentive and is a
> deregistration candidate. Expect to re-register.

## Hardware — for *running* a miner

A miner in this subnet is **`npacli submit` executed once per day**. There is no
server, no daemon, no inference.

| Resource | Requirement to submit |
| --- | --- |
| CPU | Anything that runs Python 3.10+. 1 core |
| RAM | ~200 MB |
| Storage | ~500 MB (venv + bittensor SDK) + your archive |
| GPU | **Not used at all** |
| Network | Enough to PUT your archive once/day |
| Uptime | **Zero.** The machine can be off between submissions |
| Public IP | **Not required** |
| Open ports | **None** |
| OS | Linux assumed (`scripts/miner_setup.sh` is bash); macOS/WSL fine |
| Python | **3.10+** (`pyproject.toml:10`); `docs/miner.md` says 3.10+ |
| Docker | **Not required to submit** (`scripts/miner_setup.sh` header: "Miners do not need npabench or Docker") |
| API keys | **None required to submit** |

## Hardware — for *developing* a competitive agent

This is where the real requirement lives. To test locally you must run npabench,
which spins a Paper Minecraft server container **plus** a sandboxed agent container
per slot.

| Resource | Recommended for local iteration |
| --- | --- |
| CPU | 8 vCPU (4 works at `--max-parallel 1`) |
| RAM | 16 GB (mission config asks the MC server for `memory: 2G` per slot; agent sandbox capped at 1 GB) |
| Storage | 50–100 GB SSD (Docker images, worlds, recordings) |
| GPU | **Not required.** Nothing in the agent runtime can use one — the sandbox is a `node:22-bookworm-slim` container with 1 GB RAM and no device passthrough |
| Node.js | 20+ (agent runs under Node 22 inside the sandbox) |
| Docker | Required for local testing |
| LLM key | Your **own** OpenRouter and/or Chutes key, to simulate the validator proxy |

Sources: `docs/validator.md` "Computing Requirements", `npabench/config.py:43-44`
(`DEFAULT_SANDBOX_MEMORY = "1g"`, `DEFAULT_SANDBOX_PIDS_LIMIT = 256`),
`npabench/missions/resource_gathering/configs/default.yaml` (`memory: 2G`).

## GPU verdict

> ## **A GPU is NOT REQUIRED — and cannot be used.**
>
> Your agent runs inside a validator-controlled container with `--cap-drop ALL`,
> `--read-only`, 1 GB RAM, 256 PIDs, and **no internet**
> (`npabench/agents/sandboxed_agent.py:103-108`). There is no GPU device, and no
> way to reach one. All model inference happens through the validator's HTTP
> proxy to OpenRouter/Chutes, capped at **$0.01 per task**
> (`validator/config.py:41`). A GPU is at best *optional* for offline R&D
> (e.g. training a policy you then distil into JS heuristics), and even then it
> is not on the critical path.

---

# 3. Miner Setup

### 1–2. Install repo + dependencies

```bash
git clone https://github.com/neverplayalone/neverplayalone_subnet
cd neverplayalone_subnet
./scripts/miner_setup.sh          # creates .venv, pip install -e .
```
`scripts/miner_setup.sh` — creates `.venv`, installs the package, exposes `npacli`
(`pyproject.toml:29` `npacli = "miner.cli:app"`).

### 3–4. Wallet + registration

```bash
btcli wallet new_coldkey --wallet.name miner
btcli wallet new_hotkey  --wallet.name miner --wallet.hotkey hk1
btcli subnet burn-cost   --netuid 98 --subtensor.network finney   # check price first
btcli subnet register    --netuid 98 --subtensor.network finney \
                         --wallet.name miner --wallet.hotkey hk1
```

### 5. Environment variables (miner side)

There is **no `.env` for miners**. `.env.example` is validator-only. Miner defaults
are hardcoded in `miner/config.py`:

```python
API_URL     = "https://api.neverplayalone.ai"
NPA_NETWORK = "finney"
```
Overridable knobs actually read on the miner path:

| Var | Effect | Source |
| --- | --- | --- |
| `NPA_NETUID` | Netuid (default 98) | `shared/chain.py:9` |
| `NPA_NETWORK` | Chain network — but note `miner/cli.py:16-20` forces it back to `finney` from `miner/config.py` | `shared/chain.py:10` |
| `NPA_BT_WALLET_DIR` | Non-default wallet root | `shared/chain.py:68` |
| `NPA_API_TIMEOUT_SECONDS` | HTTP timeout, default 180 | `shared/api_client.py:19` |
| `--api <url>` | Per-invocation backend override | `miner/cli.py:50` |

> **Note the quirk:** `_configure_chain_network()` (`miner/cli.py:16-20`) overwrites
> `chain.NETWORK` with `miner/config.py:NPA_NETWORK` = `"finney"`, so setting
> `NPA_NETWORK=test` in your shell **will not** point the CLI at testnet. You would
> have to edit `miner/config.py`.

### 6. Download models or data

**Nothing to download.** No model weights, no datasets, no checkpoints anywhere in
either repo. Your agent gets its "model" via the validator's LLM proxy at runtime.

### 7. Build and package the agent

```bash
# my_agent/index.js is the entrypoint; vendor node_modules if you need
# anything beyond mineflayer + mineflayer-pathfinder
tar -czf agent.tar.gz -C my_agent .
```
Hard constraints, enforced at evaluation time by
`validator/round_evaluation.py:59-69`:
- no absolute paths
- no `..` components
- no symlinks, no hardlinks (`member.issym() or member.islnk()`)
- no device files (`member.isdev()`)

Violating any of these raises `ValueError` and, because `_materialize_agents` has
no per-entry try/except (`validator/round_evaluation.py:72-95`), **aborts that
validator's entire round evaluation** — see §12 for why this is a real
availability concern.

### 8. Test locally (strongly recommended)

```bash
git clone https://github.com/neverplayalone/neverplayalone_bench
cd neverplayalone_bench && pip install -e . && (cd tools/recorder && npm install)
export OPENROUTER_API_KEY=...            # needed: the mission prompt is LLM-generated
export NPABENCH_PROMPT_MODEL=openai/gpt-4.1-mini
npabench run my_agent=/path/to/my_agent --mission resource_gathering --seed 42
```

### 9. Check the round window and your submission quota

```bash
.venv/bin/npacli status
.venv/bin/npacli usage --wallet miner --hotkey hk1
```
`usage` prints `banned`, `use_count/max_uses`, `remaining`, `used_rounds`,
`can_submit` (`miner/cli.py:108-117`). **There is a per-hotkey submission cap**
enforced by the backend. Its value is **UNVERIFIABLE** from this repo.

### 10. Submit

```bash
.venv/bin/npacli submit ./agent.tar.gz --wallet miner --hotkey hk1
```
Prints `submission_id`, `round_id`, `miner_uid`, `status`, and on acceptance
`sha256` + `size_bytes` (`miner/cli.py:75-83`).

### 11. "Connect to validators" / "Receive tasks" / "Receive rewards"

- **Connect to validators:** does not happen. There is no miner↔validator network
  path in either direction. Everything goes through the backend and the chain.
- **Receive tasks:** you never receive one. Validators generate the task
  themselves and hand it to your agent process as the `NPABENCH_AGENT_PROMPT`
  environment variable *inside their own container*.
- **Receive rewards:** automatic. Emission accrues as alpha stake to your hotkey
  whenever your UID holds incentive.

---

# 4. Response Submission

| Question | Answer | Evidence |
| --- | --- | --- |
| Run a server? | **No** | no axon/server code anywhere in `miner/` |
| Start a Bittensor Axon? | **No** | grep: `axon` appears nowhere in the repo. Live chain: netuid 98 has **1** `Axons` entry total across 256 UIDs |
| Open a public port? | **No** | — |
| Receive requests via Dendrite? | **No** | grep: no `dendrite`, no `synapse` |
| Upload files? | **Yes** — one `.tar.gz` per round to presigned object storage | `shared/api_client.py:85-86` |
| Return JSON? | Not as a submission. Your *agent* emits JSON trace lines on stdout at runtime | `npabench/evaluation/run_trace.py:45-54` |
| Return model output? | No | — |
| Submit a URL? | No — you upload bytes, the backend issues the URL | `create_submission_slot` → `slot["upload_url"]` |
| Commit a hash on-chain? | **No** | — |
| Submit data on-chain? | **No** | miners issue **zero** extrinsics apart from registration |
| Commit-reveal? | **No.** `CommitRevealWeightsEnabled` is unset on netuid 98 | live chain query |
| Must run continuously? | **No.** One CLI invocation per round | `miner/cli.py:45` |
| Public IP needed? | **No** | — |
| Which port? | **None on the miner side.** Validator-side npabench publishes MC game ports from 25665 and RCON from 25675 (`npabench/config.py:41-42`); the LLM proxy listens on container-internal 8080, never published (`validator/config.py:34-36`) | — |
| Protocol? | **HTTPS/REST to the backend**, with sr25519 request signing. Plus the chain for registration | `shared/api_client.py` |
| Is the response signed by the hotkey? | **The API calls are. The archive bytes are not.** | see below |

### Request signing (`shared/api_client.py:30-40`)

```python
message   = f"{method}\n{path}\n{body}\n{nonce}\n{timestamp}"
signature = self.wallet.hotkey.sign(message.encode()).hex()
headers   = {"X-Hotkey", "X-Nonce", "X-Signature", "X-Timestamp"}
```
Signed: `POST /miner/submissions/slot`, `POST /miner/submissions/finalize`,
`GET /miner/hotkeys/usage`. Unsigned: `GET /miner/rounds/current`,
`GET /health`, and **the `PUT` of the archive itself** (`_put_bytes`,
`shared/api_client.py:60-68` — no headers passed).

> **Finding.** The tarball is uploaded to a presigned URL with no hotkey
> signature over its content. Integrity relies entirely on the backend computing
> and returning `sha256` at finalize. Anyone holding a leaked presigned URL before
> finalize could substitute content. Whether the backend binds the object hash at
> finalize is **UNVERIFIABLE** here. A miner-side signature over the archive
> digest, checked at finalize, would close this.

---

# 5. Initial Task Data

## What the agent actually receives

Not a network payload — **five environment variables**, injected into your
container by the validator (`npabench/agents/sandboxed_agent.py:92-98`):

| Var | Meaning | Concrete value |
| --- | --- | --- |
| `NPABENCH_HOST` | Minecraft server container hostname | e.g. `npabench-eval-0` |
| `NPABENCH_PORT` | Game port | `25565` (container-internal) |
| `NPABENCH_AGENT_USERNAME` | Username you must join with | `npabench_agent` |
| `NPABENCH_AGENT_PROMPT` | The mission prompt, natural language | see below |
| `NPABENCH_TIMEOUT_SECONDS` | Wall-clock budget | **600** (`configs/default.yaml`) |

Plus, when the validator's proxy is on (it always is in production):
`OPENAI_BASE_URL`, `OPENAI_API_KEY`, `OPENROUTER_BASE_URL`, `OPENROUTER_API_KEY`,
`NPA_PROXY_SESSION_TOKEN` (`validator/proxy.py:146-152`).

Note: caller-supplied env **cannot** override the `NPABENCH_*` core vars —
`env = {**(self.spec.env or {}), **core_env}` (`sandboxed_agent.py:101`).

## Data source and task generation

`npabench/missions/resource_gathering/task.py:18-62`:

```python
rng = random.Random(seed)
essentials = ("logs", "cobblestone", "raw_meat")            # always these 3
optionals  = rng.sample(sorted(menu - essentials), 2)       # 2 of 22 others
target_count = rng.randint(*menu_entry.target_range)        # per resource
minecraft_seed = rng.getrandbits(64)                        # the world itself
```

The 22-entry optional catalogue lives in `configs/default.yaml` (sand, dirt,
sugar_cane, wool, gravel, clay, leather, feather, apple, pumpkin, melon,
sweet_berries, kelp, flowers, ferns, dead_bush, leaves, moss_carpet, vines,
tall_grass, saplings). So there are `C(22,2) = 231` optional-pair combinations ×
target-count randomisation × 2^64 world seeds.

The English prompt is then **generated by an LLM** at evaluation time
(`prompting.py:80-146`, default `openai/gpt-4.1-mini`, temp 0, 180 max tokens),
so even the phrasing varies. Schema version `resource_gathering.v3`.

## Determinism and fairness

| Question | Answer | Evidence |
| --- | --- | --- |
| Do all miners get the same task? | **Yes, within one validator's task instance.** `evaluate_multiple_agents` generates one task and runs every roster entry against it, each on its own copy of one pre-built reference world | `npabench/evaluation/evaluate.py:156-186` |
| Do all *validators* get the same task? | **No — deliberately.** Each validator derives its own seed | `validator/round_evaluation.py:118-121` |
| Are tasks random? | Yes, and unpredictable in advance: `seed = sha256(f"{mission}:{round}:{chain_block_hash}:{validator_hotkey}")` — the block hash isn't known until evaluation starts | same |
| Hidden test data? | Effectively yes — you cannot know your seed or world before the run | same |
| External APIs allowed? | **Only the validator's LLM proxy.** The sandbox network is `docker network create --internal` — no route off-host | `npabench/evaluation/reference_world.py:176` |
| Cached results allowed? | Useless. Every run is a fresh world with a fresh objective. `/tmp` is a 64 MB tmpfs wiped per container; `/agent` is read-only | `sandboxed_agent.py:105-107` |
| Internet access? | **No** | `--internal` network |
| Pretrained models allowed? | Only remote ones via the proxy, from the pinned allowlist. Shipping local weights is theoretically possible but useless: 1 GB RAM, read-only FS, Node-only runtime, 600 s budget | `docker/proxy/model_pairs.json`, `npabench/config.py:43` |
| Allowed models | `deepseek/deepseek-chat-v3-0324`, `deepseek/deepseek-r1`, `qwen/qwen3-32b`, `openai/gpt-oss-120b` (either provider's id form) | `docker/proxy/model_pairs.json` |
| Streaming | **Rejected** (HTTP 400 `stream_unsupported`) | `docker/proxy/server.py:261-262` |
| Endpoints | Only `POST /chat/completions`, `POST /responses`, `GET /models` | `docker/proxy/server.py:217-228` |
| Request body cap | 10 MB | `docker/proxy/server.py:250` |
| Spend cap | **$0.01 per miner per task** (5 tasks → $0.05/round/validator) | `validator/config.py:41`, `round_evaluation.py:160-167` |

## Example miner input

```bash
NPABENCH_HOST=npabench-eval-2
NPABENCH_PORT=25565
NPABENCH_AGENT_USERNAME=npabench_agent
NPABENCH_TIMEOUT_SECONDS=600
NPABENCH_AGENT_PROMPT="Bring back 27 logs, 24 cobblestone, 11 raw meat, 18 kelp, and 7 flowers. Keep everything in your inventory and return to within 20 blocks of spawn when you finish."
OPENAI_BASE_URL=http://npa-proxy-round-2026-08-18:8080/v1
OPENAI_API_KEY=3f9a1c...            # opaque per-(entry,task) session token
OPENROUTER_BASE_URL=http://npa-proxy-round-2026-08-18:8080/v1
OPENROUTER_API_KEY=3f9a1c...
```

Starting inventory given to you *after* you emit `ready`
(`missions/resource_gathering/environment.py:28-44`, `configs/default.yaml:12-25`):
netherite pickaxe/axe/shovel, shears, 64 cooked_beef, 64 torches (offhand),
survival mode, saturation III for 3 s, `deop`.

World rules: `difficulty: peaceful`, `doMobSpawning false`, `keep_inventory false`,
`worldborder 10000`, `time set 0` (dawn), `spawnRadius 0`, spawn point pinned to
your join position.

---

# 6. Perfect Miner Response

Your "response" is **the state of the game world at t=600 s**, read over RCON.
There is no document to format correctly. But there *is* a protocol.

## Required behaviour (the real "response fields")

| Requirement | Type | Why it matters | Source |
| --- | --- | --- | --- |
| Connect to `NPABENCH_HOST:NPABENCH_PORT` as `NPABENCH_AGENT_USERNAME` | — | If you never spawn, `agent_ready_at` stays `None` → `status="agent_never_spawned"`, `spawned=False` | `scoring.py:76-77` |
| Emit `{"kind":"ready"}` on stdout, one JSON object per line | JSON line | **Gates the entire mission setup.** `_setup_agent_after_ready` only runs on a `ready` event: it sets the spawn point, gives the starting kit, sets survival mode, starts the movement monitor. Without it you get nothing and `spawned=False` | `single_runner.py:320-332`, `353-372` |
| Gather the 5 targets into **your own inventory** | in-game state | Counted via `clear <user> minecraft:<item> 0` — inventory only. Chests/ender chest do not count | `rcon_helpers.py:21-24` |
| End within **10 blocks horizontally of world spawn** | in-game state | ×1.00 multiplier. 30 blocks → ×0.90, 100 → ×0.75 | `configs/default.yaml:27-36` |
| Emit `{"kind":"done"}` before the deadline | JSON line | Two effects: the runner **breaks out of the event loop immediately**, and it is a precondition for `time_efficiency` | `single_runner.py:333-334`, `scoring.py:64-71` |
| Stay alive | in-game state | `keep_inventory false` — dying drops everything you gathered | `environment.py:18` |
| Move at physically plausible speed | in-game state | ≥2 windows over 9 b/s horizontal or 6 b/s upward (latency-padded) ⇒ **score forced to 0.0**, `status="movement_violation"` | `movement_monitor.py:14-32`, `single_runner.py:169-179` |

Any line on stdout that is not valid JSON with a `kind` key is swallowed as an
`info` event — harmless but noisy (`run_trace.py:45-54`).

## The exact score formula

`npabench/missions/resource_gathering/scoring.py:16-100`:

```
                 base_score = Σ_r  points_r · min(count_r, target_r) / target_r

                     points: 25.0 for each of the 3 essentials (logs, cobblestone, raw_meat)
                             12.5 for each of the 2 optionals
                     ⇒ max_resource_score = 3·25 + 2·12.5 = 100.0        [task.py:14-15]

                 multiplier = f(horizontal distance from world spawn)
                     ≤10 → 1.00   ≤30 → 0.90   ≤100 → 0.75   ≤250 → 0.60
                     ≤500 → 0.50  ≤1000 → 0.40 ≤2000 → 0.30  else/None → 0.20

                      total = base_score × multiplier                    ∈ [0, 100]

            time_efficiency = max(0, (600 − elapsed) / 600)  if (not timed_out AND a "done" event fired)
                              else 0                                      ∈ [0, 1]

              ranking_score = total + time_efficiency × 1e-3              ← THIS is report.score
```

Confirmed at `single_runner.py:201`:
`score=float(raw_report.get("ranking_score", raw_report.get("score", 0.0)))`

Hard overrides to **0.0**: movement violation (`single_runner.py:169-179`) and any
evaluation exception (`single_runner.py:182-190`).

**Deaths and health are recorded but do not appear in the formula.** `deaths` and
`alive` are reported only. The penalty for dying is indirect: you drop your loot.

## Size, latency, metadata, signatures, confidence, proofs

| Asked about | Reality |
| --- | --- |
| Response size limits | Archive size limit: **UNVERIFIABLE** (backend-enforced; `finalize` returns `size_bytes`). Container: 1 GB RAM, 256 PIDs, `/tmp` 64 MB, `/agent` read-only |
| Accuracy requirement | None absolute. Relative only: you must beat the champion by the margin |
| Latency requirement | Hard 600 s wall clock. Finishing early buys at most **0.001 points** — a pure tie-breaker, never a strategy |
| Metadata | Nothing you submit. The report is written by the validator |
| Signatures | On API calls only |
| Confidence values | Not a concept here |
| Proofs / evidence | The validator produces them: `report.json`, `raw_report.json`, `trace.json`, `movement_monitor.json`, `proxy_usage.json`, and a `recording.mcpr` ReplayMod file uploaded per entry per task (`round_evaluation.py:234-244`) |

## Example of a valid agent response stream

```jsonl
{"kind":"ready","data":{"prompt":"Bring back 27 logs, ..."},"t":1755500001.12}
{"kind":"info","data":{"msg":"spawned","spawnPos":{"x":112,"y":68,"z":-340}}}
{"kind":"action","data":{"action":"gather_start","target":"logs","count":27}}
{"kind":"action","data":{"action":"dig","block":"oak_log"}}
{"kind":"action","data":{"action":"gather_done","target":"logs","have":27}}
{"kind":"action","data":{"action":"return_to_spawn","pos":{"x":112,"y":68,"z":-340}}}
{"kind":"done","data":{"msg":"plan complete","inventory":{"oak_log":27,"cobblestone":24,"beef":11,"kelp":18,"poppy":7}}}
```

Resulting scoreboard row (`round_evaluation.py:280-292`):
```json
{
  "entry_id": "sub_01J...", "entry_kind": "submission",
  "miner_uid": 141, "miner_hotkey": "5F...",
  "submission_id": "sub_01J...", "source_round_id": null,
  "score": 100.0004, "status": "ok",
  "report_s3_key": "...", "recording_s3_key": "..."
}
```

## Response taxonomy

| Class | Definition | Typical score |
| --- | --- | --- |
| **Invalid** | Archive rejected pre-run (symlink/`..`/device entry), or no `index.js`, or the process dies instantly. Never emits `ready` | `status="error"` or `"agent_never_spawned"`; **score 0.0** |
| **Timed out** | Ran the full 600 s without emitting `done`. **Not zeroed** — you keep whatever you gathered, you just forfeit `time_efficiency` (worth ≤0.001) | `status="timeout"`, score = whatever `total` you earned |
| **Valid** | Spawns, gathers something, ends somewhere | e.g. 45 logs-only at 300 blocks out → `(25 + 0 + 0 + 0 + 0) × 0.60 = 15.0` |
| **Partial** | Hits essentials, misses optionals or distance | e.g. all 3 essentials, 0 optionals, 25 blocks out → `75 × 0.90 = 67.5` |
| **Competitive** | Everything, close to spawn, but not clearly above the champion | ~92–99 |
| **Perfect** | All 5 targets at target count, ends ≤10 blocks from spawn, emits `done` at ~t=400 s | `100 × 1.00 + (200/600)×0.001 = ` **100.00033** |
| **Cheating** | Impossible movement detected twice | **forced 0.0**, `status="movement_violation"` |

> **The hardest single element is `raw_meat` (25 points, 25 % of max).** Difficulty
> is `peaceful` and `doMobSpawning` is `false`, so no animals spawn during the run —
> you can only kill the passive mobs that generated with the world, and only within
> the radius you can reach and return from in 600 s. The 64 `cooked_beef` in your
> starting kit does **not** count (`beef` ≠ `cooked_beef` in the counted item list).

---

# 7. Validator Scoring

## How validators select miners

They don't. Every entry on the frozen roster is evaluated by every validator that
started in time. No sampling, no querying, no per-miner selection.

## Which validators participate

A validator **skips the whole round** if it starts after
`evaluation_start + 0.5 × (round_end − evaluation_start)`
(`validator/loop.py:247-250`, `EVALUATION_START_CUTOFF_RATIO = 0.5`). Late starters
only do consensus/weights.

## How often miners are evaluated

Per round (≈ 1 calendar day), each validator runs each entry
`TASKS_PER_ROUND` times. **Live round schedule** (`GET /validator/rounds/current`,
2026-08-18):

| Boundary | Block | Δ | Wall clock @12 s |
| --- | --- | --- | --- |
| `submission_open_block` | 8,867,886 | — | — |
| `evaluation_start_block` | 8,875,086 | +7,200 | 24 h |
| `scoreboard_deadline_block` | 8,881,566 | +6,480 | 21.6 h |
| `round_end_block` | 8,882,286 | +720 | 2.4 h |

Rounds are **pipelined**: while `2026-08-18` takes submissions, `2026-08-17`
is being evaluated. One winner is decided per day.

> ### ⚠ Configuration inconsistency — real, and it affects scores
> `validator/config.py:31` sets `TASKS_PER_ROUND = 5` (commit `643fe96`), but both
> `.env.example` and `docs/validator.md` say `NPA_TASKS_PER_ROUND=3`. Because
> `validator/main.py:20` loads `.env` with `os.environ.setdefault`, a validator that
> ran `validator_setup.sh` (which copies `.env.example`) evaluates **3** tasks while
> one without a `.env` evaluates **5**. Different sample sizes → different variance
> in the mean, and a different total LLM budget for your agent ($0.03 vs $0.05).
> The code comment at `validator/config.py:29-30` explicitly says "Keep this the
> same on every validator" — it currently is not.

## How responses are checked

By re-reading world state over RCON, never by trusting the agent
(`final_state.py:16-41`). The agent's stdout only supplies protocol signals
(`ready`, `done`) and a trace for humans.

## Invalid responses and timeouts

- A crash inside one agent's run is caught and turned into a **zero-score report**
  so the batch continues (`single_runner.py:149-157, 182-190`).
- A timeout is **not** zeroed — `status="timeout"`, score preserved
  (`_report_status`, `single_runner.py:440-452`).
- A malformed **archive** is *not* isolated: `_materialize_agents` has no
  try/except, so one bad tarball aborts that validator's whole round
  (`round_evaluation.py:72-95`). An unmerged branch `origin/feat/upload-retry`
  addresses the related upload-failure case; it is **not on `main`**.
- Per-entry status aggregation across tasks: `ok` if **any** task was ok,
  otherwise the first non-ok status (`round_evaluation.py:124-129`).

## The full scoring formula, end to end

```
LEVEL 1 — per task, per validator                    [npabench scoring.py]
   report.score = base_score × distance_multiplier + time_efficiency × 1e-3
                  (forced 0.0 on movement_violation or evaluation error)

LEVEL 2 — per entry, per validator                   [round_evaluation.py:276-292]
   scoreboard_score(e) = (1/T) · Σ_{t=1..T} report.score(e, t)        T = TASKS_PER_ROUND
   status(e)           = "ok" if any task ok else first non-ok

LEVEL 3 — consensus, computed independently by every validator   [loop.py:51-93]
   stake_v  = metagraph.S[v] at the round's freeze block
              (falls back to the validator's self-reported stake_weight if the
               freeze-block lookup fails — loop.py:61-63,71)
   final(e) = Σ_v stake_v · scoreboard_score_v(e)  /  Σ_v stake_v
              (validators with stake ≤ 0 are skipped entirely — loop.py:72-73)

LEVEL 4 — eligibility filter                                     [loop.py:126-154]
   drop every entry whose miner_hotkey the backend reports banned

LEVEL 5 — champion defense                                       [loop.py:96-112]
   ranked = sort by (−final, miner_uid, entry_id)
   if no champion_defense entry:            winner = ranked[0]
   elif best_challenger > champion + margin: winner = best_challenger
   else:                                     winner = champion        (champion_kept)

LEVEL 6 — on-chain weights                                       [chain.py:133-161]
   w = [0]*n ; w[burn_uid] += burn_rate ; w[winner_uid] += (1 − burn_rate)
   with NPA_BURN_RATE = 0.0 (default, validator/config.py:32):
       w[winner_uid] = 1.0, everything else 0.0
   → process_weights_for_netuid → set_weights(wait_for_inclusion=False)
```

There is **no** speed term, no reliability term, no uptime term, no EMA, no moving
average, no penalty subtraction. The "Final Score = Accuracy + Speed + Reliability
− Penalties" shape does not apply here. The real formula is:

> **`Final = stake-weighted mean over validators of (mean over tasks of (resources × distance)), with a champion-margin hysteresis, then winner-take-all.`**

## How previous scores affect future scores

Only through the **champion defense** mechanism, and it is strong:
- The reigning winner is auto-re-entered by the backend as a `champion_defense`
  entry every round, without resubmitting (`README.md`; roster contains it —
  `loop.py:267-272`).
- A challenger must beat it by more than `champion_margin`. Ties go to the champion
  (`loop.py:110-112`).
- `_round_margin` returns **0.0** if the roster omits `champion_margin`
  (`loop.py:115-123`), which would reduce this to "strictly greater". The live
  margin value is **UNVERIFIABLE** — the roster endpoint requires a validator
  signature.

There is no score decay, no history buffer, no EMA anywhere in the codebase.

## Weight-setting cadence

A dedicated background thread (`_weight_worker`, `loop.py:497`) polls every
`LOOP_POLL_SECONDS = 12` and sets weights **once per `WEIGHT_EPOCH_BLOCKS = 360`**,
measured relative to `evaluation_start_block` (`loop.py:253-259`). 360 blocks
exactly matches the on-chain `Tempo = 360`.

Behaviour by phase (`loop.py:373-494`):
- **Pre-deadline:** weight the *previous* champion each epoch. The epoch that
  contains the deadline is reserved (skipped) for the post-deadline consensus.
- **Post-deadline:** compute consensus, weight the new winner.
- **No eligible winner:** fall back to the last locally-saved winner
  (`npa_last_winner.json`), re-checking eligibility. If nothing is saved, **leave
  weights unchanged** — it does *not* burn (`loop.py:321-370`).
- **API down but chain up:** re-weight the saved winner without an eligibility
  check, throttled to one epoch (`loop.py:514-528`).

## Scoring examples

Assume a task with targets: 27 logs, 24 cobblestone, 11 raw meat, 18 kelp, 7 flowers.

| Scenario | base_score | dist | total | +time | `report.score` | status |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| **Perfect** — all targets, ends 6 blocks from spawn, `done` at t=400 | 100.0 | ×1.00 | 100.0 | +0.00033 | **100.00033** | ok |
| **Accurate but slow** — all targets, ends 8 blocks out, times out at 600 s | 100.0 | ×1.00 | 100.0 | +0 | **100.0** | timeout |
| **Fast but inaccurate** — only 27 logs, `done` at t=120, 5 blocks out | 25.0 | ×1.00 | 25.0 | +0.0008 | **25.0008** | ok |
| **Partial** — 3 essentials full, no optionals, ends 220 blocks away | 75.0 | ×0.60 | 45.0 | +0 | **45.0** | ok/timeout |
| **Wandered off** — everything gathered, ends 1,400 blocks out | 100.0 | ×0.30 | 30.0 | +0 | **30.0** | timeout |
| **Died at t=590** — dropped inventory, respawned at spawn | ~0 | ×1.00 | ~0.0 | +0 | **~0.0** | ok |
| **Timeout, nothing gathered** | 0.0 | ×1.00 | 0.0 | +0 | **0.0** | timeout |
| **Offline / never spawned** | 0.0 | — | 0.0 | +0 | **0.0** | agent_never_spawned |
| **Crashed mid-run** | — | — | — | — | **0.0 (forced)** | error |
| **Teleport cheat** | — | — | — | — | **0.0 (forced)** | movement_violation |

The **round** score is the mean of `TASKS_PER_ROUND` of these, then stake-averaged
across validators. Then only the top one (subject to margin) gets anything.

---

# 8. Rewards

## Measured on-chain economics (netuid 98, block ≈ 8,874,800)

| Metric | Value | Source |
| --- | --- | --- |
| `Tempo` | **360 blocks** ≈ 72 min; 20 tempos/day | live query |
| Block time | ~12 s ⇒ 7,200 blocks/day | Bittensor constant |
| `SubnetTAO` | 5,292.57 TAO | live query |
| `SubnetAlphaIn` | 1,814,085 α | live query |
| **α price** | **0.0029174 TAO** ( `SubnetMovingPrice` decodes to 0.0029160 — consistent) | live query |
| TAO price | $190.71 | CoinGecko, 2026-08-18 |
| **α in USD** | **$0.5564** | derived |
| Total alpha emission | **1.0 α / block** = 360 α / tempo = 7,200 α / day | derived from `Emission` vector |
| Miner share | **41 %** = 0.41 α/block = **147.6 α/tempo** = **2,952 α/day** | measured: UID 236 `Emission` = 147,600,823,655 rao |
| Validator share | 41 % = 147.56 α/tempo (measured sum across UIDs 0, 38, 114, 153, 218) | live `Emission` vector |
| Owner share | 18 % = 64.8 α/tempo (paid outside the `Emission` vector) | derived |
| Miner emission/day | **2,952 α ≈ 8.61 TAO ≈ $1,642** | derived |
| Miner emission/30 d | **88,560 α ≈ 258.4 TAO ≈ $49,270** | derived |

## Distribution across miners: there is none

`Incentive` on netuid 98 has **exactly one** non-zero entry: UID 236 at 65535
(= 1.0). Every other UID is at 0. That is the mechanism working exactly as designed
(`compute_weight_vector`, `shared/chain.py:133-161`, with `BURN_RATE = 0.0`).

| Rank | Reward share | Per block | Per epoch (tempo, 72 min) | Per day | Per 30 days |
| --- | --- | --- | --- | --- | --- |
| **Champion (rank 1)** | **100 %** | 0.41 α ≈ $0.228 | 147.6 α ≈ $82.1 | 2,952 α ≈ 8.61 TAO ≈ **$1,642** | 88,560 α ≈ 258 TAO ≈ **$49,270** |
| Rank 2 (top 0.4 %) | **0 %** | 0 | 0 | **$0** | **$0** |
| Top 5 % (rank ≤ 13) | **0 %** | 0 | 0 | **$0** | **$0** |
| Top 10 % (rank ≤ 26) | **0 %** | 0 | 0 | **$0** | **$0** |
| Average miner | **0 %** | 0 | 0 | **$0** | **$0** |
| Low performer | **0 %** | 0 | 0 | **$0** | **$0**, plus deregistration risk |

### Assumptions behind these numbers
1. `NPA_BURN_RATE = 0.0` on all validators (the shipped default,
   `validator/config.py:32`) — **confirmed live**: UID 0 has zero incentive, so no
   burn share is being routed.
2. You hold the championship for the entire period. Realistically you hold it for
   `k` days out of 30 and earn `k × $1,642`.
3. Alpha price stays at 0.0029174 TAO. It will not — emitted alpha is sell
   pressure, and you are receiving ~2,952 α/day into a pool of 1.81 M α.
   Selling a full day's emission is ~0.16 % of the reserve, so slippage per day is
   small but the cumulative effect is not.
4. Rewards land as **alpha stake on your hotkey**, not liquid TAO. Realising USD
   requires unstaking (α→TAO swap through the subnet pool, with slippage) and then
   selling TAO.
5. Emission accrues per block; the *winner target* changes at most once per
   360-block epoch, and the winner identity changes at most once per day.

## Minimum score needed

There is no absolute threshold. The requirement is purely relative:
```
your_final_score > champion_final_score + champion_margin
```
If no `champion_defense` entry exists (e.g. after a schedule reset), the plain top
score wins (`loop.py:103-105`).

## Validator stake effects

Consensus is stake-weighted (`loop.py:71`). Live `Dividends` (a proxy for effective
stake share on netuid 98):

| UID | Dividends | Share of validator weight |
| --- | ---: | ---: |
| 0 | 36,380 | **55.5 %** |
| 153 | 20,851 | 31.8 % |
| 218 | 8,055 | 12.3 % |
| 38 | 178 | 0.27 % |
| 114 | 69 | 0.11 % |

> **This is the most consequential live fact for a miner.** One validator holds
> ~55 % of the scoring weight; the top two hold ~87 %. A single validator's
> scoreboard essentially determines the round, and any two of the top three
> determine it outright. The README's claim that "no single scoreboard decides a
> round" is *architecturally* true (the code is right) but **not true in practice
> at the current stake distribution**.

---

# 9. Costs

## One-time costs

| Item | Minimum | Recommended | High-performance |
| --- | --- | --- | --- |
| Registration burn | 0.1 α ≈ **$0.06** ¹ | $0.06 | $0.06 (× re-registrations) |
| TAO to buy | ~0.01 TAO ≈ $2 (burn + fees + buffer) | $20 | $100 |
| Hardware | $0 — existing laptop, submit only | Cloud dev box, 8 vCPU / 16 GB / 100 GB NVMe: ~**$80/mo** | 16 vCPU / 64 GB / 500 GB: ~**$300/mo** |
| Setup time | ~1 h | 1–2 days | 1–2 weeks |
| Domain | **$0** — not needed | $0 | $0 |
| Model download | **$0** — no models | $0 | $0 |
| Software licences | **$0** — MIT (`pyproject.toml:11`), Docker CE, Node, Paper MC | $0 | $0 |
| **Total one-time** | **≈ $2** | **≈ $50 + dev time** | **≈ $200 + dev time** |

¹ Verify with `btcli subnet burn-cost --netuid 98`. If the value is TAO-denominated
it is ~$19 instead of $0.06. Either way it is not the binding constraint.

## Cost of one "response" (one round submission)

| Component | Cost to the miner | Note |
| --- | ---: | --- |
| Compute (evaluation) | **$0.00** | Validators run your agent on their hardware |
| LLM / API at evaluation | **$0.00** | Paid by the validator, capped at $0.01/task via `NPA_PROXY_MAX_TOTAL_SPEND_USD` |
| Network | **~$0.00** | One PUT of a few hundred KB–few MB |
| Storage | **$0.00** | Backend keeps artifacts for 5 rounds (`artifact_retention_rounds: 5`) |
| On-chain tx | **$0.00** | Submitting is off-chain. Zero extrinsics |
| **Marginal cost per round** | **≈ $0.00** | |

The real cost is **your own testing**, which is not per-response:

| Local test cost | Estimate |
| --- | ---: |
| One full local npabench run (world gen + 600 s run + recording) | ~15–25 min wall clock on 8 vCPU |
| Cloud compute for that run | ~$0.03–0.05 |
| Your own OpenRouter spend per test run | ≤ $0.01 if you mirror the cap; realistically $0.05–0.20 while debugging |
| A meaningful iteration cycle (20 seeds × 3 variants) | ~$5–15 and ~10 h of machine time |

## Three setups

| | **Minimum** | **Recommended** | **High-performance** |
| --- | --- | --- | --- |
| What it is | Submit-only. Iterate blind or with a friend's rig | One 8-vCPU/16 GB box, `--max-parallel 2` | 16 vCPU/64 GB, `--max-parallel 4-6`, seed sweeps, CI |
| Hardware/mo | $0 | ~$80 | ~$300 |
| LLM R&D/mo | ~$5 | ~$40 | ~$150 |
| **Cost per response** | ~$0 | ~$0 | ~$0 |
| **Cost per day** | ~$0.17 | ~$4 | ~$15 |
| **Cost per month** | **~$5** | **~$120** | **~$450** |
| Break-even reward | ~0.03 TAO/mo | ~0.63 TAO/mo | ~2.4 TAO/mo |
| Break-even in champion-days | **0.1 days** of championship | **0.08 days** | **0.28 days** |
| **Break-even rank** | **Rank 1. Nothing else pays.** | Rank 1 | Rank 1 |

> **The economics are unusual and worth stating plainly.** Operating cost is
> negligible and championship revenue is ~$1,642/day. Holding the crown for
> **less than one day per month** covers even the high-performance setup. But the
> distribution is brutally binary: rank 2 earns exactly $0 forever. This is a
> *research-competition* payoff profile, not an infrastructure-yield profile.

---

# 10. On-Chain Actions

| # | Action | What is submitted | Who | Frequency | Costs TAO? | Auto/manual |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | **Burned registration** | `burned_register(netuid=98, hotkey)` | Miner & validator | Once (repeat if deregistered) | **Yes** — `Burn` = 0.1 α ≈ $0.06, plus tx fee | **Manual** (`btcli subnet register`) |
| 2 | **PoW registration** | — | — | — | — | **Disabled** — `Difficulty = u64::MAX` |
| 3 | **Stake** | `add_stake` | Validator (needs stake for scoring weight); miner optional | As desired | Yes (capital, not spend) | Manual. **No miner stake requirement in code** |
| 4 | **Axon serving** | `serve_axon` | — | Never | — | **Not used.** No axon code in the repo; only 1 `Axons` entry exists across all 256 UIDs |
| 5 | **IP/port registration** | — | — | Never | — | **Not used** |
| 6 | **Set weights** | `set_weights(netuid=98, uids, weights)` with `wait_for_inclusion=False` | **Validators only** | Once per `WEIGHT_EPOCH_BLOCKS = 360` per round | Free (validators are exempt) | **Automatic** (`_weight_worker`, `loop.py:497`) → `shared/chain.py:198-205` |
| 7 | **Commit-reveal** | — | — | Never | — | **Not used**; `CommitRevealWeightsEnabled` unset on netuid 98 |
| 8 | **Reward distribution** | Yuma consensus → incentive/dividends → alpha emission | Chain runtime | Every block, settled per tempo (360 blocks) | — | **Automatic**, no extrinsic |
| 9 | **Deregistration** | Implicit: a new registration replaces the lowest-pruning-score UID | Chain runtime | Whenever someone registers into a full subnet | — | **Automatic** |
| 10 | **Miner submission** | ✱ **Not on chain at all** — HTTPS to `api.neverplayalone.ai` | Miner | ≤1 per round | No | Manual (`npacli submit`) |
| 11 | **Scoreboard / consensus upload** | ✱ **Not on chain** — HTTPS to the backend | Validator | Once per round each | No | Automatic |

> **Bottom line for a miner: exactly one on-chain action ever — registration.**
> Everything else in your lifecycle is HTTPS to a centralised backend.

---

# 11. Rules and Penalties

| Rule | Value | Enforced by | Evidence |
| --- | --- | --- | --- |
| Minimum uptime | **None.** The miner process does not run | n/a | no daemon in `miner/` |
| Response deadline (agent) | 600 s wall clock per task | **Code** | `configs/default.yaml:7`, `event_stream.py:27-32` |
| Submission deadline | Before `evaluation_start_block` | **Backend** (UNVERIFIABLE) | round windows |
| Submissions per round | **One.** Resubmitting "replaces your entry only if the backend accepts it" | **Backend** | `docs/miner.md` "Submit" |
| Lifetime submission quota | **`max_uses` per hotkey — a hard cap exists.** Value unknown | **Backend** | `miner/cli.py:112-113` prints `use_count/max_uses`, `remaining`, `used_rounds` |
| Rate limits (HTTP) | Not visible | **Backend** (UNVERIFIABLE) | — |
| Allowed models | Only 4 model families, either provider id form; anything else → HTTP 403 `model_not_allowed` | **Code** | `docker/proxy/server.py:269-274`, `docker/proxy/model_pairs.json` |
| Allowed APIs | Only `POST /chat/completions`, `POST /responses`, `GET /models` on the proxy. Nothing else is reachable | **Code** | `server.py:217-228` + `--internal` network |
| Streaming | Forbidden → 400 | **Code** | `server.py:261-262` |
| LLM spend | $0.01 per task, depletes even if upstream omits `usage` | **Code** | `server.py:283-293`, `_account:322-326` |
| Request body size | 10 MB → 413 | **Code** | `server.py:250-252` |
| Archive contents | No absolute paths, `..`, symlinks, hardlinks, device files | **Code (validator-side)** | `round_evaluation.py:59-69` |
| Archive size | Unknown cap | **Backend** (UNVERIFIABLE) | `finalize` returns `size_bytes` |
| Multiple miners / multiple UIDs | Nothing in the code forbids one operator running many hotkeys | **Nothing** | — |
| Shared servers | Meaningless here — miners run no servers | n/a | — |
| Cached answers | Structurally impossible (fresh world + fresh objective + unpredictable seed) | **Code** | `round_evaluation.py:118-121` |
| Duplicate / copied submissions | **Explicitly anticipated and only softly discouraged.** All agents are open source; the champion margin is the stated defence against "copy-paste resubmissions" | **Mechanism design**, not code | `README.md` "Fully open-source competition" |
| Movement cheating | ≥2 windows over 9 b/s horizontal or 6 b/s vertical → score 0 | **Code** | `movement_monitor.py`, `single_runner.py:169-179` |
| Version requirements | `bittensor==10.5.0` pinned; Python ≥3.10; validators must run identical npabench | **Code (partially)** | `pyproject.toml:13`, `docs/validator.md` "All validators must run the same npabench version or scores diverge" |
| Hotkey ban | Backend maintains a ban list with a `policy_hash`; banned hotkeys are dropped from consensus **and** re-checked immediately before `set_weights` | **Code (validator) + Backend (policy)** | `loop.py:126-175`, `miner/cli.py:39-41` |

## Penalties

| Penalty | Trigger | Enforced by |
| --- | --- | --- |
| **Score forced to 0** | Movement violation; evaluation exception | **Code** — `single_runner.py:169-190` |
| **Score 0 (natural)** | Never spawned, crashed at start, gathered nothing | **Code** — `scoring.py:76-77` |
| **Score reduction** | Ending far from spawn (down to ×0.20); missing targets | **Code** — `scoring.py:53-59` |
| **Blacklisting / ban** | Backend policy decision. Ban reason is surfaced to the miner: `"Your hotkey is banned.\nreason: …"` | **Backend**, honoured by validator code (`loop.py:126-175`) |
| **Temporary ban** | Not distinguishable in code — ban is a boolean + reason | **Backend** (UNVERIFIABLE) |
| **Loss of the crown** | A challenger beats you by more than the margin | **Code** — `loop.py:96-112` |
| **Championship revoked retroactively** | If the reigning champion is banned, validators re-run the *previous* round's consensus and weight the replacement | **Code** — `loop.py:275-291` |
| **Deregistration / loss of UID** | Subnet is full (256/256); lowest pruning score is replaced on any new registration. Immunity only 5,000 blocks | **Chain runtime** |
| **Loss of registration cost** | Burn is non-refundable; deregistration does not return it | **Chain runtime** |
| **Loss of rewards** | Automatic and total for anyone who is not the champion | **Code** — winner-take-all |

> **Documented-only, not code-enforced:** the "one submission per round" replacement
> semantics, the archive size cap, submission rate limits, and the ban criteria all
> live in the closed backend. A miner cannot audit them.

---

# 12. Security and Anti-Cheating

## What is genuinely well defended

| Threat | Defence | Where |
| --- | --- | --- |
| **Fake responses** | Structurally impossible. Score is read from **server-side world state over RCON**, never from anything the agent asserts. Claiming `{"kind":"done","inventory":{...}}` changes nothing | `final_state.py:16-41` |
| **Replay attacks (API)** | Every signed request binds method + path + body + a fresh UUID nonce + a unix timestamp | `api_client.py:30-40` |
| **Replay attacks (task)** | Per-validator seed derived from the **chain block hash at evaluation time** — unknowable at submission time, and different per validator | `round_evaluation.py:118-121` |
| **Challenge prediction** | Same. Plus the prompt itself is LLM-generated per task | `prompting.py:80-146` |
| **Benchmark memorisation** | 3 fixed essentials + 2 of 22 optionals (231 pairs) × randomised counts × 2^64 world seeds. Memorising a world is useless | `task.py:35-62` |
| **Miner impersonation** | Submissions require an sr25519 signature from the registered hotkey; `miner_uid` is resolved from the metagraph, not trusted from input | `api_client.py:34`, `cli.py:59` |
| **Validator impersonation** | Roster, scoreboard, and consensus endpoints are all hotkey-signed | `api_client.py:97-182` |
| **API proxying / key theft** | The real OpenRouter/Chutes keys never leave the proxy container. Agents get an opaque per-(entry, task) UUID token. Verified by a test | `proxy.py:126-155`, `tests/test_proxy_manager.py:88-103` |
| **Unmetered LLM use** | Budget is pre-checked against an *estimate* and post-charged; if upstream omits `usage`, the proxy **estimates and still charges** so a session cannot get free calls | `server.py:283-293`, `322-326` |
| **Sandbox escape / lateral movement** | `--cap-drop ALL --security-opt no-new-privileges --read-only --tmpfs /tmp:64m --pids-limit 256 --memory 1g`, on a `docker network create --internal` network, `/agent` mounted read-only, running as the unprivileged `node` user | `sandboxed_agent.py:103-111`, `reference_world.py:176`, `docker/agent/Dockerfile` |
| **Movement/teleport cheating** | Server-side position sampling at 2 Hz with latency-padded speed bounds and a respawn-aware guard; 2 violations ⇒ score 0 | `movement_monitor.py` |
| **Tar-slip / zip-slip** | Absolute paths, `..`, symlinks, hardlinks, device nodes all rejected before extraction | `round_evaluation.py:59-69` |
| **Malformed agent output** | Non-JSON stdout is downgraded to an `info` event, never parsed as control | `run_trace.py:45-54` |
| **Malformed archive** | Rejected — but see the weakness below |

## Weaknesses, in order of how much they matter

### 1. Validator stake concentration undermines the consensus story *in practice*
The code is correct — stake-weighted mean over all scoreboards, computed
independently by every validator (`loop.py:51-93`). But live `Dividends` show one
validator at **~55 %** and the top two at **~87 %**. A single dishonest or
misconfigured validator can move the winner; two can set it. The README's
"no single scoreboard decides a round" is not currently true in practice.
*Safe improvement:* trimmed-mean or median across validator scoreboards instead of
a plain stake-weighted mean, and/or a minimum-quorum requirement of ≥N independent
validators before consensus is accepted.

### 2. One bad archive can abort a validator's entire round
`_materialize_agents` (`round_evaluation.py:72-95`) loops over roster entries with
no per-entry error handling. A `download_bytes` failure or a `_safe_extract_tar_gz`
`ValueError` propagates out of `run_round_evaluation`, which is caught only at the
top level (`loop.py:615-616`) and marks the round evaluated-with-exception — so
that validator uploads **no scoreboard at all**. If it hits the top-stake
validator, the round's consensus is materially changed.
*Safe improvement:* per-entry try/except that records `score=0.0,
status="error"` and continues. The unmerged branch `origin/feat/upload-retry`
does exactly this for uploads — it should be merged and extended to downloads and
extraction.

### 3. Validators are not evaluated against each other
Nothing checks whether a validator's scoreboard is plausible. There is no
cross-validation of `report.json` against `recording.mcpr`, no outlier detection,
no re-run of disputed entries. A validator could upload arbitrary scores; the only
correction is Yuma consensus clipping *after* the weights are set, which punishes
vtrust but does not undo a wrong winner.
*Safe improvement:* have each validator spot-check a random sample of another
validator's uploaded artifacts and publish an agreement metric.

### 4. Sybil mining is cheap and unbounded
Registration is ~$0.06 and there is no miner stake requirement anywhere in the code.
Nothing prevents one operator from holding many UIDs and submitting many variants
per round to maximise the chance one clears the champion margin. This is arguably
*intended* under "open-source competition", but it is not rate-limited by code —
only by the backend's opaque `max_uses`.
*Safe improvement:* require a small miner stake, or make the per-hotkey
submission quota explicit and documented.

### 5. `npabench` is pinned to a moving target
`scripts/validator_setup.sh:11` sets `BENCH_REF="${NPA_BENCH_REF:-main}"` — a
branch, not a SHA — while the comment immediately above says "keep this pinned to a
tag or commit SHA once rounds matter" and `docs/validator.md` says "All validators
must run the same npabench version or scores diverge." Combined with
`validator_autoupdate.sh` restarting only within 50 blocks before round start,
validators can straddle a bench change mid-round.
*Safe improvement:* pin `BENCH_REF` to a SHA and have the validator log/publish the
bench commit in its scoreboard so divergence is detectable.

### 6. `TASKS_PER_ROUND` disagreement (3 vs 5) across validators
See §7. Different sample counts and different LLM budgets per miner depending on
whether a given validator has a `.env`.
*Safe improvement:* make the roster carry `tasks_per_round` and have validators
read it from there rather than local config.

### 7. Denial-of-service surface
- Against the subnet: a miner archive that spawns a fork bomb or allocates
  aggressively is contained (`--pids-limit 256 --memory 1g --read-only`), so the
  blast radius is one slot. Good.
- Against the *proxy*: `ThreadingHTTPServer` with an unbounded thread-per-connection
  model, no per-session rate limit, and a 60 s upstream timeout
  (`server.py:440`, `validator/config.py:42-43`). A malicious agent can open many
  concurrent slow requests. Budget caps spend but not connections or threads.
  *Safe improvement:* cap concurrent in-flight requests per session token.
- Against the validator: `_wait_for_slot_ready(..., timeout=600)` plus
  `TASKS_PER_ROUND × N entries × ~10 min` means round wall-clock scales linearly
  with roster size; a large roster can exceed the 21.6 h evaluation window.

### 8. Latent port collision at ≥10 roster entries
`AgentRunSlot` derives ports as `base + slot_id`, with `DEFAULT_BASE_GAME_PORT =
25665` and `DEFAULT_BASE_RCON_PORT = 25675` (`npabench/config.py:41-42`). Slot 10's
game port (25675) equals slot 0's RCON port. With `max_parallel=4` these rarely
overlap in time, but it is a real latent conflict on a large roster or a slow
teardown. **INFERRED** — I did not reproduce it.

### 9. Unsigned archive bytes
See §4. The `PUT` to the presigned URL carries no hotkey signature.

### 10. Off-chain single point of failure
The backend defines the round schedule, the roster, the champion, the ban list, and
the submission quota. It is closed source. The validator code trusts
`hotkey_eligibility` so completely that an invalid response raises and blocks the
weight update (`loop.py:134-136, 163-174`) — correct fail-closed behaviour, but it
means a backend outage or compromise controls emission. The `_set_fallback_weights`
offline path (`loop.py:514-528`) mitigates outage, not compromise.

*I am deliberately not describing how to exploit any of the above.*

---

# 13. Current and Proposed Features

| Feature | Documented | Implemented | Evidence | Miner impact |
| --- | :--: | :--: | --- | --- |
| Round-based submission via `npacli submit` | ✅ | ✅ Full | `miner/cli.py:45-84`; live API returns real round windows | **Core.** This is the whole miner workflow |
| `npacli status` / `npacli usage` | ✅ | ✅ Full | `miner/cli.py:86-144` | Check window + your remaining quota before building |
| Hotkey-signed API auth | ➖ | ✅ Full | `api_client.py:30-40`; `tests/test_api_client.py` | Your hotkey must be registered or nothing works |
| Sandboxed evaluation (network-isolated) | ✅ | ✅ Full | `sandboxed_agent.py:103-111`, `reference_world.py:176` | Defines every constraint your agent lives under |
| Per-validator chain-hash seed | ✅ | ✅ Full | `round_evaluation.py:118-121` | You cannot pre-fit a seed |
| LLM egress proxy + spend cap | ✅ | ✅ Full | `docker/proxy/server.py`, `validator/proxy.py` | $0.01/task is your entire inference budget |
| Cross-provider model routing | ✅ | ✅ Full | `server.py:85-116`, `model_pairs.json` | Use either provider's model id |
| Multi-task scoring (mean over N seeds) | ⚠️ **Inconsistent** | ✅ Full | Code says **5** (`config.py:31`), docs and `.env.example` say **3** | Consistency ≥ single-run luck. Budget for 3–5 runs |
| Stake-weighted consensus | ✅ | ✅ Full | `loop.py:51-93`; `tests/test_champion_consensus.py:130-149` | Top validator dominates in practice |
| Champion-defense margin | ✅ | ✅ Full | `loop.py:96-112`; 5 dedicated tests | You must **clearly** beat the incumbent |
| Winner-take-all weights | ✅ | ✅ Full | `chain.py:133-161`; live `Incentive` has 1 non-zero UID | Rank 2 = $0 |
| Configurable emission burn | ✅ | ✅ Full, **currently off** | `config.py:32` default `0.0`; live UID 0 incentive = 0 | 100 % of miner emission reaches the winner today |
| Banned-hotkey exclusion | ➖ (miner docs silent) | ✅ Full (validator side) | `loop.py:126-175` | A ban zeroes you retroactively, even as champion |
| Banned-champion → previous-round re-consensus | ➖ | ✅ Full | `loop.py:275-291` + test | You can inherit a crown from a banned champion |
| Last-winner fallback (incl. offline) | ➖ | ✅ Full | `loop.py:321-370`; `tests/test_last_winner_fallback.py` | Emission keeps flowing to you during backend outages |
| Movement anti-cheat | ➖ **undocumented for miners** | ✅ Full | `movement_monitor.py`; not mentioned in `docs/miner.md` | **Silent 0.0.** Aggressive pathfinding could in principle trip it |
| Gameplay recording (`.mcpr`) | ✅ | ✅ Full | `recording/recorder.py`, `replay_exporter.py` | Evidence trail; not visible to you unless the backend exposes it |
| Per-miner LLM usage tracking | ✅ | ✅ Full | `round_evaluation.py:106-116` → `report.proxy_usage` | Your spend is recorded per run |
| Workspace auto-pruning | ✅ | ✅ Full | `round_evaluation.py:37-56` + 5 tests | Validator hygiene only |
| Validator auto-update | ✅ | ✅ Full | `scripts/validator_autoupdate.sh` (320 lines) | Bench changes can land between rounds |
| **Archive-upload retry / skip-failed-entry** | ❌ | ⚠️ **On an unmerged branch only** | `origin/feat/upload-retry` (`d8257de`), not in `main` | One bad entry can void a validator's whole round |
| **Bench ref pinned to SHA** | ✅ *(stated as required)* | ❌ **Not done** — defaults to `main` | `validator_setup.sh:11` vs. its own comment | Score divergence risk across validators |
| Other missions (`mining`, `crafting`, `crafting_v2`) | ❌ | ✅ In npabench, ❌ **not selected** | `missions/registry.py:9-14`; `MISSION_ID` default `resource_gathering` | Only `resource_gathering` matters today |
| Synthetic task-generation pipelines | ✅ (README) | ⚠️ Partial — LLM generates the *prompt wording*, resource sampling is `random.Random(seed)` | `prompting.py`, `task.py` | Task space is finite: 231 optional pairs |
| "Expand NPA-Bench with more diverse tasks" | ✅ (roadmap) | ❌ Planned | `README.md` Roadmap step 4 | Future task diversity |
| "Qualify for emissions" / 90–95 % burn phase | ✅ (roadmap) | ✅ Superseded — burn is 0 and emission is live | `fae1145`, live `Incentive` | Roadmap text is stale; emission is flowing now |
| Showcase Twitch server + AI narrator | ✅ (roadmap) | ❌ Not implemented | `README.md` | None |
| Commercial companion product | ✅ (README) | ❌ Not in either repo | — | The stated reason emission exists |
| Backend (`neverplayalone_api`) | ✅ referenced | ❓ **Closed source** | `README.md` "the backend lives in the separate `neverplayalone_api` repository" | Round schedule, roster, bans, quotas, archive limits all unauditable |
| Commit-reveal weights | ❌ | ❌ Not used | `CommitRevealWeightsEnabled` unset | Winner is publicly visible each epoch |
| Miner axon / dendrite | ❌ | ❌ Absent by design | no `axon`/`dendrite` in repo; 1 axon on chain | No server, no ports |
| TODO/FIXME markers | — | **Zero** in either repo | `grep -rn "TODO\|FIXME\|XXX\|HACK"` returns nothing | Codebase is unusually clean |
| Test coverage | — | ⚠️ Validator/consensus well covered (466-line consensus suite); **`round_evaluation.py` untested on `main`** (its test file exists only on the unmerged branch); **`miner/cli.py` untested** | `tests/` | Miner CLI regressions would not be caught |

**Open issues:** the GitHub issue tracker was not reachable from this analysis, so
I did not audit open issues. Unmerged branches on the remote are:
`feat/upload-retry` (the only one with content not on `main`), plus `chore/burn-rate-zero`,
`feat/burn-on-no-consensus`, `feat/last-winner-fallback`, `feat/miner-usage-command`,
`feat/workspace-cleanup`, `fix/hotkey-ban-log` — all already merged.

---

# 14. Miner Strategy

## Where the points actually are

Per task, out of 100:

| Component | Max points | Difficulty | Notes |
| --- | ---: | --- | --- |
| `logs` (essential) | 25 | **Easy** | Trees everywhere; netherite axe provided |
| `cobblestone` (essential) | 25 | **Easy** | Dig down 3 blocks; netherite pickaxe. `stone` → `cobblestone` |
| `raw_meat` (essential) | 25 | **Hard** | `peaceful` + `doMobSpawning false` ⇒ only world-gen animals. Target 7–15 |
| 2 × optionals | 12.5 each | **Varies wildly** | `dirt`/`sand`/`gravel` trivial; `clay`/`leather`/`melon`/`sweet_berries` biome-dependent |
| Distance multiplier | ×0.20–×1.00 | **Free 25 % swing** | Ending 6 blocks from spawn vs 300 blocks is the difference between 100 and 60 |
| `time_efficiency` | 0.001 | **Irrelevant** | Tie-breaker only. **Never trade score for speed** |

**The two highest-leverage insights:**
1. **Always return to spawn.** Ending at ≤10 blocks is a ×1.00 multiplier; at
   ≤250 blocks it is ×0.60. A perfect gather that ends 300 blocks out scores
   **60**, while a 70 % gather that ends at spawn scores **70**. Budget the last
   90–120 s purely for the walk home.
2. **Cobblestone and logs are nearly free 50 points.** Bank those first,
   deterministically, then spend remaining time on meat and the two optionals.

## Improving each dimension

**Accuracy**
- Do not rely on the LLM to identify targets. The prompt is generated by
  `gpt-4.1-mini` at temperature 0 from a fixed brief (`prompting.py:149-164`), so
  its structure is highly regular: `"<verb> N <resource>, N <resource>, ... Keep …
  inventory … within 20 blocks of spawn."` A deterministic regex parser over the
  **full 22-entry catalogue** in `configs/default.yaml` will beat an LLM call on
  both reliability and cost. Use the LLM only as a fallback for unparsed phrasings —
  this is exactly what the reference `llm_agent` does (`heuristicPlan`,
  `examples/agents/llm_agent/index.js:89-103`), except its rule table covers only 4
  of 22 resources.
- Map every catalogue item to its **source block**, not the item id: `cobblestone`
  ← mine `stone`; `dirt` ← `dirt`/`grass_block`; `clay_ball` ← `clay`;
  `melon_slice` ← `melon`; `sweet_berries` ← `sweet_berry_bush` (right-click, not
  dig); `leather`/`feather`/`beef` ← kill entities; `moss_carpet`/`vine`/`kelp`/
  `short_grass` need the right tool or shears.
- **Over-gather slightly.** `achieved = min(count, target)` so surplus is wasted,
  but dropped items and pathing failures are common — a 10–15 % buffer on cheap
  resources costs nothing.
- **Never die.** `keep_inventory false`. Difficulty is `peaceful` so hostile mobs
  are off; the real death risks are **fall damage** and **drowning**. Cap your
  descent, and avoid deep water when carrying a full load.

**Latency**
- Ignore it as a score component (0.001 max). Optimise *throughput within 600 s*
  instead: minimise pathfinder round-trips, batch nearby blocks, and use
  `bot.findBlocks({maxDistance: 64, count: 64})` results sorted by distance rather
  than re-searching after every dig.
- Emit `done` as soon as you are home and finished — it ends the run cleanly and
  is a precondition for the tie-breaker.

**Reliability**
- Wrap **every** `await` (`goto`, `dig`, `equip`) in try/catch. An unhandled
  rejection kills Node → non-zero exit → `status="error"` → **forced 0.0**.
- Add `process.on('unhandledRejection', ...)` and `process.on('uncaughtException', ...)`.
- Handle `bot.on('kicked')` and `bot.on('error')` — reconnect if you can, and always
  emit `done` before the deadline so the run terminates on your terms.
- Emit `ready` **immediately** on `spawn`. Everything (kit, survival mode, spawn
  point) is gated on it (`single_runner.py:320-332`).
- Then **wait for the kit** before acting — the reference agents poll
  `bot.inventory.items().length > 0` for up to 400 ticks
  (`llm_agent/index.js`, `waitForKit`).

**Uptime**
- Not applicable to the agent. Applies only to *you*: submit before
  `evaluation_start_block`. Automate it — a cron job running `npacli status` and
  `npacli submit` is enough. Missing a window costs you a full day of possible
  emission.

**Concurrency**
- Inside one run: `mineflayer` is single-bot and event-driven. Overlap the LLM call
  (if any) with walking to the first obvious resource instead of blocking on it.
- Across runs: none — the validator controls parallelism.

**Model loading**
- No local models. Your only model access is the proxy. Choose by price:
  `openai/gpt-oss-120b` on Chutes is **$0.05/$0.25 per 1M** — the cheapest allowed
  (`model_pairs.json`). At the $0.01 cap that is roughly ~30k output tokens
  vs. ~4.5k for `deepseek/deepseek-r1` ($0.55/$2.19).
- **Set a modest `max_tokens`.** The proxy pre-rejects a request whose *estimated*
  cost exceeds the remaining budget, using
  `max_output_tokens|max_completion_tokens|max_tokens` (`server.py:339-346`).
  Omitting it estimates output as 0 (so it passes) but then charges you the real
  cost; setting it huge gets you a 403 `budget_exceeded` immediately.
- Never set `stream: true` — instant 400.

**Caching**
- Between runs: impossible. `/agent` is read-only, `/tmp` is a per-run 64 MB tmpfs.
- Within a run: absolutely — cache block scans, biome observations, and the parsed
  plan in memory.
- **Bake your knowledge into the archive.** The full resource catalogue,
  item→block mappings, biome heuristics, and any distilled policy should ship as
  static data/code inside your `tar.gz`. That is your only persistent memory.

**Hardware usage**
- Yours doesn't run during evaluation. Spend hardware on **offline seed sweeps**:
  run 20–50 seeds locally and optimise the *mean*, since validators score the mean
  over 3–5 tasks and then average across validators. Variance reduction beats peak
  performance.

**Error handling**
- Treat "returned home with 70 %" as the target outcome, not "died at 95 %".
- A hard `setTimeout` at `timeout − 120 s` that aborts gathering and walks home is
  worth more than any planning improvement.

**Response validation**
- Before submitting, verify: `tar -tzf agent.tar.gz` shows no leading `/`, no `..`,
  no symlinks; `index.js` is at the archive root (the validator mounts your
  directory at `/agent` and runs `node index.js` with cwd `/agent`).
- Only `mineflayer` and `mineflayer-pathfinder` are pre-installed, via `NODE_PATH`
  (`docker/agent/Dockerfile`). There is **no `npm install` at run time.** If you
  need anything else, vendor it into `/agent/node_modules` inside your tarball —
  Node resolves `./node_modules` before `NODE_PATH`. **INFERRED** from standard Node
  resolution order; test it locally before relying on it.

**Monitoring**
- You get no live feedback. Emit a rich JSON trace — it is preserved in `trace.json`
  and folded into `report.json`. If the backend ever exposes artifacts to miners,
  that trace is your only debugging channel.
- Track the on-chain `Incentive` vector for netuid 98 to see whether you are the
  current champion, and watch `Emission[your_uid]`.

## Three approaches

### A. Minimum viable miner — ~1 day of work
- Fork `examples/agents/log_gatherer`, extend `heuristicPlan` to the full 22-resource
  catalogue with correct item→block mappings.
- Gather logs → cobblestone → the two optionals, skip raw meat entirely.
- Hard timer at `timeout − 120 s` → walk home → emit `done`.
- Wrap everything in try/catch.
- **Expected:** ~60–75 raw × ×1.00 ≈ **60–75**. Solid, will not win against a
  serious champion, but it is a real baseline and a working submission.
- **Cost:** ~$5. **Expected revenue: $0** unless the field is weak.

### B. Competitive miner — ~1–2 weeks
- Everything above, plus:
  - **Solve raw meat.** Spiral/ring search for passive entities near spawn early
    (before you wander), kill with the netherite axe, and pick up drops. This is
    25 points nobody gets for free.
  - **Biome-aware branching:** detect biome at spawn and pick the cheapest route
    for the drawn optionals (`sand`+`dead_bush` in desert, `kelp` near ocean,
    `sweet_berries` in taiga, `clay` in rivers).
  - **Return-to-spawn budgeting:** compute distance-to-home continuously and
    trigger the walk back with a real margin, not a fixed timer.
  - **Seed sweep:** run 30+ local seeds; optimise mean score, not best score.
  - Use the LLM only for unparsed prompts, on `gpt-oss-120b`, `max_tokens ≤ 300`.
- **Expected:** ~85–95 mean. Genuinely contends.
- **Cost:** ~$120/mo. **Revenue: $1,642/day while champion, $0 otherwise.**

### C. High-performance miner — ~1–2 months
- Everything above, plus:
  - **Offline policy search:** treat the 5-target × biome × terrain problem as a
    routing problem; precompute gather-order policies per (target-set, biome) and
    ship them as a lookup table inside the archive.
  - **Terrain-aware pathing:** custom `Movements` tuning (`canDig`, block costs,
    avoiding water/lava), plus a bounded search radius so you never strand yourself.
  - **Movement-monitor safety margin:** keep sustained speed well under 9 b/s
    horizontal and 6 b/s upward. Sprinting is ~5.6 b/s so this is safe by default —
    but avoid anything that could look like a jump in server-side position.
  - **Automated regression harness:** 50-seed nightly sweep in CI, tracking mean,
    p10, and failure-mode distribution. Because scoring is a mean over 3–5 tasks
    and then averaged across validators, **reducing p10 matters more than raising
    p90**.
  - **Champion-margin awareness:** you must beat the incumbent by the margin. If
    you are close, invest in variance reduction (the thing that moves the mean),
    not in a new capability.
  - **Automated daily submission** via cron with `npacli usage` quota checks.
- **Expected:** 95–100 mean, championship-competitive.
- **Cost:** ~$450/mo + serious engineering. **Revenue: up to $49k/mo if you hold
  the crown all month.**

---

# 15. Final Answers

**1. What is this subnet designed to do?**
Run a daily, sandboxed, winner-take-all competition between miner-authored
autonomous Minecraft agents, evaluated on the NPA-Bench `resource_gathering`
mission, with the winning agent intended to power a commercial "AI companion"
product for Minecraft servers.

**2. What final result does it produce?**
One winning agent per round, and a single on-chain weight vector placing 100 % of
weight on that miner's UID — plus, per entry per task, a `report.json` and a
`recording.mcpr` gameplay replay as verifiable artifacts.

**3. What exactly must miners do?**
Write a Node.js Minecraft agent whose entrypoint is `index.js`, package the
directory as `agent.tar.gz`, and run `npacli submit ./agent.tar.gz --wallet … --hotkey …`
once per round while the submission window is open. Nothing else.

**4. Is a GPU required?**
**No — and it cannot be used.** The agent runs in a 1 GB, read-only, no-internet
Node container with no GPU access. All inference goes through a validator proxy
capped at $0.01 per task.

**5. What hardware is recommended?**
To *submit*: any machine with Python 3.10+. To *develop competitively*: 8 vCPU,
16 GB RAM, 100 GB SSD, Docker + Node 20+ (~$80/mo cloud box).

**6. Must the miner run a server?**
**No.** No axon, no daemon, no listener. One CLI invocation per round.

**7. Must the miner open a port?**
**No.**

**8. Which port and protocol are used?**
Miner side: **none** — outbound HTTPS to `api.neverplayalone.ai` with sr25519
request signing, plus the chain for registration. Validator side (not yours):
Minecraft game ports from 25665, RCON from 25675, LLM proxy on container-internal
8080 (never published).

**9. Does the miner upload files?**
**Yes** — exactly one `.tar.gz` per round, PUT to a backend-issued presigned URL.

**10. Does the miner submit anything on-chain?**
**Only the registration extrinsic, once.** Submissions, scores, and artifacts are
all off-chain. No commit-reveal, no hash commitment.

**11. What data is initially provided?**
Five environment variables inside the validator's container:
`NPABENCH_HOST`, `NPABENCH_PORT`, `NPABENCH_AGENT_USERNAME`,
`NPABENCH_AGENT_PROMPT` (an LLM-generated natural-language objective), and
`NPABENCH_TIMEOUT_SECONDS` (600) — plus proxy credentials
(`OPENAI_BASE_URL`/`OPENAI_API_KEY`/`OPENROUTER_*`). Nothing is sent over a network
to you.

**12. What does a perfect response look like?**
An agent that emits `ready` immediately on spawn, waits for its starting kit,
gathers all three essentials and both optionals to their target counts, returns to
within 10 blocks of world spawn, and emits `done` before the 600 s deadline —
without dying and without tripping the movement monitor. Score: **100.000x**
(`base 100 × distance 1.00 + time_efficiency × 0.001`).

**13. How do validators score miners?**
```
per task:   score = Σ(points_r · min(count_r, target_r)/target_r) × distance_multiplier
                    + time_efficiency × 0.001        (0 if movement violation or error)
per round:  score = mean over TASKS_PER_ROUND tasks
consensus:  score = Σ_v stake_v · score_v / Σ_v stake_v   (banned hotkeys removed)
winner:     champion keeps the crown unless best_challenger > champion + margin
weights:    winner_uid = 1.0, everyone else = 0.0
```

**14. How often are miners evaluated?**
Once per round (≈ 1 calendar day, 7,200 blocks), by every participating validator,
with **3 or 5** task instances each (config disagreement — `validator/config.py:31`
says 5, `.env.example` and docs say 3).

**15. How often are rewards distributed?**
Alpha emission accrues **every block**; Yuma consensus settles each **tempo = 360
blocks ≈ 72 minutes**; validators refresh the winner target every 360 blocks; and
the winner *identity* changes at most **once per day**.

**16. What is the registration cost?**
`Burn` = 100,000,000 rao = **0.1 α ≈ 0.00029 TAO ≈ $0.06** (at the `MinBurn` floor).
PoW registration is disabled. **Verify with `btcli subnet burn-cost --netuid 98`** —
if the value is TAO-denominated it is ~$19. Either way the subnet is **full
(256/256)**, so registering deregisters the weakest UID and you should expect to
re-register.

**17. What is the cost per response?**
**Effectively $0.00.** Validators pay all compute and all LLM spend. Your only
marginal cost is one HTTPS upload. Development and local testing cost $5–450/month
depending on how seriously you iterate.

**18. What is the monthly operating cost?**
**~$5** (submit-only), **~$120** (recommended dev box + LLM R&D), **~$450**
(high-performance sweep infrastructure).

**19. What rank may be needed to break even?**
**Rank 1 — and only rank 1.** Rank 2 earns exactly zero. Break-even is not
expressed in rank but in *time held*: at $1,642/day of championship emission,
holding the crown for **under one day per month** covers even the most expensive
setup.

**20. What can cause a miner to lose rewards or its UID?**
Losing the champion margin to a challenger; scoring 0 via movement violation,
crash, or never spawning; dying in-game and dropping the haul; ending far from
spawn (×0.20); missing the submission window; exhausting the per-hotkey `max_uses`
quota; being **banned by the backend** (which strips consensus eligibility
retroactively, even for a reigning champion — `loop.py:126-175, 275-291`); and
deregistration, which is a live risk because the subnet is at 256/256 with only a
5,000-block immunity period and zero incentive on non-champion UIDs.

**21. Which subnet features are unfinished?**
Upload retry / per-entry failure isolation (implemented only on the unmerged
`origin/feat/upload-retry` branch); `BENCH_REF` pinning (defaults to `main`,
contradicting its own comment and the validator docs);
`TASKS_PER_ROUND` consistency (5 in code vs 3 in docs/`.env.example`); the
`mining`/`crafting`/`crafting_v2` missions (present in npabench, never selected);
richer synthetic task generation, the Twitch showcase server, and the commercial
product (all roadmap only). The entire backend — round scheduling, roster
derivation, ban policy, submission quotas, archive size limits — is closed source
and unauditable. There are **zero** TODO/FIXME markers in either repo.

**22. Is mining this subnet technically practical?**
**Yes, unusually so.** The barrier to *participating* is among the lowest of any
subnet: no server, no GPU, no public IP, no uptime, no on-chain activity beyond
registration, and a marginal cost per submission of essentially zero. The barrier
to *winning* is a genuine research/engineering problem — building a robust
embodied game agent under a hard 600 s budget, 1 GB RAM, no internet, and one cent
of LLM inference. The codebase is clean, well-tested on the consensus path, and the
anti-cheat design (score from server-side world state, chain-hash seeds, movement
monitoring, network-isolated sandboxes) is genuinely strong.

**23. Is mining this subnet likely to be profitable?**
**Only for the champion — but for the champion, very.** Miner emission is
~2,952 α/day ≈ 8.61 TAO ≈ **$1,642/day** (≈ $49k/month), and live on-chain state
confirms 100 % of it currently lands on a single UID with no burn. Against
operating costs of $5–450/month, the champion's margin is enormous.

For everyone else the expected value is **exactly zero revenue**, plus the risk of
deregistration. This is a **tournament payoff, not a yield**: your expected return
is `P(you hold the crown) × $1,642/day`, and with ~5 validators — one holding ~55 %
of scoring weight — and a champion-margin hysteresis protecting the incumbent, that
probability is concentrated, sticky, and hard to estimate from outside.

**Verdict:** worth entering if you can credibly build a top-1 Minecraft agent and
can absorb months of zero revenue while iterating. Not worth entering as passive
infrastructure yield — there is no such thing here.

---

## Appendix — things I could not verify

| Item | Why |
| --- | --- |
| `champion_margin` value | Only on the roster, which requires a validator signature |
| Per-hotkey `max_uses` submission quota | Backend-defined; `npacli usage` requires your own registered hotkey |
| Archive size limit | Backend-enforced |
| Submission HTTP rate limits | Backend-enforced |
| Ban criteria and appeal process | Backend policy, closed source |
| Whether `finalize` binds the uploaded object's sha256 | Backend-internal |
| Whether registration `Burn` is α- or TAO-denominated | Inferred from dTAO semantics; confirm with `btcli subnet burn-cost --netuid 98` |
| Port collision at ≥10 roster entries | Read from code, not reproduced |
| Vendored `node_modules` resolution inside `/agent` | Inferred from Node resolution order; test locally |
| GitHub open issues | Issue tracker not consulted |

## Appendix — live data captured for this report

```
GET https://api.neverplayalone.ai/validator/rounds/current      (2026-08-18)
  submission_round : 2026-08-18  open 8867886 → eval 8875086 → deadline 8881566 → end 8882286
  evaluating_round : 2026-08-17  open 8860686 → eval 8867886 → deadline 8874366 → end 8875086
  artifact_retention_rounds: 5

Chain (finney, netuid 98, block ≈ 8,874,800), via state_getStorage:
  Burn                 = 100,000,000 rao (= MinBurn)
  Difficulty           = 18446744073709551615   (PoW disabled)
  Tempo                = 360
  SubnetworkN          = 256   MaxAllowedUids = 256      (FULL)
  ImmunityPeriod       = 5000     ActivityCutoff = 5000
  MinAllowedWeights    = 1        MaxWeightsLimit = 65535
  SubnetTAO            = 5,292.57 TAO
  SubnetAlphaIn        = 1,814,085 α        → price 0.0029174 TAO/α
  SubnetMovingPrice    = 0.0029160 TAO/α
  Axons (netuid 98)    = 1 entry total
  Incentive            = 1 non-zero: UID 236 @ 65535
  Consensus            = 1 non-zero: UID 236 @ 65535
  Emission (per tempo) = UID 236: 147.60 α | UID 0: 81.94 | 153: 46.96 | 218: 18.14 | 38: 0.40 | 114: 0.16
  Dividends            = UID 0: 36380 | 153: 20851 | 218: 8055 | 38: 178 | 114: 69
  TAO/USD              = $190.71  (CoinGecko)
```
