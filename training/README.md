# Embedder fine-tune harness

One-time metric-learning fine-tune of DINOv2-S so retrieval survives glare,
perspective, and blur. It trains **photography invariance, never card
identities** — new sets still require zero training, only `sync` + `index
build`. Design rationale lives in `openwiki/training.md`; the deployed encoder
contract is `app/signals/embedding.py` (`OnnxEmbedder`).

This package is standalone: it never imports `app/` and `app/` never imports
it. It runs on a GPU box (recon'd: A100-80GB, preinstalled CUDA torch), not on
the dev machine — which is why it has no offline unit tests; its verification
is `smoke_test.py` run on the box.

## Runbook

```bash
# 1. Deploy from the dev box (tars training/, scp, venv with
#    --system-site-packages to inherit the box's CUDA torch, pip install).
bash training/deploy.sh tnr-0

# 2. Smoke test on the box (~5 min): fetch 3 tiny sets -> cache -> hub model
#    download -> 4 epochs -> export -> torch-vs-onnxruntime parity. All stages
#    must print PASS.
ssh tnr-0 'cd ~/pokeum && source .venv/bin/activate && python -m training.smoke_test'

# 3. Full run (inside tmux so an SSH drop cannot kill it):
ssh tnr-0
tmux new -s train
cd ~/pokeum && source .venv/bin/activate
python -m training.fetch_data --out training_data          # ~15-20 min, all EN sets, low-res
python -m training.cache --data training_data              # ~2 min -> cache/images.npy
python -m training.train --data training_data --run-dir runs/ft   # ~45-60 min
python -m training.export_onnx --checkpoint runs/ft/best.pt \
    --out runs/ft/dinov2s-ft-v1.onnx --data training_data   # export + parity gate

# 4. Monitor from the dev box:
ssh tnr-0 'tail -n 20 ~/pokeum/runs/ft/metrics.csv'

# 5. Bring the artifact home and promote it:
scp tnr-0:~/pokeum/runs/ft/dinov2s-ft-v1.onnx data/models/
# point EMBED_MODEL_PATH at it (or replace the default path), then:
python main.py index build --full
```

Resume an interrupted run with `python -m training.train --resume runs/ft`
(checkpoints carry model+optimizer+scheduler+epoch+RNG).

## Guards built into training

- **Epoch-0 frozen baseline** is evaluated before any training step — every
  later eval must beat it; if the combined val metric ever falls *below* it,
  the run aborts (the "LR destroyed the pretrained transfer" signature).
- Early stop after 5 evals without improvement; `best.pt` selected by
  `0.5*full_top1 + 0.5*art_top1` (mirrors the app's fusion weights).
- Collapse monitor (mean off-diagonal gallery cosine), clean-gallery drift
  report, non-finite-loss abort, one-shot batch halving on CUDA OOM.

## Versioning the artifact

Always export under a **new filename** (`dinov2s-ft-v1.onnx`, `-v2`, ...).
The app's `OnnxEmbedder.identifier` is filename-based; the identifier change is
what tells `index build` to re-embed the catalogue. The parity gate
(torch-vs-onnxruntime cosine > 0.999 on real card images, app-exact
preprocessing) must pass before an artifact leaves the box.

## Keeping in sync with the app

`training/config.py` duplicates `ARTWORK_BOX` and the ImageNet normalization
from `app/core/constants.py` / `app/signals/embedding.py` (the no-import rule
cuts both ways). If either changes in the app, mirror it here and retrain.
