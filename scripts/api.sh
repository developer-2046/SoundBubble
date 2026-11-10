#!/usr/bin/env bash
# Run only the backend (foreground). Reads .env if present.
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] && set -a && . ./.env && set +a
source .venv/bin/activate
exec uvicorn soundbubble.app:app --app-dir backend --host "${HOST:-127.0.0.1}" --port "${PORT:-8000}"
