#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -q -r requirements.txt

if [ ! -f data/typeahead.db ]; then
  echo "Ingesting dataset (first run)..."
  python scripts/ingest_dataset.py
fi

echo "Starting server at http://localhost:8000"
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
