"""Unit tests for the scan collector (app/collect/s3.py) with a stubbed client."""

from __future__ import annotations

import io
import json
import re

import pytest
from PIL import Image

from app.collect import ScanCollector
from app.core import constants


class _FakePaginator:
    def __init__(self, sizes: list[int], counter: list[int]) -> None:
        self._sizes = sizes
        self._counter = counter

    def paginate(self, **kwargs: object):
        self._counter[0] += 1
        yield {"Contents": [{"Size": size} for size in self._sizes]}


class _RecordingClient:
    def __init__(self, object_sizes: list[int] | None = None) -> None:
        self.calls: list[dict[str, object]] = []
        self.paginate_calls = [0]
        self._object_sizes = object_sizes or []

    def put_object(self, **kwargs: object) -> None:
        self.calls.append(kwargs)

    def get_paginator(self, name: str) -> _FakePaginator:
        return _FakePaginator(self._object_sizes, self.paginate_calls)


class _FailingClient(_RecordingClient):
    def put_object(self, **kwargs: object) -> None:
        raise RuntimeError("s3 is down")


def _png_bytes(width: int = 20, height: int = 28) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (200, 40, 40)).save(buffer, "PNG")
    return buffer.getvalue()


def test_disabled_collector_never_touches_client() -> None:
    client = _RecordingClient()
    collector = ScanCollector(bucket="", client=client)
    assert collector.enabled is False
    collector.store(_png_bytes(), "image/jpeg", {"card_id": "s1-1"})
    assert client.calls == []


def test_store_uploads_normalized_webp_and_sibling_json() -> None:
    client = _RecordingClient()
    collector = ScanCollector(bucket="training", key_prefix="scans", client=client)
    annotation = {"card_id": "s1-1", "consent": True}
    collector.store(_png_bytes(), "image/png", annotation)

    assert len(client.calls) == 2
    image_call, json_call = client.calls
    assert image_call["Bucket"] == "training"
    # Storage is always webp after normalization, whatever was uploaded.
    assert re.fullmatch(r"scans/\d{4}/\d{2}/\d{2}/[0-9a-f]{32}\.webp", str(image_call["Key"]))
    assert image_call["ContentType"] == "image/webp"
    body = image_call["Body"]
    assert isinstance(body, bytes)
    assert Image.open(io.BytesIO(body)).format == "WEBP"
    # The annotation lands beside the image, same key with a .json extension.
    assert str(json_call["Key"]) == str(image_call["Key"]).removesuffix(".webp") + ".json"
    assert json_call["ContentType"] == "application/json"
    json_body = json_call["Body"]
    assert isinstance(json_body, bytes)
    assert json.loads(json_body.decode()) == annotation


def test_store_caps_stored_resolution_to_training_size() -> None:
    client = _RecordingClient()
    collector = ScanCollector(bucket="training", client=client)
    collector.store(_png_bytes(width=2000, height=1500), "image/png", {})
    body = client.calls[0]["Body"]
    assert isinstance(body, bytes)
    stored = Image.open(io.BytesIO(body))
    assert max(stored.size) == constants.SCAN_STORE_MAX_SIDE
    # Aspect is preserved by the cap.
    assert stored.size[0] > stored.size[1]


def test_store_skips_undecodable_image(caplog: pytest.LogCaptureFixture) -> None:
    client = _RecordingClient()
    collector = ScanCollector(bucket="training", client=client)
    with caplog.at_level("WARNING"):
        collector.store(b"not an image", "image/jpeg", {})
    assert client.calls == []
    assert any("scan upload failed" in record.message for record in caplog.records)


def test_store_paused_when_bucket_at_cap(caplog: pytest.LogCaptureFixture) -> None:
    over_cap = [constants.SCANS_MAX_BUCKET_BYTES]
    client = _RecordingClient(object_sizes=over_cap)
    collector = ScanCollector(bucket="training", client=client)
    with caplog.at_level("WARNING"):
        collector.store(_png_bytes(), "image/png", {})
    assert client.calls == []
    assert any("scan collection paused" in record.message for record in caplog.records)


def test_usage_sweep_is_cached_across_uploads() -> None:
    client = _RecordingClient(object_sizes=[1024])
    collector = ScanCollector(bucket="training", client=client)
    collector.store(_png_bytes(), "image/png", {})
    collector.store(_png_bytes(), "image/png", {})
    # Both uploads happened, but the listing sweep ran only once (TTL cache).
    assert len(client.calls) == 4
    assert client.paginate_calls[0] == 1


def test_store_swallows_and_logs_upload_failure(caplog: pytest.LogCaptureFixture) -> None:
    collector = ScanCollector(bucket="training", client=_FailingClient())
    with caplog.at_level("WARNING"):
        collector.store(_png_bytes(), "image/jpeg", {})
    assert any("scan upload failed" in record.message for record in caplog.records)


def test_from_config_reads_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCANS_S3_BUCKET", "my-bucket")
    monkeypatch.setenv("SCANS_S3_PREFIX", "collected")
    collector = ScanCollector.from_config()
    assert collector.enabled is True


def test_from_config_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SCANS_S3_BUCKET", raising=False)
    assert ScanCollector.from_config().enabled is False
