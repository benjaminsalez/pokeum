"""Unit tests for the embedding signal (app/signals/embedding.py)."""

from __future__ import annotations

import numpy as np

from app.models import SignalScore
from app.signals.embedding import EmbeddingIndex, HistogramEmbedder, l2_normalize


def _solid(color: tuple[int, int, int]) -> np.ndarray:
    img = np.zeros((60, 60, 3), dtype=np.uint8)
    img[:, :] = color
    return img


def test_l2_normalize_unit_length() -> None:
    out = l2_normalize(np.array([3.0, 4.0]))
    assert abs(float(np.linalg.norm(out)) - 1.0) < 1e-6


def test_histogram_embedder_is_deterministic_and_normalized() -> None:
    embedder = HistogramEmbedder()
    red = _solid((200, 20, 20))
    v1 = embedder.embed(red)
    v2 = embedder.embed(red.copy())
    assert v1.shape == (embedder.dim,)
    assert np.allclose(v1, v2)
    assert abs(float(np.linalg.norm(v1)) - 1.0) < 1e-5


def test_histogram_embedder_separates_colors() -> None:
    embedder = HistogramEmbedder()
    red = embedder.embed(_solid((200, 20, 20)))
    blue = embedder.embed(_solid((20, 20, 200)))
    assert float(red @ blue) < 0.99


def test_embedding_index_retrieves_nearest() -> None:
    embedder = HistogramEmbedder()
    ids = ["red", "green", "blue"]
    colors = [(200, 20, 20), (20, 200, 20), (20, 20, 200)]
    matrix = np.vstack([embedder.embed(_solid(c)) for c in colors])
    index = EmbeddingIndex(ids, matrix)
    result = index.query(embedder.embed(_solid((190, 25, 25))), top_k=1)
    assert result[0].card_id == "red"
    assert isinstance(result[0], SignalScore)


def test_embedding_index_empty() -> None:
    index = EmbeddingIndex([], np.zeros((0, 0), dtype=np.float32))
    assert index.query(np.zeros(4, dtype=np.float32)) == []
