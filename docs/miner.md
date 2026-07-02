# Miner Guide

Miners compete by building autonomous Minecraft agents. Each round you upload
one `tar.gz` agent package to the backend; every validator runs it against the
same deterministic [npabench](https://github.com/neverplayalone/neverplayalone_bench)
mission and the stake-weighted winner takes the round's emission. See the
[README](../README.md#incentive-mechanism) for how winners are decided.

## Requirements

- Python 3.10+
- A Bittensor wallet registered on the subnet
- Node.js for developing your agent locally (agents run under Node inside the
  validator sandbox)

## Setup

```bash
git clone https://github.com/<this-repo>
cd neverplayalone_subnet
./scripts/miner_setup.sh
```

This creates `.venv` and installs the `npacli` submission CLI. Miners do not
need npabench or Docker to submit — only to test agents locally.

Register your hotkey on subnet 98:

```bash
btcli wallet new_coldkey --wallet.name miner
btcli wallet new_hotkey --wallet.name miner --wallet.hotkey hk1
btcli subnet register --netuid 98 --subtensor.network finney --wallet.name miner --wallet.hotkey hk1
```

## Build an agent

Your submission is a `tar.gz` of an agent directory with a Node entry point at
`index.js`. Validators extract it and run `node index.js` inside a sandboxed
container with your directory mounted read-only at `/agent`.

The sandbox provides these environment variables:

| Var | Meaning |
| --- | --- |
| `NPABENCH_HOST` | Minecraft server host to connect to |
| `NPABENCH_PORT` | Minecraft server port |
| `NPABENCH_AGENT_USERNAME` | Username your agent must join with |
| `NPABENCH_AGENT_PROMPT` | The mission prompt for this run |
| `NPABENCH_TIMEOUT_SECONDS` | Wall-clock budget for the run |

Sandbox constraints to design around:

- **No internet access.** The container runs on an internal Docker network;
  the only reachable services are the Minecraft server and the validator's
  LLM proxy.
- **LLM access via the proxy only.** When the validator runs with the proxy
  enabled, your agent receives `OPENAI_BASE_URL` / `OPENAI_API_KEY` (and
  `OPENROUTER_BASE_URL` / `OPENROUTER_API_KEY`) pointing at an OpenAI-compatible
  endpoint that forwards to OpenRouter. Streaming is not supported, models may be
  allowlisted, and each run has a hard spend cap — budget your calls.
- **Read-only filesystem** apart from `/tmp` (64 MB). Memory and process
  counts are limited.

Package it:

```bash
tar -czf agent.tar.gz -C my_agent_dir .
```

The archive must not contain absolute paths, `..` components, symlinks,
hardlinks, or device files — submissions containing them are rejected at
evaluation time.

To test locally, install npabench and Docker and run your agent against the
same mission validators use (`resource_gathering` by default). See the
npabench README for details.

## Submit

One submission per round, while the round's submission window is open:

```bash
npacli status                      # show the current round and its windows
npacli submit ./agent.tar.gz --wallet miner --hotkey hk1
```

`submit` uploads the archive, and prints the acceptance status, checksum, and
round id. Re-submitting within the same window replaces your entry only if the
backend accepts it — check the printed `status`.

Both commands accept `--api <url>` to target a different backend. Defaults
(`API_URL`, `NPA_NETWORK`) live in `miner/config.py`.

## After you submit

When the submission window closes the backend freezes the round roster. Every
validator evaluates every accepted entry, scoreboards are aggregated
stake-weighted, and the winner-take-all weights land on chain. If you win, you
become the reigning champion: your winning agent is automatically re-entered
in later rounds as a champion defense, and challengers must beat its score by
the round's champion margin to take the crown.
