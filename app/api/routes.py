"""HTTP routes for the recognizer service.

Thin handlers: decode the request, call the shared recognizer held in
``app.state``, and hand back the recognizer's own dictionary for FastAPI to
validate against the response models. All the real work lives in the pipeline.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query, Request, UploadFile

from app.api.schemas import CardDetailResponse, HealthResponse, IdentifyResponse
from app.core import constants
from app.recognize.pipeline import Recognizer
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
