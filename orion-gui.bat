@echo off
rem Open Orion GUI (JARVIS-style deck) launcher for Windows.
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" main.py --gui %*
) else (
    python main.py --gui %*
)