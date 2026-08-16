@echo off
rem Open Orion HUD launcher for Windows.
cd /d "%~dp0\.."
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -m orion.ui.jarvis_hud %*
) else (
    python -m orion.ui.jarvis_hud %*
)