@echo off
setlocal
cd /d "%~dp0"

if not exist .venv (
  python -m venv .venv
)
call .venv\Scripts\activate.bat
pip install -q -r requirements.txt

if not exist data\typeahead.db (
  echo Ingesting dataset (first run)...
  python scripts\ingest_dataset.py
)

echo Starting server at http://localhost:8000
uvicorn app.main:app --host 0.0.0.0 --port 8000
