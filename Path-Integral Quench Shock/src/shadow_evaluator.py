"""Shadow evaluator: score a logged prediction against the realized outcome.

The model is rebuilt from the prediction's own ``params_snapshot`` (whose
fingerprint is re-verified), never from the current YAML, so an evaluation
always scores the distribution that was actually forecast.

Interval conventions (central intervals of the forecast distribution):

* ``in_50_interval``: ``[q25, q75]``
* ``in_90_interval``: ``[q05, q95]``
* ``in_99_interval``: ``[q005, q995]``  (``[q01, q99]`` would be 98 %)
"""

from __future__ import annotations

import logging
import math
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

import pandas as pd

from src.data_loader import NY_TZ, cargar_dia
from src.frozen_params import ParamsVersionMismatch, fingerprint
from src.paolinelli_arioli import PaolinelliArioliModel

__all__ = ["evaluar_prediccion", "find_realized_outcome", "model_from_prediction"]

logger = logging.getLogger(__name__)

PROB_FLOOR = 1e-10
MAX_LOOKAHEAD_DAYS = 7


def model_from_prediction(prediction: dict[str, Any]) -> PaolinelliArioliModel:
    """Rebuild the forecast model from a prediction's ``params_snapshot``.

    Args:
        prediction: Logged prediction record.

    Returns:
        PaolinelliArioliModel: The exact model that produced the forecast.

    Raises:
        ParamsVersionMismatch: If the snapshot's fingerprint does not verify.
    """
    snap = prediction["params_snapshot"]
    if fingerprint(snap) != snap.get("integrity", {}).get("sha256"):
        raise ParamsVersionMismatch(
            f"PARAMS_VERSION_MISMATCH: re-calibration detected — params_snapshot of run_id "
            f"{prediction.get('run_id')} does not match its own fingerprint")
    tp = snap["tickers"][prediction["ticker"]]
    return PaolinelliArioliModel(sigma=tp["sigma"], alpha1=tp["alpha1"], alpha2=tp["alpha2"],
                                 T_minutes=tp["T_minutes"], N_steps=tp["N_steps"],
                                 approximation=tp["approximation"], jump_params=tp["jump_params"])


def find_realized_outcome(prediction: dict[str, Any], now_utc: datetime | None = None
                          ) -> tuple[float, pd.Timestamp] | None:
    """Find the realized price for a ``daily_close_overnight`` prediction.

    The target is the ``Open`` of the first regular-session bar (normally
    09:30 ET) of the next day after the prediction date that has data. Days
    without a CSV (weekends, holidays) are skipped, up to 7 calendar days.

    Args:
        prediction: Logged prediction record.
        now_utc: Current time (default: now). The target must be in the past.

    Returns:
        tuple | None: ``(price, bar_timestamp_et)``, or None if the horizon has
        not passed yet or the data is not available.
    """
    now_et = (now_utc or datetime.now(timezone.utc)).astimezone(NY_TZ)
    d0 = date.fromisoformat(prediction["prediction_date"])
    for k in range(1, MAX_LOOKAHEAD_DAYS + 1):
        d = d0 + timedelta(days=k)
        if d > now_et.date():
            return None
        if d.weekday() >= 5:
            continue
        if d == now_et.date() and now_et.time() < time(9, 31):
            return None
        df = cargar_dia(prediction["ticker"], d)
        if df is None:
            continue
        reg = df[df["Sesion"] == "Regular"]
        if reg.empty:
            continue
        return float(reg["Open"].iloc[0]), reg.index[0]
    return None


def evaluar_prediccion(prediction: dict[str, Any], actual_return: float, actual_price: float, *,
                       target_timestamp_et: pd.Timestamp | None = None,
                       evaluated_at_utc: datetime | None = None) -> dict[str, Any]:
    """Score one prediction against the realized log return.

    Args:
        prediction: Logged prediction record.
        actual_return: Realized log return ``ln(actual_price / reference_price)``.
        actual_price: Realized price at the target time.
        target_timestamp_et: Timestamp of the realized-price bar (for the record).
        evaluated_at_utc: Evaluation time (default: now).

    Returns:
        dict: Evaluation record (see ``docs/shadow_production.md`` §5).

    Raises:
        ValueError: If ``actual_return`` is inconsistent with ``actual_price``.
        ParamsVersionMismatch: If the prediction's snapshot fails verification.
    """
    r = float(actual_return)
    implied = math.log(actual_price / prediction["reference_price"])
    if abs(implied - r) > 1e-9:
        raise ValueError(f"actual_return {r} ≠ ln(actual_price/reference_price) = {implied}")

    model = model_from_prediction(prediction)
    gbm = model.gbm_counterpart()
    q = prediction["pdf_summary"]

    p_model = float(model.cdf(r))
    p_gbm = float(gbm.cdf(r))
    dens = float(model.pdf(r))
    # Cola en la dirección del movimiento (útil para subidas, donde la cola izquierda ≈ 1)
    up = r > q["q50"]
    t_model = 1.0 - p_model if up else p_model
    t_gbm = 1.0 - p_gbm if up else p_gbm

    horizon_actual = None
    if target_timestamp_et is not None:
        ref = pd.Timestamp(prediction["reference_bar_et"]) + pd.Timedelta(minutes=1)
        horizon_actual = int((pd.Timestamp(target_timestamp_et) - ref).total_seconds() // 60)

    return {
        "run_id": prediction["run_id"],
        "evaluated_at_utc": (evaluated_at_utc or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(),
        "prediction_date": prediction["prediction_date"],
        "ticker": prediction["ticker"],
        "mode": prediction["mode"],
        "params_version": prediction["params_version"],
        "reference_price": prediction["reference_price"],
        "target_timestamp_et": None if target_timestamp_et is None else pd.Timestamp(target_timestamp_et).isoformat(),
        "horizon_minutes_actual": horizon_actual,
        "realized_return": r,
        "realized_price": float(actual_price),
        "pit_value": p_model,
        "in_50_interval": bool(q["q25"] <= r <= q["q75"]),
        "in_90_interval": bool(q["q05"] <= r <= q["q95"]),
        "in_99_interval": bool(q["q005"] <= r <= q["q995"]),
        "prob_model": p_model,
        "prob_gbm": p_gbm,
        "ratio_model_gbm": p_model / max(p_gbm, PROB_FLOOR),
        "tail_direction": "right" if up else "left",
        "prob_model_tail": t_model,
        "prob_gbm_tail": t_gbm,
        "ratio_model_gbm_tail": t_model / max(t_gbm, PROB_FLOOR),
        "log_score": math.log(dens) if dens > 0 else None,
    }
