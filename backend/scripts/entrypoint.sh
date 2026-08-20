#!/bin/sh
set -eu

case "${1:-api}" in
  migrate)
    echo "Running database schema initialization..."
    exec python -c "from datetime import date; from app.db import init_db, cleanup_expired_tours; init_db(); print('expired removed:', cleanup_expired_tours(date.today().isoformat()))"
    ;;
  api)
    echo "Starting API with ${WEB_CONCURRENCY:-3} workers..."
    exec python -m uvicorn app.main:app \
      --host 0.0.0.0 \
      --port 8000 \
      --workers "${WEB_CONCURRENCY:-3}" \
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