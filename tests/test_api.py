"""Unit tests for the HTTP API (app/api) using a fake, index-free recognizer."""

from __future__ import annotations

import io
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from app.api.server import create_app
from app.recognize.pipeline import Recognizer
from app.reference.store import ReferenceStore
from app.signals.hashes import HashIndex


def _png_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (20, 28), (200, 40, 40)).save(buffer, "PNG")
    return buffer.getvalue()


def _recognizer(tmp_path: Path) -> Recognizer:
    store = ReferenceStore(tmp_path / "ref.db")
    store.upsert_set(
        set_id="s1",
        name="Set One",
        series="X",
        release_date="2020-01-01",
        card_count_total=1,
        card_count_official=1,
        set_code="ST1",
        symbol_url=None,
        synced_at="now",
    )
    store.upsert_card(
        card_id="s1-1",
        set_id="s1",
        name="Alpha",
        number="1",
        number_total=1,
        rarity="Common",
        release_year=2020,
        image_url=None,
        has_reverse=False,
        has_first_edition=False,
        has_holo=False,
        has_normal=True,
    )
    return Recognizer(store, hash_index=HashIndex([], {}))


def test_health_reports_card_count(tmp_path: Path) -> None:
    with TestClient(create_app(_recognizer(tmp_path))) as client:
        body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["cards_indexed"] == 1
    assert body["embedder_loaded"] is False


def test_identify_accepts_image_upload(tmp_path: Path) -> None:
    with TestClient(create_app(_recognizer(tmp_path))) as client:
        response = client.post("/identify", files={"file": ("card.png", _png_bytes(), "image/png")})
    assert response.status_code == 200
    assert response.json()["status"] == "no_match"  # empty index -> nothing to match


def test_identify_rejects_non_image(tmp_path: Path) -> None:
    with TestClient(create_app(_recognizer(tmp_path))) as client:
        response = client.post(
            "/identify", files={"file": ("x.txt", b"not an image", "text/plain")}
        )
    assert response.status_code == 400


def test_card_image_missing_returns_404(tmp_path: Path) -> None:
    with TestClient(create_app(_recognizer(tmp_path))) as client:
        # Card exists but has no cached image; unknown card also 404s.
        assert client.get("/cards/s1-1/image").status_code == 404
        assert client.get("/cards/nope/image").status_code == 404


def test_get_card_found_and_missing(tmp_path: Path) -> None:
    with TestClient(create_app(_recognizer(tmp_path))) as client:
        found = client.get("/cards/s1-1")
        missing = client.get("/cards/does-not-exist")
    assert found.status_code == 200
    assert found.json()["card_id"] == "s1-1"
    assert missing.status_code == 404
