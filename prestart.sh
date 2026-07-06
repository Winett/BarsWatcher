#!/usr/bin/env bash
set -e

echo "Running apply migrations"
alembic upgrade head
echo "Migrations applied"

exec "$@"
