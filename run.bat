@echo off
REM Windows: установка (один раз) и запуск -> http://localhost:8501
cd /d "%~dp0"
if not exist .venv (
  python -m venv .venv
  .venv\Scripts\pip install -r requirements.txt
)
.venv\Scripts\streamlit run app\app.py --server.headless true --server.port 8501
