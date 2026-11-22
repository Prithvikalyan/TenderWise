#!/usr/bin/env bash
set -Eeuo pipefail

echo "Running database migrations..."
python -m alembic upgrade head

echo "Starting TenderWise background worker..."
python -m app.worker &
WORKER_PID=$!

echo "Starting TenderWise API..."
uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-10000}" &
API_PID=$!

cleanup() {
    echo "Stopping TenderWise processes..."
    kill -TERM "$API_PID" "$WORKER_PID" 2>/dev/null || true
    wait "$API_PID" 2>/dev/null || true
    wait "$WORKER_PID" 2>/dev/null || true
}

trap cleanup EXIT INT TERM

set +e
wait -n "$API_PID" "$WORKER_PID"
STATUS=$?
exit "$STATUS"