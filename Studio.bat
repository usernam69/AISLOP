@echo off
rem Double-click to open SubtleSpawns Studio in your browser.
cd /d "%~dp0"
start "" powershell -NoProfile -Command "Start-Sleep 2; Start-Process http://127.0.0.1:5055"
python app.py
