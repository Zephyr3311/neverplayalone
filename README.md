<p align="center">
  <img src="logo.png" alt="Never Play Alone" width="200"/>
</p>

<h1 align="center">⛏️ Never Play Alone ⛏️</h1>

<p align="center"><b>A Bittensor subnet for round-based Minecraft agent evaluation</b></p>

<p align="center">
  <a href="https://neverplayalone.ai/">Website</a> ·
  <a href="docs/miner.md">Become a miner</a> ·
  <a href="docs/validator.md">Run a validator</a>
</p>

> **Now live on Bittensor mainnet — subnet 98.** Never Play Alone runs on
> netuid 98 (`finney`), winner-take-all.

## What is Never Play Alone?

**Never Play Alone** leverages the power of Bittensor to build low-latency,
living AI agents that adapt in real time and play alongside humans. Miners
build Node-based agents that play real Minecraft missions — gathering
resources, surviving, completing objectives — and submit them as sealed
packages. Validators run every agent in identical, sandboxed conditions
against the same deterministic task, score the results, and put the winner on
chain.

No central referee decides the winner. The chain does.

We want agents that feel alive — friendly, responsive, and enjoyable to play
with. Agents that naturally collaborate with players, help complete
objectives, populate servers, and make multiplayer worlds feel more alive.

## Why Minecraft? The market opportunity

Minecraft is one of the world's most-played and highest revenue-generating
games: a massive, persistent player base, hundreds of thousands of community
servers, and a thriving economy around them. That makes it both a serious
commercial opportunity and the ideal proving ground — an open-ended
environment where AI agents can explore, learn, collaborate, and solve
increasingly complex tasks rather than overfit to a narrow benchmark.

The product thesis is simple: deploying AI companions must be effortless.
Connect your Minecraft server, choose how many AI companions you want, and
the deployment is handled — spinning up an army of AI players should be a few
clicks and a few dollars away. The best-performing miner agents on this
subnet are exactly what powers that product, which gives Never Play Alone one
of the clearest paths to profitability among current subnets: emission
rewards the agents that the commercial product then sells.

## NPA-Bench

Evaluation runs on [NPA-Bench](https://github.com/neverplayalone/neverplayalone_bench),
our Minecraft benchmark. It improves upon existing benchmarks such as
MineDojo, MCU, and mc-bench by focusing on:

- **real gameplay** — agents join actual Minecraft servers and act in the
  live world, not a simplified simulator
- **evaluation quality** — deterministic missions, seeded runs, scored
  reports, and full gameplay recordings as verifiable artifacts
- **subnet-ready features** — sandboxed execution, network isolation,
  spend-capped LLM access, and per-run usage tracking, built for
  adversarial multi-validator settings

## How a round works

```
        neverplayalone_api                  subtensor
        ──────────────────                  ─────────
              │                                  │
              │ submission intake                │ metagraph / stakes
              │ derived round roster             │ set_weights
              │ artifacts + scoreboards          │
              │ consensus observability          │
              │                                  │
   ┌──────────┴──────────┐             ┌─────────┴──────────────┐
   │ validators          │             │ miners                 │
   │  - poll round       │             │  - submit one tar.gz   │
   │    windows          │             │    agent per round     │
   │  - download roster  │             └────────────────────────┘
   │  - run npabench     │
   │    batch            │
   │  - upload results   │
   │  - compute winner   │
   └─────────────────────┘
```

1. **Submission.** While the round is open, each miner uploads one `tar.gz`
   agent package to the backend.
2. **Freeze.** At evaluation start, the backend derives a single roster from
   all accepted submissions — the round id, the round seed, every admitted
   entry, and the reigning champion's defense entry.
3. **Evaluation.** Every validator downloads the same roster and runs every
   entry with npabench in sandboxed containers: no internet access, identical
   missions, per-validator seeds derived from the chain block hash. Each
   validator uploads a `report.json` and gameplay `recording.mcpr` per entry,
   plus a raw scoreboard.
4. **Consensus.** After the scoreboard deadline, every validator downloads all
   scoreboards and computes the winner locally — no trusted aggregator.

## Incentive mechanism

We're not reinventing the wheel: the subnet adopts mechanisms that have
already proven successful across Bittensor for maximizing quality,
accelerating progress, and minimizing exploits.

**Winner-take-all.** Each round produces exactly one winner, and validators
set the full weight vector on that miner's UID. Emission concentrates on the
best agent instead of spreading across mediocrity.

**Stake-weighted scoring.** Every entry's final score is the average of all
validator scoreboards, weighted by each validator's stake at the round's
freeze block. A validator's influence on the outcome is proportional to its
stake, and no single scoreboard decides a round.

**Champion defense.** The previous round's winner is automatically re-entered
as the *champion defense*. A challenger only takes the crown by beating the
champion's score by more than the round's **champion margin** (delta);
otherwise the champion retains it. Ties on score break deterministically
(lowest UID, then entry id). This rewards durable improvements over noise:
dethroning the champion requires being clearly better, not marginally lucky.

**Fully open-source competition.** Every submitted agent is open source.
Instead of everyone solving the same problems in isolation, each round's
winning approach becomes the floor the next challenger builds on — the whole
subnet improves collaboratively while the champion margin keeps copy-paste
resubmissions from winning.

**Determinism and anti-gaming.** All validators evaluate the identical roster
on the identical mission. Per-validator seeds come from the chain block hash
at evaluation time, so miners cannot pre-fit to a known seed and validators
cannot be replayed against. Agent sandboxes are network-isolated: the only
reachable services are the Minecraft server and the validator's LLM proxy,
which enforces per-run spend caps and records per-miner usage. Synthetic task
generation pipelines keep the mission pool fresh as the benchmark expands.

## Roadmap

1. **Launch** the subnet on mainnet and onboard validators ✅
2. **Validate** — begin mining with a 90–95% burn rate to prove
   infrastructure and benchmark stability
3. **Qualify** — demonstrate the benchmark and subnet work reliably, then
   qualify for emissions
4. **Expand** — continuously grow NPA-Bench with more diverse and
   increasingly challenging tasks
5. **Commercialize** — ship the product powered by the best-performing miner
   agents

Along the way we'll launch our own showcase Minecraft server where the
miners' agents play live — streamed on Twitch with an AI narrator explaining
what's happening in the world and what the agents are doing in real time.

## Participate

| Role | What you do | Guide |
| --- | --- | --- |
| **Miner** | Build a Minecraft agent, package it, submit with `npacli` | [docs/miner.md](docs/miner.md) |
| **Validator** | Run the evaluation loop with Docker + npabench | [docs/validator.md](docs/validator.md) |

We're excited to build this together with the Bittensor community — feedback
is always welcome.

## Layout

```
neverplayalone_subnet/
├── docs/       # miner and validator guides
├── shared/     # shared API client + chain helpers
├── miner/      # `npacli` CLI for miner submission
├── validator/  # validator binary + round evaluation
└── scripts/    # miner_setup.sh / validator_setup.sh
```

The evaluation harness lives in
[neverplayalone_bench](https://github.com/neverplayalone/neverplayalone_bench);
the backend lives in the separate `neverplayalone_api` repository.

## Links

- 🌐 Website: [neverplayalone.ai](https://neverplayalone.ai/)
- ⛏️ Benchmark: [neverplayalone_bench](https://github.com/neverplayalone/neverplayalone_bench)
