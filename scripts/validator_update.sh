#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SETUP_SCRIPT="${ROOT_DIR}/scripts/validator_setup.sh"
PM2_NAME="${NPA_PM2_NAME:-}"
RESTART_PM2=1

usage() {
  cat <<'EOF'
Usage: ./scripts/validator_update.sh [--pm2-name NAME] [--no-restart]

Fast-forwards the current git branch, re-runs validator_setup.sh to refresh
Python/npabench/recorder dependencies, and optionally restarts the PM2 process.

Options:
  --pm2-name NAME  Restart this PM2 app after updating.
  --no-restart     Skip PM2 restart even if --pm2-name is provided.

Environment:
  NPA_PM2_NAME     Default PM2 process name to restart.
  NPA_ALLOW_DIRTY=1
                   Allow updates even if the repo has uncommitted changes.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --pm2-name)
      [[ $# -ge 2 ]] || { echo "error: --pm2-name requires a value" >&2; exit 1; }
      PM2_NAME="$2"
      shift 2
      ;;
    --no-restart)
      RESTART_PM2=0
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "error: unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

require() { command -v "$1" >/dev/null 2>&1; }

require git || { echo "error: git is required" >&2; exit 1; }

cd "$ROOT_DIR"

if [[ "${NPA_ALLOW_DIRTY:-0}" != "1" ]] && [[ -n "$(git status --short)" ]]; then
  cat >&2 <<'EOF'
error: repository has local changes.

Commit or stash them first, or rerun with:
  NPA_ALLOW_DIRTY=1 ./scripts/validator_update.sh
EOF
  exit 1
fi

current_branch="$(git rev-parse --abbrev-ref HEAD)"
if [[ "$current_branch" == "HEAD" ]]; then
  echo "error: detached HEAD; checkout a branch before updating" >&2
  exit 1
fi

echo "Updating subnet repo on branch: ${current_branch}"
git fetch --prune origin
git pull --ff-only origin "$current_branch"

echo "Refreshing validator environment"
"$SETUP_SCRIPT"

if [[ "$RESTART_PM2" -eq 1 && -n "$PM2_NAME" ]]; then
  if require pm2; then
    echo "Restarting PM2 process: ${PM2_NAME}"
    pm2 restart "$PM2_NAME" --update-env
  else
    echo "warning: pm2 not found; skipped restart for ${PM2_NAME}" >&2
  fi
elif [[ "$RESTART_PM2" -eq 1 ]]; then
  echo "No PM2 process name supplied; skipped restart"
fi

echo "Validator update complete."
