"""Card detection: find the card's quadrilateral in a photo or frame.

A classic contour approach — blur, edge-detect, find the largest convex quad with
a plausible card size and aspect. It handles the common case (a card on a
contrasting surface) without any trained model, which keeps new sets a pure data
operation. When nothing card-like is found the caller falls back to treating the
whole (already-cropped) image as the card.
"""

from __future__ import annotations

import logging

import cv2
import numpy as np

from app.vision import geometry
from app.vision.imaging import to_gray

logger = logging.getLogger(__name__)

_MAX_CONTOURS = 10


def detect_card_quad(image_rgb: np.ndarray) -> np.ndarray | None:
    """Detect the most card-like quadrilateral in an image.

    Args:
        image_rgb: RGB image to search.

    Returns:
        A ``(4, 2)`` ``float32`` array of corner points (TL, TR, BR, BL), or
        ``None`` when no plausible card quad is found.
    """
    height, width = image_rgb.shape[:2]
    frame_area = float(height * width)
    gray = to_gray(image_rgb)
    blurred = cv2.bilateralFilter(gray, 9, 75, 75)
    edged = cv2.Canny(blurred, 50, 150)
    edged = cv2.dilate(edged, np.ones((3, 3), np.uint8), iterations=1)

    contours, _ = cv2.findContours(edged, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    ranked = sorted(contours, key=cv2.contourArea, reverse=True)[:_MAX_CONTOURS]
    for contour in ranked:
        perimeter = cv2.arcLength(contour, True)
        approx = cv2.approxPolyDP(contour, 0.02 * perimeter, True)
        if len(approx) != 4 or not cv2.isContourConvex(approx):
            continue
        quad = approx.reshape(4, 2).astype(np.float32)
        if geometry.is_plausible_card(quad, frame_area):
            return geometry.order_corners(quad)
    logger.debug("no plausible card quad detected")
    return None
