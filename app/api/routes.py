"""HTTP routes for the recognizer service.

Thin handlers: decode the request, call the shared recognizer held in
``app.state``, and hand back the recognizer's own dictionary for FastAPI to
validate against the response models. All the real work lives in the pipeline.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse

from app.api.schemas import CardDetailResponse, HealthResponse, IdentifyResponse
from app.core import config, constants
from app.recognize.pipeline import Recognizer
from app.reference import images
from app.vision.imaging import decode_bytes

logger = logging.getLogger(__name__)

router = APIRouter()


def _recognizer(request: Request) -> Recognizer:
    """Return the recognizer stored on the application state."""
    return request.app.state.recognizer


@router.get("/health", response_model=HealthResponse)
def health(request: Request) -> HealthResponse:
    """Report service readiness and how many cards are indexed."""
    recognizer = _recognizer(request)
    return HealthResponse(
        status="ok",
        cards_indexed=recognizer.store.count_cards(),
        embedder_loaded=recognizer.has_embeddings,
    )


@router.post("/identify", response_model=IdentifyResponse)
async def identify(
    request: Request,
    file: UploadFile,
    top_k: int = Query(constants.DEFAULT_TOP_K, ge=1, le=50),
) -> dict:
    """Identify the card in an uploaded image.

    Args:
        request: The incoming request (carries the recognizer).
        file: The uploaded image file.
        top_k: Maximum number of candidates to return.

    Returns:
        The recognition result as a dictionary matching :class:`IdentifyResponse`.

    Raises:
        HTTPException: 400 when the upload cannot be decoded as an image.
    """
    payload = await file.read()
    try:
        image = decode_bytes(payload)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    result = _recognizer(request).identify(image, top_k=top_k)
    return result.as_dict()


@router.get("/cards/{card_id}", response_model=CardDetailResponse)
def get_card(request: Request, card_id: str) -> dict:
    """Return catalogue detail for one card.

    Raises:
        HTTPException: 404 when the card id is unknown.
    """
    card = _recognizer(request).store.get_card(card_id)
    if card is None:
        raise HTTPException(status_code=404, detail=f"unknown card: {card_id}")
    return card.as_dict()


@router.get("/cards/{card_id}/image")
def get_card_image(request: Request, card_id: str) -> FileResponse:
    """Serve a small cached thumbnail of one card (used by the scan UI).

    The hi-res reference PNG is ~1 MB; the UI needs ~320px. A JPEG thumbnail is
    derived lazily on first request, cached on disk, and served with immutable
    cache headers so repeat views never refetch.

    Raises:
        HTTPException: 404 when the card is unknown or its image is not cached.
    """
    card = _recognizer(request).store.get_card(card_id)
    if card is None or not card.image_path or not Path(card.image_path).is_file():
        raise HTTPException(status_code=404, detail=f"no image for card: {card_id}")
    thumb = images.ensure_thumbnail(
        Path(card.image_path),
        images.thumbnail_path(config.data_dir(), card_id),
        constants.CARD_THUMB_WIDTH,
    )
    return FileResponse(
        thumb,
        media_type="image/jpeg",
        headers={"Cache-Control": "public, max-age=86400, immutable"},
    )
