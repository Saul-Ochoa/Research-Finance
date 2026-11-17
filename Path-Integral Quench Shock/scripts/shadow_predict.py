"""Daily shadow forecast CLI: one frozen-parameter prediction per ticker.

Run after the regular session closes (trigger 16:05 ET, Mon–Fri)::

    python scripts/shadow_predict.py                         # all tickers, default mode
    python scripts/shadow_predict.py --tickers NVDA --dry-run
    python scripts/shadow_predict.py --timestamp 2026-11-11T16:05:00-05:00 --dry-run   # replay

Behavior
--------
* Loads ``config/params_calibrated.yaml`` and verifies its SHA-256. Any
  mismatch aborts the whole run (``PARAMS_VERSION_MISMATCH``); parameters are
  **never** re-calibrated here.
* For each ticker: refuses to append to a history logged under other
  parameters, generates the prediction (``src.shadow_predictor``) and appends
  it to ``data/predictions/{TICKER}/{date}.jsonl``. A re-run on the same day is
  a no-op (deterministic ``run_id``, dedup).
* Weekends, holidays and runs before the trigger are **skipped with a
  warning**, not failures. A failing ticker never stops the others.
* ``--dry-run`` computes everything but writes no JSONL and does not save
  downloaded raw data. The log file is still written (lines tagged DRY-RUN).

Exit codes
----------
* ``0`` every ticker was written, already present (duplicate) or skipped.
* ``1`` at least one ticker failed (others were still processed).
* ``2`` fatal: frozen parameters tampered / version mismatch, or invalid
  configuration. Nothing was written.

Logs: console + ``logs/shadow_predict_YYYY-MM-DD.log`` (ET date of the run),
structured as ``timestamp | level | ticker | run_id | logger | message``.
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import yaml  # noqa: E402

from src.data_loader import NY_TZ  # noqa: E402
from src.frozen_params import PARAMS_PATH, ParamsVersionMismatch, load_frozen_params  # noqa: E402
from src.shadow_logger import append_prediction, assert_history_version, load_predictions  # noqa: E402
from src.shadow_predictor import PredictionSkipped, generar_prediccion  # noqa: E402
from src.structured_logging import setup_structured_logging  # noqa: E402

logger = logging.getLogger("shadow_predict")

CONFIG_PATH = PROJECT_ROOT / "config" / "shadow_config.yaml"
EXIT_OK, EXIT_TICKER_FAILED, EXIT_FATAL = 0, 1, 2


@dataclass
class TickerResult:
    """Outcome of one ticker in a run (for the end-of-run summary)."""

    ticker: str
    status: str                  # written | duplicate | would_write | skipped | failed
    detail: str = ""
    run_id: str = "-"
    reference_price: float | None = None
    q01: float | None = None
    q50: float | None = None
    q99: float | None = None


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments.

    Args:
        argv: Argument list (default ``sys.argv[1:]``).

    Returns:
        argparse.Namespace: Parsed arguments.
    """
    p = argparse.ArgumentParser(description="Generate frozen-parameter shadow predictions (never re-calibrates).")
    p.add_argument("--mode", default="daily_close_overnight", help="Mode in shadow_config.yaml (default: %(default)s)")
    p.add_argument("--tickers", nargs="+", default=None, help="Tickers (default: 'tickers' in shadow_config.yaml)")
    p.add_argument("--dry-run", action="store_true", help="Compute and report, but write no JSONL and save no raw data")
    p.add_argument("--timestamp", default=None,
                   help="Prediction time, ISO-8601 with offset (default: now). For replays/tests.")
    p.add_argument("--config", type=Path, default=CONFIG_PATH, help="Shadow config YAML (default: %(default)s)")
    p.add_argument("--params", type=Path, default=PARAMS_PATH, help="Frozen params YAML (default: %(default)s)")
    return p.parse_args(argv)


def load_config(path: Path) -> dict[str, Any]:
    """Load the shadow configuration YAML.

    Args:
        path: Path to ``shadow_config.yaml``.

    Returns:
        dict: Parsed configuration.
    """
    with Path(path).open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _resolve_path(config: dict[str, Any], key: str, default: str) -> Path:
    rel = (config.get("paths") or {}).get(key, default)
    p = Path(rel)
    return p if p.is_absolute() else PROJECT_ROOT / p


def _parse_timestamp(value: str | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    ts = datetime.fromisoformat(value)
    if ts.tzinfo is None:
        raise ValueError("--timestamp must include a UTC offset, e.g. 2026-11-11T16:05:00-05:00")
    return ts


def predict_ticker(ticker: str, mode: str, config: dict[str, Any], params: dict[str, Any],
                   ts_utc: datetime, predictions_root: Path, dry_run: bool) -> TickerResult:
    """Generate and (unless dry run) log one ticker's prediction.

    Args:
        ticker: Ticker symbol.
        mode: Shadow mode.
        config: Shadow configuration.
        params: Verified frozen parameters.
        ts_utc: Prediction time (tz-aware).
        predictions_root: Predictions directory.
        dry_run: If True, nothing is written.

    Returns:
        TickerResult: Outcome for the summary.

    The history-version check is done beforehand for **all** tickers in
    :func:`main`, so a mismatch aborts the run before anything is written.
    """
    tag = "[DRY-RUN] " if dry_run else ""
    try:
        pred = generar_prediccion(ticker, mode, config, params, ts_utc,
                                  allow_download=True, persist_data=not dry_run)
    except PredictionSkipped as exc:
        logger.warning("%sskipped: %s", tag, exc, extra={"ticker": ticker})
        return TickerResult(ticker, "skipped", str(exc))

    rid = pred["run_id"]
    ex = {"ticker": ticker, "run_id": rid}
    s = pred["pdf_summary"]
    res = TickerResult(ticker, "", run_id=rid, reference_price=pred["reference_price"],
                       q01=s["q01"], q50=s["q50"], q99=s["q99"])
    logger.info("%sprediction for %s: ref=%.4f (bar %s), q01=%+.4f q50=%+.4f q99=%+.4f, bad_ticks=%d, "
                "missing_bars=%d", tag, pred["prediction_date"], pred["reference_price"],
                pred["reference_bar_et"][11:16], s["q01"], s["q50"], s["q99"],
                pred["data_quality"]["bad_ticks_removed"], pred["data_quality"]["missing_bars"], extra=ex)
    if pred["notes"]:
        logger.info("%snotes: %s", tag, pred["notes"], extra=ex)

    if dry_run:
        fecha = datetime.fromisoformat(pred["prediction_date"]).date()
        exists = any(r.get("run_id") == rid for r in load_predictions(ticker, fecha, fecha, root=predictions_root))
        res.status, res.detail = ("duplicate", "already logged") if exists else ("would_write", "not written")
        logger.info("%s%s", tag, "run_id already logged" if exists else "would append (not written)", extra=ex)
        return res

    written = append_prediction(ticker, pred, root=predictions_root)
    res.status = "written" if written else "duplicate"
    res.detail = "appended" if written else "run_id already logged; no-op"
    logger.info("%s", res.detail, extra=ex)
    return res


def print_summary(results: list[TickerResult], mode: str, ts_et: datetime, params: dict[str, Any],
                  dry_run: bool) -> None:
    """Print the end-of-run summary table to stdout.

    Args:
        results: Per-ticker outcomes.
        mode: Shadow mode.
        ts_et: Prediction time in ET.
        params: Frozen parameters.
        dry_run: Whether this was a dry run.
    """
    print("\n" + "=" * 92)
    print(f"SHADOW PREDICT  mode={mode}  date={ts_et:%Y-%m-%d} ({ts_et:%a} {ts_et:%H:%M} ET)  "
          f"params=v{params['version']} sha={params['integrity']['sha256'][:12]}"
          + ("  [DRY-RUN]" if dry_run else ""))
    print("=" * 92)
    print(f"{'ticker':<7}{'status':<13}{'ref price':>11}{'q01':>9}{'q50':>9}{'q99':>9}  run_id / detail")
    for r in results:
        if r.reference_price is None:
            print(f"{r.ticker:<7}{r.status:<13}{'':>11}{'':>9}{'':>9}{'':>9}  {r.detail}")
        else:
            print(f"{r.ticker:<7}{r.status:<13}{r.reference_price:>11.4f}{r.q01:>+9.2%}{r.q50:>+9.2%}"
                  f"{r.q99:>+9.2%}  {r.run_id}")
    print("(quantiles are overnight log returns from the frozen model)")


def main(argv: list[str] | None = None) -> int:
    """Run the daily prediction batch.

    Args:
        argv: Command-line arguments.

    Returns:
        int: Exit code (0 ok, 1 some ticker failed, 2 fatal).
    """
    args = parse_args(argv)
    try:
        ts_utc = _parse_timestamp(args.timestamp)
    except ValueError as exc:
        setup_structured_logging()
        logger.critical("invalid --timestamp: %s", exc)
        return EXIT_FATAL
    ts_et = ts_utc.astimezone(NY_TZ)

    try:
        config = load_config(args.config)
    except (OSError, yaml.YAMLError) as exc:
        setup_structured_logging()
        logger.critical("cannot read config %s: %s", args.config, exc)
        return EXIT_FATAL

    log_dir = _resolve_path(config, "logs", "logs")
    setup_structured_logging(log_dir / f"shadow_predict_{ts_et:%Y-%m-%d}.log")
    logger.info("start: mode=%s timestamp_et=%s dry_run=%s", args.mode, ts_et.isoformat(), args.dry_run)

    try:
        params = load_frozen_params(args.params)
    except ParamsVersionMismatch as exc:
        logger.critical("%s", exc)
        return EXIT_FATAL
    except OSError as exc:
        logger.critical("cannot read frozen params %s: %s", args.params, exc)
        return EXIT_FATAL
    logger.info("frozen params v%s verified (sha256 %s…)", params["version"], params["integrity"]["sha256"][:12])

    mcfg = (config.get("modes") or {}).get(args.mode)
    if not mcfg or not mcfg.get("enabled", False):
        logger.critical("mode %r is not enabled in %s", args.mode, args.config)
        return EXIT_FATAL

    tickers = [t.upper() for t in (args.tickers or config.get("tickers") or [])]
    if not tickers:
        logger.critical("no tickers given and none in config")
        return EXIT_FATAL
    predictions_root = _resolve_path(config, "predictions", "data/predictions")

    # Pre-flight: ninguna historia puede estar registrada con otros parámetros.
    # Se verifica para todos los tickers ANTES de escribir nada.
    for ticker in tickers:
        try:
            assert_history_version(ticker, str(params["version"]), params["integrity"]["sha256"],
                                   root=predictions_root)
        except ParamsVersionMismatch as exc:
            logger.critical("%s", exc, extra={"ticker": ticker})
            logger.critical("aborting: nothing was written")
            return EXIT_FATAL

    results: list[TickerResult] = []
    for ticker in tickers:
        try:
            results.append(predict_ticker(ticker, args.mode, config, params, ts_utc, predictions_root,
                                          args.dry_run))
        except Exception as exc:  # un ticker que falla no detiene el lote
            logger.exception("failed: %s", exc, extra={"ticker": ticker})
            results.append(TickerResult(ticker, "failed", f"{type(exc).__name__}: {exc}"))

    print_summary(results, args.mode, ts_et, params, args.dry_run)
    n_failed = sum(r.status == "failed" for r in results)
    counts = {s: sum(r.status == s for r in results) for s in
              ("written", "would_write", "duplicate", "skipped", "failed")}
    logger.info("end: %s", ", ".join(f"{k}={v}" for k, v in counts.items() if v))
    return EXIT_TICKER_FAILED if n_failed else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
