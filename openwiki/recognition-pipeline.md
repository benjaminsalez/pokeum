---
title: Recognition pipeline
sources: ["app/models.py", "app/vision/**", "app/signals/**", "app/variants/**", "app/recognize/pipeline.py", "app/recognize/fusion.py", "app/recognize/temporal.py", "app/core/constants.py"]
read-when: "changing detection/rectification, any recognition signal (hash, embedding, OCR, symbol), the fusion/confidence maths, variant detection, or webcam aggregation"
verified: 26c9ccc9aeb1
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
  classic OpenCV approach (bilateral blur → Canny → contours → largest convex
  4-gon with a plausible card size and aspect). No trained model, so new sets
  cost nothing here.
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
  always available). Full-card and artwork-crop embeddings are separate signals.
- **OCR** ([`ocr.py`](../app/signals/ocr.py)) — reads the bottom strip and
  parses the collector number and set code. `parse_collector_number`,
  `parse_set_code`, and `interpret_lines` are **pure and heavily tested**;
  `RapidOcrEngine` wraps the model. OCR is never a hard gate (glare defeats it) —
  it only nudges fusion.
- **Symbol** ([`symbol.py`](../app/signals/symbol.py)) — zero-mean normalized
  cross-correlation of the card's symbol-zone crop against per-set symbol
  templates, giving a per-set score the pipeline broadcasts to that set's
  candidates. Only consulted when OCR found no set code (the WOTC-era path).

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

- Keep pure logic pure: `geometry`, `fusion`, `temporal`, and the OCR parsers
  have no heavy imports and are unit-tested directly — don't pull OpenCV or a
  model into them.
- Tune weights/thresholds in `constants.py`; the fusion tests assert the
  renormalization, OCR boost/penalty, and threshold behaviour.
- Region boxes are fractions of the canonical card — adjust them in
  `constants.py`, and every crop follows.
