#!/bin/sh
set -eu

case "${1:-api}" in
  migrate)
    echo "Running database schema initialization..."
    exec python -c "from datetime import date; from app.db import init_db, cleanup_expired_tours; init_db(); print('expired removed:', cleanup_expired_tours(date.today().isoformat()))"
    ;;
  api)
    # PORT ni platforma beradi (Railway, Render, Fly). Berilmasa 8000.
    echo "Starting API on port ${PORT:-8000} with ${WEB_CONCURRENCY:-2} workers..."
    exec python -m uvicorn app.main:app \
      --host 0.0.0.0 \
      --port "${PORT:-8000}" \
      --workers "${WEB_CONCURRENCY:-2}" \
      --proxy-headers \
      --forwarded-allow-ips="*"
    ;;
  worker)
    echo "Starting Redis background worker..."
    exec python -m app.worker
    ;;
  scheduler)
    echo "Starting pipeline scheduler..."
    exec python -m app.scheduler
    ;;
  *)
    exec "$@"
    ;;
esac