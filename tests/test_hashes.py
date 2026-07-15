"""Unit tests for the perceptual-hash signal (app/signals/hashes.py)."""

from __future__ import annotations

import numpy as np

from app.signals.hashes import HASH_KINDS, HashIndex, compute_hashes


def _noise(seed: int) -> np.ndarray:
    # Distinct, non-degenerate content so hashes do not collide, mimicking the
    # variety of real card artwork.
    rng = np.random.default_rng(seed)
    return rng.integers(0, 256, size=(96, 96, 3), dtype=np.uint8)


def _images() -> dict[str, np.ndarray]:
    return {"a": _noise(1), "b": _noise(2), "c": _noise(3)}


def test_compute_hashes_has_all_kinds() -> None:
    values = compute_hashes(_noise(1))
    assert set(values) == set(HASH_KINDS)
    assert all(isinstance(v, str) and v for v in values.values())


def test_hash_index_ranks_exact_match_first() -> None:
    images = _images()
    rows = [(cid, compute_hashes(img)) for cid, img in images.items()]
    index = HashIndex.from_store_rows(rows)
    top = index.query(images["a"], top_k=1)
    assert top[0].card_id == "a"
    assert top[0].score == 1.0


def test_hash_index_robust_to_brightness_shift() -> None:
    images = _images()
    rows = [(cid, compute_hashes(img)) for cid, img in images.items()]
    index = HashIndex.from_store_rows(rows)
    dimmed = (images["a"].astype(np.float32) * 0.8).astype(np.uint8)
    assert index.query(dimmed, top_k=1)[0].card_id == "a"


def test_empty_index_returns_nothing() -> None:
    index = HashIndex.from_store_rows([])
    assert index.query(_noise(1)) == []
    assert len(index) == 0
