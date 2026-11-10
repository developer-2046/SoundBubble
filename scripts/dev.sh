#!/usr/bin/env bash
# Start the local SoundBubble backend (with model) and the Vite dev server.
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] && set -a && . ./.env && set +a
source .venv/bin/activate
uvicorn soundbubble.app:app --app-dir backend --host 127.0.0.1 --port 8000 &
API=$!
trap 'kill $API' EXIT
(cd frontend && npm run dev -- --host 127.0.0.1)
