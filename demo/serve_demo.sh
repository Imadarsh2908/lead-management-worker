#!/usr/bin/env bash
# demo/serve_demo.sh — launch a zero-dependency local demo server.
#
# Starts the API on SQLite (no Postgres) in development mode (demo seams on).
# Redis is optional — the checkpointer falls back to in-memory automatically.
#
# Per-scenario server env can be layered on top, e.g.:
#   LLM_ENABLED=true LLM_API_KEY=sk-or-... bash demo/serve_demo.sh
#   ENRICHMENT_API_URL=http://127.0.0.1:9 LLM_ENABLED=true LLM_API_KEY=... bash demo/serve_demo.sh
#   LLM_FORCE_MALFORMED=true LLM_ENABLED=true LLM_API_KEY=... bash demo/serve_demo.sh
#   LLM_FORCE_CONFIDENCE=0.40 LLM_ENABLED=true LLM_API_KEY=... bash demo/serve_demo.sh
#
# Defaults (offline): ENVIRONMENT=development, SQLite, LLM disabled.
set -euo pipefail
cd "$(dirname "$0")/.."

export ENVIRONMENT="${ENVIRONMENT:-development}"
export DATABASE_URL="${DATABASE_URL:-sqlite:///./demo.db}"
export LLM_ENABLED="${LLM_ENABLED:-false}"

echo "Starting demo server: ENVIRONMENT=$ENVIRONMENT DATABASE_URL=$DATABASE_URL LLM_ENABLED=$LLM_ENABLED"
exec python -m uvicorn app.main:app --host 127.0.0.1 --port "${PORT:-8000}"
