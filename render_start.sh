#!/usr/bin/env bash
set -e

echo "Running migrations..."
alembic upgrade head

echo "Seeding core admin user..."
python seed.py

echo "Seeding dummy data..."
python seed_dummy.py

echo "Starting server..."
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
