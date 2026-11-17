"""Export one prediction date's JSONL records to flat CSVs in ``output/exports/``.

Convenience only (D-022): CSVs are overwritten on every run and are never
evidence. Nested fields are flattened (``pdf_summary.q01`` → ``pdf_summary_q01``);
the bulky ``pdf_samples`` and ``params_snapshot`` are left out of the
predictions CSV (``params_sha256`` is kept). Floats are written at full
precision so tail probabilities are not lost.

Usage::

    python scripts/export_csv.py                          # yesterday (ET), predictions + evaluations
    python scripts/export_csv.py --predictions --date 2026-11-16

Exit codes: 0 ok (including "no records for that date"); 2 invalid config.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd  # noqa: E402

from src.data_loader import hoy_nueva_york  # noqa: E402
from src.shadow_config import CONFIG_PATH, config_path, load_config  # noqa: E402
from src.shadow_logger import load_evaluations, load_predictions  # noqa: E402
from src.structured_logging import setup_structured_logging  # noqa: E402

logger = logging.getLogger("export_csv")
DROP_PREDICTION_FIELDS = ("pdf_samples", "params_snapshot")


def flatten(record: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    """Flatten nested dicts with ``_``-joined keys.

    Args:
        record: JSON record.
        prefix: Key prefix (used in recursion).

    Returns:
        dict: Flat mapping.
    """
    out: dict[str, Any] = {}
    for k, v in record.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(flatten(v, f"{key}_"))
        else:
            out[key] = v
    return out


def prediction_row(rec: dict[str, Any]) -> dict[str, Any]:
    """Flat CSV row for a prediction (without samples and snapshot).

    Args:
        rec: Prediction record.

    Returns:
        dict: Flat row with ``params_sha256`` added.
    """
    sha = (rec.get("params_snapshot") or {}).get("integrity", {}).get("sha256")
    row = flatten({k: v for k, v in rec.items() if k not in DROP_PREDICTION_FIELDS})
    row["params_sha256"] = sha
    return row


def export(kind: str, day: date, tickers: list[str], config: dict[str, Any]) -> Path | None:
    """Write ``output/exports/{kind}_{day}.csv`` for all tickers.

    Args:
        kind: ``"predictions"`` or ``"evaluations"``.
        day: Prediction date.
        tickers: Tickers.
        config: Parsed shadow configuration.

    Returns:
        Path | None: The CSV written, or None if there were no records.
    """
    loader, root = ((load_predictions, config_path(config, "predictions")) if kind == "predictions"
                    else (load_evaluations, config_path(config, "evaluations")))
    rows = []
    for t in tickers:
        for rec in loader(t, day, day, root=root):
            rows.append(prediction_row(rec) if kind == "predictions" else flatten(rec))
    if not rows:
        logger.warning("no %s for %s; nothing exported", kind, day)
        return None
    df = pd.DataFrame(rows)
    lead = [c for c in ("prediction_date", "ticker", "run_id") if c in df.columns]
    df = df[lead + sorted(c for c in df.columns if c not in lead)].sort_values(lead, kind="stable")
    path = config_path(config, "output") / "exports" / f"{kind}_{day.isoformat()}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, lineterminator="\n", float_format="%.17g")
    logger.info("wrote %s (%d rows, %d columns)", path, len(df), df.shape[1])
    return path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments.

    Args:
        argv: Argument list (default ``sys.argv[1:]``).

    Returns:
        argparse.Namespace: Parsed arguments.
    """
    p = argparse.ArgumentParser(description="Export shadow JSONL records to CSV (convenience, overwritten).")
    p.add_argument("--predictions", action="store_true", help="Export predictions")
    p.add_argument("--evaluations", action="store_true", help="Export evaluations")
    p.add_argument("--date", type=date.fromisoformat, default=None, metavar="YYYY-MM-DD",
                   help="Prediction date (default: yesterday, ET)")
    p.add_argument("--tickers", nargs="+", default=None, help="Tickers (default: shadow_config.yaml)")
    p.add_argument("--config", type=Path, default=CONFIG_PATH, help="Shadow config YAML (default: %(default)s)")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the export.

    Args:
        argv: Command-line arguments.

    Returns:
        int: Exit code (0 ok, 2 bad config).
    """
    args = parse_args(argv)
    setup_structured_logging()
    try:
        config = load_config(args.config)
    except OSError as exc:
        logger.critical("cannot read config %s: %s", args.config, exc)
        return 2
    day = args.date or (hoy_nueva_york() - timedelta(days=1))
    tickers = [t.upper() for t in (args.tickers or config.get("tickers") or [])]
    kinds = [k for k, on in (("predictions", args.predictions), ("evaluations", args.evaluations)) if on] \
        or ["predictions", "evaluations"]
    for kind in kinds:
        export(kind, day, tickers, config)
    return 0


if __name__ == "__main__":
    sys.exit(main())
