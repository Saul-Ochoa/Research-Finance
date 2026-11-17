"""Rebuild derived metrics in ``output/metrics/`` from the source of truth in ``data/``.

``output/`` is never a source of truth (D-022): if a metrics file is missing or
corrupt, regenerate it here. The output is deterministic, so running this twice
on the same ``data/`` gives byte-identical files.

Usage::

    python scripts/aggregate_metrics.py --rebuild-all
    python scripts/aggregate_metrics.py --rebuild-date 2026-11-13   # that day + its week, month, cumulative
    python scripts/aggregate_metrics.py --rebuild-from 2026-11-01   # every day from there to today

Exit codes: 0 ok; 1 aggregation failed (e.g. mixed parameter versions);
2 invalid arguments or configuration.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.data_loader import hoy_nueva_york  # noqa: E402
from src.metrics_aggregator import rebuild_all, rebuild_date, rebuild_from  # noqa: E402
from src.shadow_config import CONFIG_PATH, config_path, config_thresholds, load_config  # noqa: E402
from src.structured_logging import setup_structured_logging  # noqa: E402

logger = logging.getLogger("aggregate_metrics")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments.

    Args:
        argv: Argument list (default ``sys.argv[1:]``).

    Returns:
        argparse.Namespace: Parsed arguments.
    """
    p = argparse.ArgumentParser(description="Rebuild output/metrics/ from data/ (deterministic, idempotent).")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--rebuild-all", action="store_true", help="Delete and regenerate every metrics file")
    g.add_argument("--rebuild-date", type=date.fromisoformat, metavar="YYYY-MM-DD",
                   help="Rebuild that prediction date, its ISO week, its month and cumulative.json")
    g.add_argument("--rebuild-from", type=date.fromisoformat, metavar="YYYY-MM-DD",
                   help="Rebuild every date from this one to today")
    p.add_argument("--tickers", nargs="+", default=None, help="Tickers (default: shadow_config.yaml)")
    p.add_argument("--config", type=Path, default=CONFIG_PATH, help="Shadow config YAML (default: %(default)s)")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the rebuild.

    Args:
        argv: Command-line arguments.

    Returns:
        int: Exit code (0 ok, 1 aggregation failed, 2 bad config).
    """
    args = parse_args(argv)
    try:
        config = load_config(args.config)
    except OSError as exc:
        setup_structured_logging()
        logger.critical("cannot read config %s: %s", args.config, exc)
        return 2
    setup_structured_logging(config_path(config, "logs") / f"aggregate_metrics_{hoy_nueva_york():%Y-%m-%d}.log")
    tickers = [t.upper() for t in (args.tickers or config.get("tickers") or [])]
    if not tickers:
        logger.critical("no tickers given and none in config")
        return 2
    kw = dict(output_dir=config_path(config, "output"), predictions_root=config_path(config, "predictions"),
              evaluations_root=config_path(config, "evaluations"), thresholds=config_thresholds(config))
    try:
        if args.rebuild_all:
            what, files = "all", rebuild_all(tickers, **kw)
        elif args.rebuild_date:
            what, files = str(args.rebuild_date), rebuild_date(args.rebuild_date, tickers, **kw)
        else:
            what, files = f"from {args.rebuild_from}", rebuild_from(args.rebuild_from, tickers, **kw)
    except Exception as exc:  # p. ej. PARAMS_VERSION_MISMATCH al mezclar versiones
        logger.exception("aggregation failed: %s", exc)
        return 1
    out = kw["output_dir"]
    for f in files:
        logger.info("wrote %s", f.relative_to(out) if f.is_relative_to(out) else f)
    logger.info("rebuild %s: %d files written under %s (tickers %s)", what, len(files), out, ",".join(tickers))
    return 0


if __name__ == "__main__":
    sys.exit(main())
