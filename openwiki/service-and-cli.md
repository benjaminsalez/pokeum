---
title: Service, CLI & webcam
sources: ["app/cli.py", "app/api/**", "main.py", "app/recognize/factory.py", "app/recognize/webcam.py", "app/recognize/eval.py"]
read-when: "changing the CLI commands, the FastAPI endpoints, webcam scanning, the recognizer factory/wiring, or the accuracy eval harness"
---

# Service, CLI & webcam

The three ways to drive the recognizer, plus the wiring that builds it. The
recognition logic itself is in [recognition-pipeline](recognition-pipeline.md);
this page is the surface around it.

## Building a recognizer (`app/recognize/factory.py`)

`build_recognizer()` is the single construction path: it reads configuration,
opens the store, loads the hash and embedding indexes, and constructs the heavy
collaborators (embedder, OCR engine, symbol matcher). The CLI and API both go
through it so they share one path; **tests build `Recognizer` directly with
fakes and never touch this module**. Missing embedding indexes simply disable the
embedding signals rather than failing, and OCR degrades to `None` if
unavailable — the service still starts.

## CLI (`app/cli.py`, entered via `main.py`)

`main.py` is a thin dispatch into `app.cli.main()`. Heavy modules are imported
lazily inside each handler, so `--help` and unrelated commands stay fast. Results
are written to stdout; progress and diagnostics go through logging to stderr.

| Command | What it does |
|---|---|
| `sync [--set ID] [--full] [--no-details]` | Fetch reference data from TCGdex (see [reference-data](reference-data.md)) |
| `index [build] [--full]` | Compute hashes + embeddings over synced images |
| `identify IMAGE [--top-k N] [--json] [--no-ocr]` | Recognize one image file |
| `scan [--camera N]` | Live webcam recognition |
| `serve [--host H] [--port P]` | Start the FastAPI service |
| `eval FOLDER [--top-k N]` | Score recognition over labelled images |

`--data-dir` overrides the data root for any command. `identify` prints a
human-readable summary by default (card, set, number, confidence, variants,
alternates) or a JSON object with `--json`.

## FastAPI service (`app/api/`)

`create_app()` ([`server.py`](../app/api/server.py)) builds the app; the
recognizer is expensive to construct, so it is built once during the app's
lifespan and stashed on `app.state` for every request to share. Tests inject a
ready-made recognizer to skip that cost.

Routes ([`routes.py`](../app/api/routes.py)) are thin — decode, call the shared
recognizer, return its own dict for FastAPI to validate against the response
models in [`schemas.py`](../app/api/schemas.py):

- `GET /health` → `{status, cards_indexed, embedder_loaded}`
- `POST /identify` (multipart `file`, query `top_k`) → the recognition result;
  `400` when the upload cannot be decoded as an image
- `GET /cards/{card_id}` → catalogue detail; `404` when unknown

Start it with `python main.py serve` (binds `API_HOST:API_PORT`, defaults
`127.0.0.1:8000`).

## Webcam (`app/recognize/webcam.py`)

`run_webcam()` reads frames from an OpenCV capture device, processes ~5 fps,
runs each frame through `Recognizer.identify(require_detection=True)`, and feeds
results to the pure `TemporalAggregator` (see
[recognition-pipeline](recognition-pipeline.md)). Only stable identifications are
emitted — one JSON object per card to stdout. This is runtime glue and stays
thin; the decision logic is in the aggregator. The headless OpenCV build has no
preview window, which is why emissions go to stdout rather than an overlay.

## Eval harness (`app/recognize/eval.py`)

`evaluate_folder()` recognizes every `{card_id}__*.jpg` labelled image in a
folder and reports top-1 / top-k accuracy plus the list of misses. It is the
acceptance instrument for the recognition milestones: it makes a change's effect
on real photos measurable rather than guessed. Run it with `python main.py eval
<folder>`.

## Changing this area

- Keep routes thin: no recognition logic in `routes.py` — extend the pipeline
  instead. The API test (`tests/test_api.py`) uses `TestClient` with a fake,
  index-free recognizer and asserts the happy path, a bad upload, and lookups.
- New config values follow the settings checklist in [workflow](workflow.md):
  an accessor in `app/core/config.py` and a key in `.env.example`.
- CLI output goes to stdout via `sys.stdout.write` (results are the product);
  logging stays on stderr.
