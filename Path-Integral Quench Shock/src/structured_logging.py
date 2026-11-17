"""Structured logging for the shadow CLI scripts.

Every log line carries the same fields, in this order::

    timestamp (UTC, ISO-8601) | level | ticker | run_id | logger | message

``ticker`` and ``run_id`` are taken from the ``extra`` dict of the logging
call (``logger.info("...", extra={"ticker": "NVDA", "run_id": rid})``) and
default to ``-`` when absent, so library modules that do not pass them still
produce well-formed lines.
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

__all__ = ["LOG_FORMAT", "StructuredFormatter", "setup_structured_logging"]

LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(ticker)-5s | %(run_id)s | %(name)s | %(message)s"


class _DefaultFields(logging.Filter):
    """Rellena ``ticker`` y ``run_id`` con '-' si el registro no los trae."""

    def filter(self, record: logging.LogRecord) -> bool:
        for field in ("ticker", "run_id"):
            if not hasattr(record, field):
                setattr(record, field, "-")
        return True


class StructuredFormatter(logging.Formatter):
    """Formatter with UTC ISO-8601 timestamps (millisecond precision)."""

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:  # noqa: N802
        return datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(timespec="milliseconds")


def setup_structured_logging(log_file: Path | None = None, level: int = logging.INFO) -> logging.Logger:
    """Configure the root logger for a shadow script.

    Args:
        log_file: If given, lines are also appended to this file (UTF-8). Its
            parent directory is created if needed.
        level: Minimum level for console and file.

    Returns:
        logging.Logger: The configured root logger. Existing handlers are
        replaced, so calling this twice does not duplicate output.
    """
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
        h.close()
    root.setLevel(level)
    fmt = StructuredFormatter(LOG_FORMAT)
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if log_file is not None:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file, mode="a", encoding="utf-8"))
    for h in handlers:
        h.setFormatter(fmt)
        h.addFilter(_DefaultFields())
        root.addHandler(h)
    # yfinance / urllib3 son muy verbosos en INFO
    for noisy in ("yfinance", "urllib3", "peewee"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    return root
