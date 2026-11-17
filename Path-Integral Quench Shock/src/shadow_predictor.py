"""Shadow predictor: one frozen-parameter forecast per (ticker, mode, trading day).

The model never sees data in a way that can change its parameters: day data is
used **only** to obtain the reference price and data-quality metrics. The
forecast distribution depends solely on the frozen YAML (``params_snapshot``)
and the deterministic seed.

Determinism
-----------
* ``seed = int(sha256(f"{ticker}|{mode}|{fecha}").hexdigest()[:8], 16)``
* ``run_id = uuid5(NAMESPACE_URL, "piqs|{version}|{sha256}|{ticker}|{mode}|{fecha}")``
  — a UUID derived from the same key, so re-running the predictor for the
  same day yields the **same** ``run_id`` and the logger's dedup keeps exactly
  one prediction per (params, ticker, mode, day).
* ``pdf_summary`` / ``gbm_summary`` are computed from the **exact** terminal
  distribution (Poisson–normal mixture), not from Monte Carlo samples; the
  stored ``pdf_samples`` are for plotting only.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import date, datetime, time, timezone
from typing import Any

import pandas as pd

from src.data_loader import NY_TZ, cargar_dia, descargar_dia, guardar_dia, hoy_nueva_york
from src.paolinelli_arioli import PaolinelliArioliModel
from src.returns import detectar_bad_ticks

__all__ = ["PredictionSkipped", "prediction_seed", "prediction_run_id", "generar_prediccion",
           "load_day_for_prediction"]

logger = logging.getLogger(__name__)

REGULAR_BARS = 390
REGULAR_OPEN, REGULAR_CLOSE = time(9, 30), time(16, 0)
GBM_EQUIVALENCE_KURTOSIS = 0.01   # κ_T por debajo de esto ⇒ el modelo ≈ GBM en el horizonte


class PredictionSkipped(Exception):
    """Expected, non-failure reason not to predict (weekend, holiday, session still open)."""


def prediction_seed(ticker: str, mode: str, fecha: date) -> int:
    """Deterministic simulation seed: ``int(sha256("{ticker}|{mode}|{fecha}")[:8 hex], 16)``.

    Args:
        ticker: Ticker symbol (upper case).
        mode: Shadow mode name.
        fecha: Prediction date (ET).

    Returns:
        int: 32-bit seed.
    """
    return int(hashlib.sha256(f"{ticker}|{mode}|{fecha.isoformat()}".encode()).hexdigest()[:8], 16)


def prediction_run_id(params: dict[str, Any], ticker: str, mode: str, fecha: date) -> str:
    """Deterministic UUID for a (params, ticker, mode, day) prediction.

    Args:
        params: Frozen parameters (must include ``version`` and ``integrity.sha256``).
        ticker: Ticker symbol.
        mode: Shadow mode name.
        fecha: Prediction date (ET).

    Returns:
        str: UUID (version 5) as a string.
    """
    key = f"piqs|{params['version']}|{params['integrity']['sha256']}|{ticker}|{mode}|{fecha.isoformat()}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, key))


def load_day_for_prediction(ticker: str, fecha: date, *, allow_download: bool = True,
                            persist: bool = True) -> pd.DataFrame | None:
    """Load the day's bars, downloading them if missing or incomplete.

    A CSV is considered incomplete when ``fecha`` is today and its last
    regular-session bar is earlier than 15:59 ET (e.g. fetched intraday).

    Args:
        ticker: Ticker symbol.
        fecha: Trading day (ET).
        allow_download: Fetch from Yahoo if the CSV is missing/incomplete.
        persist: Save a fresh download to ``data/raw/yahoo_1m`` (False in dry runs).

    Returns:
        pd.DataFrame | None: Bars with a ``Fecha`` column, or None if no data.
    """
    df = cargar_dia(ticker, fecha)
    incompleto = False
    if df is not None and fecha == hoy_nueva_york():
        reg = df[df["Sesion"] == "Regular"]
        incompleto = reg.empty or reg.index.max().time() < time(15, 59)
    if (df is None or incompleto) and allow_download:
        logger.info("%s: CSV for %s %s; downloading", ticker, fecha, "incomplete" if incompleto else "missing",
                    extra={"ticker": ticker})
        nuevo = descargar_dia(ticker, fecha)
        if nuevo is not None:
            if persist:
                guardar_dia(nuevo, ticker, fecha)
            df = nuevo
    return None if df is None else df.assign(Fecha=fecha)


def _ticker_params(params: dict[str, Any], ticker: str) -> dict[str, Any]:
    try:
        return params["tickers"][ticker]
    except KeyError as exc:
        raise KeyError(f"{ticker} not in frozen params {params.get('version')} — adding a ticker requires "
                       "a new parameter version (docs/shadow_production.md §7)") from exc


def _model_from(tp: dict[str, Any]) -> PaolinelliArioliModel:
    return PaolinelliArioliModel(sigma=tp["sigma"], alpha1=tp["alpha1"], alpha2=tp["alpha2"],
                                 T_minutes=tp["T_minutes"], N_steps=tp["N_steps"],
                                 approximation=tp["approximation"], jump_params=tp["jump_params"])


def generar_prediccion(ticker: str, mode: str, config: dict[str, Any], params: dict[str, Any],
                       timestamp_utc: datetime, *, allow_download: bool = True,
                       persist_data: bool = True) -> dict[str, Any]:
    """Generate one shadow prediction with frozen parameters.

    Args:
        ticker: Ticker symbol.
        mode: Mode name; must be enabled in ``config["modes"]``.
        config: Parsed ``config/shadow_config.yaml``.
        params: Frozen parameters already verified by ``load_frozen_params``.
        timestamp_utc: Time the prediction is made (tz-aware). Its ET date is
            the prediction date.
        allow_download: Fetch the day's bars if the CSV is missing/incomplete.
        persist_data: Save fetched bars to ``data/raw`` (False for dry runs).

    Returns:
        dict: Prediction record (see ``docs/shadow_production.md`` §5).

    Raises:
        PredictionSkipped: Weekend, holiday/no data, or session not yet closed.
        ValueError / KeyError: Configuration errors (disabled mode, unknown
            ticker, horizon ≠ frozen T).
    """
    ticker = ticker.upper()
    mcfg = (config.get("modes") or {}).get(mode)
    if not mcfg or not mcfg.get("enabled", False):
        raise ValueError(f"mode {mode!r} is not enabled in shadow_config.yaml")
    if mode != "daily_close_overnight":
        raise ValueError(f"mode {mode!r} is not implemented in the v1.0.0 shadow")
    if timestamp_utc.tzinfo is None:
        raise ValueError("timestamp_utc must be timezone-aware")

    ts_utc = timestamp_utc.astimezone(timezone.utc)
    ts_et = ts_utc.astimezone(NY_TZ)
    fecha = ts_et.date()
    if fecha.weekday() >= 5:
        raise PredictionSkipped(f"{fecha} is a weekend")
    trigger = time.fromisoformat(mcfg.get("trigger_et", "16:05"))
    if ts_et.time() < trigger:
        raise PredictionSkipped(f"{ts_et:%H:%M} ET is before the {trigger:%H:%M} ET trigger (session not closed)")

    tp = _ticker_params(params, ticker)
    if int(mcfg["horizon_minutes"]) != int(tp["T_minutes"]):
        raise ValueError(f"horizon_minutes={mcfg['horizon_minutes']} ≠ frozen T_minutes={tp['T_minutes']}")

    # 1. Datos del día hasta el cierre regular (solo para precio de referencia y calidad)
    df = load_day_for_prediction(ticker, fecha, allow_download=allow_download, persist=persist_data)
    if df is None or df.empty:
        raise PredictionSkipped(f"no data for {fecha} (holiday or not yet available)")
    cutoff = pd.Timestamp.combine(fecha, REGULAR_CLOSE).tz_localize(NY_TZ)
    bars = df[(df.index < cutoff) & (df["Sesion"] != "Fuera de sesión")]

    # 2. Filtro de bad ticks
    bad = detectar_bad_ticks(bars)
    clean = bars[~bad]
    reg_all = bars[bars["Sesion"] == "Regular"]
    reg = clean[clean["Sesion"] == "Regular"]
    if reg.empty:
        raise PredictionSkipped(f"no regular-session bars on {fecha} (holiday?)")
    reference_price = float(reg["Close"].iloc[-1])
    reference_bar = reg.index[-1]
    missing = max(REGULAR_BARS - len(reg_all), 0)
    missing_pct = missing / REGULAR_BARS

    # 3. Modelo con parámetros congelados
    model = _model_from(tp)
    gbm = model.gbm_counterpart()

    # 4. Simulación determinista (solo para muestras de reporte)
    seed = prediction_seed(ticker, mode, fecha)
    pcfg = config.get("prediction") or {}
    n_sim, n_store = int(pcfg.get("n_samples_simulated", 100_000)), int(pcfg.get("n_samples_stored", 1000))
    samples = model.simulate_pdf(n_samples=n_sim, seed=seed)[:n_store]

    # 5. Resúmenes exactos + notas
    notes: list[str] = []
    if model.excess_kurtosis_horizon() < GBM_EQUIVALENCE_KURTOSIS:
        notes.append(f"v{params['version']}: model ≈ GBM at T={tp['T_minutes']} "
                     f"(horizon excess kurtosis {model.excess_kurtosis_horizon():.4f}; see "
                     "docs/model_behavior.md F-004/F-005); ratio_model_gbm expected ≈ 1.00")
    max_missing = float((config.get("data_quality") or {}).get("max_missing_bars_pct", 0.05))
    if missing_pct > max_missing:
        notes.append(f"data quality: {missing} of {REGULAR_BARS} regular bars missing "
                     f"({missing_pct:.1%} > {max_missing:.0%}); early close or data gap")
        logger.warning("%s: %.1f%% regular bars missing on %s", ticker, 100 * missing_pct, fecha,
                       extra={"ticker": ticker})
    if reference_bar.time() != time(15, 59):
        notes.append(f"reference bar is {reference_bar:%H:%M} ET, not 15:59")
    if fecha.weekday() == 4:
        # Opción A (D-020): se predice igual con T congelado; el horizonte real
        # (≥ 3,930 min) queda en horizon_minutes_actual de la evaluación.
        notes.append(f"Friday: target is the next session's open (>= 3930 min) but the forecast uses the "
                     f"frozen T={tp['T_minutes']} (L-014, option A per D-020), report separately")

    return {
        "run_id": prediction_run_id(params, ticker, mode, fecha),
        "timestamp_utc": ts_utc.isoformat(),
        "timestamp_et": ts_et.isoformat(),
        "prediction_date": fecha.isoformat(),
        "ticker": ticker,
        "mode": mode,
        "horizon_minutes": int(tp["T_minutes"]),
        "reference_price": reference_price,
        "reference_bar_et": reference_bar.isoformat(),
        "target": mcfg.get("target", "open_next_session_09:30"),
        "params_version": str(params["version"]),
        "params_snapshot": params,
        "seed": seed,
        "summary_method": "exact_mixture",
        "pdf_summary": model.analytic_summary(),
        "pdf_samples": [float(x) for x in samples],
        "gbm_summary": gbm.analytic_summary(),
        "data_quality": {"bars_used": int(len(reg)), "missing_bars": int(missing),
                         "missing_bars_pct": round(missing_pct, 6), "bad_ticks_removed": int(bad.sum())},
        "notes": "; ".join(notes),
    }
