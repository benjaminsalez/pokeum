"""End-to-end recognition: image in, :class:`RecognitionResult` out.

The :class:`Recognizer` wires the stages together — detect and rectify the card,
run whichever signals are available, fuse them, then assess variants on the
winner. Every heavy collaborator (indexes, embedder, OCR engine, symbol matcher)
is injected, so tests can drive the pipeline with fakes and the API/CLI can build
it once and reuse it.
"""

from __future__ import annotations

import dataclasses
import logging
import time
from concurrent.futures import Executor

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
from app.signals.embedding import EmbeddingIndex, embed_images
from app.signals.hashes import HashIndex
from app.signals.ocr import read_card
from app.signals.symbol import SymbolMatcher
from app.variants.assess import assess_variants
from app.vision import regions
from app.vision.detect import detect_card_quad
from app.vision.rectify import rectify_or_whole

logger = logging.getLogger(__name__)

# How many candidates each DEBUG diagnostic line shows per signal and after
# fusion — enough to see who beat whom without flooding the log.
_DEBUG_TOP_N = 5


def _describe(card: CardRef | None, card_id: str) -> str:
    """Return a compact human-readable label for a candidate card."""
    if card is None:
        return card_id
    return f"{card.name} [{card.card_id} {card.number}/{card.number_total or '?'}]"


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
        executor: Executor | None = None,
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
            executor: When given, OCR runs on it concurrently with the dense
                signals (they release the GIL); ``None`` keeps everything serial.
        """
        self._store = store
        self._hash_index = hash_index
        self._embedder = embedder
        self._emb_full = emb_full_index
        self._emb_art = emb_art_index
        self._ocr = ocr_engine
        self._symbols = symbol_matcher
        self._executor = executor
        # Lazy cache of the catalogue's printed set codes, for OCR validation.
        self._known_set_codes: frozenset[str] | None = None

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
        started = time.perf_counter()
        quad = detect_card_quad(image_rgb)
        if quad is None and require_detection:
            logger.debug(
                "identify: input %dx%d, no card quad -> reporting no_card_detected",
                image_rgb.shape[1],
                image_rgb.shape[0],
            )
            return RecognitionResult(status=RecognitionStatus.NO_CARD_DETECTED)
        card = rectify_or_whole(image_rgb, quad)
        logger.debug(
            "identify: input %dx%d, card quad %s",
            image_rgb.shape[1],
            image_rgb.shape[0],
            "detected" if quad is not None else "NOT found (whole-frame fallback)",
        )
        t_detect = time.perf_counter()

        # OCR and the dense signals are independent until fusion; with an
        # executor they overlap (ONNX Runtime and NumPy release the GIL).
        ocr_future = (
            self._executor.submit(read_card, self._ocr, card)
            if self._executor is not None and self._ocr is not None
            else None
        )
        dense = self._dense_signals(card)
        t_dense = time.perf_counter()
        resolved = self._resolve_candidates(dense)
        t_resolve = time.perf_counter()
        observation: OcrObservation | None
        if ocr_future is not None:
            observation = ocr_future.result()
        else:
            observation = read_card(self._ocr, card) if self._ocr is not None else None
        observation = self._validate_set_code(observation)
        t_ocr = time.perf_counter()
        self._add_symbol_signal(dense, card, resolved, observation)

        consistent = self._ocr_consistent_ids(observation)
        if logger.isEnabledFor(logging.DEBUG):
            self._log_signal_diagnostics(dense, resolved, observation, consistent)
        status, candidates = fusion.fuse(
            dense,
            resolved.get,
            ocr=observation,
            ocr_consistent_ids=consistent,
            top_k=top_k,
        )
        result = self._assemble(status, candidates, card, observation)
        if logger.isEnabledFor(logging.DEBUG):
            self._log_fusion_diagnostics(result)
        done = time.perf_counter()
        logger.debug(
            "identify timings ms: total=%.0f detect=%.0f dense=%.0f resolve=%.0f "
            "ocr_wait=%.0f fuse=%.0f",
            (done - started) * 1000,
            (t_detect - started) * 1000,
            (t_dense - t_detect) * 1000,
            (t_resolve - t_dense) * 1000,
            (t_ocr - t_resolve) * 1000,
            (done - t_ocr) * 1000,
        )
        return result

    def _dense_signals(self, card: np.ndarray) -> dict[str, list[SignalScore]]:
        """Run the hash and embedding signals over a rectified card."""
        dense: dict[str, list[SignalScore]] = {fusion.SIGNAL_HASH: self._hash_index.query(card)}
        if self._embedder is not None and self._emb_full is not None and self._emb_art is not None:
            full_vec, art_vec = embed_images(self._embedder, [card, regions.artwork(card)])
            dense[fusion.SIGNAL_EMB_FULL] = self._emb_full.query(full_vec)
            dense[fusion.SIGNAL_EMB_ART] = self._emb_art.query(art_vec)
        return dense

    def _resolve_candidates(self, dense: dict[str, list[SignalScore]]) -> dict[str, CardRef]:
        """Resolve every card id surfaced by the dense signals to a CardRef."""
        ids = {score.card_id for scores in dense.values() for score in scores}
        return self._store.get_cards(ids)

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
            logger.debug("symbol: skipped (OCR read set code %s)", observation.set_code)
            return
        set_scores = self._symbols.match(card)
        if logger.isEnabledFor(logging.DEBUG):
            best_sets = sorted(set_scores.items(), key=lambda item: item[1], reverse=True)
            logger.debug(
                "symbol: matched against %d set templates, top: %s",
                len(set_scores),
                ", ".join(f"{sid}={score:.3f}" for sid, score in best_sets[:_DEBUG_TOP_N]),
            )
        symbol_scores = [
            SignalScore(card_id, set_scores.get(ref.set_id, 0.0))
            for card_id, ref in resolved.items()
        ]
        if any(score.score > 0 for score in symbol_scores):
            dense[fusion.SIGNAL_SYMBOL] = symbol_scores

    def _log_signal_diagnostics(
        self,
        dense: dict[str, list[SignalScore]],
        resolved: dict[str, CardRef],
        observation: OcrObservation | None,
        consistent: frozenset[str],
    ) -> None:
        """Log each signal's top candidates and the OCR read (DEBUG only).

        The point is diagnosing wrong matches: when the pick is bad, this shows
        which signal pulled it in and whether OCR agreed or was blind.
        """
        for signal, scores in dense.items():
            top = ", ".join(
                f"{_describe(resolved.get(s.card_id), s.card_id)}={s.score:.3f}"
                for s in scores[:_DEBUG_TOP_N]
            )
            logger.debug("signal %s top%d: %s", signal, _DEBUG_TOP_N, top or "(empty)")
        if observation is None:
            logger.debug("ocr: not run")
        else:
            logger.debug(
                "ocr: raw=%r number=%s/%s set_code=%s conf=%.2f -> %d catalogue cards consistent",
                observation.raw_text,
                observation.number,
                observation.number_total,
                observation.set_code,
                observation.confidence,
                len(consistent),
            )

    def _log_fusion_diagnostics(self, result: RecognitionResult) -> None:
        """Log the fused verdict and ranked top candidates (DEBUG only)."""
        ranked = ([result.match] if result.match is not None else []) + list(result.alternates)
        lines = [
            f"  {rank}. {_describe(candidate.card, candidate.card.card_id)} "
            f"conf={candidate.confidence:.3f} signals={{"
            + ", ".join(f"{k}={v:.3f}" for k, v in sorted(candidate.per_signal.items()))
            + "}"
            for rank, candidate in enumerate(ranked[:_DEBUG_TOP_N], start=1)
        ]
        logger.debug(
            "fused: status=%s variants=[%s]\n%s",
            result.status.value,
            ", ".join(f"{v.kind.value}:{v.confidence:.2f}" for v in result.variants),
            "\n".join(lines) or "  (no candidates)",
        )

    def _validate_set_code(self, observation: OcrObservation | None) -> OcrObservation | None:
        """Drop an OCR set code that does not exist in the catalogue.

        Random uppercase scene text (a webpage header, a brand name) can pass
        the set-code regex. Trusting it is doubly harmful: the symbol signal is
        skipped ("OCR already knows the set") and fusion penalizes every
        candidate through ``is_useful``. Only codes the catalogue actually
        prints survive.
        """
        if observation is None or not observation.set_code:
            return observation
        if self._known_set_codes is None:
            self._known_set_codes = self._store.known_set_codes()
        if observation.set_code in self._known_set_codes:
            return observation
        logger.debug("ocr set code %s not in catalogue; ignoring", observation.set_code)
        return dataclasses.replace(observation, set_code=None)

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
