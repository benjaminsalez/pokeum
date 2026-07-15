"""Live webcam recognition loop.

Reads frames from an OpenCV capture device, runs each sampled frame through the
recognizer, and feeds results to a :class:`TemporalAggregator` so only stable
identifications are emitted. This is runtime glue (it needs a real camera), so it
stays thin: the decision logic lives in the pure aggregator.

The headless OpenCV build has no preview window; emissions are written to stdout
as one JSON object per identified card.
"""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import Callable

import cv2

from app.core import constants
from app.models import RecognitionResult
from app.recognize.pipeline import Recognizer
from app.recognize.temporal import TemporalAggregator

logger = logging.getLogger(__name__)

EmitFn = Callable[[RecognitionResult], None]


def _emit_json(result: RecognitionResult) -> None:
    """Write one recognition result to stdout as a JSON line."""
    sys.stdout.write(json.dumps(result.as_dict()) + "\n")
    sys.stdout.flush()


def _frame_step(capture: cv2.VideoCapture, target_fps: int) -> int:
    """Return how many frames to skip between processed frames."""
    fps = capture.get(cv2.CAP_PROP_FPS)
    if not fps or fps <= 0:
        return 1
    return max(1, round(fps / target_fps))


def run_webcam(
    recognizer: Recognizer,
    camera_index: int,
    *,
    target_fps: int = constants.SCAN_TARGET_FPS,
    on_emit: EmitFn | None = None,
) -> None:
    """Run the live recognition loop until interrupted.

    Args:
        recognizer: The recognizer to run per frame.
        camera_index: OpenCV capture device index.
        target_fps: Approximate frames-per-second to process.
        on_emit: Callback for each stable identification; defaults to JSON stdout.

    Raises:
        RuntimeError: When the camera cannot be opened.
    """
    emit = on_emit or _emit_json
    aggregator = TemporalAggregator()
    capture = cv2.VideoCapture(camera_index)
    if not capture.isOpened():
        raise RuntimeError(f"cannot open camera {camera_index}")
    step = _frame_step(capture, target_fps)
    logger.info("scanning camera %d (processing every %d frame[s])", camera_index, step)

    frame_index = 0
    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            frame_index += 1
            if frame_index % step != 0:
                continue
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            result = recognizer.identify(rgb, require_detection=True)
            emission = aggregator.add(result)
            if emission is not None:
                emit(emission)
    except KeyboardInterrupt:
        logger.info("scan stopped")
    finally:
        capture.release()
