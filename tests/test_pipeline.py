"""End-to-end pipeline test with local synthetic data (app/recognize/pipeline.py).

Offline by construction: builds a tiny catalogue from solid-colour images, indexes
it with the pure-NumPy embedder, and checks the recognizer picks the right card.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from app.models import RecognitionStatus
from app.recognize.pipeline import Recognizer
from app.reference import index as index_module
from app.reference.store import ReferenceStore
from app.signals.embedding import HistogramEmbedder
from app.vision.imaging import load_image

_COLORS = {"s1-1": (220, 20, 20), "s1-2": (20, 220, 20), "s1-3": (20, 20, 220)}


def _build(tmp_path: Path) -> Recognizer:
    store = ReferenceStore(tmp_path / "ref.db")
    store.upsert_set(
        set_id="s1",
        name="Set One",
        series="X",
        release_date="2020-01-01",
        card_count_total=3,
        card_count_official=3,
        set_code="ST1",
        symbol_url=None,
        synced_at="now",
    )
    for card_id, rgb in _COLORS.items():
        store.upsert_card(
            card_id=card_id,
            set_id="s1",
            name=card_id,
            number=card_id.split("-")[-1],
            number_total=3,
            rarity=None,
            release_year=2020,
            image_url="http://x",
            has_reverse=False,
            has_first_edition=False,
            has_holo=False,
            has_normal=True,
        )
        path = tmp_path / f"{card_id}.png"
        Image.new("RGB", (300, 420), rgb).save(path)
        store.set_image_path(card_id, str(path))

    embedder = HistogramEmbedder()
    index_module.build_index(store, embedder, tmp_path)
    full_index, art_index = index_module.load_embedding_indexes(tmp_path)
    return Recognizer(
        store,
        hash_index=index_module.load_hash_index(store),
        embedder=embedder,
        emb_full_index=full_index,
        emb_art_index=art_index,
    )


def test_recognizes_indexed_card(tmp_path: Path) -> None:
    recognizer = _build(tmp_path)
    result = recognizer.identify(load_image(tmp_path / "s1-2.png"))
    assert result.status in (RecognitionStatus.CONFIDENT, RecognitionStatus.UNCERTAIN)
    assert result.match is not None
    assert result.match.card.card_id == "s1-2"


def test_no_card_detected_when_required(tmp_path: Path) -> None:
    recognizer = _build(tmp_path)
    # A uniform image yields no card quad; with require_detection this is reported.
    blank = tmp_path / "blank.png"
    Image.new("RGB", (300, 420), (128, 128, 128)).save(blank)
    result = recognizer.identify(load_image(blank), require_detection=True)
    assert result.status == RecognitionStatus.NO_CARD_DETECTED
