@echo off
rem Double-click to open SubtleSpawns Studio in your browser.
cd /d "%~dp0"
start "" http://127.0.0.1:5055
python app.py
