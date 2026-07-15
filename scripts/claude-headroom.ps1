# Start a Claude Code session behind the Headroom context-compression proxy.
#
# Headroom compresses tool output, logs, and history before they reach the
# model (60-95% fewer tokens on log-heavy content, reversible on demand).
# It is OPTIONAL and opt-in per session: plain `claude` works unchanged.
#
# One-time setup (installs into the repo venv, nothing global):
#   pip install -r requirements-dev.txt
#
# Usage (any extra arguments are passed through to claude):
#   .\scripts\claude-headroom.ps1
#   .\scripts\claude-headroom.ps1 -p "run the verify gate"

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

if (-not (Get-Command headroom -ErrorAction SilentlyContinue)) {
    Write-Host "headroom not found. Activate the venv and install dev deps first:" -ForegroundColor Yellow
    Write-Host "  . .venv/Scripts/activate; pip install -r requirements-dev.txt"
    exit 1
}

headroom wrap claude -- @args
