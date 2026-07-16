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
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from app.core import config, constants
from app.vision import imaging

logger = logging.getLogger(__name__)


def _normalize_image(data: bytes) -> bytes:
    """Re-encode an uploaded scan at training-grade size as lossy webp.

    Clients already downscale, but the server is the enforcement point: stored
    scans never exceed ``SCAN_STORE_MAX_SIDE`` on the long side — enough for
    the training cache resolution with the guide margin included — regardless
    of what was uploaded.

    Args:
        data: The uploaded image bytes.

    Returns:
        The normalized webp bytes.

    Raises:
        ValueError: When the bytes cannot be decoded or re-encoded.
    """
    image = imaging.decode_bytes(data)
    capped = imaging.cap_long_side(image, constants.SCAN_STORE_MAX_SIDE)
    return imaging.encode_webp(capped, constants.SCAN_STORE_WEBP_QUALITY)


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
        # (monotonic timestamp, total bytes) of the last bucket-usage sweep.
        self._usage: tuple[float, int] | None = None

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
        The image is normalized to training-grade resolution and webp before
        upload (see ``SCAN_STORE_*`` in constants), and nothing is uploaded
        while the bucket sits at or above ``SCANS_MAX_BUCKET_BYTES``.

        Args:
            image: The captured photo, as uploaded.
            content_type: MIME type of the uploaded image (storage is always
                webp after normalization).
            annotation: The scan's annotation payload, stored as JSON.
        """
        if not self.enabled:
            logger.debug("scan collection disabled; dropping %d-byte scan", len(image))
            return
        try:
            normalized = _normalize_image(image)
            payload = json.dumps(annotation).encode("utf-8")
            client = self._s3_client()
            usage = self._bucket_usage(client)
            if usage >= constants.SCANS_MAX_BUCKET_BYTES:
                logger.warning(
                    "scan collection paused: bucket usage %d bytes >= %d cap",
                    usage,
                    constants.SCANS_MAX_BUCKET_BYTES,
                )
                return
            base_key = self._object_base_key()
            client.put_object(
                Bucket=self._bucket,
                Key=f"{base_key}.webp",
                Body=normalized,
                ContentType="image/webp",
            )
            client.put_object(
                Bucket=self._bucket,
                Key=f"{base_key}.json",
                Body=payload,
                ContentType="application/json",
            )
            self._record_uploaded(len(normalized) + len(payload))
            logger.info(
                "collected scan %s.webp (%d bytes, from %d-byte %s upload)",
                base_key,
                len(normalized),
                len(image),
                content_type,
            )
        except Exception as error:  # noqa: BLE001 - collection must never surface
            logger.warning("scan upload failed: %s", error)

    def _bucket_usage(self, client: Any) -> int:
        """Return total bucket usage in bytes, from a TTL-cached listing sweep.

        A full listing per upload would be prohibitive, so the sweep runs at
        most once per ``SCANS_USAGE_CACHE_TTL_SECONDS``; between sweeps the
        cached figure grows by our own uploads (`_record_uploaded`), so a burst
        cannot blow far past the cap while the cache is warm.
        """
        now = time.monotonic()
        if (
            self._usage is not None
            and now - self._usage[0] < constants.SCANS_USAGE_CACHE_TTL_SECONDS
        ):
            return self._usage[1]
        total = 0
        paginator = client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self._bucket):
            total += sum(item["Size"] for item in page.get("Contents", []))
        self._usage = (now, total)
        logger.debug("bucket usage sweep: %d bytes across bucket %s", total, self._bucket)
        return total

    def _record_uploaded(self, size: int) -> None:
        """Grow the cached usage figure by bytes we just uploaded."""
        if self._usage is not None:
            self._usage = (self._usage[0], self._usage[1] + size)

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
