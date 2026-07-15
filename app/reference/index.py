"""Build and load the retrieval index over the reference catalogue.

Two kinds of precomputed artifacts back recognition:

* perceptual hashes, stored per card in the SQLite catalogue;
* embedding matrices (full card and artwork crop), stored as ``.npy`` files
  alongside a ``row_ids.json`` that maps matrix rows back to card ids.

Building is incremental and idempotent: only cards missing hashes/embeddings are
processed, so syncing a new set and re-running ``build`` is fast. Swapping the
embedder (a different ``identifier``) invalidates the matrices and forces a full
rebuild, because embeddings from different encoders are not comparable.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import numpy as np

from app.reference.store import ReferenceStore
from app.signals import hashes
from app.signals.base import Embedder
from app.signals.embedding import EmbeddingIndex
from app.vision import regions
from app.vision.imaging import load_image
from app.vision.rectify import center_crop_to_card

logger = logging.getLogger(__name__)

_META_EMBEDDER = "embedder_id"
_PROGRESS_EVERY = 200


def index_dir(data_dir: str | Path) -> Path:
    """Return the directory holding embedding matrices and row ids."""
    return Path(data_dir) / "index"


def _paths(data_dir: str | Path) -> tuple[Path, Path, Path]:
    """Return the ``(emb_full, emb_art, row_ids)`` artifact paths."""
    root = index_dir(data_dir)
    return root / "emb_full.npy", root / "emb_art.npy", root / "row_ids.json"


def _atomic_save_npy(path: Path, array: np.ndarray) -> None:
    """Write a NumPy array to ``path`` atomically (temp file then replace)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    # Write through a file handle: np.save() would otherwise append ".npy" to a
    # path that does not already end in it, misplacing the temp file.
    with open(tmp, "wb") as handle:
        np.save(handle, array)
    os.replace(tmp, path)


def _atomic_write_text(path: Path, text: str) -> None:
    """Write text to ``path`` atomically (temp file then replace)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def load_row_ids(data_dir: str | Path) -> list[str]:
    """Return the card-id row order of the embedding matrices, or ``[]``."""
    _, _, row_ids_path = _paths(data_dir)
    if not row_ids_path.is_file():
        return []
    return list(json.loads(row_ids_path.read_text(encoding="utf-8")))


def build_index(
    store: ReferenceStore,
    embedder: Embedder,
    data_dir: str | Path,
    *,
    full: bool = False,
) -> dict[str, int]:
    """Compute missing hashes and embeddings and persist the index.

    Args:
        store: Catalogue to read cards from and write hashes to.
        embedder: Encoder used for the embedding matrices.
        data_dir: Root directory for index artifacts.
        full: Recompute everything, ignoring what already exists.

    Returns:
        Counts ``{"hashed", "embedded"}`` describing the work performed.
    """
    with_images = store.cards_with_images()
    embedder_changed = store.get_meta(_META_EMBEDDER) != embedder.identifier
    rebuild_all = full or embedder_changed
    if embedder_changed and not full:
        logger.info("embedder changed to %s; rebuilding all embeddings", embedder.identifier)

    existing_ids = [] if rebuild_all else load_row_ids(data_dir)
    embedded_set = set(existing_ids)
    need_hash = {cid for cid, _ in with_images} if full else set(store.card_ids_missing_hashes())
    need_embed = [cid for cid, _ in with_images if cid not in embedded_set]
    need_embed_set = set(need_embed)

    new_full: dict[str, np.ndarray] = {}
    new_art: dict[str, np.ndarray] = {}
    hashed = 0
    for processed, (card_id, image_path) in enumerate(with_images, start=1):
        do_hash = card_id in need_hash
        do_embed = card_id in need_embed_set
        if not (do_hash or do_embed):
            continue
        try:
            card = center_crop_to_card(load_image(image_path))
        except (FileNotFoundError, ValueError) as error:
            logger.warning("skipping %s: %s", card_id, error)
            continue
        if do_hash:
            store.set_hashes(card_id, hashes.compute_hashes(card))
            hashed += 1
        if do_embed:
            new_full[card_id] = embedder.embed(card)
            new_art[card_id] = embedder.embed(regions.artwork(card))
        if processed % _PROGRESS_EVERY == 0:
            logger.info("indexed %d/%d cards", processed, len(with_images))

    embedded = _persist_embeddings(data_dir, existing_ids, need_embed, new_full, new_art)
    store.set_meta(_META_EMBEDDER, embedder.identifier)
    logger.info("index build done: %d hashed, %d embedded", hashed, embedded)
    return {"hashed": hashed, "embedded": embedded}


def _persist_embeddings(
    data_dir: str | Path,
    existing_ids: list[str],
    new_ids: list[str],
    new_full: dict[str, np.ndarray],
    new_art: dict[str, np.ndarray],
) -> int:
    """Merge new embedding rows with any existing matrices and save them."""
    added = [cid for cid in new_ids if cid in new_full]
    if not added and not existing_ids:
        return 0
    full_path, art_path, _ = _paths(data_dir)
    full_rows = [new_full[cid] for cid in added]
    art_rows = [new_art[cid] for cid in added]
    row_ids = list(existing_ids) + added

    full_matrix = _stack_with_existing(full_path, existing_ids, full_rows)
    art_matrix = _stack_with_existing(art_path, existing_ids, art_rows)

    _atomic_save_npy(full_path, full_matrix)
    _atomic_save_npy(art_path, art_matrix)
    _, _, row_ids_path = _paths(data_dir)
    _atomic_write_text(row_ids_path, json.dumps(row_ids))
    return len(added)


def _stack_with_existing(
    path: Path, existing_ids: list[str], new_rows: list[np.ndarray]
) -> np.ndarray:
    """Vertically stack an existing matrix (if any) with freshly computed rows."""
    parts: list[np.ndarray] = []
    if existing_ids and path.is_file():
        parts.append(np.load(path))
    if new_rows:
        parts.append(np.vstack(new_rows).astype(np.float32))
    if not parts:
        return np.zeros((0, 0), dtype=np.float32)
    return np.vstack(parts).astype(np.float32)


def load_embedding_indexes(
    data_dir: str | Path,
) -> tuple[EmbeddingIndex | None, EmbeddingIndex | None]:
    """Load the full-card and artwork embedding indexes, or ``(None, None)``."""
    full_path, art_path, _ = _paths(data_dir)
    row_ids = load_row_ids(data_dir)
    if not row_ids or not full_path.is_file() or not art_path.is_file():
        return None, None
    full_index = EmbeddingIndex(row_ids, np.load(full_path))
    art_index = EmbeddingIndex(row_ids, np.load(art_path))
    return full_index, art_index


def load_hash_index(store: ReferenceStore) -> hashes.HashIndex:
    """Load a :class:`~app.signals.hashes.HashIndex` from the store's hashes."""
    return hashes.HashIndex.from_store_rows(list(store.iter_hashes()))
