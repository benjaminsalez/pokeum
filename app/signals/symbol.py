"""Set-symbol matching signal.

Modern cards print a set code the OCR can read; WOTC-era cards do not, so their
set is disambiguated by the little set symbol instead. This signal compares the
card's symbol-zone crop against each set's symbol template by zero-mean
normalized cross-correlation, yielding a per-set similarity that the pipeline
broadcasts to the candidate cards of those sets.

Each template is cropped from the era-appropriate zone, so a query needs no prior
knowledge of its own era.
"""

from __future__ import annotations

import logging

import numpy as np

from app.reference.store import ReferenceStore
from app.vision import regions
from app.vision.imaging import load_image, resize, to_gray

logger = logging.getLogger(__name__)

_TEMPLATE_SIZE = 48
_EPS = 1e-8


def _normalize_patch(image: np.ndarray) -> np.ndarray:
    """Resize to the template size and zero-mean unit-normalize for ZNCC."""
    gray = to_gray(image) if image.ndim == 3 else image
    small = resize(gray, _TEMPLATE_SIZE, _TEMPLATE_SIZE).astype(np.float32)
    centered = small - float(small.mean())
    norm = float(np.linalg.norm(centered))
    if norm < _EPS:
        return centered
    return centered / norm


class SymbolMatcher:
    """Matches a card's set symbol against cached per-set symbol templates."""

    def __init__(self, templates: dict[str, tuple[str, np.ndarray]]) -> None:
        """Create a matcher.

        Args:
            templates: Mapping of set id to ``(era, normalized_template)``.
        """
        self._templates = templates

    @classmethod
    def from_store(cls, store: ReferenceStore) -> SymbolMatcher:
        """Build a matcher from every set symbol cached in the store."""
        templates: dict[str, tuple[str, np.ndarray]] = {}
        for set_id, era, path in store.symbol_templates():
            try:
                patch = _normalize_patch(load_image(path))
            except (FileNotFoundError, ValueError) as error:
                logger.warning("symbol template for %s failed: %s", set_id, error)
                continue
            templates[set_id] = (era, patch)
        return cls(templates)

    def __len__(self) -> int:
        """Return the number of set templates held."""
        return len(self._templates)

    def match(self, card: np.ndarray) -> dict[str, float]:
        """Return a per-set similarity in ``[0, 1]`` for a rectified card.

        Args:
            card: Rectified, canonical-size card image.

        Returns:
            Mapping of set id to symbol similarity. Empty when no templates.
        """
        scores: dict[str, float] = {}
        for set_id, (era, template) in self._templates.items():
            crop = regions.symbol_zone(card, era)
            query = _normalize_patch(crop)
            zncc = float(np.dot(query.ravel(), template.ravel()))
            scores[set_id] = max(0.0, (zncc + 1.0) / 2.0)
        return scores
