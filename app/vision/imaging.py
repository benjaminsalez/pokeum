"""Image loading and colour/space conversions.

A single choke point for turning files or upload bytes into the interchange
format the rest of the recognizer expects: a contiguous ``uint8`` RGB array of
shape ``(H, W, 3)``. OpenCV works in BGR, so every entry point converts here and
nothing downstream has to remember that.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np


def load_image(path: str | Path) -> np.ndarray:
    """Load an image file as an RGB array.

    Args:
        path: Path to a readable image file.

    Returns:
        The image as ``uint8`` RGB.

    Raises:
        FileNotFoundError: When the file cannot be read or decoded.
    """
    data = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if data is None:
        raise FileNotFoundError(f"cannot read image: {path}")
    return cv2.cvtColor(data, cv2.COLOR_BGR2RGB)


def decode_bytes(data: bytes) -> np.ndarray:
    """Decode raw image bytes (e.g. an upload) as an RGB array.

    Args:
        data: Encoded image bytes (PNG, JPEG, ...).

    Returns:
        The image as ``uint8`` RGB.

    Raises:
        ValueError: When the bytes cannot be decoded as an image.
    """
    buffer = np.frombuffer(data, dtype=np.uint8)
    decoded = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
    if decoded is None:
        raise ValueError("cannot decode image bytes")
    return cv2.cvtColor(decoded, cv2.COLOR_BGR2RGB)


def to_gray(image_rgb: np.ndarray) -> np.ndarray:
    """Convert an RGB image to single-channel grayscale."""
    return cv2.cvtColor(image_rgb, cv2.COLOR_RGB2GRAY)


def resize(image: np.ndarray, width: int, height: int) -> np.ndarray:
    """Resize an image to an exact ``width`` x ``height`` using area interpolation."""
    return cv2.resize(image, (width, height), interpolation=cv2.INTER_AREA)
