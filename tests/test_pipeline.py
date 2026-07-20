"""End-to-end pipeline test with local synthetic data (app/recognize/pipeline.py).

Offline by construction: builds a tiny catalogue from solid-colour images, indexes
it with the pure-NumPy embedder, and checks the recognizer picks the right card.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from app.models import Candidate, RecognitionStatus
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
        set_code="STO",  # letters-only: parse_set_code only reads [A-Z]{2,4} tokens
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


def test_guided_scan_recognizes_card_without_quad(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recognizer = _build(tmp_path)
    frame = np.full((546, 390, 3), (128, 128, 128), dtype=np.uint8)
    frame[63:483, 45:345] = _COLORS["s1-2"]
    monkeypatch.setattr("app.recognize.pipeline.detect_card_quad", lambda _image: None)

    result = recognizer.identify(frame, guide_margin=0.15)

    assert result.status == RecognitionStatus.CONFIDENT
    assert result.match is not None
    assert result.match.card.card_id == "s1-2"


def test_guided_fallback_rejects_uncertain_match(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recognizer = _build(tmp_path)
    card = recognizer.store.get_card("s1-2")
    assert card is not None
    candidate = Candidate(card=card, confidence=0.7)
    monkeypatch.setattr("app.recognize.pipeline.detect_card_quad", lambda _image: None)
    monkeypatch.setattr(
        "app.recognize.pipeline.fusion.fuse",
        lambda *_args, **_kwargs: (RecognitionStatus.UNCERTAIN, [candidate]),
    )

    result = recognizer.identify(
        np.full((546, 390, 3), _COLORS["s1-2"], dtype=np.uint8), guide_margin=0.15
    )

    assert result.status == RecognitionStatus.NO_MATCH
    assert result.match is None


class _FakeOcr:
    """OCR fake returning nothing useful, to exercise the concurrent path."""

    def read_text(self, image: np.ndarray) -> list[tuple[str, float]]:
        return [("2/3", 0.9)]


def test_executor_path_matches_serial_result(tmp_path: Path) -> None:
    serial = _build(tmp_path)
    serial._ocr = _FakeOcr()  # noqa: SLF001 - white-box: same store, add OCR
    baseline = serial.identify(load_image(tmp_path / "s1-2.png"))

    with ThreadPoolExecutor(max_workers=2) as executor:
        concurrent = Recognizer(
            serial.store,
            hash_index=serial._hash_index,  # noqa: SLF001
            embedder=serial._embedder,  # noqa: SLF001
            emb_full_index=serial._emb_full,  # noqa: SLF001
            emb_art_index=serial._emb_art,  # noqa: SLF001
            ocr_engine=_FakeOcr(),
            executor=executor,
        )
        result = concurrent.identify(load_image(tmp_path / "s1-2.png"))

    assert result.status == baseline.status
    assert result.match is not None and baseline.match is not None
    assert result.match.card.card_id == baseline.match.card.card_id


class _SetCodeOcr:
    """OCR fake reading a fixed uppercase token (a set-code candidate)."""

    def __init__(self, token: str) -> None:
        self.token = token

    def read_text(self, image: np.ndarray) -> list[tuple[str, float]]:
        return [(self.token, 0.9)]


def test_unknown_ocr_set_code_is_ignored(tmp_path: Path) -> None:
    recognizer = _build(tmp_path)
    recognizer._ocr = _SetCodeOcr("ZZZZ")  # noqa: SLF001 - not a catalogue code
    result = recognizer.identify(load_image(tmp_path / "s1-2.png"))
    # The bogus code is stripped; with no number either, the observation is
    # not useful and must not appear in the result (nor poison fusion).
    assert result.ocr is None
    assert result.match is not None and result.match.card.card_id == "s1-2"


def test_catalogue_ocr_set_code_survives(tmp_path: Path) -> None:
    recognizer = _build(tmp_path)
    recognizer._ocr = _SetCodeOcr("STO")  # noqa: SLF001 - the synthetic set's code
    result = recognizer.identify(load_image(tmp_path / "s1-2.png"))
    assert result.ocr is not None
    assert result.ocr.set_code == "STO"


def test_no_card_detected_when_required(tmp_path: Path) -> None:
    recognizer = _build(tmp_path)
    # A uniform image yields no card quad; with require_detection this is reported.
    blank = tmp_path / "blank.png"
    Image.new("RGB", (300, 420), (128, 128, 128)).save(blank)
    result = recognizer.identify(load_image(blank), require_detection=True)
    assert result.status == RecognitionStatus.NO_CARD_DETECTED
