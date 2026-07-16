"""Unit tests for image ingest helpers (app/vision/imaging.py)."""

from __future__ import annotations

import numpy as np

from app.vision.imaging import cap_long_side


def test_cap_long_side_shrinks_preserving_aspect() -> None:
    image = np.zeros((3000, 4000, 3), dtype=np.uint8)
    capped = cap_long_side(image, 2000)
    assert capped.shape == (1500, 2000, 3)


def test_cap_long_side_portrait() -> None:
    image = np.zeros((4000, 3000, 3), dtype=np.uint8)
    capped = cap_long_side(image, 1000)
    assert capped.shape == (1000, 750, 3)


def test_cap_long_side_noop_below_cap() -> None:
    image = np.zeros((800, 600, 3), dtype=np.uint8)
    assert cap_long_side(image, 2000) is image
