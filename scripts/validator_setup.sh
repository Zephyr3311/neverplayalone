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

cd "$ROOT_DIR"

require() { command -v "$1" >/dev/null 2>&1; }

APT_UPDATED=0
apt_update_once() {
  if [[ "$APT_UPDATED" -eq 0 ]]; then
    sudo apt-get update -y
    APT_UPDATED=1
  fi
}

ensure_git() {
  if require git; then return; fi
  echo "installing git..."
  apt_update_once
  sudo apt-get install -y git
}

ensure_uv() {
  if require uv; then return; fi
  echo "installing uv..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
  require uv || { echo "error: uv installed but not on PATH; add \$HOME/.local/bin to PATH" >&2; exit 1; }
}

ensure_node() {
  if require node; then
    local major
    major="$(node -v | sed 's/v//' | cut -d. -f1)"
    if [[ "$major" -ge 20 ]]; then return; fi
    echo "node $(node -v) is < 20; upgrading to 20 LTS..."
  fi
  echo "installing Node.js 20 LTS + npm..."
  curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
  sudo apt-get install -y nodejs
}

ensure_docker() {
  if ! require docker; then
    echo "installing Docker Engine..."
    curl -fsSL https://get.docker.com | sudo sh
  fi
  sudo systemctl enable --now docker 2>/dev/null || true
  getent group docker >/dev/null || sudo groupadd docker || true
  sudo usermod -aG docker "$(id -un)" || true
}

ensure_git
ensure_uv
ensure_node
ensure_docker

docker info >/dev/null 2>&1 || \
  echo "note: docker is not usable as this user yet — log out and back in (or run 'newgrp docker') before starting the validator" >&2

if [[ ! -d "$VENV_DIR" ]]; then
  uv venv "$VENV_DIR"
fi
export VIRTUAL_ENV="$VENV_DIR"

uv pip install -e "$ROOT_DIR"

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
uv pip install -e "$BENCH_DIR"

# The npabench recorder is a Node tool that needs its dependencies in place.
(cd "${BENCH_DIR}/tools/recorder" && npm install)

# pm2 runs the validator as a managed background process. Prefer a global
# install; fall back to a repo-local one if the global install lacks permission.
if require pm2; then
  PM2="pm2"
elif npm install -g pm2 >/dev/null 2>&1; then
  PM2="pm2"
else
  echo "global pm2 install failed; installing pm2 into node_modules/ instead" >&2
  npm install pm2 >/dev/null 2>&1
  PM2="npx pm2"
fi

if [[ ! -f "${ROOT_DIR}/.env" ]]; then
  cp "${ROOT_DIR}/.env.example" "${ROOT_DIR}/.env"
  echo "created .env from .env.example — edit NPA_WALLET / NPA_HOTKEY before running"
fi

echo
echo "Setup complete (npabench @ $(git -C "$BENCH_DIR" rev-parse --short HEAD))."
echo "Edit .env (NPA_WALLET / NPA_HOTKEY / OPENROUTER_API_KEY), then start the validator:"
echo "  source .venv/bin/activate"
echo "  ${PM2} start validator/main.py"
