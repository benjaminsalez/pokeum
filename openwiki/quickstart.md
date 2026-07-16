---
title: OpenWiki quickstart
verified: 6f101a33957a
---

# pokeum — Pokémon card recognizer

pokeum identifies a Pokémon card from a photo or webcam frame down to the exact
printing — name, set, and collector number (e.g. *Pikachu on the Ball,
Pokémon Futsal 2020 1/5*) — and flags print variants (reverse holo, 1st Edition,
shadowless, promo stamp). It ships as a Python library, a CLI, and a FastAPI
service.

## The one idea that shapes everything: retrieval, not classification

A CNN classifier over card ids would need retraining every time a set releases.
pokeum instead does **nearest-neighbour retrieval against a reference index**,
fused with **OCR of the collector number**:

- a frozen image encoder and perceptual hashes match the card image against
  reference images pulled from [TCGdex](https://tcgdex.dev);
- OCR of the bottom strip ("025/193" plus a set code like "PAL") disambiguates
  reprints that share the same artwork; set-symbol matching covers WOTC-era
  cards that print no set code.

**The invariant: a new set is a data operation, never training.** You `sync` its
cards and images, `index build` recomputes hashes/embeddings, and the card is
recognizable. The only model — the encoder — is frozen; see
[reference-data](reference-data.md). A one-time encoder fine-tune (for
photography robustness, not card identities) lives in `training/` and runs on a
GPU box — see [training](training.md); it preserves this invariant.

## How it's organized

```
app/
  core/            config.py (env settings) · constants.py (tuning knobs) · logging_config.py
  models.py        immutable domain types: CardRef, SignalScore, Candidate,
                   VariantGuess, RecognitionResult
  reference/       TCGdex client, SQLite store, image cache, sync, index build/load
  vision/          geometry (pure) · detect · rectify · regions · imaging (OpenCV)
  signals/         base (Protocols) · hashes · embedding · ocr · symbol
  variants/        reverse_holo · wotc (1st Ed, shadowless) · stamps · assess
  recognize/       pipeline · fusion (pure) · temporal (pure) · webcam · factory · eval
  cli.py           sync · index · identify · scan · serve · eval
  api/             FastAPI app: schemas · routes · server
main.py            entry point → app.cli
scripts/
  export_embedder.py   dev-only: export an encoder to ONNX (needs torch)
training/          one-time GPU fine-tune harness (standalone; see training.md)
tests/             offline pytest suite (no network, no model downloads)
```

Import direction is one-way: `core ← models ← (reference, vision, signals,
variants) ← recognize ← (cli, api)`. `app/core/` never imports from the rest of
`app/`.

## The documentation

- **[recognition-pipeline](recognition-pipeline.md)** — how an image becomes a
  ranked answer: detection and rectification, the four signals, how fusion
  combines them into a confidence, variant checks, and webcam aggregation.
- **[reference-data](reference-data.md)** — the catalogue and index: syncing
  from TCGdex into SQLite plus an image cache, building hash/embedding
  artifacts, and why a new set never needs training.
- **[service-and-cli](service-and-cli.md)** — the command line, the FastAPI
  endpoints, live webcam scanning, and the accuracy eval harness.
- **[training](training.md)** — the one-time GPU fine-tune of the encoder
  (photography invariance) and its export/parity contract with the app.
- **[workflow](workflow.md)** — the settings-vs-constants split, coding
  conventions, the quality gate, and the Claude Code automation in `.claude/`.
- **[troubleshooting](troubleshooting.md)** — known failure signatures and their
  fixes (machine-read by a hook).

## Run it

```
# 1. install runtime deps (numpy, opencv, imagehash, onnxruntime, rapidocr,
#    httpx, fastapi, uvicorn, boto3 — all pinned)
python -m pip install -r requirements.txt

# 2. populate the local catalogue + image cache from TCGdex
python main.py sync --set fut2020        # or omit --set for everything (~20k cards)

# 3. compute hashes + embeddings over the synced images
python main.py index build

# 4. identify a card
python main.py identify path/to/card.jpg            # human-readable
python main.py identify path/to/card.jpg --json     # machine-readable

# live webcam, or serve the HTTP API (+ the built frontend, if present)
python main.py scan
python main.py serve       # /api: POST /identify, POST /scans, GET /health, GET /cards/{id}

# browser scan UI (Vue): start the API first, then
cd frontend && npm install && npm run dev    # http://localhost:5173
```

Reference data lives under `DATA_DIR` (default `./data`, gitignored):
`reference.db` (SQLite), `images/`, `index/*.npy`, `symbols/`, `models/`.
Configuration is read only through [`app/core/config.py`](../app/core/config.py)
accessors; see [`.env.example`](../.env.example) for every key and default.
`python main.py ...` loads a local `.env` automatically (shell variables win).

## The encoder

`EMBED_MODEL_PATH` defaults to `./data/models/dinov2s-ft-v1.onnx` — the
DINOv2-small fine-tuned for photography invariance in [training](training.md)
(heavy-distortion retrieval: 98% top-1 vs the frozen model's 73%). When the
file is absent, the embedding signal falls back to a pure-NumPy
`HistogramEmbedder`, so the system still runs out of the box — far weaker under
glare and steep angles, but a real, deterministic baseline. No per-set
retraining either way.
