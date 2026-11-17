"""Append-only JSONL store for shadow predictions and evaluations.

Layout (relative to the project root, configurable via ``root``)::

    data/predictions/{TICKER}/YYYY-MM-DD.jsonl   one line per prediction
    data/evaluations/{TICKER}/YYYY-MM-DD.jsonl   one line per evaluation

Rules:

* **Append-only.** Files are opened in ``"a"`` mode; lines are never rewritten
  or deleted. Each line is flushed and fsync'ed.
* **Deduplication by ``run_id``.** A record whose ``run_id`` already exists in
  the target file is not written again (the call returns ``False``).
* **Filing date.** Predictions are filed under their *prediction date* (ET
  date of the reference timestamp). Evaluations are filed under the **same
  prediction date**, so a prediction and its evaluation share a file name.
* **One parameter version per history.** :func:`assert_history_version`
  refuses to mix ``params_version``/fingerprint values within one
  predictions directory (``PARAMS_VERSION_MISMATCH``).
"""

from __future__ import annotations

import json
import logging
import math
import os
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterator

from src.frozen_params import ParamsVersionMismatch

__all__ = [
    "PROJECT_ROOT", "PREDICTIONS_DIR", "EVALUATIONS_DIR",
    "append_prediction", "append_evaluation", "load_predictions", "load_evaluations",
    "assert_history_version",
]

logger = logging.getLogger(__name__)

PROJECT_ROOT: Path = Path(__file__).resolve().parents[1]
PREDICTIONS_DIR: Path = PROJECT_ROOT / "data" / "predictions"
EVALUATIONS_DIR: Path = PROJECT_ROOT / "data" / "evaluations"


# ----------------------------------------------------------------------
# Internals
# ----------------------------------------------------------------------
def _sanitize(obj: Any) -> Any:
    """JSON estricto: NaN/inf → None (json no los admite con allow_nan=False)."""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {str(k): _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize(v) for v in obj]
    if hasattr(obj, "item"):  # escalares numpy
        return _sanitize(obj.item())
    return obj


def _file_for(base: Path, ticker: str, filing_date: date) -> Path:
    return base / ticker.upper() / f"{filing_date.isoformat()}.jsonl"


def _iter_lines(path: Path) -> Iterator[dict]:
    if not path.exists():
        return
    with path.open(encoding="utf-8") as fh:
        for n, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                logger.warning("Corrupt JSONL line skipped: %s:%d", path, n)


def _append(base: Path, ticker: str, filing_date: date, record: dict) -> bool:
    run_id = record.get("run_id")
    if not run_id:
        raise ValueError("record has no run_id")
    path = _file_for(base, ticker, filing_date)
    path.parent.mkdir(parents=True, exist_ok=True)
    if any(r.get("run_id") == run_id for r in _iter_lines(path)):
        logger.info("Duplicate run_id %s already in %s; not written", run_id, path,
                    extra={"ticker": ticker.upper(), "run_id": run_id})
        return False
    line = json.dumps(_sanitize(record), ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(line + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    return True


def _load(base: Path, ticker: str, date_from: date, date_to: date) -> list[dict]:
    out: list[dict] = []
    d = date_from
    while d <= date_to:
        out.extend(_iter_lines(_file_for(base, ticker, d)))
        d += timedelta(days=1)
    return out


# ----------------------------------------------------------------------
# Public API
# ----------------------------------------------------------------------
def append_prediction(ticker: str, prediction_dict: dict, root: Path | None = None) -> bool:
    """Append a prediction to ``predictions/{TICKER}/{prediction_date}.jsonl``.

    Args:
        ticker: Ticker symbol.
        prediction_dict: Record produced by ``shadow_predictor.generar_prediccion``;
            must contain ``run_id`` and ``prediction_date`` (ISO date).
        root: Predictions directory (default ``data/predictions``).

    Returns:
        bool: True if written, False if the ``run_id`` was already present.
    """
    return _append(root or PREDICTIONS_DIR, ticker,
                   date.fromisoformat(prediction_dict["prediction_date"]), prediction_dict)


def append_evaluation(ticker: str, evaluation_dict: dict, root: Path | None = None) -> bool:
    """Append an evaluation to ``evaluations/{TICKER}/{prediction_date}.jsonl``.

    Args:
        ticker: Ticker symbol.
        evaluation_dict: Record produced by ``shadow_evaluator.evaluar_prediccion``;
            must contain ``run_id`` and ``prediction_date``.
        root: Evaluations directory (default ``data/evaluations``).

    Returns:
        bool: True if written, False if this prediction was already evaluated.
    """
    return _append(root or EVALUATIONS_DIR, ticker,
                   date.fromisoformat(evaluation_dict["prediction_date"]), evaluation_dict)


def load_predictions(ticker: str, date_from: date, date_to: date, root: Path | None = None) -> list[dict]:
    """Load predictions filed between two prediction dates (inclusive).

    Args:
        ticker: Ticker symbol.
        date_from: First prediction date.
        date_to: Last prediction date.
        root: Predictions directory (default ``data/predictions``).

    Returns:
        list[dict]: Records in file/line order.
    """
    return _load(root or PREDICTIONS_DIR, ticker, date_from, date_to)


def load_evaluations(ticker: str, date_from: date, date_to: date, root: Path | None = None) -> list[dict]:
    """Load evaluations filed between two prediction dates (inclusive).

    Args:
        ticker: Ticker symbol.
        date_from: First prediction date.
        date_to: Last prediction date.
        root: Evaluations directory (default ``data/evaluations``).

    Returns:
        list[dict]: Records in file/line order.
    """
    return _load(root or EVALUATIONS_DIR, ticker, date_from, date_to)


def assert_history_version(ticker: str, params_version: str, params_sha256: str,
                           root: Path | None = None) -> None:
    """Refuse to append to a history logged under different frozen parameters.

    Args:
        ticker: Ticker symbol.
        params_version: Version of the parameters about to be used.
        params_sha256: Fingerprint of the parameters about to be used.
        root: Predictions directory (default ``data/predictions``).

    Raises:
        ParamsVersionMismatch: If any existing prediction for ``ticker`` used a
            different version or fingerprint. A new parameter version must be
            launched as a new shadow (separate ``paths.predictions``).
    """
    base = (root or PREDICTIONS_DIR) / ticker.upper()
    if not base.exists():
        return
    for f in sorted(base.glob("*.jsonl")):
        for r in _iter_lines(f):
            v = r.get("params_version")
            sha = (r.get("params_snapshot") or {}).get("integrity", {}).get("sha256")
            if v != params_version or sha != params_sha256:
                raise ParamsVersionMismatch(
                    f"PARAMS_VERSION_MISMATCH: re-calibration detected — {f} contains "
                    f"predictions under params {v} (sha {str(sha)[:12]}…) but current params are "
                    f"{params_version} (sha {params_sha256[:12]}…). Launch the new version as a "
                    "new shadow with its own paths (docs/shadow_production.md §8).")
