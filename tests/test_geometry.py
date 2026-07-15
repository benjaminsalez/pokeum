"""Unit tests for pure geometry helpers (app/vision/geometry.py)."""

from __future__ import annotations

import numpy as np

from app.vision import geometry


def test_order_corners_from_scrambled_input() -> None:
    # Given an axis-aligned rectangle whose corners are in a jumbled order,
    ordered = geometry.order_corners(
        np.array([[10, 90], [10, 10], [50, 90], [50, 10]], dtype=np.float32)
    )
    # they come back as TL, TR, BR, BL.
    assert ordered[0].tolist() == [10, 10]
    assert ordered[1].tolist() == [50, 10]
    assert ordered[2].tolist() == [50, 90]
    assert ordered[3].tolist() == [10, 90]


def test_polygon_area_of_rectangle() -> None:
    rect = np.array([[0, 0], [40, 0], [40, 88], [0, 88]], dtype=np.float32)
    assert geometry.polygon_area(rect) == 40 * 88


def test_aspect_ratio_portrait_card() -> None:
    rect = np.array([[0, 0], [63, 0], [63, 88], [0, 88]], dtype=np.float32)
    assert abs(geometry.aspect_ratio(rect) - 63 / 88) < 1e-5


def test_is_plausible_card_accepts_card_shape() -> None:
    frame_area = 600.0 * 800.0
    card = np.array([[150, 150], [450, 150], [450, 570], [150, 570]], dtype=np.float32)
    assert geometry.is_plausible_card(card, frame_area)


def test_is_plausible_card_rejects_tiny_region() -> None:
    frame_area = 600.0 * 800.0
    tiny = np.array([[0, 0], [20, 0], [20, 28], [0, 28]], dtype=np.float32)
    assert not geometry.is_plausible_card(tiny, frame_area)


def test_is_plausible_card_rejects_landscape() -> None:
    frame_area = 600.0 * 800.0
    wide = np.array([[50, 50], [550, 50], [550, 300], [50, 300]], dtype=np.float32)
    assert not geometry.is_plausible_card(wide, frame_area)
