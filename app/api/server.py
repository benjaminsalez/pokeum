"""FastAPI application factory.

The recognizer is expensive to build (it loads indexes and models), so it is
constructed once during the app's lifespan and stashed on ``app.state`` for every
request to share. Tests inject a ready-made recognizer to skip that cost.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.recognize.factory import build_recognizer
from app.recognize.pipeline import Recognizer

logger = logging.getLogger(__name__)


def create_app(recognizer: Recognizer | None = None) -> FastAPI:
    """Create the FastAPI application.

    Args:
        recognizer: A pre-built recognizer to serve (used in tests). When
            omitted, one is built from configured reference data on startup.

    Returns:
        The configured application.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.recognizer = recognizer or build_recognizer()
        logger.info("recognizer loaded into app state")
        yield

    app = FastAPI(title="pokeum card recognizer", version="1.0.0", lifespan=lifespan)
    # Open CORS: the service is a local, account-less recognizer, and the
    # frontend may be served from another origin (dev server, tunnel). Nothing
    # here is credentialed, so a wildcard is safe.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)
    return app
