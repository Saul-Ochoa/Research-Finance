"""Construcción de retornos de 1 minuto y control de calidad (bad ticks).

Reglas:

* Retorno logarítmico ``r_t = ln(C_t / C_{t-1})`` solo entre barras
  consecutivas del **mismo día**, excluyendo la barra "Fuera de sesión".
* ``dt`` = minutos entre las dos barras; un retorno "de 1 minuto" exige
  ``dt == 1``.
* Un **spike** (impresión errónea aislada) es una barra cuyo cierre se
  aleja mucho de los dos vecinos y vuelve inmediatamente:

      |r_t| > k·σ̂,  |r_{t+1}| > k·σ̂,  signo(r_t) ≠ signo(r_{t+1}),
      |r_t + r_{t+1}| < reversion · |r_t|

  donde σ̂ es una volatilidad robusta (1.4826·MAD) del bloque
  (día, sesión). Los dos retornos que tocan la barra se marcan
  ``spike=True``. Un salto real que **no** revierte (p. ej. un anuncio)
  no se marca.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

__all__ = ["retornos_1m", "sigma_robusta", "detectar_bad_ticks"]


def sigma_robusta(r: pd.Series) -> float:
    """Volatilidad robusta 1.4826·MAD; si el MAD es 0 usa la desviación típica.

    Args:
        r: Retornos.

    Returns:
        float: Estimación robusta de σ.
    """
    r = r.dropna()
    if r.empty:
        return np.nan
    mad = 1.4826 * np.median(np.abs(r - np.median(r)))
    return float(mad) if mad > 0 else float(r.std())


def retornos_1m(df: pd.DataFrame, k: float = 8.0, reversion: float = 0.25) -> pd.DataFrame:
    """Calcula retornos log de 1 minuto y marca los spikes.

    Args:
        df: Barras con columnas ``Close``, ``Sesion`` y ``Fecha``
            (salida de ``cargar_rango``).
        k: Umbral en múltiplos de la σ robusta del bloque (día, sesión).
        reversion: Fracción máxima del salto que puede quedar sin revertir
            para considerarlo spike.

    Returns:
        pd.DataFrame: Índice = timestamp de la barra final, columnas
        ``r``, ``dt`` (min), ``Sesion``, ``Fecha``, ``spike`` (bool) y
        ``barra_spike`` (bool, True en el retorno que *entra* al spike).
    """
    out = []
    for fecha, g in df.groupby("Fecha"):
        g = g[g["Sesion"] != "Fuera de sesión"].sort_index()
        r = np.log(g["Close"]).diff()
        dt = g.index.to_series().diff().dt.total_seconds().div(60)
        sig = r.groupby(g["Sesion"]).transform(sigma_robusta)

        r_sig = r.shift(-1)
        dt_sig = dt.shift(-1)
        entra = (
            (dt == 1) & (dt_sig == 1)
            & (r.abs() > k * sig) & (r_sig.abs() > k * sig)
            & (np.sign(r) != np.sign(r_sig))
            & ((r + r_sig).abs() < reversion * r.abs())
        )
        spike = entra | entra.shift(1, fill_value=False)
        out.append(pd.DataFrame({"r": r, "dt": dt, "Sesion": g["Sesion"], "Fecha": fecha,
                                 "spike": spike, "barra_spike": entra}))
    if not out:
        return pd.DataFrame(columns=["r", "dt", "Sesion", "Fecha", "spike", "barra_spike"])
    return pd.concat(out).dropna(subset=["r"])


def detectar_bad_ticks(df: pd.DataFrame, k: float = 8.0, reversion: float = 0.25) -> pd.Series:
    """Marca las barras que son impresiones erróneas aisladas (bad ticks).

    Es el filtro referenciado en ``config/shadow_config.yaml``
    (``data_quality.bad_tick_filter``). Usa **exactamente** el mismo criterio
    que :func:`retornos_1m` (y que el notebook 01): una barra es bad tick si su
    retorno de entrada y el de salida superan ``k`` σ robustas del bloque
    (día, sesión), tienen signo opuesto y se compensan en más de
    ``1 - reversion``.

    Args:
        df: Barras con columnas ``Close``, ``Sesion`` y ``Fecha``.
        k: Umbral en múltiplos de la σ robusta.
        reversion: Fracción máxima del salto que puede quedar sin revertir.

    Returns:
        pd.Series: Booleana con el mismo índice que ``df``; ``True`` en las
        barras a descartar. Para excluirlas: ``df[~detectar_bad_ticks(df)]``.
        (Al quitar la barra, los dos retornos que la tocan desaparecen: el
        siguiente retorno pasa a tener ``dt = 2`` y se descarta por la regla
        Δt = 1.)
    """
    r = retornos_1m(df, k=k, reversion=reversion)
    malas = r.index[r["barra_spike"]]
    return pd.Series(df.index.isin(malas), index=df.index, name="bad_tick")
