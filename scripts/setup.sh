#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
command -v uv >/dev/null || { echo "Install uv from https://docs.astral.sh/uv/ first."; exit 1; }
command -v docker >/dev/null || { echo "Install and start Docker first."; exit 1; }
command -v npm >/dev/null || { echo "Install Node.js 20 or newer first."; exit 1; }
if [[ ! -f .env ]]; then
  cp .env.example .env
  chmod 600 .env
fi
uv sync --frozen
uv venv .tools/codegraph --python 3.12 --allow-existing
uv pip install --python .tools/codegraph/bin/python -r codegraph-requirements.txt
docker compose up -d --wait falkordb
npm --prefix frontend ci
npm --prefix frontend run build
echo "Setup complete. Start Atlas with: ./scripts/start.sh"
