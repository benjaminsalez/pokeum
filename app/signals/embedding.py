"""Embedding signal: frozen-encoder features and cosine retrieval.

Recognition is retrieval, not classification: we embed the rectified card and
its artwork crop, then rank the catalogue by cosine similarity. Because the
encoder is frozen, a new set is only new index rows — never retraining.

Two encoders implement the same :class:`~app.signals.base.Embedder` interface:

* :class:`OnnxEmbedder` — an exported model (e.g. DINOv2-small) run via ONNX
  Runtime. Robust to glare and perspective; the recommended path.
* :class:`HistogramEmbedder` — a pure-NumPy spatial colour descriptor needing no
  model file, so the whole system runs out of the box. Weaker, but a real,
  deterministic baseline and the automatic fallback when no ONNX model is set.

The catalogue side is :class:`EmbeddingIndex`: an L2-normalized matrix scanned
with a single matmul (milliseconds for tens of thousands of cards — no ANN
library needed).
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
from PIL import Image

from app.core import constants
from app.models import SignalScore
from app.signals.base import Embedder

logger = logging.getLogger(__name__)

_EPS = 1e-8
_IMAGENET_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
_IMAGENET_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)
# HistogramEmbedder layout: GRID x GRID cells, each contributing these features.
_HIST_GRID = 4
_HIST_CELL_FEATURES = 5


def l2_normalize(vector: np.ndarray) -> np.ndarray:
    """Return the L2-normalized ``float32`` copy of a vector."""
    vec = np.asarray(vector, dtype=np.float32).ravel()
    norm = float(np.linalg.norm(vec))
    if norm < _EPS:
        return vec
    return vec / norm


class HistogramEmbedder:
    """Pure-NumPy spatial colour descriptor (no model, always available).

    Splits the image into a grid and, per cell, encodes mean hue (as a
    cos/sin pair so the hue wheel stays continuous), mean saturation, mean value,
    and value contrast. The result is layout- and colour-aware, so distinct card
    arts separate well, while being cheap and deterministic.
    """

    identifier = "histogram-v1"
    dim = _HIST_GRID * _HIST_GRID * _HIST_CELL_FEATURES

    def embed(self, image: np.ndarray) -> np.ndarray:
        """Return the normalized descriptor of one RGB image."""
        pil = Image.fromarray(np.asarray(image, dtype=np.uint8), mode="RGB")
        hsv = np.asarray(pil.resize((96, 96)).convert("HSV"), dtype=np.float32)
        hue = hsv[:, :, 0] / 255.0 * 2.0 * np.pi
        sat = hsv[:, :, 1] / 255.0
        val = hsv[:, :, 2] / 255.0
        features: list[float] = []
        rows = np.array_split(np.arange(96), _HIST_GRID)
        cols = np.array_split(np.arange(96), _HIST_GRID)
        for row_idx in rows:
            for col_idx in cols:
                cell = np.ix_(row_idx, col_idx)
                features.extend(
                    (
                        float(np.mean(np.cos(hue[cell]))),
                        float(np.mean(np.sin(hue[cell]))),
                        float(np.mean(sat[cell])),
                        float(np.mean(val[cell])),
                        float(np.std(val[cell])),
                    )
                )
        return l2_normalize(np.array(features, dtype=np.float32))


class OnnxEmbedder:
    """Runs an exported ONNX image encoder via ONNX Runtime on CPU."""

    def __init__(self, model_path: str | Path) -> None:
        """Load the ONNX model and probe its output dimensionality.

        Args:
            model_path: Path to the ``.onnx`` encoder.
        """
        import onnxruntime as ort

        self._path = Path(model_path)
        self.identifier = f"onnx:{self._path.name}"
        self._session = ort.InferenceSession(str(self._path), providers=["CPUExecutionProvider"])
        self._input_name = self._session.get_inputs()[0].name
        size = constants.EMBED_INPUT_SIZE
        probe = self._forward(np.zeros((size, size, 3), dtype=np.uint8))
        self.dim = int(probe.shape[0])

    def _forward(self, image: np.ndarray) -> np.ndarray:
        """Preprocess one RGB image and run a forward pass, returning a 1-D vector."""
        size = constants.EMBED_INPUT_SIZE
        pil = Image.fromarray(np.asarray(image, dtype=np.uint8), mode="RGB")
        arr = np.asarray(pil.resize((size, size)), dtype=np.float32) / 255.0
        arr = (arr - _IMAGENET_MEAN) / _IMAGENET_STD
        tensor = np.transpose(arr, (2, 0, 1))[None, :, :, :].astype(np.float32)
        outputs = self._session.run(None, {self._input_name: tensor})
        return np.asarray(outputs[0], dtype=np.float32).ravel()

    def embed(self, image: np.ndarray) -> np.ndarray:
        """Return the L2-normalized embedding of one RGB image."""
        return l2_normalize(self._forward(image))


def load_embedder(model_path: str | Path) -> Embedder:
    """Return the ONNX encoder when the model exists, else the fallback.

    Args:
        model_path: Configured path to an ONNX encoder.

    Returns:
        An :class:`~app.signals.base.Embedder`. Falls back to
        :class:`HistogramEmbedder` when the file is missing or fails to load.
    """
    path = Path(model_path)
    if path.is_file():
        try:
            embedder = OnnxEmbedder(path)
            logger.info("using ONNX embedder %s (dim=%d)", embedder.identifier, embedder.dim)
            return embedder
        except Exception as error:  # noqa: BLE001 - degrade rather than crash startup
            logger.warning("ONNX embedder failed (%s); using histogram fallback", error)
    logger.info("using histogram fallback embedder")
    return HistogramEmbedder()


class EmbeddingIndex:
    """An L2-normalized embedding matrix scanned by cosine similarity."""

    def __init__(self, card_ids: list[str], matrix: np.ndarray) -> None:
        """Create an index from parallel card ids and their embedding rows.

        Args:
            card_ids: Row order of ``matrix``.
            matrix: ``(N, D)`` ``float32`` array of L2-normalized rows.
        """
        self.card_ids = card_ids
        self.matrix = matrix.astype(np.float32) if matrix.size else matrix

    def __len__(self) -> int:
        """Return the number of indexed cards."""
        return len(self.card_ids)

    def query(
        self, vector: np.ndarray, top_k: int = constants.RETRIEVAL_SHORTLIST
    ) -> list[SignalScore]:
        """Return the ``top_k`` closest cards to an embedding by cosine similarity.

        Args:
            vector: A query embedding (need not be pre-normalized).
            top_k: Number of best matches to return.

        Returns:
            The highest-scoring cards, best first, scored in ``[0, 1]``.
        """
        if not self.card_ids or self.matrix.size == 0:
            return []
        query = l2_normalize(vector)
        cosine = self.matrix @ query
        scores = np.clip((cosine + 1.0) / 2.0, 0.0, 1.0)
        order = np.argsort(scores)[::-1][:top_k]
        return [SignalScore(self.card_ids[i], float(scores[i])) for i in order]
