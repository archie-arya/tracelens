#!/usr/bin/env bash
# Run TraceLens locally (no Docker): builds the dashboard, installs the server into ./.venv, starts it on localhost:8000.
# Data stays on this machine in ./data/tracelens.db.
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PYTHON:-python3}"
if [ ! -f server/tracelens_server/static/index.html ]; then
  if command -v npm >/dev/null; then
    (cd dashboard && npm ci --no-audit --no-fund && VITE_OUT_DIR=../server/tracelens_server/static npx vite build)
  else
    echo "npm not found: starting API-only (install Node 18+ for the dashboard)" >&2
  fi
fi
[ -d .venv ] || "$PY" -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q ./server
export TRACELENS_INIT_API_KEY="${TRACELENS_INIT_API_KEY:-tl-sk-local-dev}"
export TRACELENS_PUBLIC_DEMO="${TRACELENS_PUBLIC_DEMO:-false}"
echo "API key for your apps: $TRACELENS_INIT_API_KEY"
exec .venv/bin/tracelens-server --data-dir ./data "$@"
