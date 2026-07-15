"""RAM-backed batch feeder for training and eval.

The whole catalogue lives in RAM as one uint8 tensor (~5.4 GB at 20k cards),
so a "batch" is an index-select plus one host-to-device copy (~10 ms for 512
cards) — the CPU never decodes images inside the loop and cannot starve the
GPU. Augmentation and normalization happen on-device afterwards
(:mod:`training.augment`, :func:`training.model.preprocess`).
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import torch

from training.cache import load_cache

logger = logging.getLogger(__name__)


class CardImages:
    """The cached card catalogue held in RAM, indexable by position."""

    def __init__(self, card_ids: list[str], images: np.ndarray) -> None:
        """Wrap the loaded cache arrays.

        Args:
            card_ids: Card id per row.
            images: ``(N, H, W, 3)`` uint8 array.
        """
        self.card_ids = card_ids
        # torch.from_numpy shares memory with the numpy array — no extra copy.
        self._images = torch.from_numpy(np.ascontiguousarray(images))

    @classmethod
    def load(cls, data_dir: str | Path) -> CardImages:
        """Load the cache blob from ``data_dir`` into RAM."""
        card_ids, images = load_cache(Path(data_dir))
        logger.info("loaded %d cached cards (%.1f GB)", len(card_ids), images.nbytes / 1e9)
        return cls(card_ids, images)

    def __len__(self) -> int:
        """Return the number of cards."""
        return len(self.card_ids)

    def batch(self, indices: torch.Tensor, device: torch.device) -> torch.Tensor:
        """Ship the selected cards to the device as a float [0,1] NCHW batch.

        Args:
            indices: 1-D long tensor of row indices (CPU).
            device: Target device.

        Returns:
            ``(B, 3, H, W)`` float32 tensor in ``[0, 1]`` on ``device``.
        """
        selected = self._images.index_select(0, indices)  # (B, H, W, 3) uint8 CPU
        gpu = selected.to(device, non_blocking=True)
        return gpu.permute(0, 3, 1, 2).float() / 255.0


def epoch_batches(
    count: int, batch_size: int, generator: torch.Generator
) -> Iterator[torch.Tensor]:
    """Yield shuffled index batches covering all rows once (drop tiny tail).

    Args:
        count: Number of rows.
        batch_size: Cards per batch.
        generator: CPU generator driving the shuffle.

    Yields:
        1-D long tensors of indices. A final partial batch is kept when it has
        at least 8 cards (NT-Xent needs some negatives), else dropped.
    """
    order = torch.randperm(count, generator=generator)
    for start in range(0, count, batch_size):
        chunk = order[start : start + batch_size]
        if len(chunk) >= 8:
            yield chunk
