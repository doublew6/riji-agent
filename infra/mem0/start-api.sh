#!/bin/sh
set -eu

mkdir -p /models
cp -R -n /opt/fastembed-seed/. /models/

alembic upgrade head
exec uvicorn main:app --host 0.0.0.0 --port 8000
