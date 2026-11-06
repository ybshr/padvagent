#!/bin/bash
set -e

HOST="${BIND_HOST:-0.0.0.0}"
PORT="${PORT:-8000}"

if [ "${APP_ENV:-dev}" = "production" ]; then
    exec poetry run uvicorn src.main:app --host "$HOST" --port "$PORT"
fi

exec poetry run uvicorn src.main:app --host "$HOST" --port "$PORT" --reload
