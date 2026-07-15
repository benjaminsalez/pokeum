"""Unit tests for the rule-based variant detectors (app/variants)."""

from __future__ import annotations

import numpy as np

from app.core import constants
from app.models import CardRef, VariantKind
from app.variants.assess import assess_variants
from app.variants.reverse_holo import detect_reverse_holo
from app.variants.stamps import detect_promo_stamp
from app.variants.wotc import detect_first_edition, detect_shadowless


def _blank_card(value: int = 240) -> np.ndarray:
    return np.full((constants.CARD_HEIGHT_PX, constants.CARD_WIDTH_PX, 3), value, dtype=np.uint8)


def _checkerboard_card() -> np.ndarray:
    card = _blank_card()
    card[::2, ::2] = 0
    card[1::2, 1::2] = 0
    return card


def _card_ref(**kwargs: object) -> CardRef:
    defaults: dict[str, object] = {
        "card_id": "c",
        "name": "C",
        "set_id": "s",
        "set_name": "S",
        "number": "1",
    }
    defaults.update(kwargs)
    return CardRef(**defaults)  # type: ignore[arg-type]


def test_reverse_holo_gated_off_when_no_variant() -> None:
    assert detect_reverse_holo(_checkerboard_card(), _card_ref(has_reverse=False)) is None


def test_reverse_holo_present_on_foil_texture() -> None:
    guess = detect_reverse_holo(_checkerboard_card(), _card_ref(has_reverse=True))
    assert guess is not None and guess.present
    assert guess.kind == VariantKind.REVERSE_HOLO


def test_reverse_holo_absent_on_flat_card() -> None:
    guess = detect_reverse_holo(_blank_card(), _card_ref(has_reverse=True))
    assert guess is not None and not guess.present


def test_first_edition_gated_off_for_modern() -> None:
    card = _blank_card()
    ref = _card_ref(era=constants.ERA_MODERN, has_first_edition=True)
    assert detect_first_edition(card, ref) is None


def test_first_edition_present_on_dark_stamp() -> None:
    card = _blank_card()
    # A compact dark block inside the stamp region (both edges, ~15% dark).
    card[360:420, 40:100] = 8
    ref = _card_ref(era=constants.ERA_WOTC, has_first_edition=True)
    guess = detect_first_edition(card, ref)
    assert guess is not None and guess.present


def test_shadowless_present_on_flat_frame_edge() -> None:
    ref = _card_ref(era=constants.ERA_WOTC)
    guess = detect_shadowless(_blank_card(), ref)
    assert guess is not None and guess.present


def test_shadowless_absent_with_strong_edge() -> None:
    card = _blank_card()
    # A hard vertical edge in the right-of-art frame region raises contrast.
    right = int(0.90 * constants.CARD_WIDTH_PX)
    card[:, right:] = 0
    guess = detect_shadowless(card, _card_ref(era=constants.ERA_WOTC))
    assert guess is not None and not guess.present


def test_promo_stamp_absent_on_gray() -> None:
    card = np.full((constants.CARD_HEIGHT_PX, constants.CARD_WIDTH_PX, 3), 128, dtype=np.uint8)
    guess = detect_promo_stamp(card, _card_ref())
    assert guess is not None and not guess.present


def test_assess_variants_only_returns_applicable() -> None:
    guesses = assess_variants(_blank_card(), _card_ref(has_reverse=False))
    kinds = {g.kind for g in guesses}
    assert VariantKind.REVERSE_HOLO not in kinds  # gated off (no reverse variant)
