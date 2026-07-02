#!/usr/bin/env bash
set -euo pipefail

# Never Play Alone validator setup.
# Creates a virtualenv, installs the subnet package, and installs npabench
# from GitHub into vendor/ at the pinned ref below.

BENCH_REPO_URL="https://github.com/neverplayalone/neverplayalone_bench"
# All validators must run the same bench code or scores diverge across the
# subnet — keep this pinned to a tag or commit SHA once rounds matter.
BENCH_REF="${NPA_BENCH_REF:-main}"

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BENCH_DIR="${ROOT_DIR}/vendor/neverplayalone_bench"
VENV_DIR="${ROOT_DIR}/.venv"
PIP="${VENV_DIR}/bin/pip"

cd "$ROOT_DIR"

if [[ ! -d "$VENV_DIR" ]]; then
  python3 -m venv "$VENV_DIR"
fi

"$PIP" install --upgrade pip
"$PIP" install -e "$ROOT_DIR"

mkdir -p "${ROOT_DIR}/vendor"
if [[ ! -d "${BENCH_DIR}/.git" ]]; then
  git clone "$BENCH_REPO_URL" "$BENCH_DIR"
fi
git -C "$BENCH_DIR" fetch --tags origin
# Prefer the remote-tracking ref so a branch pin picks up new commits;
# fall back to plain checkout for tags and commit SHAs.
git -C "$BENCH_DIR" checkout --detach --quiet "origin/${BENCH_REF}" 2>/dev/null \
  || git -C "$BENCH_DIR" checkout --detach --quiet "$BENCH_REF"

# npabench resolves its docker/ and tools/ directories relative to the repo
# checkout, so it must be installed editable from the clone — a plain
# `pip install git+URL` would strip those files and break evaluation.
"$PIP" install -e "$BENCH_DIR"

# The npabench recorder is a Node tool that needs its dependencies in place.
if command -v npm >/dev/null 2>&1; then
  (cd "${BENCH_DIR}/tools/recorder" && npm install)
else
  echo "warning: npm not found; run 'cd ${BENCH_DIR}/tools/recorder && npm install' before evaluating rounds" >&2
fi

if [[ ! -f "${ROOT_DIR}/.env" ]]; then
  cp "${ROOT_DIR}/.env.example" "${ROOT_DIR}/.env"
  echo "created .env from .env.example — edit NPA_WALLET / NPA_HOTKEY before running"
fi

echo
echo "Setup complete (npabench @ $(git -C "$BENCH_DIR" rev-parse --short HEAD)). Run the validator with:"
echo "  set -a; source .env; set +a"
echo "  ${VENV_DIR}/bin/npa-validator"
