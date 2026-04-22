@echo off
cd /d %~dp0
if not exist .venv (
    echo Tworze virtual environment...
    py -m venv .venv

    echo Instaluje wymagane biblioteki...
    .\.venv\Scripts\python.exe -m pip install -r requirements.txt
)
echo Uruchamiam aplikacje...
.\.venv\Scripts\python.exe main_google_api.py
pause
