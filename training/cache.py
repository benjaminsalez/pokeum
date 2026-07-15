"""Build the RAM-friendly training cache from fetched images.

Decodes every manifest image once into a single ``(N, 352, 256, 3)`` uint8
array (`images.npy`) plus a parallel ``card_ids.json``. Training then restarts
in seconds — no per-epoch PNG/WEBP decoding, which matters because the box has
only 16 vCPUs and the GPU must never wait on image decode.

The 352x256 cache size closely preserves the 63:88 card aspect and keeps real
resolution in the artwork window before the final 224 squash-resize.

Usage::

    python -m training.cache --data training_data
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image

from training.config import CACHE_HEIGHT, CACHE_WIDTH

logger = logging.getLogger(__name__)


def _decode_one(path_str: str) -> np.ndarray | None:
    """Decode one image file to a (CACHE_HEIGHT, CACHE_WIDTH, 3) uint8 array."""
    try:
        with Image.open(path_str) as img:
            rgb = img.convert("RGB").resize((CACHE_WIDTH, CACHE_HEIGHT), Image.BILINEAR)
        return np.asarray(rgb, dtype=np.uint8)
    except OSError:
        return None


def build_cache(data_dir: Path) -> int:
    """Decode all manifest images into ``cache/images.npy`` + ``card_ids.json``.

    Args:
        data_dir: Root containing ``manifest.json`` and ``images/``.

    Returns:
        The number of cards cached.
    """
    manifest = json.loads((data_dir / "manifest.json").read_text(encoding="utf-8"))
    entries = sorted(manifest.items())
    paths = [str(data_dir / "images" / info["file"]) for _, info in entries]
    logger.info("decoding %d image(s)", len(paths))

    with ProcessPoolExecutor() as pool:
        decoded = list(pool.map(_decode_one, paths, chunksize=64))

    card_ids: list[str] = []
    arrays: list[np.ndarray] = []
    for (card_id, _), array in zip(entries, decoded, strict=True):
        if array is None:
            logger.warning("undecodable image for %s; skipped", card_id)
            continue
        card_ids.append(card_id)
        arrays.append(array)

    cache_dir = data_dir / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    stacked = np.stack(arrays) if arrays else np.zeros((0, CACHE_HEIGHT, CACHE_WIDTH, 3), np.uint8)
    with open(cache_dir / "images.npy", "wb") as handle:
        np.save(handle, stacked)
    (cache_dir / "card_ids.json").write_text(json.dumps(card_ids), encoding="utf-8")
    logger.info("cache written: %s cards, %.1f MB", len(card_ids), stacked.nbytes / 1e6)
    return len(card_ids)


def load_cache(data_dir: Path) -> tuple[list[str], np.ndarray]:
    """Load the cache blob into RAM.

    Args:
        data_dir: Root containing ``cache/``.

    Returns:
        ``(card_ids, images)`` where images is ``(N, H, W, 3)`` uint8 in RAM.
    """
    cache_dir = data_dir / "cache"
    card_ids = json.loads((cache_dir / "card_ids.json").read_text(encoding="utf-8"))
    images = np.load(cache_dir / "images.npy")
    return list(card_ids), images


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and build the cache."""
    parser = argparse.ArgumentParser(description="Build the training image cache")
    parser.add_argument("--data", default="training_data", help="data directory")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    count = build_cache(Path(args.data))
    return 0 if count > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
