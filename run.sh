#!/usr/bin/env bash
# Local-only startup with isolated Python dependencies and a locked UI install.
set -euo pipefail
cd "$(dirname "$0")"
if [ -f .env ]; then set -a; . ./.env; set +a; fi
python3 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements-lock.txt
(cd frontend && npm ci && npm run build)
.venv/bin/python -m uvicorn app:app --app-dir backend --host 127.0.0.1 --port 8791 &
BACKEND_PID=$!
(cd frontend && exec npm start) &
FRONTEND_PID=$!
cleanup() { kill "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null || true; }
trap cleanup EXIT INT TERM
wait -n "$BACKEND_PID" "$FRONTEND_PID"
