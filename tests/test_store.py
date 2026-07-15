"""Unit tests for the reference store (app/reference/store.py)."""

from __future__ import annotations

from pathlib import Path

from app.reference.store import ReferenceStore, normalize_number


def _store(tmp_path: Path) -> ReferenceStore:
    store = ReferenceStore(tmp_path / "ref.db")
    store.upsert_set(
        set_id="sv02",
        name="Paldea Evolved",
        series="Scarlet & Violet",
        release_date="2023-06-09",
        card_count_total=193,
        card_count_official=193,
        set_code="PAL",
        symbol_url=None,
        synced_at="now",
    )
    return store


def test_normalize_number_strips_leading_zeros() -> None:
    assert normalize_number("025") == "25"
    assert normalize_number(" H1 ") == "H1"


def test_upsert_and_get_card_roundtrip(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.upsert_card(
        card_id="sv02-025",
        set_id="sv02",
        name="Pikachu",
        number="25",
        number_total=193,
        rarity="Common",
        release_year=2023,
        image_url="http://x/25/high.png",
        has_reverse=True,
        has_first_edition=False,
        has_holo=False,
        has_normal=True,
    )
    card = store.get_card("sv02-025")
    assert card is not None
    assert card.name == "Pikachu"
    assert card.set_name == "Paldea Evolved"
    assert card.set_code == "PAL"
    assert card.era == "modern"
    assert card.has_reverse is True
    store.close()


def test_find_card_ids_by_number_normalizes(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.upsert_card(
        card_id="sv02-025",
        set_id="sv02",
        name="Pikachu",
        number="25",
        number_total=193,
        rarity=None,
        release_year=2023,
        image_url=None,
        has_reverse=False,
        has_first_edition=False,
        has_holo=False,
        has_normal=True,
    )
    assert store.find_card_ids_by_number("025", 193) == ["sv02-025"]
    assert store.find_card_ids_by_number("025", 999) == []
    store.close()


def test_wotc_era_from_release_year(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.upsert_card(
        card_id="base-4",
        set_id="sv02",
        name="Charizard",
        number="4",
        number_total=102,
        rarity="Rare Holo",
        release_year=1999,
        image_url=None,
        has_reverse=False,
        has_first_edition=True,
        has_holo=True,
        has_normal=False,
    )
    card = store.get_card("base-4")
    assert card is not None
    assert card.era == "wotc"
    store.close()


def test_hashes_roundtrip_and_missing(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.upsert_card(
        card_id="sv02-1",
        set_id="sv02",
        name="Card",
        number="1",
        number_total=193,
        rarity=None,
        release_year=2023,
        image_url="http://x",
        has_reverse=False,
        has_first_edition=False,
        has_holo=False,
        has_normal=True,
    )
    store.set_image_path("sv02-1", str(tmp_path / "1.png"))
    assert store.card_ids_missing_hashes() == ["sv02-1"]
    store.set_hashes("sv02-1", {"phash": "ff00", "dhash": "00ff"})
    assert store.card_ids_missing_hashes() == []
    stored = dict(store.iter_hashes())
    assert stored["sv02-1"]["phash"] == "ff00"
    store.close()


def test_meta_get_set(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert store.get_meta("missing") is None
    store.set_meta("embedder_id", "histogram-v1")
    assert store.get_meta("embedder_id") == "histogram-v1"
    store.close()
