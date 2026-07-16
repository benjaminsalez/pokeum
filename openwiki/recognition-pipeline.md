---
title: Recognition pipeline
sources: ["app/models.py", "app/vision/**", "app/signals/**", "app/variants/**", "app/recognize/pipeline.py", "app/recognize/fusion.py", "app/recognize/temporal.py", "app/core/constants.py"]
read-when: "changing detection/rectification, any recognition signal (hash, embedding, OCR, symbol), the fusion/confidence maths, variant detection, or webcam aggregation"
verified: 2b0cb908a530
---

# Recognition pipeline

How one image becomes a ranked, scored answer. The orchestrator is
[`app/recognize/pipeline.py`](../app/recognize/pipeline.py) (`Recognizer`);
every heavy collaborator is injected, so tests drive it with fakes and the
CLI/API build it once (see [service-and-cli](service-and-cli.md)).

```
image → detect card quad → rectify to canonical 630×880 → signals → fuse → variants
```

All tuning knobs (region boxes, hash config, fusion weights/thresholds, temporal
window) live in [`app/core/constants.py`](../app/core/constants.py) — change
behaviour there, not with scattered literals.

## Detection and rectification (`app/vision/`)

- [`detect.py`](../app/vision/detect.py) finds the card's quadrilateral with a
  classic OpenCV approach (bilateral blur → auto-Canny → contours → largest
  convex 4-gon with a plausible card size and aspect). No trained model, so new
  sets cost nothing here. Detection runs on a copy capped to `DETECT_MAX_SIDE`
  (edges need contrast, not resolution) and the quad is scaled back to source
  coordinates, so rectification still samples the original pixels. Canny
  thresholds are median-adaptive (`CANNY_MEDIAN_LO/HI` × the blurred image's
  median) — fixed literals missed card edges entirely in dim scenes. When a raw
  contour isn't a clean convex 4-gon (rounded corners, glare nicks, fingers),
  its convex hull is re-approximated at progressively looser tolerances
  (`_HULL_EPSILONS`) before giving up. Frames that still fail detection can be
  dumped for inspection via `SCAN_DEBUG_DIR` (see `.env.example`).
- Ingest is capped too: API uploads and CLI images pass through
  `imaging.cap_long_side(..., INGEST_MAX_SIDE)` before recognition — a 12MP
  photo adds nothing for a 630×880 rectification but multiplies every filter's
  cost.
- [`geometry.py`](../app/vision/geometry.py) is **pure NumPy** (corner ordering,
  area, aspect, plausibility) and carries the unit-tested maths.
- [`rectify.py`](../app/vision/rectify.py) perspective-warps the quad to a
  canonical 630×880 card so every crop box lands in the same place. When no quad
  is found it centre-crops the whole frame to card aspect instead — good enough
  for already-cropped scans. `Recognizer.identify(require_detection=True)`
  instead reports `NO_CARD_DETECTED`, which the webcam path relies on.
- [`regions.py`](../app/vision/regions.py) turns the fractional boxes in
  `constants.py` into crops: artwork window, bottom strip, symbol zone (era
  dependent), and the variant regions.
- [`imaging.py`](../app/vision/imaging.py) is the single place that turns files
  or upload bytes into the interchange format: a `uint8` RGB `(H, W, 3)` array
  (OpenCV's BGR is converted here).

## The four signals (`app/signals/`)

Each signal scores catalogue cards in `[0, 1]`. Concrete heavy implementations
are imported lazily; [`base.py`](../app/signals/base.py) declares the `Embedder`
and `OcrEngine` Protocols so pure-logic code and tests never load a model.

- **Hashes** ([`hashes.py`](../app/signals/hashes.py)) — a family of perceptual
  hashes (DCT `phash`, gradient `dhash`, per-RGB-channel `phash`). Matching is
  one XOR-and-popcount over a bit matrix; the closest hash across the family
  wins. Cheap and excellent on clean images, weak under glare/angle.
- **Embeddings** ([`embedding.py`](../app/signals/embedding.py)) — cosine
  retrieval over an L2-normalized matrix (`EmbeddingIndex`), one matmul, no ANN
  library. Two encoders share the `Embedder` interface: `OnnxEmbedder` (an
  exported DINOv2-small, robust) and `HistogramEmbedder` (pure-NumPy fallback,
  always available). Full-card and artwork-crop embeddings are separate signals,
  but run as **one batched forward pass**: `embed_images()` prefers an
  encoder's duck-typed `embed_batch` (the exported ONNX graph has a dynamic
  batch axis, probed at load) and falls back to looping `embed()`.
- **OCR** ([`ocr.py`](../app/signals/ocr.py)) — reads the bottom strip and
  parses the collector number and set code. `parse_collector_number`,
  `parse_set_code`, and `interpret_lines` are **pure and heavily tested**;
  `RapidOcrEngine` wraps the model. OCR is never a hard gate (glare defeats it) —
  it only nudges fusion. A parsed set code is only trusted if the catalogue
  actually prints it (`Recognizer._validate_set_code` against
  `store.known_set_codes()`): random uppercase scene text used to both skip the
  symbol signal and penalize every candidate via `is_useful`.
- **Symbol** ([`symbol.py`](../app/signals/symbol.py)) — zero-mean normalized
  cross-correlation of the card's symbol-zone crop against per-set symbol
  templates, giving a per-set score the pipeline broadcasts to that set's
  candidates. Only consulted when OCR found no set code (the WOTC-era path).

## Latency inside `Recognizer.identify`

- With an injected `executor` (the factory passes a 2-worker
  `ThreadPoolExecutor`; tests default to `None` = serial), **OCR runs
  concurrently with the dense signals** — ONNX Runtime and NumPy release the
  GIL, so the scan's wall time is roughly `max(dense, OCR)` instead of their
  sum. `RapidOcrEngine` serializes access to its shared reader with a lock.
- Candidate resolution is one chunked `store.get_cards(ids)` `IN` query, not a
  round-trip per shortlist id.
- Every `identify` emits a per-stage DEBUG timing line
  (`identify timings ms: ...`) — run with `LOG_LEVEL=debug` to attribute a slow
  scan before changing anything.
- `LOG_LEVEL=debug` also emits full recognition diagnostics per scan: detection
  outcome, each signal's top-5 candidates with scores, the raw OCR lines and
  the parsed number/set (plus how many catalogue cards agree), symbol-template
  scores on the WOTC path, and the fused top-5 with per-signal contributions —
  enough to see which signal pulled in a wrong pick.

## Fusion (`app/recognize/fusion.py`)

`fuse()` is **pure maths** — dictionaries and floats, no image code — so every
rule is directly testable:

- Dense signals contribute a weighted average, renormalized over the signals
  that actually ran, so a missing model never zeroes a card.
- OCR multiplies each candidate up when its collector number matches the read
  (`ocr_consistent_ids`) and down when it contradicts — never eliminating anyone.
- Confidence normalizes to the top raw score scaled by its absolute quality. The
  decision: `CONFIDENT` needs both a high top confidence **and** a margin over
  the runner-up; otherwise `UNCERTAIN`, else `NO_MATCH`.

The domain types it returns (`Candidate`, `RecognitionResult`, `SignalScore`,
`OcrObservation`) live in [`app/models.py`](../app/models.py); their `as_dict()`
methods produce the JSON the CLI and API emit.

## Variants (`app/variants/`)

Variant checks run **only on the winning card**, are rule-based CV (no training),
and each self-gates on era or a catalogue flag, returning `None` when it does not
apply. [`assess.py`](../app/variants/assess.py) runs them and keeps what fires:
reverse holo (foil texture in the card body,
[`reverse_holo.py`](../app/variants/reverse_holo.py)), 1st Edition stamp and
shadowless layout ([`wotc.py`](../app/variants/wotc.py)), and promo stamp
([`stamps.py`](../app/variants/stamps.py)). A single still is weak evidence for a
foil, so variant confidence is capped; the webcam path is the stronger signal.
Shared measurements are in [`features.py`](../app/variants/features.py).

## Temporal aggregation (`app/recognize/temporal.py`)

`TemporalAggregator` is **pure** — it consumes per-frame `RecognitionResult`s and
emits one when a card first becomes stable: it must win at least
`TEMPORAL_STABLE_VOTES` of a sliding window **and** clear the confidence bar via
an EMA. After a run of card-free frames (the card was removed) it resets, ready
for the next card. The webcam loop that feeds it is
[`webcam.py`](../app/recognize/webcam.py) (see [service-and-cli](service-and-cli.md)).

## Changing this area

- Reference images are low-res webp (~245×337, see
  [reference-data](reference-data.md)) — this is fine because every signal
  operates at or below that scale (embeddings 224px, hashes on the DCT's low
  frequencies, symbols 48px). Don't add a signal that assumes hi-res
  references without revisiting that decision.
- Keep pure logic pure: `geometry`, `fusion`, `temporal`, and the OCR parsers
  have no heavy imports and are unit-tested directly — don't pull OpenCV or a
  model into them.
- Tune weights/thresholds in `constants.py`; the fusion tests assert the
  renormalization, OCR boost/penalty, and threshold behaviour.
- Region boxes are fractions of the canonical card — adjust them in
  `constants.py`, and every crop follows.
