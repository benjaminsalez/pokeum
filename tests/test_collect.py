"""Unit tests for the scan collector (app/collect/s3.py) with a stubbed client."""

from __future__ import annotations

import json
import re

import pytest

from app.collect import ScanCollector


class _RecordingClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def put_object(self, **kwargs: object) -> None:
        self.calls.append(kwargs)


class _FailingClient:
    def put_object(self, **kwargs: object) -> None:
        raise RuntimeError("s3 is down")


def test_disabled_collector_never_touches_client() -> None:
    client = _RecordingClient()
    collector = ScanCollector(bucket="", client=client)
    assert collector.enabled is False
    collector.store(b"img", "image/jpeg", {"card_id": "s1-1"})
    assert client.calls == []


def test_store_uploads_image_and_sibling_json() -> None:
    client = _RecordingClient()
    collector = ScanCollector(bucket="training", key_prefix="scans", client=client)
    annotation = {"card_id": "s1-1", "consent": True}
    collector.store(b"img-bytes", "image/jpeg", annotation)

    assert len(client.calls) == 2
    image_call, json_call = client.calls
    assert image_call["Bucket"] == "training"
    assert re.fullmatch(r"scans/\d{4}/\d{2}/\d{2}/[0-9a-f]{32}\.jpg", str(image_call["Key"]))
    assert image_call["Body"] == b"img-bytes"
    assert image_call["ContentType"] == "image/jpeg"
    # The annotation lands beside the image, same key with a .json extension.
    assert str(json_call["Key"]) == str(image_call["Key"]).removesuffix(".jpg") + ".json"
    assert json_call["ContentType"] == "application/json"
    body = json_call["Body"]
    assert isinstance(body, bytes)
    assert json.loads(body.decode()) == annotation


def test_store_maps_png_content_type_to_extension() -> None:
    client = _RecordingClient()
    collector = ScanCollector(bucket="training", client=client)
    collector.store(b"png-bytes", "image/png", {})
    assert str(client.calls[0]["Key"]).endswith(".png")


def test_store_swallows_and_logs_upload_failure(caplog: pytest.LogCaptureFixture) -> None:
    collector = ScanCollector(bucket="training", client=_FailingClient())
    with caplog.at_level("WARNING"):
        collector.store(b"img", "image/jpeg", {})
    assert any("scan upload failed" in record.message for record in caplog.records)


def test_from_config_reads_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCANS_S3_BUCKET", "my-bucket")
    monkeypatch.setenv("SCANS_S3_PREFIX", "collected")
    collector = ScanCollector.from_config()
    assert collector.enabled is True


def test_from_config_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SCANS_S3_BUCKET", raising=False)
    assert ScanCollector.from_config().enabled is False
