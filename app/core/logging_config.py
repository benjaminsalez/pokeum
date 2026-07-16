"""One place that configures logging for the whole app.

Standardised so there is no print-statement hell: every module logs through the
stdlib ``logging`` (``logger = logging.getLogger(__name__)``) and this module owns
*how* those records are leveled, formatted, and routed.

Two levels do the work:

* ``INFO`` — milestones (what each feature is doing). The clean format.
* ``DEBUG`` — *everything our code does*, with ``module:function:line`` on each line
  so you can trace exactly where a record came from.

Third-party chatter (``httpx``, ``urllib3`` …) is pinned to ``WARNING`` even in
debug, so "debug" means *our* code, not library internals. Set ``LOG_JSON=true``
for one-line JSON records (handy for log shippers).
"""

from __future__ import annotations

import json
import logging
import sys

from app.core import config

# Libraries whose internal logging we never want, even at DEBUG. Add the noisy
# dependencies your project pulls in here.
_THIRD_PARTY_NOISE = (
    "httpx",
    "httpcore",
    "urllib3",
    "asyncio",
    # Multipart parsing logs one line per streamed chunk — hundreds per upload.
    "python_multipart",
    # PIL logs every image-plugin import at DEBUG on first use.
    "PIL",
)

_INFO_FORMAT = "%(asctime)s %(levelname)-7s %(name)s | %(message)s"
_DEBUG_FORMAT = (
    "%(asctime)s.%(msecs)03d %(levelname)-7s %(name)s %(funcName)s:%(lineno)d | %(message)s"
)
_DATE_FORMAT = "%H:%M:%S"


class _JsonFormatter(logging.Formatter):
    """Render each record as a single JSON object (for log shippers)."""

    def format(self, record: logging.LogRecord) -> str:
        """Serialise one log record as a compact JSON object."""
        payload = {
            "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "func": f"{record.module}:{record.funcName}:{record.lineno}",
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def _level_value(level: str | None) -> int:
    """Resolve a level name (or ``config.log_level()``) to a logging constant."""
    name = (level or config.log_level()).upper()
    return getattr(logging, name, logging.INFO)


def _formatter(level: int) -> logging.Formatter:
    """Pick the formatter: JSON if requested, else level-appropriate text."""
    if config.log_json():
        return _JsonFormatter()
    fmt = _DEBUG_FORMAT if level <= logging.DEBUG else _INFO_FORMAT
    return logging.Formatter(fmt, datefmt=_DATE_FORMAT)


def configure(level: str | None = None) -> None:
    """Configure logging once, idempotently.

    Installs a single stderr handler with our format, replacing any handlers a
    previous call (or ``logging.basicConfig``) installed, so log lines are never
    duplicated. Call this once at start-up before anything logs.

    Args:
        level: Level name to use; falls back to the ``LOG_LEVEL`` setting.
    """
    resolved = _level_value(level)
    root = logging.getLogger()
    root.setLevel(resolved)
    logging.getLogger("app").setLevel(resolved)

    for existing in list(root.handlers):
        root.removeHandler(existing)
    handler = logging.StreamHandler(sys.stderr)
    handler.setLevel(resolved)
    handler.setFormatter(_formatter(resolved))
    root.addHandler(handler)

    for noisy in _THIRD_PARTY_NOISE:
        logging.getLogger(noisy).setLevel(logging.WARNING)
