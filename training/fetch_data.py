"""Standalone async fetcher for TCGdex card images (training data).

Deliberately independent of ``app/`` (the training harness is never coupled to
the runtime): TCGdex set-detail responses already carry every card's image base
URL, so the whole English catalogue is ~170 set requests plus one GET per card
image. Low-quality renders (~245x337) are fetched by default — more than enough
for 224-pixel training input and 5-10x faster than hi-res.

Resume-safe: images already on disk are skipped, so an interrupted fetch just
continues on re-run. Output layout::

    <out>/images/<card_id>.<ext>
    <out>/manifest.json      # card_id -> {set_id, url, file}

Usage::

    python -m training.fetch_data --out training_data              # all EN sets
    python -m training.fetch_data --out smoke_data --sets fut2020,xya
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sys
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://api.tcgdex.net/v2"
LANGUAGE = "en"
CONCURRENCY = 32
MAX_RETRIES = 3
TIMEOUT_S = 30.0

_SAFE = re.compile(r"[^A-Za-z0-9._-]")


def _safe(name: str) -> str:
    """Return a filesystem-safe version of a card id."""
    return _SAFE.sub("_", name)


async def _get_json(client: httpx.AsyncClient, url: str) -> Any:
    """GET a JSON document with retry/backoff."""
    last: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            response = await client.get(url)
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as error:
            last = error
            await asyncio.sleep(1.0 * (attempt + 1))
    raise RuntimeError(f"GET {url} failed after retries") from last


async def _get_bytes(client: httpx.AsyncClient, url: str) -> bytes:
    """GET raw bytes with retry/backoff."""
    last: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            response = await client.get(url)
            response.raise_for_status()
            return response.content
        except httpx.HTTPError as error:
            last = error
            await asyncio.sleep(1.0 * (attempt + 1))
    raise RuntimeError(f"GET {url} failed after retries") from last


async def _list_set_briefs(client: httpx.AsyncClient, only_sets: list[str] | None) -> list[str]:
    """Return the set ids to fetch (all EN sets, or the requested subset)."""
    data = await _get_json(client, f"{BASE_URL}/{LANGUAGE}/sets")
    all_ids = [str(item["id"]) for item in data if item.get("id")]
    if only_sets:
        wanted = set(only_sets)
        return [set_id for set_id in all_ids if set_id in wanted]
    return all_ids


async def _collect_cards(
    client: httpx.AsyncClient, set_ids: list[str], semaphore: asyncio.Semaphore
) -> dict[str, dict[str, str]]:
    """Fetch set details and return ``card_id -> {set_id, url}`` for all cards."""

    async def _one(set_id: str) -> list[tuple[str, str, str]]:
        async with semaphore:
            detail = await _get_json(client, f"{BASE_URL}/{LANGUAGE}/sets/{set_id}")
        rows: list[tuple[str, str, str]] = []
        for card in detail.get("cards", []) or []:
            card_id = card.get("id")
            image_base = card.get("image")
            if card_id and image_base:
                rows.append((str(card_id), set_id, str(image_base)))
        return rows

    results = await asyncio.gather(*(_one(s) for s in set_ids), return_exceptions=True)
    cards: dict[str, dict[str, str]] = {}
    failures = 0
    for set_id, result in zip(set_ids, results, strict=True):
        if isinstance(result, BaseException):
            failures += 1
            logger.warning("set %s failed: %s", set_id, result)
            continue
        for card_id, sid, image_base in result:
            cards[card_id] = {"set_id": sid, "url": image_base}
    if failures:
        logger.warning("%d set(s) failed to list", failures)
    return cards


async def _download_images(
    client: httpx.AsyncClient,
    cards: dict[str, dict[str, str]],
    images_dir: Path,
    quality: str,
    extension: str,
    semaphore: asyncio.Semaphore,
) -> dict[str, dict[str, str]]:
    """Download every missing card image; return the manifest entries."""
    images_dir.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, dict[str, str]] = {}
    done = 0
    total = len(cards)

    async def _one(card_id: str, info: dict[str, str]) -> None:
        nonlocal done
        filename = f"{_safe(card_id)}.{extension}"
        dest = images_dir / filename
        url = f"{info['url']}/{quality}.{extension}"
        if not (dest.is_file() and dest.stat().st_size > 0):
            try:
                async with semaphore:
                    data = await _get_bytes(client, url)
                dest.write_bytes(data)
            except RuntimeError as error:
                logger.warning("image %s failed: %s", card_id, error)
                return
        manifest[card_id] = {"set_id": info["set_id"], "url": url, "file": filename}
        done += 1
        if done % 1000 == 0:
            logger.info("downloaded %d/%d images", done, total)

    await asyncio.gather(*(_one(cid, info) for cid, info in cards.items()))
    return manifest


async def fetch(out_dir: Path, only_sets: list[str] | None, quality: str, extension: str) -> int:
    """Fetch card images and write the manifest.

    Args:
        out_dir: Output root (``images/`` and ``manifest.json`` live under it).
        only_sets: Restrict to these set ids, or ``None`` for every EN set.
        quality: TCGdex image quality segment (``low`` or ``high``).
        extension: Image file extension (``webp`` or ``png``).

    Returns:
        The number of cards in the written manifest.
    """
    semaphore = asyncio.Semaphore(CONCURRENCY)
    async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
        set_ids = await _list_set_briefs(client, only_sets)
        logger.info("listing %d set(s)", len(set_ids))
        cards = await _collect_cards(client, set_ids, semaphore)
        logger.info("collected %d card(s)", len(cards))
        manifest = await _download_images(
            client, cards, out_dir / "images", quality, extension, semaphore
        )
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=0), encoding="utf-8")
    logger.info("manifest written: %d card(s)", len(manifest))
    return len(manifest)


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and run the fetch."""
    parser = argparse.ArgumentParser(description="Fetch TCGdex card images for training")
    parser.add_argument("--out", default="training_data", help="output directory")
    parser.add_argument("--sets", default=None, help="comma-separated set ids (default: all)")
    parser.add_argument("--quality", default="low", choices=["low", "high"])
    parser.add_argument("--ext", default="webp", choices=["webp", "png"])
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    only_sets = [s.strip() for s in args.sets.split(",")] if args.sets else None
    count = asyncio.run(fetch(Path(args.out), only_sets, args.quality, args.ext))
    return 0 if count > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
