#!/usr/bin/env bash
# Deploy the training harness from the dev box to the training box (tnr-0).
#
# No git remote involved: tars the training package, ships it over scp, and
# prepares a venv on the box. The venv uses --system-site-packages ON PURPOSE:
# the box ships a preinstalled CUDA torch that must be inherited, never
# re-resolved.
#
# Usage:  bash training/deploy.sh [host]      (default host: tnr-0)
set -euo pipefail

HOST="${1:-tnr-0}"
ARCHIVE="pokeum-training.tgz"

cd "$(dirname "$0")/.."

echo "== packing =="
tar -czf "$ARCHIVE" \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  training

echo "== shipping to $HOST =="
scp -q "$ARCHIVE" "$HOST":~/
rm -f "$ARCHIVE"

echo "== installing on $HOST =="
ssh "$HOST" bash -s <<'REMOTE'
set -euo pipefail
mkdir -p ~/pokeum
tar -xzf ~/pokeum-training.tgz -C ~/pokeum
rm -f ~/pokeum-training.tgz
cd ~/pokeum
if [ ! -d .venv ]; then
  python3 -m venv --system-site-packages .venv
fi
source .venv/bin/activate
pip install --quiet --upgrade pip
pip install --quiet -r training/requirements.txt
echo "== sanity: torch/torchvision pair + CUDA =="
python - <<'PY'
import torch, torchvision
print("torch", torch.__version__, "| torchvision", torchvision.__version__)
print("cuda available:", torch.cuda.is_available())
assert torch.cuda.is_available(), "CUDA must be available on the training box"
PY
echo "deploy OK"
REMOTE

echo "== done. next: ssh $HOST 'cd ~/pokeum && source .venv/bin/activate && python -m training.smoke_test' =="
