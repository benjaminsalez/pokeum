"""Unit tests for webcam temporal aggregation (app/recognize/temporal.py)."""

from __future__ import annotations

from app.models import Candidate, CardRef, RecognitionResult, RecognitionStatus
from app.recognize.temporal import TemporalAggregator


def _frame(card_id: str | None, confidence: float = 0.9) -> RecognitionResult:
    if card_id is None:
        return RecognitionResult(status=RecognitionStatus.NO_CARD_DETECTED)
    card = CardRef(card_id=card_id, name=card_id, set_id="s", set_name="S", number="1")
    return RecognitionResult(
        status=RecognitionStatus.CONFIDENT,
        match=Candidate(card=card, confidence=confidence),
    )


def test_emits_only_after_enough_stable_votes() -> None:
    agg = TemporalAggregator(window=6, stable_votes=4, reset_after_empty=8)
    assert agg.add(_frame("a")) is None
    assert agg.add(_frame("a")) is None
    assert agg.add(_frame("a")) is None
    emission = agg.add(_frame("a"))
    assert emission is not None
    assert emission.match is not None
    assert emission.match.card.card_id == "a"


def test_does_not_re_emit_while_locked() -> None:
    agg = TemporalAggregator(window=6, stable_votes=4, reset_after_empty=8)
    for _ in range(4):
        agg.add(_frame("a"))
    assert agg.add(_frame("a")) is None
    assert agg.add(_frame("a")) is None


def test_resets_after_empty_streak_then_emits_again() -> None:
    agg = TemporalAggregator(window=6, stable_votes=4, reset_after_empty=3)
    for _ in range(4):
        agg.add(_frame("a"))
    for _ in range(3):
        agg.add(_frame(None))  # card removed -> reset
    assert agg.add(_frame("a")) is None
    assert agg.add(_frame("a")) is None
    assert agg.add(_frame("a")) is None
    assert agg.add(_frame("a")) is not None


def test_low_confidence_never_emits() -> None:
    agg = TemporalAggregator(window=6, stable_votes=4, reset_after_empty=8)
    emissions = [agg.add(_frame("a", confidence=0.5)) for _ in range(6)]
    assert all(e is None for e in emissions)
