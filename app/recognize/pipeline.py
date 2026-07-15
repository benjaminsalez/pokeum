"""End-to-end recognition: image in, :class:`RecognitionResult` out.

The :class:`Recognizer` wires the stages together — detect and rectify the card,
run whichever signals are available, fuse them, then assess variants on the
winner. Every heavy collaborator (indexes, embedder, OCR engine, symbol matcher)
is injected, so tests can drive the pipeline with fakes and the API/CLI can build
it once and reuse it.
"""

from __future__ import annotations

import logging

import numpy as np

from app.core import constants
from app.models import (
    Candidate,
    CardRef,
    OcrObservation,
    RecognitionResult,
    RecognitionStatus,
    SignalScore,
)
from app.recognize import fusion
from app.reference.store import ReferenceStore
from app.signals.base import Embedder, OcrEngine
from app.signals.embedding import EmbeddingIndex
from app.signals.hashes import HashIndex
from app.signals.ocr import read_card
from app.signals.symbol import SymbolMatcher
from app.variants.assess import assess_variants
from app.vision import regions
from app.vision.detect import detect_card_quad
from app.vision.rectify import rectify_or_whole

logger = logging.getLogger(__name__)


class Recognizer:
    """Recognizes cards by fusing hash, embedding, OCR, and symbol signals."""

    def __init__(
        self,
        store: ReferenceStore,
        *,
        hash_index: HashIndex,
        embedder: Embedder | None = None,
        emb_full_index: EmbeddingIndex | None = None,
        emb_art_index: EmbeddingIndex | None = None,
        ocr_engine: OcrEngine | None = None,
        symbol_matcher: SymbolMatcher | None = None,
    ) -> None:
        """Assemble a recognizer from a catalogue and its signal collaborators.

        Args:
            store: Reference catalogue for card resolution and OCR validation.
            hash_index: Perceptual-hash index (may be empty).
            embedder: Encoder used to embed queries; required for embedding signals.
            emb_full_index: Full-card embedding index, or ``None`` if not built.
            emb_art_index: Artwork-crop embedding index, or ``None`` if not built.
            ocr_engine: OCR engine, or ``None`` to skip the OCR signal.
            symbol_matcher: Set-symbol matcher, or ``None`` to skip it.
        """
        self._store = store
        self._hash_index = hash_index
        self._embedder = embedder
        self._emb_full = emb_full_index
        self._emb_art = emb_art_index
        self._ocr = ocr_engine
        self._symbols = symbol_matcher

    @property
    def store(self) -> ReferenceStore:
        """Return the reference catalogue this recognizer reads from."""
        return self._store

    @property
    def has_embeddings(self) -> bool:
        """Return whether embedding indexes are loaded and usable."""
        return (
            self._embedder is not None and self._emb_full is not None and self._emb_art is not None
        )

    def identify(
        self,
        image_rgb: np.ndarray,
        *,
        top_k: int = constants.DEFAULT_TOP_K,
        require_detection: bool = False,
    ) -> RecognitionResult:
        """Recognize the card in an RGB image.

        Args:
            image_rgb: The image to identify.
            top_k: Maximum candidates to return.
            require_detection: When ``True``, return ``NO_CARD_DETECTED`` if no
                card quad is found instead of assuming the whole frame is a card.

        Returns:
            The recognition result, including the pick, alternates, and variants.
        """
        quad = detect_card_quad(image_rgb)
        if quad is None and require_detection:
            return RecognitionResult(status=RecognitionStatus.NO_CARD_DETECTED)
        card = rectify_or_whole(image_rgb, quad)

        dense = self._dense_signals(card)
        resolved = self._resolve_candidates(dense)
        observation = read_card(self._ocr, card) if self._ocr is not None else None
        self._add_symbol_signal(dense, card, resolved, observation)

        consistent = self._ocr_consistent_ids(observation)
        status, candidates = fusion.fuse(
            dense,
            resolved.get,
            ocr=observation,
            ocr_consistent_ids=consistent,
            top_k=top_k,
        )
        return self._assemble(status, candidates, card, observation)

    def _dense_signals(self, card: np.ndarray) -> dict[str, list[SignalScore]]:
        """Run the hash and embedding signals over a rectified card."""
        dense: dict[str, list[SignalScore]] = {fusion.SIGNAL_HASH: self._hash_index.query(card)}
        if self._embedder is not None and self._emb_full is not None and self._emb_art is not None:
            dense[fusion.SIGNAL_EMB_FULL] = self._emb_full.query(self._embedder.embed(card))
            art_vec = self._embedder.embed(regions.artwork(card))
            dense[fusion.SIGNAL_EMB_ART] = self._emb_art.query(art_vec)
        return dense

    def _resolve_candidates(self, dense: dict[str, list[SignalScore]]) -> dict[str, CardRef]:
        """Resolve every card id surfaced by the dense signals to a CardRef."""
        ids = {score.card_id for scores in dense.values() for score in scores}
        resolved: dict[str, CardRef] = {}
        for card_id in ids:
            card = self._store.get_card(card_id)
            if card is not None:
                resolved[card_id] = card
        return resolved

    def _add_symbol_signal(
        self,
        dense: dict[str, list[SignalScore]],
        card: np.ndarray,
        resolved: dict[str, CardRef],
        observation: OcrObservation | None,
    ) -> None:
        """Add per-set symbol scores, but only for the WOTC (no set code) case."""
        if self._symbols is None or len(self._symbols) == 0:
            return
        if observation is not None and observation.set_code:
            return
        set_scores = self._symbols.match(card)
        symbol_scores = [
            SignalScore(card_id, set_scores.get(ref.set_id, 0.0))
            for card_id, ref in resolved.items()
        ]
        if any(score.score > 0 for score in symbol_scores):
            dense[fusion.SIGNAL_SYMBOL] = symbol_scores

    def _ocr_consistent_ids(self, observation: OcrObservation | None) -> frozenset[str]:
        """Return card ids whose collector number matches the OCR observation."""
        if observation is None or not observation.number:
            return frozenset()
        return frozenset(
            self._store.find_card_ids_by_number(observation.number, observation.number_total)
        )

    def _assemble(
        self,
        status: RecognitionStatus,
        candidates: list[Candidate],
        card: np.ndarray,
        observation: OcrObservation | None,
    ) -> RecognitionResult:
        """Package the fused candidates and variant guesses into a result."""
        ocr = observation if observation is not None and observation.is_useful else None
        if status in (RecognitionStatus.CONFIDENT, RecognitionStatus.UNCERTAIN) and candidates:
            winner = candidates[0]
            variants = assess_variants(card, winner.card)
            return RecognitionResult(
                status=status,
                match=winner,
                alternates=tuple(candidates[1:]),
                variants=variants,
                ocr=ocr,
            )
        return RecognitionResult(
            status=RecognitionStatus.NO_MATCH,
            alternates=tuple(candidates),
            ocr=ocr,
        )
