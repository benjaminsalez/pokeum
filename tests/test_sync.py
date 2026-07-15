"""Offline unit tests for reference sync (app/reference/sync.py).

Uses httpx.MockTransport to serve canned TCGdex responses, so the test exercises
the real sync logic with no network.
"""

from __future__ import annotations

import io
from pathlib import Path

import httpx
from PIL import Image

from app.reference import sync as sync_module
from app.reference.store import ReferenceStore
from app.reference.tcgdex import TCGdexClient

_SET_DETAIL = {
    "id": "t1",
    "name": "Test Set",
    "serie": {"name": "Test Serie"},
    "releaseDate": "2020-01-01",
    "cardCount": {"total": 2, "official": 2},
    "symbol": "https://cdn.test/t1/symbol",
    "cards": [
        {"id": "t1-1", "localId": "1", "name": "Alpha", "image": "https://cdn.test/t1/1"},
        {"id": "t1-2", "localId": "2", "name": "Beta", "image": "https://cdn.test/t1/2"},
    ],
}
_CARDS = {
    "t1-1": {
        "id": "t1-1",
        "localId": "1",
        "name": "Alpha",
        "image": "https://cdn.test/t1/1",
        "rarity": "Common",
        "variants": {"normal": True, "reverse": True},
    },
    "t1-2": {
        "id": "t1-2",
        "localId": "2",
        "name": "Beta",
        "image": "https://cdn.test/t1/2",
        "rarity": "Uncommon",
        "variants": {"normal": True, "reverse": False},
    },
}


def _png_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (10, 14), (120, 60, 30)).save(buffer, "PNG")
    return buffer.getvalue()


def _handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/v2/en/sets":
        return httpx.Response(200, json=[{"id": "t1", "cardCount": {"total": 2}}])
    if path == "/v2/en/sets/t1":
        return httpx.Response(200, json=_SET_DETAIL)
    if path.startswith("/v2/en/cards/"):
        return httpx.Response(200, json=_CARDS[path.rsplit("/", 1)[1]])
    if path.endswith(("high.png", "symbol.png")):
        return httpx.Response(200, content=_png_bytes())
    return httpx.Response(404)


def _client() -> TCGdexClient:
    http = httpx.Client(base_url="https://api.test", transport=httpx.MockTransport(_handler))
    return TCGdexClient("https://api.test/v2", "en", client=http)


def test_sync_populates_store_and_images(tmp_path: Path) -> None:
    store = ReferenceStore(tmp_path / "ref.db")
    summary = sync_module.sync(store, _client(), tmp_path)

    assert summary.cards_upserted == 2
    assert summary.images_downloaded == 2
    assert summary.symbols_downloaded == 1
    assert store.count_cards() == 2
    assert len(store.cards_with_images()) == 2

    alpha = store.get_card("t1-1")
    assert alpha is not None
    assert alpha.has_reverse is True
    assert alpha.set_name == "Test Set"
    assert alpha.era == "modern"
    store.close()


def test_sync_is_idempotent(tmp_path: Path) -> None:
    store = ReferenceStore(tmp_path / "ref.db")
    sync_module.sync(store, _client(), tmp_path)
    # Second run: the set is already current, so nothing new is processed.
    again = sync_module.sync(store, _client(), tmp_path)
    assert again.sets_processed == 0
    assert again.images_downloaded == 0
    store.close()
