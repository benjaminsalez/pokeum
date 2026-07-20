"""Unit tests for OCR parsing (app/signals/ocr.py)."""

from __future__ import annotations

import sys
from types import SimpleNamespace

import numpy as np
import pytest

from app.core import constants
from app.signals import ocr


def test_rapidocr_uses_card_strip_optimized_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class FakeRapidOcr:
        def __init__(self, *, params: dict[str, object]) -> None:
            captured.update(params)

        def __call__(self, image: np.ndarray) -> SimpleNamespace:
            return SimpleNamespace(txts=(), scores=())

    monkeypatch.setitem(sys.modules, "rapidocr", SimpleNamespace(RapidOCR=FakeRapidOcr))
    engine = ocr.RapidOcrEngine()

    assert engine.read_text(np.zeros((88, 630, 3), dtype=np.uint8)) == []
    assert captured == {
        "Det.limit_type": "max",
        "Det.limit_side_len": constants.OCR_DETECT_MAX_SIDE,
        "EngineConfig.onnxruntime.intra_op_num_threads": constants.OCR_INTRA_OP_THREADS,
        "EngineConfig.onnxruntime.inter_op_num_threads": constants.OCR_INTER_OP_THREADS,
        "Global.use_cls": False,
    }


@pytest.mark.parametrize(
    ("text", "number", "total"),
    [
        ("025/193", "25", 193),
        ("25 / 193", "25", 193),
        ("O25/193", "25", 193),  # letter O corrected to zero
        ("007/091", "7", 91),
        ("nothing here", None, None),
        ("SWSH123", None, None),  # no slash -> no collector number
    ],
)
def test_parse_collector_number(text: str, number: str | None, total: int | None) -> None:
    assert ocr.parse_collector_number(text) == (number, total)


def test_parse_set_code_finds_code() -> None:
    assert ocr.parse_set_code("PAL 025/193") == "PAL"


def test_parse_set_code_skips_stopwords() -> None:
    assert ocr.parse_set_code("EN 025/193") is None


def test_interpret_lines_prefers_confident_number_line() -> None:
    observation = ocr.interpret_lines([("025/193", 0.95), ("PAL", 0.80)])
    assert observation.number == "25"
    assert observation.number_total == 193
    assert observation.set_code == "PAL"
    assert observation.confidence == pytest.approx(0.95)
    assert observation.is_useful


def test_interpret_lines_empty() -> None:
    observation = ocr.interpret_lines([])
    assert observation.number is None
    assert not observation.is_useful
