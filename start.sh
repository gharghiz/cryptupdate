#!/bin/bash
# CryptositNews - Start Script for Railway
set -e

PORT="${PORT:-8000}"
echo "Starting CryptositNews on port $PORT..."

exec gunicorn wsgi:app \
    --workers 2 \
    --threads 4 \
    --timeout 120 \
    --bind "0.0.0.0:${PORT}" \
    --access-logfile - \
    --error-logfile -
