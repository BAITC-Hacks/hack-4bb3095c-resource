#!/bin/sh
# Установка (один раз) и запуск: ./run.sh  -> http://localhost:8501
set -e
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  python3 -m venv .venv
  .venv/bin/pip install -q -r requirements.txt
fi
exec .venv/bin/streamlit run app/app.py --server.headless true --server.port "${PORT:-8501}"
