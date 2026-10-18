#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
docker compose --profile app --profile tools build atlas sandbox
docker compose --profile app up -d atlas runner
printf 'Atlas is running at http://localhost:8001\n'
