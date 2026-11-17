"""Calibración (FUERA de producción) de σ, α₁, α₂ y escritura del YAML congelado.

Es el único flujo autorizado a escribir ``config/params_calibrated.yaml``.
Exige una versión **mayor** que la existente y archiva la anterior en
``config/archive/``. El shadow detecta el cambio de versión/huella y se niega a
mezclar predicciones de versiones distintas.

Método (por ticker)
-------------------
1. Retornos log de 1 minuto, **sesión regular**, ventana
   ``[6-nov 04:00, 11-nov 16:00)`` ET, sin huecos (Δt = 1) y sin bad ticks
   (``src/returns.py``). Ningún dato posterior al cierre regular del 11-nov.
2. ``σ`` = desviación típica empírica (volatilidad total de 1 minuto).
3. ``α₁, α₂`` por perfil sobre los retornos estandarizados ``z``:

   * ``α₁(α₂)`` iguala exactamente la curtosis: ``3 α₁ α₂⁴ = κ_emp``.
   * ``α₂`` minimiza ``KS(z, JD₁ₘᵢₙ(α₁(α₂), α₂))`` (rejilla de 400 puntos +
     ``minimize_scalar`` acotado).

   El KS se calcula contra la CDF **exacta** del JD a 1 minuto (mezcla
   Poisson-normal): sin ruido Monte Carlo, totalmente determinista.
4. ``T_minutes = 1050`` (fijo por diseño) y ``N_steps = 80`` (fijo; se
   registra la verificación de convergencia 40/80/160).

Uso:
    python scripts/calibrate_params.py --version 1.0.0
"""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import optimize, stats

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.data_loader import NY_TZ, cargar_rango, hoy_nueva_york  # noqa: E402
from src.frozen_params import PARAMS_PATH, fingerprint, load_frozen_params  # noqa: E402
from src.paolinelli_arioli import PaolinelliArioliModel, jump_params_from_alphas  # noqa: E402
from src.returns import retornos_1m  # noqa: E402

logger = logging.getLogger("calibrate")

CAL_DESDE = date(2026, 11, 6)
CAL_HASTA = date(2026, 11, 11)
CAL_FIN = pd.Timestamp("2026-11-11 16:00", tz=NY_TZ)
SESIONES = ["Regular"]
T_MINUTES = 1050
N_STEPS = 80
SEED = 42


def _version_tuple(v: str) -> tuple[int, ...]:
    return tuple(int(p) for p in str(v).split("."))


def retornos_calibracion(ticker: str) -> pd.Series:
    """Retornos limpios de 1 minuto de la ventana de calibración (sesión regular)."""
    r = retornos_1m(cargar_rango(ticker, CAL_DESDE, CAL_HASTA))
    r = r[(r.index < CAL_FIN) & (r["dt"] == 1) & ~r["spike"] & r["Sesion"].isin(SESIONES)]
    if len(r) < 500:
        raise RuntimeError(f"{ticker}: solo {len(r)} retornos en la ventana de calibración")
    return r["r"]


def calibrar_ticker(ticker: str) -> dict:
    """Calibra (σ, α₁, α₂) para un ticker y devuelve parámetros + diagnósticos."""
    r = retornos_calibracion(ticker)
    sigma = float(r.std(ddof=1))
    z = np.sort(((r - r.mean()) / sigma).to_numpy())
    k_emp = float(stats.kurtosis(z))

    n = len(z)
    ecdf_hi = np.arange(1, n + 1) / n
    ecdf_lo = np.arange(0, n) / n

    def ks_exacto(a1: float, a2: float) -> float:
        """KS de una muestra contra la CDF exacta del JD estandarizado a 1 minuto."""
        # Se usa la σ real (no σ=1) para que el compensador exp(σ_J²/2) no desborde,
        # y se compara la forma: media 0, varianza 1 (se quita la deriva del modelo).
        w, mu, sd = PaolinelliArioliModel(sigma, a1, a2, 1, 1)._mixture()
        F = (w * stats.norm.cdf((z[:, None] - (mu - (w * mu).sum()) / sigma) / (sd / sigma))).sum(1)
        return float(max((ecdf_hi - F).max(), (F - ecdf_lo).max()))

    # Búsqueda por perfil: α₁ queda fijado por la curtosis (3 α₁ α₂⁴ = κ_emp) y α₂
    # minimiza el KS sobre esa curva. Rejilla densa + refinamiento acotado: determinista
    # y sin los mínimos locales que da differential_evolution en este valle estrecho.
    def a1_de(a2: float) -> float:
        return k_emp / (3 * a2 ** 4)

    a2_min = (k_emp / (3 * 0.95)) ** 0.5   # restricción α₁α₂² < 0.95
    if k_emp <= 0:
        raise RuntimeError(f"{ticker}: curtosis empírica {k_emp:.3f} ≤ 0, el modelo de saltos no aplica")
    rejilla = np.geomspace(a2_min * 1.001, 40.0, 400)
    perfil = np.array([ks_exacto(a1_de(a), a) for a in rejilla])
    i = int(perfil.argmin())
    lo, hi = rejilla[max(i - 1, 0)], rejilla[min(i + 1, len(rejilla) - 1)]
    res = optimize.minimize_scalar(lambda a: ks_exacto(a1_de(a), a), bounds=(lo, hi), method="bounded",
                                   options={"xatol": 1e-6})
    a2 = float(res.x)
    a1 = float(a1_de(a2))
    ks_jd = ks_exacto(a1, a2)
    ks_gbm = ks_exacto(0.0, 0.0)

    modelo = PaolinelliArioliModel(sigma, a1, a2, T_MINUTES, N_STEPS)
    qs = [0.01, 0.05, 0.5, 0.95, 0.99]
    cuant = {n: np.quantile(PaolinelliArioliModel(sigma, a1, a2, T_MINUTES, n).simulate_pdf(200_000, seed=SEED), qs)
             for n in (40, 80, 160)}
    conv = float(max(np.abs(cuant[40] - cuant[160]).max(), np.abs(cuant[80] - cuant[160]).max()))

    logger.info("%s: σ=%.6f α₁=%.5f α₂=%.3f | KS JD=%.4f GBM=%.4f | κ emp=%.2f JD=%.2f",
                ticker, sigma, a1, a2, ks_jd, ks_gbm, k_emp, modelo.excess_kurtosis_1min())
    return {
        "sigma": sigma, "alpha1": a1, "alpha2": a2,
        "jump_params": jump_params_from_alphas(sigma, a1, a2),
        "diag": {"n_returns": int(len(z)), "ks_jd": ks_jd, "ks_gbm": ks_gbm,
                 "kurt_emp": k_emp, "kurt_model_1min": modelo.excess_kurtosis_1min(),
                 "kurt_model_T": modelo.excess_kurtosis_horizon(),
                 "jumps_per_horizon": a1 * T_MINUTES, "conv_max_abs_dq": conv},
    }


def render_yaml(version: str, calibrated_at: str, res: dict[str, dict]) -> str:
    """Genera el YAML con comentarios inline (yaml.dump no conserva comentarios)."""
    window = ["2026-11-06", "2026-11-11T16:00:00-05:00"]
    tickers = {t: {"sigma": v["sigma"], "alpha1": v["alpha1"], "alpha2": v["alpha2"],
                   "T_minutes": T_MINUTES, "N_steps": N_STEPS, "approximation": "jump_diffusion",
                   "jump_params": v["jump_params"]} for t, v in res.items()}
    sha = fingerprint({"version": version, "calibration_window": window, "tickers": tickers})

    lines = [
        "# =====================================================================",
        "# Parámetros CONGELADOS del modelo Paolinelli–Arioli (jump-diffusion)",
        "# Generado por scripts/calibrate_params.py — NO EDITAR A MANO.",
        "# Cualquier cambio rompe integrity.sha256 y el shadow falla con",
        "#   PARAMS_VERSION_MISMATCH: re-calibration detected",
        "# Para re-calibrar: python scripts/calibrate_params.py --version <mayor>",
        "# =====================================================================",
        f'version: "{version}"                 # versión semántica; el shadow no mezcla versiones',
        f'calibrated_at: "{calibrated_at}"      # fecha de la calibración (America/New_York)',
        f'calibration_window: ["{window[0]}", "{window[1]}"]  # [inicio, fin) — nada posterior al cierre regular del 11-nov',
        'calibration_data: "yahoo_1m, sesión Regular, retornos log Δt=1 min, sin bad ticks (src/returns.py)"',
        "",
        "tickers:",
    ]
    for t, v in res.items():
        jp, d = v["jump_params"], v["diag"]
        lines += [
            f"  {t}:",
            f"    sigma: {v['sigma']!r}           # CALIBRADO: volatilidad total de 1 minuto (desv. típica del log-retorno)",
            f"    alpha1: {v['alpha1']!r}          # CALIBRADO: intensidad de saltos por órdenes, λ (saltos/minuto)",
            f"    alpha2: {v['alpha2']!r}          # CALIBRADO: tamaño del salto en unidades de σ (σ_J = α2·σ)",
            f"    T_minutes: {T_MINUTES}                # FIJO por diseño: close 16:00 → open 09:30 = 17.5 h = 1050 min",
            f"    N_steps: {N_STEPS}                    # FIJO por convergencia: N=40/80/160 → máx |Δcuantil| = {d['conv_max_abs_dq']:.5f}",
            '    approximation: "jump_diffusion"  # kernel MH del paper no implementado; ver src/paolinelli_arioli.py',
            "    jump_params:                   # DERIVADOS de (sigma, alpha1, alpha2); el modelo verifica coherencia",
            f"      lambda_jumps_per_min: {jp['lambda_jumps_per_min']!r}   # = alpha1",
            f"      mu_jump: {jp['mu_jump']!r}                 # saltos simétricos (no se pronostica dirección)",
            f"      sigma_jump: {jp['sigma_jump']!r}    # = alpha2 · sigma (log-retorno)",
        ]
    lines += ["", "# Diagnósticos de la calibración (informativos, no firmados)", "diagnostics:"]
    for t, v in res.items():
        d = v["diag"]
        lines += [
            f"  {t}:",
            f"    n_returns: {d['n_returns']}            # retornos de 1 minuto usados",
            f"    ks_model: {d['ks_jd']:.5f}           # KS(z empírico, JD 1 min)",
            f"    ks_gbm: {d['ks_gbm']:.5f}             # KS(z empírico, normal) — referencia",
            f"    kurtosis_empirical: {d['kurt_emp']:.4f}   # curtosis en exceso empírica a 1 min",
            f"    kurtosis_model_1min: {d['kurt_model_1min']:.4f}  # 3·α1·α2⁴",
            f"    kurtosis_model_T: {d['kurt_model_T']:.6f}   # a horizonte T (= κ_1min / T)",
            f"    expected_jumps_in_T: {d['jumps_per_horizon']:.4f}  # α1·T",
        ]
    lines += ["", "integrity:", f'  sha256: "{sha}"   # huella de version + calibration_window + tickers', ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--version", required=True, help="Versión semántica nueva (p. ej. 1.0.0)")
    p.add_argument("--tickers", nargs="+", default=["NVDA", "SPY"])
    p.add_argument("--out", type=Path, default=PARAMS_PATH)
    a = p.parse_args(argv)

    if a.out.exists():
        try:
            prev = load_frozen_params(a.out)["version"]
        except Exception:  # YAML corrupto o alterado: igual exigimos versión nueva
            import yaml
            prev = yaml.safe_load(a.out.read_text(encoding="utf-8")).get("version", "0.0.0")
        if _version_tuple(a.version) <= _version_tuple(prev):
            logger.error("La versión %s no es mayor que la existente %s. Re-calibrar exige versión nueva.",
                         a.version, prev)
            return 1
        archivo = a.out.parent / "archive" / f"params_calibrated_v{prev}.yaml"
        archivo.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(a.out, archivo)
        logger.warning("Versión anterior %s archivada en %s", prev, archivo)

    res = {t: calibrar_ticker(t) for t in a.tickers}
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(render_yaml(a.version, hoy_nueva_york().isoformat(), res), encoding="utf-8")
    load_frozen_params(a.out)  # verificación de ida y vuelta
    logger.info("Escrito %s (versión %s)", a.out, a.version)
    return 0


if __name__ == "__main__":
    sys.exit(main())
