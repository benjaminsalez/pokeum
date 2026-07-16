"""Named constants: tuning knobs that are design decisions, not configuration.

Every magic number or fixed string that is shared, tuned, or load-bearing gets
a name here, grouped by domain, so reviewers see one overview of the knobs and
a change is one edit instead of a scavenger hunt.

The dividing rule between this module and :mod:`app.core.config`:

- varies per **environment** (endpoints, keys, log levels) → ``config.py``,
  read from the environment at runtime;
- a fixed **design decision** (limits, batch sizes, schedules) → a constant
  here, changed only through a reviewed code change.

A value used by exactly one module and meaningless outside it may stay a
module-level constant there; promote it here once a second module needs it or
it becomes something the team discusses.
"""

from __future__ import annotations

# A fractional box on a normalized image: (left, top, right, bottom), each in
# [0, 1] as a fraction of width/height. Independent of pixel size.
FractionBox = tuple[float, float, float, float]

# --- Canonical card geometry ------------------------------------------------
# A real Pokémon card is 63 mm x 88 mm. Rectified cards are warped to this pixel
# size before any signal runs, so every crop box below is resolution-independent
# and every card is compared at the same scale.
CARD_WIDTH_PX = 630
CARD_HEIGHT_PX = 880

# --- Card detection ---------------------------------------------------------
# A candidate contour is only accepted as a card when it is a convex quad that
# fills at least this fraction of the frame and has a plausible card aspect
# ratio (width / height). Values chosen to reject text blocks and table edges
# while tolerating perspective foreshortening.
DETECT_MIN_AREA_FRACTION = 0.05
CARD_ASPECT_MIN = 0.55
CARD_ASPECT_MAX = 0.85

# --- Region crops (fractions of the rectified card) -------------------------
# Upper-central artwork window: the most discriminative region shared by nearly
# all layouts. Used for the artwork-crop embedding signal.
ARTWORK_BOX: FractionBox = (0.06, 0.10, 0.94, 0.56)
# Bottom strip carrying the collector number (bottom-right) and, on modern
# cards, the set code (bottom-left). OCR reads this whole band.
BOTTOM_STRIP_BOX: FractionBox = (0.0, 0.90, 1.0, 1.0)
# Set-symbol location differs by era: modern cards place it near the number at
# the bottom; WOTC-era cards place it on the right, beside the description text.
SYMBOL_ZONE_MODERN: FractionBox = (0.55, 0.905, 0.82, 0.99)
SYMBOL_ZONE_WOTC: FractionBox = (0.80, 0.57, 0.975, 0.72)
# Card body outside the artwork, used to judge the reverse-holo foil texture.
REVERSE_HOLO_BODY_BOX: FractionBox = (0.06, 0.60, 0.94, 0.88)
# WOTC 1st Edition stamp sits at the lower-left of the art frame.
FIRST_EDITION_STAMP_BOX: FractionBox = (0.05, 0.40, 0.24, 0.62)
# Right edge of the art frame, where a non-shadowless card casts a drop shadow.
ART_FRAME_RIGHT_BOX: FractionBox = (0.86, 0.14, 0.98, 0.52)
# Lower-left of the artwork, a common promo-stamp position.
PROMO_STAMP_BOX: FractionBox = (0.06, 0.44, 0.36, 0.58)

# --- Ingest & detection performance ------------------------------------------
# Uploads are capped to this long side before recognition: a 12MP phone photo
# adds no signal for a 630x880 rectification but multiplies every filter and
# warp's cost. 2000px keeps rectification ~3x oversampled even when the card
# occupies only half the frame.
INGEST_MAX_SIDE = 2000
# Card detection runs on a copy capped to this long side; the found quad is
# scaled back to source coordinates so rectification still samples the original.
# bilateralFilter and Canny are O(pixels): 960px finds the same quads an order
# of magnitude faster on photos and leaves webcam frames (1080p) barely touched.
DETECT_MAX_SIDE = 960
# Canny thresholds as fractions of the blurred image's median brightness
# (classic auto-Canny). Fixed literals (50/150) miss card edges entirely in dim
# scenes — real webcam sessions showed detection never firing; median-relative
# thresholds track the scene's actual brightness. A flat image still yields no
# edges (zero gradient), so blank frames keep returning "no card".
CANNY_MEDIAN_LO = 0.66
CANNY_MEDIAN_HI = 1.33

# --- Perceptual hashing -----------------------------------------------------
# Hash side length; the hash is HASH_SIZE**2 bits. 16 → 256-bit hashes, a good
# balance between discrimination and tolerance to resampling.
HASH_SIZE = 16
HASH_BITS = HASH_SIZE * HASH_SIZE

# --- Embedding retrieval ----------------------------------------------------
# Square input the ONNX encoder expects (DINOv2 uses multiples of 14; 224 is the
# standard). The fallback HistogramEmbedder ignores this and uses its own size.
EMBED_INPUT_SIZE = 224
# How many top matches each dense signal (embedding, hash) contributes to fusion.
RETRIEVAL_SHORTLIST = 50

# --- Signal fusion ----------------------------------------------------------
# Weights of the dense similarity signals; renormalized over whichever signals
# are actually present for a given image (a missing model must not zero a card).
WEIGHT_EMBED_FULL = 0.40
WEIGHT_EMBED_ART = 0.30
WEIGHT_HASH = 0.20
WEIGHT_SYMBOL = 0.10
# OCR is never a hard gate (glare defeats it); it multiplies a candidate's score
# up when the parsed number/set agrees and down when it contradicts.
OCR_BOOST = 0.5
OCR_PENALTY = 0.4
# Decision thresholds on the normalized confidence of the top candidate.
CONFIDENT_THRESHOLD = 0.80
CONFIDENT_MARGIN = 0.10
UNCERTAIN_THRESHOLD = 0.50
# Default number of candidates returned to the caller.
DEFAULT_TOP_K = 5

# --- Webcam temporal aggregation --------------------------------------------
# Sliding window of recent per-frame results and the vote/quality bar a card
# must clear before a stable identification is emitted.
TEMPORAL_WINDOW = 6
TEMPORAL_STABLE_VOTES = 4
TEMPORAL_EMA_ALPHA = 0.4
# Consecutive card-free frames that reset the aggregator (card removed).
TEMPORAL_RESET_AFTER_EMPTY = 8
# Target frames-per-second to actually process from the capture stream.
SCAN_TARGET_FPS = 5

# --- Reference sync ---------------------------------------------------------
# TCGdex is latency-bound (~1.4 s/request) and tolerates well above this
# comfortably (verified at 32 during training-data fetch); 12 keeps a full
# catalogue sync under an hour while staying polite.
SYNC_CONCURRENCY = 12
SYNC_MAX_RETRIES = 3
SYNC_BACKOFF_SECONDS = 1.0
HTTP_TIMEOUT_SECONDS = 30.0
# TCGdex serves images from a base URL with no extension; append quality and
# extension. "low" + "webp" (~245x337 lossy, ~60 KB) is deliberate: every
# consumer of a reference image (224px embeddings, perceptual hashes, 320px UI
# thumbnails, 48px symbol templates) works at or below this size, and the full
# catalogue then costs ~1.5 GB instead of ~18 GB. Beware: the extension drives
# the encoding — "low.png" is still 600x825 LOSSLESS (~900 KB per card, filled
# the dev drive twice); only the webp variants are actually small.
TCGDEX_IMAGE_QUALITY = "low"
TCGDEX_IMAGE_EXTENSION = "webp"

# --- API image serving --------------------------------------------------------
# Width of the derived JPEG thumbnails the API serves to the scan UI; small
# enough to load instantly, large enough to be sharp at confirmation-sheet size.
CARD_THUMB_WIDTH = 320

# --- Scan collection ----------------------------------------------------------
# Stored scans are normalized server-side before upload: the long side is capped
# so the card region (guide-cropped scans keep ~15% background margin) still
# exceeds the training cache height (352 px — training/config.py CACHE_HEIGHT),
# then re-encoded as lossy webp like the reference images. Anything larger adds
# no training signal, only storage cost.
SCAN_STORE_MAX_SIDE = 512
SCAN_STORE_WEBP_QUALITY = 80
# Hard cap on total bucket usage: uploads stop while usage is at or above this,
# per collection's silent best-effort contract. ~2.5M scans at ~40 KB each.
SCANS_MAX_BUCKET_BYTES = 100 * 1024**3
# Usage is measured by summing a full object listing; cache the result this
# long so the sweep runs at most once an hour, not per upload. Uploaded bytes
# are added to the cached figure in between, so bursts still count against it.
SCANS_USAGE_CACHE_TTL_SECONDS = 3600.0

# --- Scan collection ----------------------------------------------------------
# Version stamped into every uploaded scan annotation so downstream training
# tooling can evolve the JSON shape without guessing at old objects.
SCAN_ANNOTATION_SCHEMA_VERSION = 1

# --- Card era ---------------------------------------------------------------
# Cards released in or before this year are treated as WOTC-era for variant
# gating (1st Edition / shadowless checks) and set-symbol placement.
WOTC_ERA_UNTIL_YEAR = 2003
ERA_WOTC = "wotc"
ERA_MODERN = "modern"

# --- Variant detection (heuristic, rule-based CV) ---------------------------
# These thresholds are empirical and tunable. A single still photo is weak
# evidence for a foil, so variant confidence is capped; the webcam path, which
# aggregates specular evidence across frames, is the stronger signal.
VARIANT_SINGLE_STILL_CONF_CAP = 0.85
# Reverse holo: high-frequency (foil sparkle) energy in the card body, above
# which the body is judged foiled. Energy is normalized by this scale.
REVERSE_HOLO_ENERGY_SCALE = 0.02
REVERSE_HOLO_MIN_ENERGY = 0.006
# 1st Edition: the black stamp is a compact dark mark; accept a dark-pixel
# fraction inside this band together with visible edges. The edge bar is far
# lower than the foil energy above — a stamp outline, not a sparkle field.
FIRST_EDITION_DARK_MIN = 0.04
FIRST_EDITION_DARK_MAX = 0.45
FIRST_EDITION_MIN_EDGE_ENERGY = 0.002
# Shadowless: the drop shadow right of the art frame raises brightness spread;
# below this standard deviation the card is judged shadowless.
SHADOWLESS_MAX_STD = 0.06
# Promo stamp: minimum blob score before asserting a stamp is present.
PROMO_STAMP_MIN_SCORE = 0.35
