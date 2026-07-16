"""OCR signal: read and interpret the card's bottom strip.

The collector number ("025/193") and, on modern cards, a short set code ("PAL")
uniquely separate reprints that share the same artwork — the one cue embeddings
and hashes cannot provide. OCR is never a hard gate though: glare or blur can
wipe it out, so fusion only uses it to nudge scores.

Parsing is split from the engine: :func:`parse_collector_number`,
:func:`parse_set_code`, and :func:`interpret_lines` are pure and heavily tested,
while :class:`RapidOcrEngine` wraps the model and is imported lazily.
"""

from __future__ import annotations

import logging
import re
import threading

import numpy as np

from app.models import OcrObservation
from app.signals.base import OcrEngine
from app.vision import regions

logger = logging.getLogger(__name__)

# Map digit look-alikes so "O25" reads as "025". Applied only when hunting for
# the numeric collector pattern, never to set-code text.
_DIGIT_LOOKALIKES = str.maketrans({"O": "0", "o": "0", "I": "1", "l": "1", "|": "1"})
_NUMBER_RE = re.compile(r"(\d{1,4})\s*/\s*(\d{1,4})")
_SET_CODE_RE = re.compile(r"\b([A-Z]{2,4})\b")
# Short uppercase tokens that appear on cards but are not set codes.
_SET_CODE_STOPWORDS = frozenset({"EN", "HP", "LV", "NO", "GX", "EX", "GG", "TG"})


def parse_collector_number(text: str) -> tuple[str | None, int | None]:
    """Parse a collector ``number/total`` from OCR text.

    Digit look-alikes are corrected first, so ``O25 / 193`` yields ``("25", 193)``.
    The number is returned normalized (leading zeros stripped) to match the
    catalogue's stored key.

    Args:
        text: Raw OCR text from the bottom strip.

    Returns:
        ``(number, total)`` where either element may be ``None`` if not found.
    """
    digitized = text.translate(_DIGIT_LOOKALIKES)
    match = _NUMBER_RE.search(digitized)
    if not match:
        return None, None
    number = str(int(match.group(1)))
    total = int(match.group(2))
    return number, total


def parse_set_code(text: str) -> str | None:
    """Parse a short uppercase set code from OCR text, or ``None``.

    Args:
        text: Raw OCR text from the bottom strip.

    Returns:
        The first plausible 2-4 letter code that is not a known stopword.
    """
    # Match uppercase runs in the original text (real set codes are printed in
    # caps); this avoids picking mixed-case words like "Illus." from the credit.
    for candidate in _SET_CODE_RE.findall(text):
        if candidate not in _SET_CODE_STOPWORDS:
            return candidate
    return None


def interpret_lines(lines: list[tuple[str, float]]) -> OcrObservation:
    """Combine OCR text lines into a single :class:`OcrObservation`.

    Prefers the number/total from the highest-confidence line that contains a
    slash pattern; falls back to scanning the joined text. The observation's
    confidence is the confidence of the contributing line.

    Args:
        lines: ``(text, confidence)`` pairs from an OCR engine.

    Returns:
        The interpreted observation (may carry nothing useful).
    """
    raw = " ".join(text for text, _ in lines).strip()
    best_number: str | None = None
    best_total: int | None = None
    best_conf = 0.0
    for text, conf in sorted(lines, key=lambda item: item[1], reverse=True):
        number, total = parse_collector_number(text)
        if number is not None:
            best_number, best_total, best_conf = number, total, conf
            break
    if best_number is None:
        best_number, best_total = parse_collector_number(raw)
        best_conf = max((c for _, c in lines), default=0.0)
    return OcrObservation(
        raw_text=raw,
        number=best_number,
        number_total=best_total,
        set_code=parse_set_code(raw),
        confidence=float(best_conf),
    )


class RapidOcrEngine:
    """OCR engine backed by RapidOCR (PP-OCR models via ONNX Runtime)."""

    def __init__(self) -> None:
        """Construct the underlying RapidOCR reader (loaded lazily on import)."""
        from rapidocr import RapidOCR

        self._reader = RapidOCR()
        # The service handles requests in a thread pool; RapidOCR's reader is
        # not documented as thread-safe, so serialize access to the shared one.
        self._lock = threading.Lock()

    def read_text(self, image: np.ndarray) -> list[tuple[str, float]]:
        """Return ``(text, confidence)`` pairs found in an RGB image region."""
        with self._lock:
            result = self._reader(np.asarray(image))
        # RapidOCROutput carries parallel txts/scores tuples; both are None
        # when detection found no text at all.
        texts = result.txts or ()
        scores = result.scores or (0.0,) * len(texts)
        return [(str(text), float(score)) for text, score in zip(texts, scores, strict=False)]


def read_card(engine: OcrEngine, card: np.ndarray) -> OcrObservation:
    """Run OCR over a rectified card's bottom strip and interpret the result.

    Args:
        engine: OCR engine to use.
        card: A rectified, canonical-size card image.

    Returns:
        The interpreted OCR observation.
    """
    strip = regions.bottom_strip(card)
    lines = engine.read_text(strip)
    logger.debug("ocr raw lines: %s", [(text, round(conf, 2)) for text, conf in lines])
    return interpret_lines(lines)
