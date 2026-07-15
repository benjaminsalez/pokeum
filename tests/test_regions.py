"""Unit tests for fractional region crops (app/vision/regions.py)."""

from __future__ import annotations

import numpy as np

from app.core.constants import ERA_MODERN, ERA_WOTC
from app.vision import regions


def test_crop_fraction_quadrant() -> None:
    image = np.zeros((100, 200, 3), dtype=np.uint8)
    crop = regions.crop_fraction(image, (0.0, 0.0, 0.5, 0.5))
    assert crop.shape[:2] == (50, 100)


def test_crop_fraction_degenerate_box_is_nonempty() -> None:
    image = np.zeros((100, 100, 3), dtype=np.uint8)
    crop = regions.crop_fraction(image, (0.5, 0.5, 0.5, 0.5))
    assert crop.shape[0] >= 1 and crop.shape[1] >= 1


def test_symbol_zone_differs_by_era() -> None:
    card = np.zeros((880, 630, 3), dtype=np.uint8)
    modern = regions.symbol_zone(card, ERA_MODERN)
    wotc = regions.symbol_zone(card, ERA_WOTC)
    assert modern.size > 0 and wotc.size > 0
