"""Filesystem layout and IO for the cached reference images.

Card images and set symbols are stored under ``DATA_DIR`` in a stable, readable
tree so a re-run can tell at a glance what is already downloaded and skip it.
This module only computes paths and writes bytes; fetching them is
:class:`app.reference.tcgdex.TCGdexClient`'s job.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from app.core import constants

logger = logging.getLogger(__name__)

_SAFE = re.compile(r"[^A-Za-z0-9._-]")


def _safe(name: str) -> str:
    """Return a filesystem-safe version of an id segment."""
    return _SAFE.sub("_", name)


def images_root(data_dir: str | Path) -> Path:
    """Return the directory holding cached card images."""
    return Path(data_dir) / "images"


def symbols_root(data_dir: str | Path) -> Path:
    """Return the directory holding cached set-symbol images."""
    return Path(data_dir) / "symbols"


def card_image_path(data_dir: str | Path, set_id: str, card_id: str) -> Path:
    """Return the on-disk path for a card image, grouped by set."""
    ext = constants.TCGDEX_IMAGE_EXTENSION
    return images_root(data_dir) / _safe(set_id) / f"{_safe(card_id)}.{ext}"


def symbol_image_path(data_dir: str | Path, set_id: str) -> Path:
    """Return the on-disk path for a set-symbol image."""
    return symbols_root(data_dir) / f"{_safe(set_id)}.{constants.TCGDEX_IMAGE_EXTENSION}"


def thumbs_root(data_dir: str | Path) -> Path:
    """Return the directory holding derived card thumbnails."""
    return Path(data_dir) / "thumbs"


def thumbnail_path(data_dir: str | Path, card_id: str) -> Path:
    """Return the on-disk path for a card's UI thumbnail."""
    return thumbs_root(data_dir) / f"{_safe(card_id)}.jpg"


def ensure_thumbnail(source: Path, dest: Path, width: int) -> Path:
    """Create a small JPEG thumbnail of ``source`` at ``dest`` if not present.

    Serving the full hi-res reference PNG (~1 MB) to a UI is wasteful; a
    width-bounded JPEG is ~20x smaller and visually identical at card size.

    Args:
        source: The cached hi-res reference image.
        dest: Thumbnail destination (created on first request).
        width: Target width in pixels; height follows the aspect ratio.

    Returns:
        The thumbnail path.
    """
    if not (dest.is_file() and dest.stat().st_size > 0):
        from PIL import Image

        with Image.open(source) as img:
            rgb = img.convert("RGB")
            height = round(rgb.height * width / rgb.width)
            small = rgb.resize((width, height), Image.Resampling.LANCZOS)
        dest.parent.mkdir(parents=True, exist_ok=True)
        small.save(dest, "JPEG", quality=85)
    return dest


def save_bytes(path: Path, data: bytes) -> None:
    """Write bytes to ``path``, creating parent directories as needed.

    Args:
        path: Destination file.
        data: Raw image bytes to write.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def exists_nonempty(path: Path) -> bool:
    """Return whether ``path`` is a file with a non-zero size."""
    return path.is_file() and path.stat().st_size > 0
