"""Unit tests for card detection and rectification (app/vision)."""

from __future__ import annotations

import numpy as np

from app.core import constants
from app.vision.detect import detect_card_quad
from app.vision.geometry import is_plausible_card
from app.vision.rectify import rectify_or_whole


def _frame_with_card() -> np.ndarray:
    frame = np.full((800, 600, 3), 30, dtype=np.uint8)
    # A bright portrait rectangle (~0.71 aspect) centred on a dark background.
    frame[190:610, 150:450] = 235
    return frame


def test_detects_card_quad() -> None:
    frame = _frame_with_card()
    quad = detect_card_quad(frame)
    assert quad is not None
    assert is_plausible_card(quad, float(frame.shape[0] * frame.shape[1]))


def test_blank_image_has_no_card() -> None:
    assert detect_card_quad(np.full((800, 600, 3), 120, dtype=np.uint8)) is None


def test_rectify_or_whole_without_quad_returns_canonical() -> None:
    card = rectify_or_whole(np.full((880, 630, 3), 100, dtype=np.uint8), None)
    assert card.shape == (constants.CARD_HEIGHT_PX, constants.CARD_WIDTH_PX, 3)


def test_rectify_or_whole_with_quad_returns_canonical() -> None:
    frame = _frame_with_card()
    quad = detect_card_quad(frame)
    card = rectify_or_whole(frame, quad)
    assert card.shape == (constants.CARD_HEIGHT_PX, constants.CARD_WIDTH_PX, 3)
