"""Thin client and parsers for the TCGdex REST API.

TCGdex (https://tcgdex.dev) is a free, keyless catalogue of Pokémon cards. This
module keeps two concerns apart:

* transport — :class:`TCGdexClient` issues the HTTP calls with retries;
* interpretation — the module-level ``parse_*`` functions turn TCGdex's JSON
  into flat, storage-ready records and are pure, so they are unit-tested without
  any network.

TCGdex serves images and symbols from extension-less base URLs; the URL helpers
here append the quality/extension suffix the API expects.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

from app.core import constants

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ParsedSet:
    """Storage-ready fields extracted from a TCGdex set detail response."""

    set_id: str
    name: str
    series: str | None
    release_date: str | None
    card_count_total: int | None
    card_count_official: int | None
    set_code: str | None
    symbol_url: str | None


@dataclass(frozen=True, slots=True)
class ParsedCard:
    """Storage-ready fields extracted from a TCGdex card detail response."""

    card_id: str
    set_id: str
    name: str
    number: str
    number_total: int | None
    rarity: str | None
    release_year: int | None
    image_url: str | None
    has_reverse: bool
    has_first_edition: bool
    has_holo: bool
    has_normal: bool


def card_image_url(image_base: str | None) -> str | None:
    """Return the high-resolution PNG URL for a TCGdex image base, or ``None``."""
    if not image_base:
        return None
    return f"{image_base}/{constants.TCGDEX_IMAGE_QUALITY}.{constants.TCGDEX_IMAGE_EXTENSION}"


def symbol_image_url(symbol_base: str | None) -> str | None:
    """Return the PNG URL for a TCGdex set-symbol base, or ``None``."""
    if not symbol_base:
        return None
    return f"{symbol_base}.{constants.TCGDEX_IMAGE_EXTENSION}"


def _year_from_date(date: str | None) -> int | None:
    """Extract a four-digit year from a ``YYYY-MM-DD`` string, if present."""
    if not date or len(date) < 4 or not date[:4].isdigit():
        return None
    return int(date[:4])


def _extract_set_code(raw: dict[str, Any]) -> str | None:
    """Best-effort read of a set's short printed code from varied TCGdex fields."""
    abbr = raw.get("abbreviation")
    if isinstance(abbr, dict):
        code = abbr.get("official") or abbr.get("localId")
        if code:
            return str(code).upper()
    for key in ("tcgOnline", "ptcgoCode", "abbreviation"):
        value = raw.get(key)
        if isinstance(value, str) and value:
            return value.upper()
    return None


def parse_set(raw: dict[str, Any]) -> ParsedSet:
    """Turn a TCGdex set detail dict into a :class:`ParsedSet`.

    Args:
        raw: Decoded JSON from ``GET /{lang}/sets/{id}``.

    Returns:
        The storage-ready set record.
    """
    series_name = _as_dict(raw.get("serie")).get("name")
    count = _as_dict(raw.get("cardCount"))
    return ParsedSet(
        set_id=str(raw["id"]),
        name=str(raw.get("name", raw["id"])),
        series=series_name,
        release_date=raw.get("releaseDate"),
        card_count_total=_as_int(count.get("total")),
        card_count_official=_as_int(count.get("official")),
        set_code=_extract_set_code(raw),
        symbol_url=symbol_image_url(raw.get("symbol")),
    )


def parse_card(
    raw: dict[str, Any],
    *,
    set_id: str,
    number_total: int | None,
    release_year: int | None,
) -> ParsedCard:
    """Turn a TCGdex card detail dict into a :class:`ParsedCard`.

    Args:
        raw: Decoded JSON from ``GET /{lang}/cards/{id}`` (or a set-detail brief).
        set_id: Id of the owning set (briefs omit it).
        number_total: Printed set total to attach, from the set context.
        release_year: Release year to attach, from the set context.

    Returns:
        The storage-ready card record.
    """
    variants = _as_dict(raw.get("variants"))
    return ParsedCard(
        card_id=str(raw["id"]),
        set_id=set_id,
        name=str(raw.get("name", raw["id"])),
        number=str(raw.get("localId", raw["id"])),
        number_total=number_total,
        rarity=raw.get("rarity"),
        release_year=release_year,
        image_url=card_image_url(raw.get("image")),
        has_reverse=bool(variants.get("reverse", False)),
        has_first_edition=bool(variants.get("firstEdition", False)),
        has_holo=bool(variants.get("holo", False)),
        has_normal=bool(variants.get("normal", True)),
    )


def _as_int(value: Any) -> int | None:
    """Coerce a JSON number to ``int`` where possible, else ``None``."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return None


def _as_dict(value: Any) -> dict[str, Any]:
    """Return ``value`` when it is a dict, else an empty dict."""
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    """Return ``value`` when it is a list, else an empty list."""
    return value if isinstance(value, list) else []


class TCGdexClient:
    """Synchronous TCGdex transport with bounded retries."""

    def __init__(
        self,
        base_url: str,
        language: str,
        client: httpx.Client | None = None,
    ) -> None:
        """Create a client.

        Args:
            base_url: API root, e.g. ``https://api.tcgdex.net/v2``.
            language: Language segment used in every path (e.g. ``en``).
            client: Pre-built httpx client (injected in tests); a default one
                with a sane timeout is created when omitted.
        """
        self.base_url = base_url.rstrip("/")
        self.language = language
        self._client = client or httpx.Client(timeout=constants.HTTP_TIMEOUT_SECONDS)

    def close(self) -> None:
        """Close the underlying httpx client."""
        self._client.close()

    def _get_json(self, path: str) -> Any:
        """GET a JSON document with retry/backoff on transport or 5xx errors."""
        url = f"{self.base_url}/{path.lstrip('/')}"
        last_error: Exception | None = None
        for attempt in range(constants.SYNC_MAX_RETRIES):
            try:
                response = self._client.get(url)
                response.raise_for_status()
                return response.json()
            except (httpx.HTTPError, ValueError) as error:  # ValueError: bad JSON
                last_error = error
                logger.debug("GET %s failed (attempt %d): %s", url, attempt + 1, error)
                time.sleep(constants.SYNC_BACKOFF_SECONDS * (attempt + 1))
        raise RuntimeError(f"GET {url} failed after retries") from last_error

    def list_sets(self) -> list[dict[str, Any]]:
        """Return the brief listing of every set for the configured language."""
        data = self._get_json(f"{self.language}/sets")
        return list(data) if isinstance(data, list) else []

    def get_set(self, set_id: str) -> dict[str, Any]:
        """Return the full detail (including card briefs) for one set."""
        return dict(self._get_json(f"{self.language}/sets/{set_id}"))

    def get_card(self, card_id: str) -> dict[str, Any]:
        """Return the full detail for one card."""
        return dict(self._get_json(f"{self.language}/cards/{card_id}"))

    def get_bytes(self, url: str) -> bytes:
        """GET raw bytes (an image), with the same retry policy as JSON calls."""
        last_error: Exception | None = None
        for attempt in range(constants.SYNC_MAX_RETRIES):
            try:
                response = self._client.get(url)
                response.raise_for_status()
                return response.content
            except httpx.HTTPError as error:
                last_error = error
                logger.debug("GET %s failed (attempt %d): %s", url, attempt + 1, error)
                time.sleep(constants.SYNC_BACKOFF_SECONDS * (attempt + 1))
        raise RuntimeError(f"GET {url} failed after retries") from last_error
