# Open Orion — one-command installer for Windows (PowerShell).
#
# Usage:
#   .\install.ps1                  # everything (default: core + cloud API + voice deps)
#   .\install.ps1 -Core            # CLI core only (no cloud API, no voice deps)
#   .\install.ps1 -Api             # + cloud API backends (LiteLLM)
#   .\install.ps1 -Voice           # + local STT/TTS packages
#   .\install.ps1 -VoicePipeline   # + streaming voice-cloning tool
#
# Note: talk mode (mic + spoken replies) is currently Linux-only — the voice
# packages install fine on Windows, but the agent's voice engine needs
# PulseAudio/PipeWire and disables itself gracefully on native Windows.

param(
    [switch]$Core,
    [switch]$Api,
    [switch]$Voice,
    [switch]$VoicePipeline,
    [switch]$Recreate,
    [switch]$Help
)

$ErrorActionPreference = "Stop"

if ($Help) {
    Get-Content $PSCommandPath | Select-Object -First 16
    exit 0
}

# This script lives in scripts/; every path is relative to the repo root.
Set-Location (Join-Path $PSScriptRoot "..")

# Pick the variant to install.
$variant = "all"
if ($Core) { $variant = "core" }
elseif ($Api) { $variant = "api" }
elseif ($Voice) { $variant = "voice" }
elseif ($VoicePipeline) { $variant = "voice-pipeline" }

# --- find a Python 3.11+ ---------------------------------------------
# Probe candidate interpreters with Get-Command first: under
# $ErrorActionPreference = "Stop" an unguarded `&` against a missing
# executable raises CommandNotFoundException and kills the whole script.
$py = $null
foreach ($candidate in @("python3.13", "python3.12", "python3.11", "python")) {
    if (-not (Get-Command $candidate -ErrorAction SilentlyContinue)) { continue }
    try {
        $probe = & $candidate -c "import sys; print(sys.version_info[0]*10 + sys.version_info[1])" 2>$null
    } catch { continue }
    if ($LASTEXITCODE -eq 0 -and [int]$probe -ge 311) {
        $py = $candidate
        break
    }
}
# Windows `py` launcher: accept any 3.11+ even when "python" is the Store stub.
if (-not $py -and (Get-Command py -ErrorAction SilentlyContinue)) {
    foreach ($ver in @("3.13", "3.12", "3.11")) {
        try {
            $probe = & py "-$ver" -c "import sys; print(sys.version_info[0]*10 + sys.version_info[1])" 2>$null
        } catch { continue }
        if ($LASTEXITCODE -eq 0 -and [int]$probe -ge 311) {
            $py = @("py", "-$ver")
            break
        }
    }
}
if (-not $py) {
    Write-Host "install.ps1: no Python >= 3.11 found. Install one from https://www.python.org/downloads/ and retry." -ForegroundColor Red
    exit 1
}
Write-Host "-> using $py"

# --- create / refresh the venv -----------------------------------------
$venv = ".venv"
if ($Recreate -and (Test-Path $venv)) {
    Write-Host "-> removing existing $venv"
    Remove-Item -Recurse -Force $venv
}
if (-not (Test-Path $venv)) {
    Write-Host "-> creating virtual environment in $venv"
    & $py -m venv $venv
    if ($LASTEXITCODE -ne 0) { Write-Host "install.ps1: venv creation failed" -ForegroundColor Red; exit 1 }
}

$venvPy = Join-Path $venv "Scripts\python.exe"
$venvPip = Join-Path $venv "Scripts\pip.exe"
& $venvPy -m pip install --upgrade pip -q

# --- install the package -------------------------------------------------
Write-Host "-> installing $variant variant"
switch ($variant) {
    "core"          { & $venvPip install -e . }
    "api"           { & $venvPip install -e ".[api]" }
    "voice"         { & $venvPip install -e ".[voice]" }
    "voice-pipeline"{ & $venvPip install -e ".[voice-pipeline]" }
    "all"           { & $venvPip install -e ".[all]" }
}
if ($LASTEXITCODE -ne 0) { Write-Host "install.ps1: package install failed" -ForegroundColor Red; exit 1 }

# --- optional first-run niceties -----------------------------------------
if (-not (Test-Path .env)) {
    Copy-Item .env.example .env
    Write-Host "-> created .env from .env.example (edit it to tweak settings)"
}

Write-Host ""
Write-Host "Open Orion installed in $venv" -ForegroundColor Green
Write-Host ""
Write-Host "Next steps:"
Write-Host "  1. Start a local model:     ollama serve && ollama pull qwen3.5:4b"
Write-Host "  2. Run the CLI:             .\$venv\Scripts\python.exe main.py"
Write-Host "  3. Run the HUD:             .\$venv\Scripts\python.exe main.py --gui"
Write-Host "                              (or .\$venv\Scripts\python.exe -m orion.ui.jarvis_hud)"
Write-Host "  4. Voice-cloning pipeline:  .\$venv\Scripts\python.exe tools\voice_pipeline.py --interactive"