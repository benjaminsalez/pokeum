"""Reference sync: pull TCGdex data into the local store and image cache.

This is the "new set" path. Syncing is a pure data operation — download
metadata and images, never train anything — so a freshly released set becomes
recognisable by running ``sync`` then ``index build``.

The sync is incremental and idempotent: sets whose card count already matches
the catalogue are skipped, images that already exist on disk are not
re-downloaded, and every record is upserted so re-running only fills gaps.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core import constants
from app.reference import images
from app.reference.store import ReferenceStore
from app.reference.tcgdex import ParsedCard, TCGdexClient, parse_card, parse_set

logger = logging.getLogger(__name__)

ProgressFn = Callable[[str], None]


@dataclass(frozen=True, slots=True)
class SyncSummary:
    """Counts describing what one sync run changed."""

    sets_processed: int
    cards_upserted: int
    images_downloaded: int
    symbols_downloaded: int


def sync(
    store: ReferenceStore,
    client: TCGdexClient,
    data_dir: str | Path,
    *,
    only_set: str | None = None,
    fetch_details: bool = True,
    force: bool = False,
) -> SyncSummary:
    """Sync sets and cards from TCGdex into ``store`` and the image cache.

    Args:
        store: Catalogue to write into.
        client: TCGdex transport to read from.
        data_dir: Root directory for cached images and symbols.
        only_set: When given, sync just this set id.
        fetch_details: Fetch per-card detail (variants, rarity). When ``False``,
            only the cheaper set-detail briefs are used (no variant flags).
        force: Re-process sets even when their card count already matches.

    Returns:
        A :class:`SyncSummary` of the work performed.
    """
    now = datetime.now(UTC).isoformat()
    briefs = client.list_sets()
    known = store.known_set_ids()

    sets_processed = 0
    cards_upserted = 0
    with ThreadPoolExecutor(max_workers=constants.SYNC_CONCURRENCY) as pool:
        for brief in briefs:
            set_id = str(brief.get("id", ""))
            if not set_id or (only_set and set_id != only_set):
                continue
            if not force and _set_is_current(store, set_id, brief, known):
                logger.debug("set %s already current; skipping", set_id)
                continue
            cards_upserted += _sync_one_set(
                store, client, data_dir, set_id, now, fetch_details, pool
            )
            sets_processed += 1

        images_downloaded = _download_missing_images(store, client, data_dir, pool)

    symbols_downloaded = _download_symbols(store, client, data_dir)
    store.set_meta("last_sync", now)
    logger.info(
        "sync done: %d set(s), %d card(s), %d image(s), %d symbol(s)",
        sets_processed,
        cards_upserted,
        images_downloaded,
        symbols_downloaded,
    )
    return SyncSummary(sets_processed, cards_upserted, images_downloaded, symbols_downloaded)


def _set_is_current(
    store: ReferenceStore, set_id: str, brief: dict[str, Any], known: set[str]
) -> bool:
    """Return whether a set's cards are already fully present locally."""
    if set_id not in known:
        return False
    count = brief.get("cardCount")
    brief_total = count.get("total") if isinstance(count, dict) else None
    stored_total = store.set_card_count(set_id)
    return brief_total is not None and stored_total == brief_total


def _sync_one_set(
    store: ReferenceStore,
    client: TCGdexClient,
    data_dir: str | Path,
    set_id: str,
    now: str,
    fetch_details: bool,
    pool: ThreadPoolExecutor,
) -> int:
    """Fetch one set and its cards, upsert them, and return the card count."""
    detail = client.get_set(set_id)
    parsed_set = parse_set(detail)
    store.upsert_set(**asdict(parsed_set), synced_at=now)

    raw_cards = detail.get("cards")
    briefs = raw_cards if isinstance(raw_cards, list) else []
    release_year = _year_from_date(parsed_set.release_date)
    total = parsed_set.card_count_official

    if fetch_details:
        raws = list(pool.map(_safe_get_card(client), (b.get("id") for b in briefs)))
    else:
        raws = list(briefs)

    count = 0
    for raw in raws:
        if not raw:
            continue
        parsed = parse_card(raw, set_id=set_id, number_total=total, release_year=release_year)
        _upsert_card(store, parsed)
        count += 1
    logger.info("synced set %s: %d card(s)", set_id, count)
    return count


def _safe_get_card(client: TCGdexClient) -> Callable[[Any], dict[str, Any] | None]:
    """Return a worker that fetches one card detail, swallowing failures."""

    def _worker(card_id: Any) -> dict[str, Any] | None:
        if not card_id:
            return None
        try:
            return client.get_card(str(card_id))
        except RuntimeError as error:
            logger.warning("card %s detail failed: %s", card_id, error)
            return None

    return _worker


def _upsert_card(store: ReferenceStore, parsed: ParsedCard) -> None:
    """Upsert a parsed card into the store."""
    store.upsert_card(
        card_id=parsed.card_id,
        set_id=parsed.set_id,
        name=parsed.name,
        number=parsed.number,
        number_total=parsed.number_total,
        rarity=parsed.rarity,
        release_year=parsed.release_year,
        image_url=parsed.image_url,
        has_reverse=parsed.has_reverse,
        has_first_edition=parsed.has_first_edition,
        has_holo=parsed.has_holo,
        has_normal=parsed.has_normal,
    )


def _download_missing_images(
    store: ReferenceStore,
    client: TCGdexClient,
    data_dir: str | Path,
    pool: ThreadPoolExecutor,
) -> int:
    """Download every card image the store lacks, concurrently."""
    jobs = store.cards_missing_image()
    if not jobs:
        return 0

    def _fetch(job: tuple[str, str, str]) -> tuple[str, Path] | None:
        card_id, set_id, url = job
        dest = images.card_image_path(data_dir, set_id, card_id)
        if images.exists_nonempty(dest):
            return card_id, dest
        try:
            images.save_bytes(dest, client.get_bytes(url))
        except RuntimeError as error:
            logger.warning("image for %s failed: %s", card_id, error)
            return None
        return card_id, dest

    downloaded = 0
    for result in pool.map(_fetch, jobs):
        if result is None:
            continue
        card_id, dest = result
        store.set_image_path(card_id, str(dest))
        downloaded += 1
    return downloaded


def _download_symbols(store: ReferenceStore, client: TCGdexClient, data_dir: str | Path) -> int:
    """Download set-symbol images for sets that have a URL but no cached file."""
    downloaded = 0
    for set_id, symbol_url in store.sets_needing_symbols():
        dest = images.symbol_image_path(data_dir, set_id)
        if images.exists_nonempty(dest):
            store.set_symbol_path(set_id, str(dest))
            continue
        try:
            images.save_bytes(dest, client.get_bytes(symbol_url))
        except RuntimeError as error:
            logger.warning("symbol for %s failed: %s", set_id, error)
            continue
        store.set_symbol_path(set_id, str(dest))
        downloaded += 1
    return downloaded


def _year_from_date(date: str | None) -> int | None:
    """Extract a four-digit year from a ``YYYY-MM-DD`` string, if present."""
    if not date or len(date) < 4 or not date[:4].isdigit():
        return None
    return int(date[:4])
