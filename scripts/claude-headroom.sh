#!/usr/bin/env bash
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
#   ./scripts/claude-headroom.sh
#   ./scripts/claude-headroom.sh -p "run the verify gate"

set -euo pipefail
cd "$(dirname "$0")/.."

if ! command -v headroom >/dev/null 2>&1; then
  echo "headroom not found. Activate the venv and install dev deps first:" >&2
  echo "  . .venv/bin/activate && pip install -r requirements-dev.txt" >&2
  exit 1
fi

exec headroom wrap claude -- "$@"
