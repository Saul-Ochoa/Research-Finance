"""Deterministic aggregation of shadow records into derived metrics (``output/metrics/``).

``data/`` (predictions, evaluations) is the **source of truth**; everything this
module writes is **derived and regenerable** (D-022). The same ``data/`` always
produces byte-identical JSON: keys are sorted, floats are rounded to 6
decimals, lists are ordered by (prediction date, run_id), and there is **no**
wall-clock timestamp in the output. Provenance is given instead by
``inputs.sha256``, a hash of the exact records that were aggregated.

Periods are keyed by **prediction date** (evaluations are filed under the
prediction date, D-019): daily ``YYYY-MM-DD``, ISO week ``YYYY-Www``, month
``YYYY-MM`` and cumulative (all data).

Every per-ticker block has the same shape at every period level (D-023)::

    n_predictions, n_evaluations, n_pending,
    coverage_50, coverage_90, coverage_99, mean_pit, ks_pit_vs_uniform,
    mean_ratio_model_gbm, mean_ratio_model_gbm_tail, mean_log_score,
    n_miss_99, max_consecutive_misses_99,
    by_horizon: {standard_1050: {...}, weekend_extended: {...}},
    alerts, notes
    (+ series: dates, realized_returns, pit_values — daily/weekly/monthly)
    (+ alerts_history — cumulative)

A statistic that cannot be computed (no evaluations) is ``null``, never 0.

Alerts (D-023) use only the **standard_1050** bucket, the primary sample
(weekend horizons are known to be too narrow, D-020):

* ``miss_99``: a realized return outside [q005, q995] (daily/weekly/monthly).
* ``consecutive_misses_99_ge_{k}``: k consecutive standard-horizon misses.
  Daily: the streak *ending that day* over the whole history; other periods:
  the longest streak inside the period.
* ``coverage_90_below_{x}`` and ``pit_ks_above_{x}``: rate alerts, only when
  the bucket has at least ``min_evaluations_for_rate_alerts`` evaluations.

Run ``python -m src.metrics_aggregator`` for the inline tests (synthetic data).
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterable

from src.frozen_params import ParamsVersionMismatch
from src.shadow_logger import EVALUATIONS_DIR, PREDICTIONS_DIR, PROJECT_ROOT

__all__ = [
    "SCHEMA_VERSION", "OUTPUT_DIR", "DEFAULT_THRESHOLDS", "STANDARD", "EXTENDED",
    "aggregate_daily", "aggregate_weekly", "aggregate_monthly", "aggregate_cumulative",
    "data_dates", "dumps_canonical", "rebuild_date", "rebuild_from", "rebuild_all",
]

SCHEMA_VERSION = 1
OUTPUT_DIR: Path = PROJECT_ROOT / "output"
DECIMALS = 6
STANDARD, EXTENDED = "standard_1050", "weekend_extended"
DEFAULT_THRESHOLDS: dict[str, float] = {
    "alert_pit_deviation": 0.15,
    "alert_coverage_90_below": 0.85,
    "alert_consecutive_misses_99": 3,
    "min_evaluations_for_rate_alerts": 20,
}
_GBM_NOTE = "{v} ≈ GBM at T=1050 (docs/model_behavior.md F-004/F-005); ratio_model_gbm ≈ 1.00 is expected"
_WEEKEND_NOTE = "includes weekend/holiday-horizon evaluations, reported under by_horizon.weekend_extended (D-020)"


# ----------------------------------------------------------------------
# Lectura con caché (clave: ruta + mtime + tamaño ⇒ nunca devuelve datos viejos)
# ----------------------------------------------------------------------
_CACHE: dict[tuple[str, int, int], list[dict]] = {}


def _read_jsonl(path: Path) -> list[dict]:
    try:
        st = path.stat()
    except FileNotFoundError:
        return []
    key = (str(path), st.st_mtime_ns, st.st_size)
    if key not in _CACHE:
        out = []
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue  # el logger ya advierte de líneas corruptas
        _CACHE[key] = out
    return _CACHE[key]


def _load(root: Path, ticker: str, d0: date, d1: date) -> list[dict]:
    out: list[dict] = []
    d = d0
    while d <= d1:
        out.extend(_read_jsonl(root / ticker / f"{d.isoformat()}.jsonl"))
        d += timedelta(days=1)
    return out


def data_dates(tickers: Iterable[str], predictions_root: Path | None = None,
               evaluations_root: Path | None = None) -> list[date]:
    """List prediction dates that have a predictions or evaluations file.

    Args:
        tickers: Tickers to scan.
        predictions_root: Predictions directory (default ``data/predictions``).
        evaluations_root: Evaluations directory (default ``data/evaluations``).

    Returns:
        list[date]: Sorted unique dates.
    """
    found: set[date] = set()
    for root in (predictions_root or PREDICTIONS_DIR, evaluations_root or EVALUATIONS_DIR):
        for t in tickers:
            for f in (Path(root) / t.upper()).glob("*.jsonl"):
                try:
                    found.add(date.fromisoformat(f.stem))
                except ValueError:
                    continue
    return sorted(found)


# ----------------------------------------------------------------------
# Estadísticos
# ----------------------------------------------------------------------
def _round(obj: Any) -> Any:
    if isinstance(obj, bool) or obj is None:
        return obj
    if isinstance(obj, float):
        if not math.isfinite(obj):
            return None
        r = round(obj, DECIMALS)
        return 0.0 if r == 0 else r          # evita "-0.0"
    if isinstance(obj, dict):
        return {str(k): _round(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_round(v) for v in obj]
    return obj


def dumps_canonical(obj: Any) -> str:
    """Serialize to the canonical metrics JSON (sorted keys, 6-decimal floats, LF, trailing newline).

    Args:
        obj: JSON-compatible object.

    Returns:
        str: Canonical JSON text.
    """
    return json.dumps(_round(obj), sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n"


def _ks_uniform(values: list[float]) -> float | None:
    """Estadístico KS de una muestra contra Uniform(0,1)."""
    if not values:
        return None
    u = sorted(values)
    n = len(u)
    return max(max((i + 1) / n - x, x - i / n) for i, x in enumerate(u))


def _mean(evals: list[dict], key: str) -> float | None:
    vals = [float(e[key]) for e in evals if e.get(key) is not None]
    return sum(vals) / len(vals) if vals else None


def _max_run(flags: list[bool]) -> int:
    best = cur = 0
    for f in flags:
        cur = cur + 1 if f else 0
        best = max(best, cur)
    return best


def _stats(evals: list[dict]) -> dict[str, Any]:
    misses = [e.get("in_99_interval") is False for e in evals]
    return {
        "n_evaluations": len(evals),
        "coverage_50": _mean(evals, "in_50_interval"),
        "coverage_90": _mean(evals, "in_90_interval"),
        "coverage_99": _mean(evals, "in_99_interval"),
        "mean_pit": _mean(evals, "pit_value"),
        "ks_pit_vs_uniform": _ks_uniform([float(e["pit_value"]) for e in evals if e.get("pit_value") is not None]),
        "mean_ratio_model_gbm": _mean(evals, "ratio_model_gbm"),
        "mean_ratio_model_gbm_tail": _mean(evals, "ratio_model_gbm_tail"),
        "mean_log_score": _mean(evals, "log_score"),
        "n_miss_99": sum(misses) if evals else None,
        "max_consecutive_misses_99": _max_run(misses) if evals else None,
    }


def _bucket(ev: dict, pred: dict | None) -> str:
    t = (pred or {}).get("horizon_minutes")
    h = ev.get("horizon_minutes_actual")
    return STANDARD if (t is not None and h is not None and h <= t) else EXTENDED


def _sort_key(r: dict) -> tuple[str, str]:
    return (str(r.get("prediction_date")), str(r.get("run_id")))


def _rate_alerts(std: dict[str, Any], th: dict[str, float]) -> list[str]:
    out: list[str] = []
    if std["n_evaluations"] >= th["min_evaluations_for_rate_alerts"]:
        if std["coverage_90"] is not None and std["coverage_90"] < th["alert_coverage_90_below"]:
            out.append(f"coverage_90_below_{th['alert_coverage_90_below']}")
        if std["ks_pit_vs_uniform"] is not None and std["ks_pit_vs_uniform"] > th["alert_pit_deviation"]:
            out.append(f"pit_ks_above_{th['alert_pit_deviation']}")
    return out


def _notes(preds: list[dict], has_extended: bool) -> str:
    parts: list[str] = []
    versions = sorted({str(p.get("params_version")) for p in preds if "≈ GBM" in str(p.get("notes", ""))})
    if versions:
        parts.append(_GBM_NOTE.format(v="/".join(f"v{v}" for v in versions)))
    if has_extended:
        parts.append(_WEEKEND_NOTE)
    return "; ".join(parts)


# ----------------------------------------------------------------------
# Bloque por ticker y documento de período
# ----------------------------------------------------------------------
class _Ctx:
    """Raíces y umbrales de una agregación."""

    def __init__(self, predictions_root: Path | None, evaluations_root: Path | None,
                 thresholds: dict[str, float] | None) -> None:
        self.proot = Path(predictions_root or PREDICTIONS_DIR)
        self.eroot = Path(evaluations_root or EVALUATIONS_DIR)
        self.th = {**DEFAULT_THRESHOLDS, **(thresholds or {})}

    def records(self, ticker: str, d0: date, d1: date) -> tuple[list[dict], list[dict]]:
        preds = sorted(_load(self.proot, ticker, d0, d1), key=_sort_key)
        evals = sorted(_load(self.eroot, ticker, d0, d1), key=_sort_key)
        return preds, evals

    def std_streak_until(self, ticker: str, d: date) -> int:
        """Racha de misses estándar consecutivos que termina en la última evaluación ≤ d."""
        first = data_dates([ticker], self.proot, self.eroot)
        if not first:
            return 0
        preds, evals = self.records(ticker, first[0], d)
        by_id = {p["run_id"]: p for p in preds}
        streak = 0
        for e in evals:
            if _bucket(e, by_id.get(e["run_id"])) != STANDARD:
                continue
            streak = streak + 1 if e.get("in_99_interval") is False else 0
        return streak


def _ticker_block(ctx: _Ctx, ticker: str, d0: date, d1: date, *, series: bool,
                  point_alerts: bool) -> tuple[dict[str, Any], list[dict]]:
    preds, evals = ctx.records(ticker, d0, d1)
    by_id = {p["run_id"]: p for p in preds}
    evaluated = {e["run_id"] for e in evals}
    buckets = {STANDARD: [], EXTENDED: []}
    for e in evals:
        buckets[_bucket(e, by_id.get(e["run_id"]))].append(e)
    std = _stats(buckets[STANDARD])

    alerts: list[str] = []
    k = int(ctx.th["alert_consecutive_misses_99"])
    if point_alerts and std["n_miss_99"]:
        alerts.append("miss_99")
    if not point_alerts and std["max_consecutive_misses_99"] and std["max_consecutive_misses_99"] >= k:
        alerts.append(f"consecutive_misses_99_ge_{k}")
    if point_alerts and d0 != d1 and std["max_consecutive_misses_99"] and std["max_consecutive_misses_99"] >= k:
        alerts.append(f"consecutive_misses_99_ge_{k}")          # semana / mes
    alerts += _rate_alerts(std, ctx.th)

    block: dict[str, Any] = {
        "n_predictions": len(preds),
        "n_pending": sum(rid not in evaluated for rid in by_id),
        **_stats(evals),
        "by_horizon": {name: _stats(evs) for name, evs in buckets.items()},
        "alerts": alerts,
        "notes": _notes(preds, bool(buckets[EXTENDED])),
    }
    if series:
        block["series"] = {
            "dates": [e["prediction_date"] for e in evals],
            "realized_returns": [e.get("realized_return") for e in evals],
            "pit_values": [e.get("pit_value") for e in evals],
            "horizon_bucket": [_bucket(e, by_id.get(e["run_id"])) for e in evals],
        }
    return block, preds + evals


def _period_doc(ctx: _Ctx, tickers: Iterable[str], d0: date, d1: date, header: dict[str, Any], *,
                series: bool, point_alerts: bool) -> dict[str, Any] | None:
    blocks: dict[str, Any] = {}
    records: list[dict] = []
    for t in sorted({x.upper() for x in tickers}):
        b, recs = _ticker_block(ctx, t, d0, d1, series=series, point_alerts=point_alerts)
        blocks[t] = b
        records += recs
    if not records:
        return None
    versions = sorted({str(r.get("params_version")) for r in records if r.get("params_version") is not None})
    shas = sorted({(r.get("params_snapshot") or {}).get("integrity", {}).get("sha256") for r in records
                   if r.get("params_snapshot")})
    if len(versions) > 1 or len(shas) > 1:
        raise ParamsVersionMismatch(f"PARAMS_VERSION_MISMATCH: metrics period {d0}..{d1} mixes parameter "
                                    f"versions {versions} — a new version needs its own data paths (D-019)")
    canon = sorted(json.dumps(r, sort_keys=True, separators=(",", ":"), ensure_ascii=False) for r in records)
    return {
        "schema_version": SCHEMA_VERSION,
        **header,
        "date_from": d0.isoformat(),
        "date_to": d1.isoformat(),
        "params_version": versions[0] if versions else None,
        "params_sha256": shas[0] if shas else None,
        "tickers": blocks,
        "alerts_triggered": sorted(f"{t}_{a}" for t, b in blocks.items() for a in b["alerts"]),
        "inputs": {"n_records": len(records),
                   "sha256": hashlib.sha256("\n".join(canon).encode("utf-8")).hexdigest()},
    }


# ----------------------------------------------------------------------
# API pública: agregadores (puros: solo leen data/)
# ----------------------------------------------------------------------
def aggregate_daily(day: date, tickers: Iterable[str], *, predictions_root: Path | None = None,
                    evaluations_root: Path | None = None,
                    thresholds: dict[str, float] | None = None) -> dict[str, Any] | None:
    """Aggregate one prediction date.

    Args:
        day: Prediction date (ET).
        tickers: Tickers to include.
        predictions_root: Predictions directory (default ``data/predictions``).
        evaluations_root: Evaluations directory (default ``data/evaluations``).
        thresholds: Alert thresholds (defaults: :data:`DEFAULT_THRESHOLDS`).

    Returns:
        dict | None: Daily metrics document, or None if there are no records.
    """
    ctx = _Ctx(predictions_root, evaluations_root, thresholds)
    tickers = list(tickers)
    doc = _period_doc(ctx, tickers, day, day, {"period": "daily", "date": day.isoformat()},
                      series=True, point_alerts=True)
    if doc is None:
        return None
    k = int(ctx.th["alert_consecutive_misses_99"])
    for t, b in doc["tickers"].items():
        if b["by_horizon"][STANDARD]["n_miss_99"] and ctx.std_streak_until(t, day) >= k:
            b["alerts"].append(f"consecutive_misses_99_ge_{k}")
    doc["alerts_triggered"] = sorted(f"{t}_{a}" for t, b in doc["tickers"].items() for a in b["alerts"])
    return doc


def aggregate_weekly(iso_year: int, iso_week: int, tickers: Iterable[str], *,
                     predictions_root: Path | None = None, evaluations_root: Path | None = None,
                     thresholds: dict[str, float] | None = None) -> dict[str, Any] | None:
    """Aggregate one ISO week (Monday–Sunday) of prediction dates.

    Args:
        iso_year: ISO year.
        iso_week: ISO week number.
        tickers: Tickers to include.
        predictions_root: Predictions directory.
        evaluations_root: Evaluations directory.
        thresholds: Alert thresholds.

    Returns:
        dict | None: Weekly metrics document, or None if there are no records.
    """
    d0 = date.fromisocalendar(iso_year, iso_week, 1)
    return _period_doc(_Ctx(predictions_root, evaluations_root, thresholds), tickers, d0, d0 + timedelta(days=6),
                       {"period": "weekly", "iso_week": f"{iso_year}-W{iso_week:02d}"},
                       series=True, point_alerts=True)


def aggregate_monthly(year: int, month: int, tickers: Iterable[str], *,
                      predictions_root: Path | None = None, evaluations_root: Path | None = None,
                      thresholds: dict[str, float] | None = None) -> dict[str, Any] | None:
    """Aggregate one calendar month of prediction dates.

    Args:
        year: Year.
        month: Month (1–12).
        tickers: Tickers to include.
        predictions_root: Predictions directory.
        evaluations_root: Evaluations directory.
        thresholds: Alert thresholds.

    Returns:
        dict | None: Monthly metrics document, or None if there are no records.
    """
    d0 = date(year, month, 1)
    d1 = (date(year + month // 12, month % 12 + 1, 1)) - timedelta(days=1)
    return _period_doc(_Ctx(predictions_root, evaluations_root, thresholds), tickers, d0, d1,
                       {"period": "monthly", "month": f"{year}-{month:02d}"},
                       series=True, point_alerts=True)


def aggregate_cumulative(tickers: Iterable[str], date_to: date | None = None, *,
                         predictions_root: Path | None = None, evaluations_root: Path | None = None,
                         thresholds: dict[str, float] | None = None) -> dict[str, Any] | None:
    """Aggregate all records from the first prediction date up to ``date_to``.

    ``as_of`` is the last prediction date **with data** (not today), so the
    document only changes when ``data/`` changes.

    Args:
        tickers: Tickers to include.
        date_to: Last prediction date to include (default: all data).
        predictions_root: Predictions directory.
        evaluations_root: Evaluations directory.
        thresholds: Alert thresholds.

    Returns:
        dict | None: Cumulative metrics document, or None if there is no data.
    """
    tickers = list(tickers)
    ctx = _Ctx(predictions_root, evaluations_root, thresholds)
    dates = [d for d in data_dates(tickers, ctx.proot, ctx.eroot) if date_to is None or d <= date_to]
    if not dates:
        return None
    doc = _period_doc(ctx, tickers, dates[0], dates[-1], {"period": "cumulative", "as_of": dates[-1].isoformat()},
                      series=False, point_alerts=False)
    if doc is None:
        return None
    eval_dates: set[str] = set()
    for t, b in doc["tickers"].items():
        history = []
        for d in dates:
            daily = aggregate_daily(d, [t], predictions_root=ctx.proot, evaluations_root=ctx.eroot,
                                    thresholds=ctx.th)
            if daily is None:
                continue
            if daily["tickers"][t]["n_evaluations"]:
                eval_dates.add(d.isoformat())
            history += [{"date": d.isoformat(), "alert": a} for a in daily["tickers"][t]["alerts"]]
        b["alerts_history"] = history
    doc["n_days_evaluated"] = len(eval_dates)
    return doc


# ----------------------------------------------------------------------
# Escritura en output/metrics (derivado, regenerable)
# ----------------------------------------------------------------------
def _write(path: Path, doc: dict[str, Any] | None) -> Path | None:
    """Escritura atómica; si no hay datos, borra el derivado obsoleto."""
    if doc is None:
        if path.exists():
            path.unlink()
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(dumps_canonical(doc))
    os.replace(tmp, path)
    return path


def _paths(metrics_dir: Path, d: date) -> tuple[Path, Path, Path]:
    y, w, _ = d.isocalendar()
    return (metrics_dir / "daily" / f"{d.isoformat()}.json",
            metrics_dir / "weekly" / f"{y}-W{w:02d}.json",
            metrics_dir / "monthly" / f"{d:%Y-%m}.json")


def rebuild_date(day: date, tickers: Iterable[str], *, output_dir: Path | None = None,
                 predictions_root: Path | None = None, evaluations_root: Path | None = None,
                 thresholds: dict[str, float] | None = None) -> list[Path]:
    """Rewrite the daily, weekly and monthly files containing ``day``, and ``cumulative.json``.

    This is what ``shadow_evaluate.py`` calls after appending evaluations.

    Args:
        day: Prediction date.
        tickers: Tickers.
        output_dir: ``output/`` directory (default: project ``output/``).
        predictions_root: Predictions directory.
        evaluations_root: Evaluations directory.
        thresholds: Alert thresholds.

    Returns:
        list[Path]: Files written (files removed for lack of data are not listed).
    """
    return _rebuild([day], list(tickers), output_dir, predictions_root, evaluations_root, thresholds)


def rebuild_from(day: date, tickers: Iterable[str], date_to: date | None = None, *,
                 output_dir: Path | None = None, predictions_root: Path | None = None,
                 evaluations_root: Path | None = None,
                 thresholds: dict[str, float] | None = None) -> list[Path]:
    """Rewrite every daily file from ``day`` to ``date_to`` (default: last data date or today).

    Args:
        day: First prediction date.
        tickers: Tickers.
        date_to: Last date (default: max(today, last data date)).
        output_dir: ``output/`` directory.
        predictions_root: Predictions directory.
        evaluations_root: Evaluations directory.
        thresholds: Alert thresholds.

    Returns:
        list[Path]: Files written.
    """
    tickers = list(tickers)
    dd = data_dates(tickers, predictions_root, evaluations_root)
    end = date_to or max([date.today(), *dd])
    days = [day + timedelta(days=i) for i in range((end - day).days + 1)]
    return _rebuild(days, tickers, output_dir, predictions_root, evaluations_root, thresholds)


def rebuild_all(tickers: Iterable[str], *, output_dir: Path | None = None,
                predictions_root: Path | None = None, evaluations_root: Path | None = None,
                thresholds: dict[str, float] | None = None) -> list[Path]:
    """Delete every metrics JSON and regenerate ``output/metrics/`` from ``data/``.

    Args:
        tickers: Tickers.
        output_dir: ``output/`` directory.
        predictions_root: Predictions directory.
        evaluations_root: Evaluations directory.
        thresholds: Alert thresholds.

    Returns:
        list[Path]: Files written.
    """
    tickers = list(tickers)
    mdir = Path(output_dir or OUTPUT_DIR) / "metrics"
    if mdir.exists():
        for f in mdir.rglob("*.json"):
            f.unlink()
    return _rebuild(data_dates(tickers, predictions_root, evaluations_root), tickers, output_dir,
                    predictions_root, evaluations_root, thresholds)


def _rebuild(days: list[date], tickers: list[str], output_dir: Path | None, proot: Path | None,
             eroot: Path | None, th: dict[str, float] | None) -> list[Path]:
    mdir = Path(output_dir or OUTPUT_DIR) / "metrics"
    kw = dict(predictions_root=proot, evaluations_root=eroot, thresholds=th)
    written: list[Path] = []
    weeks: set[tuple[int, int]] = set()
    months: set[tuple[int, int]] = set()
    for d in sorted(set(days)):
        written.append(_write(_paths(mdir, d)[0], aggregate_daily(d, tickers, **kw)))
        weeks.add(d.isocalendar()[:2])
        months.add((d.year, d.month))
    for y, w in sorted(weeks):
        written.append(_write(mdir / "weekly" / f"{y}-W{w:02d}.json", aggregate_weekly(y, w, tickers, **kw)))
    for y, m in sorted(months):
        written.append(_write(mdir / "monthly" / f"{y}-{m:02d}.json", aggregate_monthly(y, m, tickers, **kw)))
    written.append(_write(mdir / "cumulative.json", aggregate_cumulative(tickers, **kw)))
    return [p for p in written if p is not None]


# ----------------------------------------------------------------------
# Tests inline (datos sintéticos)
# ----------------------------------------------------------------------
def _selftest() -> None:  # pragma: no cover
    import tempfile

    tmp = Path(tempfile.mkdtemp(prefix="piqs_metrics_"))
    proot, eroot, out = tmp / "pred", tmp / "eval", tmp / "output"
    snap = {"version": "9.9.9", "integrity": {"sha256": "abc"}}

    def put(root: Path, rec: dict) -> None:
        p = root / rec["ticker"] / f"{rec['prediction_date']}.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")

    # NVDA: 2026-11-02 .. 11-13 (lun–vie). Viernes → horizonte 3930.
    # pits: miss_99 los días 9, 10, 11 (racha de 3), viernes 13 sin evaluar (pendiente).
    pits = {2: 0.10, 3: 0.30, 4: 0.50, 5: 0.70, 6: 0.90, 9: 0.001, 10: 0.999, 11: 0.002, 12: 0.40}
    for day in [2, 3, 4, 5, 6, 9, 10, 11, 12, 13]:
        d = date(2026, 11, day)
        rid = f"r{day:02d}"
        put(proot, {"run_id": rid, "prediction_date": d.isoformat(), "ticker": "NVDA", "params_version": "9.9.9",
                    "params_snapshot": snap, "horizon_minutes": 1050,
                    "notes": "v9.9.9: model ≈ GBM at T=1050" + ("; Friday: ..." if d.weekday() == 4 else "")})
        if day in pits:
            u = pits[day]
            put(eroot, {"run_id": rid, "prediction_date": d.isoformat(), "ticker": "NVDA", "params_version": "9.9.9",
                        "horizon_minutes_actual": 3930 if d.weekday() == 4 else 1050,
                        "realized_return": u - 0.5, "pit_value": u,
                        "in_50_interval": 0.25 <= u <= 0.75, "in_90_interval": 0.05 <= u <= 0.95,
                        "in_99_interval": 0.005 <= u <= 0.995, "ratio_model_gbm": 1.01,
                        "ratio_model_gbm_tail": 1.02, "log_score": -1.0})
    tickers = ["NVDA", "SPY"]
    th = {"min_evaluations_for_rate_alerts": 3}
    kw = dict(predictions_root=proot, evaluations_root=eroot, thresholds=th)
    ok = 0

    def check(cond: bool, msg: str) -> None:
        nonlocal ok
        assert cond, msg
        ok += 1

    # 1. Diario: valores, nulls para SPY (sin datos), sin evaluaciones → null
    d4 = aggregate_daily(date(2026, 11, 4), tickers, **kw)
    check(d4["tickers"]["NVDA"]["coverage_50"] == 1.0 and d4["tickers"]["NVDA"]["mean_pit"] == 0.5, "daily values")
    check(d4["tickers"]["SPY"]["n_predictions"] == 0 and d4["tickers"]["SPY"]["coverage_90"] is None, "null, not 0")
    d13 = aggregate_daily(date(2026, 11, 13), tickers, **kw)
    check(d13["tickers"]["NVDA"]["n_pending"] == 1 and d13["tickers"]["NVDA"]["mean_pit"] is None, "pending → null")
    check(aggregate_daily(date(2026, 11, 7), tickers, **kw) is None, "no records → None")
    # 2. KS exacto: pits {0.1,...,0.9} → 0.1
    w45 = aggregate_weekly(2026, 45, tickers, **kw)
    check(w45["tickers"]["NVDA"]["by_horizon"][STANDARD]["n_evaluations"] == 4, "Friday 6 → extended bucket")
    check(w45["tickers"]["NVDA"]["by_horizon"][EXTENDED]["n_evaluations"] == 1, "extended bucket count")
    check(abs(w45["tickers"]["NVDA"]["ks_pit_vs_uniform"] - 0.1) < 1e-12, "KS vs uniform")
    check(w45["iso_week"] == "2026-W45" and w45["date_from"] == "2026-11-02", "ISO week")
    check("weekend" in w45["tickers"]["NVDA"]["notes"] and "≈ GBM" in w45["tickers"]["NVDA"]["notes"], "notes")
    # 3. Alertas: racha de 3 misses estándar → dispara el 11-nov, no el 10
    a10 = aggregate_daily(date(2026, 11, 10), tickers, **kw)["tickers"]["NVDA"]["alerts"]
    a11 = aggregate_daily(date(2026, 11, 11), tickers, **kw)["tickers"]["NVDA"]["alerts"]
    check(a10 == ["miss_99"] and a11 == ["miss_99", "consecutive_misses_99_ge_3"], f"streak alert {a10} {a11}")
    w46 = aggregate_weekly(2026, 46, tickers, **kw)
    check("NVDA_consecutive_misses_99_ge_3" in w46["alerts_triggered"], "weekly streak alert")
    check("NVDA_coverage_90_below_0.85" in w46["alerts_triggered"], "rate alert with n ≥ min_n")
    check(not _rate_alerts(_stats([{"in_90_interval": False, "pit_value": 0.0}]), {**DEFAULT_THRESHOLDS}),
          "rate alerts gated by min n")
    # 4. Acumulado
    cum = aggregate_cumulative(tickers, **kw)
    nv = cum["tickers"]["NVDA"]
    check(cum["as_of"] == "2026-11-13" and cum["n_days_evaluated"] == 9, "cumulative as_of / days")
    check(nv["n_evaluations"] == 9 and nv["n_predictions"] == 10 and nv["n_pending"] == 1, "cumulative counts")
    check({"date": "2026-11-11", "alert": "consecutive_misses_99_ge_3"} in nv["alerts_history"], "alerts_history")
    check("miss_99" not in nv["alerts"] and "series" not in nv, "cumulative: no point alerts / series")
    # 5. Mensual
    m11 = aggregate_monthly(2026, 11, tickers, **kw)
    check(m11["date_to"] == "2026-11-30" and m11["tickers"]["NVDA"]["n_evaluations"] == 9, "monthly")
    # 6. Determinismo byte a byte y rebuild idempotente
    files1 = rebuild_all(tickers, output_dir=out, **kw)
    snap1 = {p: p.read_bytes() for p in files1}
    files2 = rebuild_all(tickers, output_dir=out, **kw)
    check(files1 == files2 and all(p.read_bytes() == snap1[p] for p in files2), "rebuild_all byte-identical")
    rebuild_date(date(2026, 11, 11), tickers, output_dir=out, **kw)
    check(all(p.read_bytes() == snap1[p] for p in files1), "rebuild_date leaves identical bytes")
    check(len([p for p in files1 if "daily" in p.parts]) == 10, "one daily file per data date")
    txt = (out / "metrics" / "cumulative.json").read_text(encoding="utf-8")
    check("generated_at" not in txt and txt.endswith("}\n"), "no volatile timestamp, trailing newline")
    # 7. Derivado obsoleto se borra si desaparece el dato
    (out / "metrics" / "daily" / "2026-11-07.json").write_text("{}", encoding="utf-8")
    rebuild_from(date(2026, 11, 7), tickers, date_to=date(2026, 11, 8), output_dir=out, **kw)
    check(not (out / "metrics" / "daily" / "2026-11-07.json").exists(), "stale derived file removed")
    # 8. Versiones mezcladas → PARAMS_VERSION_MISMATCH
    put(proot, {"run_id": "x", "prediction_date": "2026-11-12", "ticker": "SPY", "params_version": "1.0.0",
                "params_snapshot": {"integrity": {"sha256": "zzz"}}, "horizon_minutes": 1050, "notes": ""})
    try:
        aggregate_weekly(2026, 46, tickers, **kw)
        check(False, "mixed versions must raise")
    except ParamsVersionMismatch:
        check(True, "")
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)
    print(f"metrics_aggregator: {ok} checks OK")


if __name__ == "__main__":
    _selftest()
