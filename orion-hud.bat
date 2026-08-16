@echo off
rem Open Orion HUD launcher for Windows.
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" jarvis_hud.py %*
) else (
    python jarvis_hud.py %*
)