#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UPDATE_SCRIPT="${ROOT_DIR}/scripts/validator_update.sh"
BENCH_DIR="${ROOT_DIR}/vendor/neverplayalone_bench"

if [[ -f "${ROOT_DIR}/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "${ROOT_DIR}/.env"
  set +a
fi

LOCK_FILE="${NPA_UPDATE_LOCK_FILE:-/tmp/npa_validator_autoupdate.lock}"
VALIDATOR_PM2_NAME="${NPA_VALIDATOR_PM2_NAME:-${NPA_PM2_NAME:-validator}}"
VALIDATOR_ENTRYPOINT="${NPA_VALIDATOR_ENTRYPOINT:-${ROOT_DIR}/validator/main.py}"
VALIDATOR_INTERPRETER="${NPA_VALIDATOR_INTERPRETER:-${ROOT_DIR}/.venv/bin/python}"
AUTOSTART_VALIDATOR="${NPA_AUTOSTART_VALIDATOR:-1}"
SUBNET_BRANCH="${NPA_UPDATE_SUBNET_BRANCH:-main}"
BENCH_REF="${NPA_BENCH_REF:-main}"
INTERVAL_SECONDS="${NPA_UPDATE_INTERVAL_SECONDS:-600}"
EARLY_WINDOW_BLOCKS="${NPA_UPDATE_EARLY_WINDOW_BLOCKS:-50}"

usage() {
  cat <<'EOF'
Usage: ./scripts/validator_autoupdate.sh

Permanent loop that:
  1. checks every NPA_UPDATE_INTERVAL_SECONDS for upstream drift in:
     - the subnet repo branch (default: origin/main)
     - vendor/neverplayalone_bench (default: origin/main)
  2. updates/restarts only when the current block is within
     NPA_UPDATE_EARLY_WINDOW_BLOCKS blocks before the next round start
  3. delegates the actual update to validator_update.sh

Environment:
  NPA_VALIDATOR_PM2_NAME=validator       PM2 validator app to start/restart
  NPA_PM2_NAME=validator                 legacy fallback for NPA_VALIDATOR_PM2_NAME
  NPA_AUTOSTART_VALIDATOR=1              start validator on updater boot if missing
  NPA_VALIDATOR_ENTRYPOINT=...           validator entrypoint passed to pm2 start
  NPA_VALIDATOR_INTERPRETER=...          Python interpreter passed to pm2 start
  NPA_UPDATE_SUBNET_BRANCH=main          subnet branch to track
  NPA_BENCH_REF=main                     bench ref to track
  NPA_UPDATE_INTERVAL_SECONDS=600        drift check interval
  NPA_UPDATE_EARLY_WINDOW_BLOCKS=50      restart window before round start
  NPA_UPDATE_LOCK_FILE=/tmp/...          flock file path
  NPA_ALLOW_DIRTY=1                      forwarded to validator_update.sh
EOF
}

require() { command -v "$1" >/dev/null 2>&1; }

log() {
  printf '%s %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*"
}

resolve_remote_ref() {
  local repo="$1"
  local ref="$2"
  if git -C "$repo" rev-parse --verify -q "refs/remotes/origin/${ref}" >/dev/null; then
    git -C "$repo" rev-parse "refs/remotes/origin/${ref}"
  else
    git -C "$repo" rev-parse "$ref"
  fi
}

has_subnet_drift() {
  git -C "$ROOT_DIR" fetch --prune origin "$SUBNET_BRANCH" >/dev/null 2>&1
  local local_ref remote_ref
  local_ref="$(git -C "$ROOT_DIR" rev-parse HEAD)"
  remote_ref="$(resolve_remote_ref "$ROOT_DIR" "$SUBNET_BRANCH")"
  if [[ "$local_ref" != "$remote_ref" ]]; then
    log "subnet update available local=${local_ref} remote=${remote_ref}"
    return 0
  fi
  return 1
}

has_bench_drift() {
  if [[ ! -d "$BENCH_DIR/.git" ]]; then
    log "bench checkout missing at ${BENCH_DIR}"
    return 0
  fi
  git -C "$BENCH_DIR" fetch --prune --tags origin >/dev/null 2>&1
  local local_ref remote_ref
  local_ref="$(git -C "$BENCH_DIR" rev-parse HEAD)"
  remote_ref="$(resolve_remote_ref "$BENCH_DIR" "$BENCH_REF")"
  if [[ "$local_ref" != "$remote_ref" ]]; then
    log "bench update available local=${local_ref} remote=${remote_ref}"
    return 0
  fi
  return 1
}

in_prestart_window() {
  (
    cd "$ROOT_DIR"
    set -a
    source .env
    set +a
    .venv/bin/python - <<'PY'
from shared import chain
from shared.api_client import APIClient
from validator.config import API_URL

WINDOW = int(__import__("os").environ.get("NPA_UPDATE_EARLY_WINDOW_BLOCKS", "50"))

wallet = chain.make_wallet(
    __import__("os").environ.get("NPA_WALLET", "default"),
    __import__("os").environ.get("NPA_HOTKEY", "default"),
)
api = APIClient(wallet, base_url=API_URL)
try:
    rounds = api.get_current_rounds()
finally:
    api.close()

submission = rounds.get("submission_round")
if not submission:
    print("no_submission_round")
    raise SystemExit(1)

current_block = int(chain.current_block())
round_start_block = int(submission["evaluation_start_block"])
blocks_until_start = round_start_block - current_block

if blocks_until_start <= 0:
    print(
        f"round_already_started round={submission['round_id']} current_block={current_block} "
        f"round_start_block={round_start_block}"
    )
    raise SystemExit(1)
if blocks_until_start > WINDOW:
    print(
        f"outside_prestart_window round={submission['round_id']} current_block={current_block} "
        f"round_start_block={round_start_block} blocks_until_start={blocks_until_start} window={WINDOW}"
    )
    raise SystemExit(1)

print(
    f"inside_prestart_window round={submission['round_id']} current_block={current_block} "
    f"round_start_block={round_start_block} blocks_until_start={blocks_until_start} window={WINDOW}"
)
PY
  )
}

run_update() {
  log "running validator update via ${UPDATE_SCRIPT}"
  (
    cd "$ROOT_DIR"
    export NPA_VALIDATOR_PM2_NAME="$VALIDATOR_PM2_NAME"
    export NPA_PM2_NAME="$VALIDATOR_PM2_NAME"
    export NPA_VALIDATOR_ENTRYPOINT="$VALIDATOR_ENTRYPOINT"
    export NPA_VALIDATOR_INTERPRETER="$VALIDATOR_INTERPRETER"
    "$UPDATE_SCRIPT" --pm2-name "$VALIDATOR_PM2_NAME"
  )
  log "validator update complete"
}

ensure_validator_pm2() {
  if [[ "$AUTOSTART_VALIDATOR" != "1" ]]; then
    log "validator autostart disabled validator_pm2=${VALIDATOR_PM2_NAME}"
    return 0
  fi
  if ! require pm2; then
    log "pm2 not found; validator autostart skipped validator_pm2=${VALIDATOR_PM2_NAME}"
    return 0
  fi
  if pm2 describe "$VALIDATOR_PM2_NAME" >/dev/null 2>&1; then
    log "validator PM2 process already exists validator_pm2=${VALIDATOR_PM2_NAME}"
    return 0
  fi

  log "starting validator PM2 process validator_pm2=${VALIDATOR_PM2_NAME} entrypoint=${VALIDATOR_ENTRYPOINT}"
  (
    cd "$ROOT_DIR"
    pm2 start "$VALIDATOR_ENTRYPOINT" \
      --name "$VALIDATOR_PM2_NAME" \
      --interpreter "$VALIDATOR_INTERPRETER" \
      --cwd "$ROOT_DIR" \
      --update-env
  )
}

main() {
  if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    usage
    exit 0
  fi

  require git || { echo "error: git is required" >&2; exit 1; }

  mkdir -p "$(dirname "$LOCK_FILE")"
  exec 9>"$LOCK_FILE"
  flock -n 9 || { echo "error: validator_autoupdate.sh already running" >&2; exit 1; }

  log "autoupdate loop started validator_pm2=${VALIDATOR_PM2_NAME} subnet_branch=${SUBNET_BRANCH} bench_ref=${BENCH_REF} interval=${INTERVAL_SECONDS}s prestart_window_blocks=${EARLY_WINDOW_BLOCKS} autostart_validator=${AUTOSTART_VALIDATOR}"
  ensure_validator_pm2

  while true; do
    subnet_drift=0
    bench_drift=0

    if has_subnet_drift; then
      subnet_drift=1
    fi
    if has_bench_drift; then
      bench_drift=1
    fi

    if [[ "$subnet_drift" -eq 1 || "$bench_drift" -eq 1 ]]; then
      if window_msg="$(in_prestart_window 2>&1)"; then
        log "$window_msg"
        if run_update; then
          :
        else
          log "update attempt failed"
        fi
      else
        log "update pending but skipped: ${window_msg}"
      fi
    else
      log "no updates detected"
    fi

    sleep "$INTERVAL_SECONDS"
  done
}

main "$@"
