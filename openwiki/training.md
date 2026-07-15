---
title: Embedder fine-tune harness
sources: ["training/**"]
read-when: "changing the fine-tune training code (loss, augmentations, eval, export), re-running training, or promoting a new encoder artifact"
---

# Embedder fine-tune harness

`training/` is the **one-time** metric-learning fine-tune of the DINOv2-S
encoder that recognition retrieves with. It teaches the encoder *photography
invariance* — glare, imperfect rectification, blur, sleeve reflections — never
card identities, so the project's core invariant survives: **a new set is still
just `sync` + `index build`, zero training** (see
[reference-data](reference-data.md)). The harness is standalone: it never
imports `app/`, `app/` never imports it, and it runs on a GPU box, not the dev
machine.

## How it trains

- **NT-Xent (InfoNCE) instance discrimination**, temperature 0.05: two views
  per card, in-batch negatives (512 cards/step ⇒ ~1022 negatives). Same-set
  siblings act as free hard negatives.
- **Asymmetric views** ([`train.py`](../training/train.py) `_make_views`): one
  *near-clean* view (what the deployed gallery stores) and one *heavy
  photo-simulation* view — because at inference similarity is always
  query(photo) × gallery(clean render), never photo × photo.
- **Mixed crop modes**: 60% full card / 40% artwork crop per pair, same mode on
  both views. One encoder serves the app's full-card and art-crop fusion
  signals, so both are trained and both are evaluated.
- **Augmentations in plain torch on GPU**
  ([`augment.py`](../training/augment.py)): batched homographies
  (`grid_sample`), analytic elliptical/band glare with screen blending,
  per-sample gaussian/motion blur via grouped conv, sensor noise, print-wear
  mottle, optional CUDA-JPEG artifacts (feature-probed), finger occlusion.
  Kornia was rejected deliberately — a compat failure against a bleeding-edge
  torch on a rented GPU costs real money. GPU-side augs also mean the CPU's
  only per-step job is one uint8 memcpy, so 16 vCPUs cannot starve the A100.
- **Partial fine-tune**: patch-embed + blocks 0–3 frozen; AdamW with layer-wise
  LR decay (head 1e-3, backbone top 5e-5 × 0.8/block), warmup + cosine, bf16
  autocast, grad-clip 1.0 ([`model.py`](../training/model.py)). A projection
  head absorbs the contrastive compression; **backbone CLS features are what
  ship** — eval and export never use the head.
- **Data**: [`fetch_data.py`](../training/fetch_data.py) pulls *low-quality*
  TCGdex renders (~245×337, enough for 224 input) at concurrency 32 — the
  app's sync is not reused (it builds the runtime catalogue: sqlite, hi-res,
  slower). [`cache.py`](../training/cache.py) decodes everything once into a
  single `(N, 352, 256, 3)` uint8 `images.npy` held in RAM. Note: many old
  promo sets carry **no image URLs** on TCGdex and are skipped.

## Guards and eval

[`evaluate.py`](../training/evaluate.py) mirrors the deployed task: clean-render
gallery vs heavy-augmented queries (fixed 2000-card subset, deterministic
per-eval RNG, fixed batch order ⇒ bit-stable across epochs), top-1/top-5 on
**both tracks** (full card, artwork crop). `best.pt` is selected by
`0.5·full_top1 + 0.5·art_top1`. The **epoch-0 frozen baseline** runs before any
training step; the run **aborts** if the combined metric ever falls below it
(the "LR destroyed the pretrained transfer" failure). Also: early stop after 5
flat evals, embedding-collapse monitor, clean-gallery drift report,
non-finite-loss abort, one-shot batch halving on CUDA OOM. Checkpoints resume
losslessly (`--resume`).

## Export contract (the part that must never drift)

[`export_onnx.py`](../training/export_onnx.py) exports the backbone to the
exact graph the app's `OnnxEmbedder` expects: input `"image"` (dynamic batch,
3×224×224 fp32), output `"features"` (B, 384), opset 17. Two hard-won rules:

1. **Single-input wrapper required**: DINOv2's `forward(x, masks=None)` makes
   modern torch exporters emit `masks` as a required graph input. Both this
   module and `scripts/export_embedder.py` wrap the model to pin the signature.
2. **New filename per export** (`dinov2s-ft-v1.onnx`, `-v2`, …):
   `OnnxEmbedder.identifier` is filename-based, and the identifier change is
   what triggers the app's automatic re-embed on `index build`.

A mandatory **parity gate** embeds real card images through torch (using the
app's exact preprocessing — PIL squash-resize, ImageNet norm, re-implemented
locally) and through onnxruntime; per-image cosine must exceed 0.999 or the
artifact is rejected.

## Running it

The full runbook (deploy over ssh/scp without a git remote, smoke test, full
run under nohup, monitoring, promotion) lives in
[`training/README.md`](../training/README.md). The short form:
`bash training/deploy.sh tnr-0` → `python -m training.smoke_test` on the box
(six staged PASS/FAIL checks, <10 min) → fetch/cache/train/export → scp the
`.onnx` into `data/models/` → point `EMBED_MODEL_PATH` at it → `python main.py
index build --full`.

## Changing this area

- There are **no offline unit tests** for `training/` by design: it needs
  torch + a GPU, which the dev machine and CI don't have. Its verification is
  `smoke_test.py` on the box — keep the staged PASS/FAIL structure when
  extending it.
- `training/config.py` duplicates `ARTWORK_BOX` and the ImageNet normalization
  constants from the app (the no-import rule cuts both ways) — if the app's
  values change, mirror them here and retrain.
- Anything touching preprocessing or the ONNX graph must keep the parity gate
  passing against `app/signals/embedding.py`.
