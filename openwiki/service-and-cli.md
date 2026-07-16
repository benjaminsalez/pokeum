---
title: Service, CLI, frontend & webcam
sources: ["app/cli.py", "app/api/**", "app/collect/**", "main.py", "app/recognize/factory.py", "app/recognize/webcam.py", "app/recognize/eval.py", "frontend/**", "scripts/make_synthetic_eval.py"]
read-when: "changing the CLI commands, the FastAPI endpoints, the Vue scan frontend, webcam scanning, the recognizer factory/wiring, or the eval harness"
verified: a9b48fc5656f
---

# Service, CLI, frontend & webcam

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
- `POST /identify` (multipart `file`, query `top_k`, `require_detection`) → the
  recognition result; `400` when the upload cannot be decoded as an image.
  Decode + recognition run via `run_in_threadpool` (they are CPU-bound; inline
  they would stall every concurrent request), and the upload is capped to
  `INGEST_MAX_SIDE` first. With `require_detection=true` the API answers
  `no_card_detected` when no card quad is found instead of guessing from the
  whole frame — the live camera path sets it; pre-cropped photo uploads keep
  the whole-frame fallback.
- `POST /scans` (multipart `file` + `annotation` JSON form field) → `202`
  immediately; the upload to S3 happens as a background task after the
  response. Annotations without `consent: true` are dropped server-side with an
  identical response. `422` on a malformed annotation.
- `GET /cards/{card_id}` → catalogue detail; `404` when unknown
- `GET /cards/{card_id}/image` → a 320px JPEG thumbnail of the card, derived
  lazily from the cached reference image into `data/thumbs/` and served with
  immutable cache headers (the scan UI's confirmation sheet uses this — never
  serve the full reference image to a UI)

CORS is wide open on purpose: the service is local and account-less, and the
frontend may load from another origin (dev server, tunnel). Start it with
`python main.py serve` (binds `API_HOST:API_PORT`, defaults `127.0.0.1:8000`).

## Scan collection (`app/collect/`)

`ScanCollector` ([`s3.py`](../app/collect/s3.py)) uploads each accepted scan —
the captured photo plus a sibling annotation JSON — to S3 under
`{prefix}/YYYY/MM/DD/{uuid}.{jpg,json}`, for future encoder fine-tunes. It is
strictly best-effort: it runs as a FastAPI background task after the response,
never raises (failures are logged and swallowed, no retries), and **no-ops
entirely while `SCANS_S3_BUCKET` is empty** — the default, so the app runs
identically without any AWS setup. Configuration: `SCANS_S3_BUCKET`,
`SCANS_S3_REGION`, `SCANS_S3_PREFIX`, `SCANS_S3_ENDPOINT_URL` (S3-compatible
stores), with credentials via boto3's standard chain (see `.env.example`).
boto3 is imported lazily on the first enabled upload. The lifespan wires one
collector onto `app.state.collector`; tests inject a fake. The annotation
carries `SCAN_ANNOTATION_SCHEMA_VERSION` so training tooling can evolve the
shape.

## Scan frontend (`frontend/`)

A Vue 3 + TypeScript + Tailwind app with hand-written shadcn-style components
(`src/components/ui/` — they take a `class` prop merged via tailwind-merge so
callers can override styles; bypassing that caused a real white-buttons bug).
Two views in [`App.vue`](../frontend/src/App.vue): a full-screen camera scan
view (guide frame, one scan button, then a confirmation sheet showing the
matched card's thumbnail with Skip/Save) and an export view (a thumbnail grid
of the collection with a TCGplayer-style CSV download —
[`exporters.ts`](../frontend/src/lib/exporters.ts)).
No confidence values are shown anywhere by design.

Camera captures are **cropped to the guide frame** before upload: `captureFrame`
maps the ScannerFrame's on-screen rect through the video's `object-cover`
geometry to source pixels (`guideCropSourceRect` in
[`image.ts`](../frontend/src/lib/image.ts), pure and unit-testable), keeping a
`GUIDE_CROP_MARGIN` of background around the card so quad detection can close
the contour. Camera scans send `require_detection=true` and show "No card
found — center it in the frame" on `no_card_detected`; photo uploads keep the
server-side whole-frame fallback.

Data collection: a first-visit notice ([`DataNotice.vue`](../frontend/src/components/DataNotice.vue),
acknowledged flag in localStorage via [`notice.ts`](../frontend/src/lib/notice.ts))
tells the user that saved scans are collected. On **Save**, the captured blob
(retained alongside `pending`) and its annotation are POSTed fire-and-forget to
`/scans` ([`api.ts`](../frontend/src/lib/api.ts) `submitScan`) — failures never
surface. File-library uploads are downscaled client-side first
([`image.ts`](../frontend/src/lib/image.ts), max side 1600px); camera captures
are already 1080p-bounded.

Dev: `npm run dev` in `frontend/` (port 5173) proxies `/api/*` to the local
service, so start the API first; `allowedHosts` covers cloudflared/ngrok
tunnels, and `VITE_API_BASE` points the client at a cross-origin API when the
proxy isn't in play. Build check: `npm run build` (vue-tsc + vite).

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
folder and reports the full scorecard: top-1 / top-k accuracy, verdict counts
(confident / uncertain / no_match), mean confidence, mean margin over the
runner-up, and the list of misses. It is the acceptance instrument for the
recognition milestones: it makes a change's effect on real photos measurable
rather than guessed. Run it with `python main.py eval <folder>`.

Labelled test sets live under `data/eval/` (gitignored): `synthetic/` is
regenerable via `scripts/make_synthetic_eval.py` (PIL-based distortions,
deliberately independent of the training augmentations), and `real/` holds
real-world listing photos named `{card_id}__*.jpg`.

## Changing this area

- Keep routes thin: no recognition logic in `routes.py` — extend the pipeline
  instead. The API test (`tests/test_api.py`) uses `TestClient` with a fake,
  index-free recognizer and asserts the happy path, a bad upload, and lookups.
- New config values follow the settings checklist in [workflow](workflow.md):
  an accessor in `app/core/config.py` and a key in `.env.example`.
- CLI output goes to stdout via `sys.stdout.write` (results are the product);
  logging stays on stderr.
