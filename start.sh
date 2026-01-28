#!/usr/bin/env bash
set -e

echo "Running migrations..."
alembic upgrade head

if [ "${RUN_CREATE_ADMIN:-1}" = "1" ]; then
  ADMIN_EMAIL="${ADMIN_EMAIL:-admin@ascend.com}"
  ADMIN_PASSWORD="${ADMIN_PASSWORD:-password}"
  ADMIN_ROLE="${ADMIN_ROLE:-ADMIN}"
  echo "Ensuring admin user..."
  python scripts/create_admin_user.py \
    --email "$ADMIN_EMAIL" \
    --password "$ADMIN_PASSWORD" \
    --role "$ADMIN_ROLE" \
    --skip-if-exists
fi

echo "Starting server..."
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
