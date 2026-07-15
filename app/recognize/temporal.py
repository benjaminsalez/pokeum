"""Temporal aggregation of per-frame results for the webcam path.

A single frame can be blurry or glared; stability across frames is the real
signal. This aggregator keeps a short sliding window of per-frame picks, tracks
an exponential moving average of each card's confidence, and emits a card only
once it both wins enough of the window and clears the confidence bar. After a run
of card-free frames (the card was removed) it resets, ready for the next card.

It is deliberately pure — it consumes :class:`RecognitionResult` values and emits
them, with no camera or image code — so the emit/lock/reset logic is unit-tested
directly.
"""

from __future__ import annotations

from collections import deque

from app.core import constants
from app.models import RecognitionResult, RecognitionStatus


def _picked_id(result: RecognitionResult) -> str | None:
    """Return the winning card id of a frame result, or ``None``."""
    return result.match.card.card_id if result.match else None


class TemporalAggregator:
    """Aggregates per-frame results into stable, de-duplicated emissions."""

    def __init__(
        self,
        window: int = constants.TEMPORAL_WINDOW,
        stable_votes: int = constants.TEMPORAL_STABLE_VOTES,
        ema_alpha: float = constants.TEMPORAL_EMA_ALPHA,
        reset_after_empty: int = constants.TEMPORAL_RESET_AFTER_EMPTY,
    ) -> None:
        """Configure the aggregation window and thresholds.

        Args:
            window: Number of recent frames considered for the vote.
            stable_votes: Votes within the window required to emit.
            ema_alpha: Weight of the newest frame in the confidence EMA.
            reset_after_empty: Consecutive empty frames that trigger a reset.
        """
        self._window_size = window
        self._stable_votes = stable_votes
        self._alpha = ema_alpha
        self._reset_after_empty = reset_after_empty
        self._frames: deque[tuple[str | None, RecognitionResult]] = deque(maxlen=window)
        self._ema: dict[str, float] = {}
        self._empty_streak = 0
        self._locked_id: str | None = None

    def reset(self) -> None:
        """Clear all state (window, EMA, lock)."""
        self._frames.clear()
        self._ema.clear()
        self._empty_streak = 0
        self._locked_id = None

    def add(self, result: RecognitionResult) -> RecognitionResult | None:
        """Feed one frame's result and return an emission when one is ready.

        Args:
            result: The recognition result for the current frame.

        Returns:
            A :class:`RecognitionResult` to report when a card first becomes
            stable, or ``None`` while accumulating or already locked.
        """
        card_id = _picked_id(result)
        if card_id is None or result.status == RecognitionStatus.NO_CARD_DETECTED:
            self._empty_streak += 1
            if self._empty_streak >= self._reset_after_empty:
                self.reset()
            return None

        self._empty_streak = 0
        self._frames.append((card_id, result))
        confidence = result.match.confidence if result.match else 0.0
        prior = self._ema.get(card_id, confidence)
        self._ema[card_id] = self._alpha * confidence + (1 - self._alpha) * prior

        if self._locked_id == card_id:
            return None
        votes = sum(1 for cid, _ in self._frames if cid == card_id)
        if votes >= self._stable_votes and self._ema[card_id] >= constants.CONFIDENT_THRESHOLD:
            self._locked_id = card_id
            return result
        return None
