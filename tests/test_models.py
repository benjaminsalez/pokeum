"""Unit tests for the domain models (app/models.py)."""

from __future__ import annotations

from app.models import (
    Candidate,
    CardRef,
    OcrObservation,
    RecognitionResult,
    RecognitionStatus,
    VariantGuess,
    VariantKind,
)


def _card(card_id: str = "sv02-025", total: int | None = 193) -> CardRef:
    return CardRef(
        card_id=card_id,
        name="Pikachu",
        set_id="sv02",
        set_name="Paldea Evolved",
        number="25",
        set_code="PAL",
        number_total=total,
        rarity="Common",
    )


def test_display_number_with_total() -> None:
    assert _card().display_number == "25/193"


def test_display_number_without_total() -> None:
    assert _card(total=None).display_number == "25"


def test_cardref_as_dict_shape() -> None:
    data = _card().as_dict()
    assert data["card_id"] == "sv02-025"
    assert data["set"] == {"id": "sv02", "name": "Paldea Evolved", "code": "PAL"}
    assert data["number"] == "25/193"


def test_ocr_observation_is_useful() -> None:
    assert OcrObservation(raw_text="25/193", number="25").is_useful
    assert OcrObservation(raw_text="", set_code="PAL").is_useful
    assert not OcrObservation(raw_text="noise").is_useful


def test_result_as_dict_includes_variants_on_match() -> None:
    candidate = Candidate(card=_card(), confidence=0.9, per_signal={"hash": 0.9})
    variant = VariantGuess(VariantKind.REVERSE_HOLO, present=True, confidence=0.7)
    result = RecognitionResult(
        status=RecognitionStatus.CONFIDENT, match=candidate, variants=(variant,)
    )
    data = result.as_dict()
    assert data["status"] == "confident"
    assert data["match"]["variants"][0]["kind"] == "reverse_holo"
    assert data["match"]["confidence"] == 0.9


def test_result_as_dict_no_match() -> None:
    data = RecognitionResult(status=RecognitionStatus.NO_MATCH).as_dict()
    assert data["match"] is None
    assert data["alternates"] == []
