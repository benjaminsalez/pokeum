"""Background S3 upload of accepted scans (image + annotation JSON).

When a user saves a scan in the UI — and has consented — the captured photo and
its match annotation are collected as training data for future encoder
fine-tunes. Collection is strictly best-effort: it runs after the HTTP response,
never raises, and no-ops entirely while no bucket is configured, so the app
behaves identically with or without S3 credentials.

boto3 is imported lazily inside the client factory: the dependency is installed,
but nothing S3-related loads until the first enabled upload.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from app.core import config

logger = logging.getLogger(__name__)

# Object extension per uploaded content type; anything unknown is stored as
# .jpg because the scan UI always captures JPEG.
_EXTENSIONS = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}


class ScanCollector:
    """Uploads accepted scans to S3 in the background, or no-ops when disabled."""

    def __init__(
        self,
        *,
        bucket: str,
        region: str = "",
        key_prefix: str = "scans",
        endpoint_url: str = "",
        client: Any | None = None,
    ) -> None:
        """Create a collector for one bucket.

        Args:
            bucket: Target S3 bucket; empty disables the collector.
            region: AWS region; empty defers to boto3's default resolution.
            key_prefix: Prefix under which objects are stored.
            endpoint_url: Custom endpoint for S3-compatible stores; empty = AWS.
            client: Pre-built S3 client, used by tests; one is created lazily
                from the other settings when omitted.
        """
        self._bucket = bucket
        self._region = region
        self._prefix = key_prefix.strip("/")
        self._endpoint_url = endpoint_url
        self._client = client

    @classmethod
    def from_config(cls) -> ScanCollector:
        """Build a collector from the ``SCANS_S3_*`` settings."""
        return cls(
            bucket=config.scans_s3_bucket(),
            region=config.scans_s3_region(),
            key_prefix=config.scans_s3_prefix(),
            endpoint_url=config.scans_s3_endpoint_url(),
        )

    @property
    def enabled(self) -> bool:
        """Return whether a bucket is configured and uploads will be attempted."""
        return bool(self._bucket)

    def store(self, image: bytes, content_type: str, annotation: dict[str, object]) -> None:
        """Upload one accepted scan: the image and a sibling annotation JSON.

        Designed to run as a FastAPI background task after the response: any
        failure is logged and swallowed so collection can never affect a user.

        Args:
            image: The captured photo, as uploaded.
            content_type: MIME type of the image.
            annotation: The scan's annotation payload, stored as JSON.
        """
        if not self.enabled:
            logger.debug("scan collection disabled; dropping %d-byte scan", len(image))
            return
        try:
            base_key = self._object_base_key()
            extension = _EXTENSIONS.get(content_type, "jpg")
            client = self._s3_client()
            client.put_object(
                Bucket=self._bucket,
                Key=f"{base_key}.{extension}",
                Body=image,
                ContentType=content_type,
            )
            client.put_object(
                Bucket=self._bucket,
                Key=f"{base_key}.json",
                Body=json.dumps(annotation).encode("utf-8"),
                ContentType="application/json",
            )
            logger.info("collected scan %s.%s (%d bytes)", base_key, extension, len(image))
        except Exception as error:  # noqa: BLE001 - collection must never surface
            logger.warning("scan upload failed: %s", error)

    def _object_base_key(self) -> str:
        """Return a fresh date-partitioned object key without extension."""
        now = datetime.now(UTC)
        return f"{self._prefix}/{now:%Y/%m/%d}/{uuid.uuid4().hex}"

    def _s3_client(self) -> Any:
        """Return the S3 client, building and caching it on first use."""
        if self._client is None:
            import boto3

            kwargs: dict[str, str] = {}
            if self._region:
                kwargs["region_name"] = self._region
            if self._endpoint_url:
                kwargs["endpoint_url"] = self._endpoint_url
            self._client = boto3.client("s3", **kwargs)
        return self._client
