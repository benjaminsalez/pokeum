"""Offline accuracy evaluation over a folder of labelled card images.

Each image is named ``{card_id}__{anything}.jpg`` (the true card id before the
double underscore). This is the acceptance instrument for the recognition
milestones: it reports top-1 and top-k accuracy so a change's effect on real
photos is measurable rather than guessed.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from app.recognize.factory import build_recognizer
from app.vision.imaging import load_image

logger = logging.getLogger(__name__)

_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


def truth_from_name(path: Path) -> str | None:
    """Extract the labelled card id from a ``{card_id}__*`` filename."""
    stem = path.stem
    if "__" not in stem:
        return None
    return stem.split("__", 1)[0]


def evaluate_folder(
    folder: str | Path, *, data_dir: str | None = None, top_k: int = 5
) -> dict[str, Any]:
    """Recognize every labelled image in a folder and report accuracy.

    Args:
        folder: Directory of ``{card_id}__*`` labelled images.
        data_dir: Reference data root; defaults to the configured directory.
        top_k: Rank considered for top-k accuracy.

    Returns:
        A report dict with counts, top-1/top-k accuracy, and the list of misses.
    """
    root = Path(folder)
    images = sorted(
        p for p in root.iterdir() if p.is_file() and p.suffix.lower() in _IMAGE_SUFFIXES
    )
    recognizer = build_recognizer(data_dir=data_dir)

    total = 0
    top1 = 0
    topk = 0
    misses: list[dict[str, str]] = []
    for path in images:
        truth = truth_from_name(path)
        if truth is None:
            logger.warning("skipping unlabelled file: %s", path.name)
            continue
        total += 1
        result = recognizer.identify(load_image(path), top_k=top_k)
        ranked = _ranked_ids(result)
        if ranked[:1] == [truth]:
            top1 += 1
        if truth in ranked:
            topk += 1
        else:
            misses.append({"file": path.name, "truth": truth, "got": ranked[0] if ranked else ""})

    return {
        "count": total,
        "top1": top1,
        "topk": topk,
        "top1_accuracy": round(top1 / total, 3) if total else 0.0,
        "topk_accuracy": round(topk / total, 3) if total else 0.0,
        "top_k": top_k,
        "misses": misses,
    }


def _ranked_ids(result: object) -> list[str]:
    """Return the recognized card ids, best first, from a result."""
    data = result.as_dict()  # type: ignore[attr-defined]
    ids: list[str] = []
    if data["match"] is not None:
        ids.append(data["match"]["card_id"])
    ids.extend(alt["card_id"] for alt in data["alternates"])
    return ids
