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
    return images_root(data_dir) / _safe(set_id) / f"{_safe(card_id)}.png"


def symbol_image_path(data_dir: str | Path, set_id: str) -> Path:
    """Return the on-disk path for a set-symbol image."""
    return symbols_root(data_dir) / f"{_safe(set_id)}.png"


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
