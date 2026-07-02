#!/usr/bin/env bash
set -euo pipefail

# Never Play Alone miner setup.
# Creates a virtualenv and installs the `npacli` submission CLI.
# Miners do not need npabench or Docker.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${ROOT_DIR}/.venv"
PIP="${VENV_DIR}/bin/pip"

cd "$ROOT_DIR"

if [[ ! -d "$VENV_DIR" ]]; then
  python3 -m venv "$VENV_DIR"
fi

"$PIP" install --upgrade pip
"$PIP" install -e "$ROOT_DIR"

echo
echo "Setup complete. Use the CLI with:"
echo "  ${VENV_DIR}/bin/npacli status"
echo "  ${VENV_DIR}/bin/npacli submit ./agent.tar.gz --wallet miner --hotkey hk1"
