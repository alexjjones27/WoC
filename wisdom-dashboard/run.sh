#!/usr/bin/env bash
# Single-command launcher: installs/updates backend deps, builds the
# frontend if needed, then serves API + frontend from one FastAPI process
# on one port -- no CORS, no second terminal.
set -euo pipefail
cd "$(dirname "$0")"

PORT="${PORT:-8000}"

echo "==> checking backend dependencies"
python3 -m pip install -q -r backend/requirements.txt

if [ ! -d "frontend/dist" ] || [ "${REBUILD_FRONTEND:-0}" = "1" ]; then
  echo "==> building frontend"
  (cd frontend && npm install --no-audit --no-fund --silent && npm run build --silent)
else
  echo "==> using existing frontend/dist (set REBUILD_FRONTEND=1 to force a rebuild)"
fi

echo "==> starting server on http://localhost:${PORT}"
cd backend
exec python3 -m uvicorn app:app --host 0.0.0.0 --port "${PORT}"
