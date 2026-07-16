"""Tests for single-origin SPA serving (app/api/static.py + server mount)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.server import create_app
from app.recognize.pipeline import Recognizer
from app.reference.store import ReferenceStore
from app.signals.hashes import HashIndex


def _recognizer(tmp_path: Path) -> Recognizer:
    return Recognizer(ReferenceStore(tmp_path / "ref.db"), hash_index=HashIndex([], {}))


def _dist(tmp_path: Path) -> Path:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>pokeum shell</html>", encoding="utf-8")
    (dist / "assets" / "app-abc123.js").write_text("console.log('app')", encoding="utf-8")
    return dist


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("FRONTEND_DIST_DIR", str(_dist(tmp_path)))
    with TestClient(create_app(_recognizer(tmp_path))) as test_client:
        yield test_client


def test_root_serves_index(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "pokeum shell" in response.text
    assert response.headers["cache-control"] == "no-cache"


def test_unknown_route_falls_back_to_index(client: TestClient) -> None:
    response = client.get("/some/client/route")
    assert response.status_code == 200
    assert "pokeum shell" in response.text
    assert response.headers["cache-control"] == "no-cache"


def test_hashed_asset_served_without_no_cache(client: TestClient) -> None:
    response = client.get("/assets/app-abc123.js")
    assert response.status_code == 200
    assert response.headers.get("cache-control") != "no-cache"


def test_unknown_api_route_stays_json_404(client: TestClient) -> None:
    response = client.get("/api/nope")
    assert response.status_code == 404
    assert response.json()["detail"] == "Not Found"


def test_serving_disabled_without_dist_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FRONTEND_DIST_DIR", str(tmp_path / "missing"))
    with TestClient(create_app(_recognizer(tmp_path))) as client:
        assert client.get("/").status_code == 404
        assert client.get("/api/health").status_code == 200


def test_serving_disabled_with_empty_setting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _dist(tmp_path)
    monkeypatch.setenv("FRONTEND_DIST_DIR", "")
    with TestClient(create_app(_recognizer(tmp_path))) as client:
        assert client.get("/").status_code == 404
