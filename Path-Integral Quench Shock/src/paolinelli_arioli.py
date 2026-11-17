"""Modelo de Paolinelli & Arioli (2018) — aproximación jump-diffusion.

Contexto
--------
Paolinelli & Arioli (2018, *Physica A*) obtienen la PDF de precios a partir de
una integral de camino de estados coherentes (Ilinski) cuya acción tiene una
parte gaussiana (GBM) más una perturbación no lineal debida a las órdenes,
con intensidades α₁ y α₂. El kernel de Metropolis–Hastings de la sección 4 del
paper **no** está implementado aquí (no se dispone del texto de las ecs. 6–7).

Aproximación usada (``approximation = "jump_diffusion"``)
---------------------------------------------------------
La perturbación por órdenes se representa como saltos de Poisson sobre el
log-precio ``x = ln S`` (Merton, 1976)::

    dx = m dt + σ_d dW + J dN,     N ~ Poisson(λ),   J ~ N(μ_J, σ_J²)

con la parametrización

    λ   = α₁                  (saltos por minuto)
    σ_J = α₂ · σ              (tamaño típico del salto, en unidades de σ)
    μ_J = 0                   (saltos simétricos)
    σ_d² = σ² − λ σ_J² = σ² (1 − α₁ α₂²)

Así ``σ`` es la volatilidad **total** de 1 minuto (la que mide la desviación
típica empírica) y α₁, α₂ solo redistribuyen esa varianza entre difusión y
saltos. Consecuencias:

* Con α₁ = 0 o α₂ = 0 el modelo es **exactamente** el GBM con la misma σ.
* El GBM de referencia ("misma σ, mismo T") tiene la **misma varianza**, de
  modo que cualquier diferencia de probabilidad en las colas se debe a la
  forma de la distribución y no a una volatilidad distinta.
* Curtosis en exceso a 1 minuto: ``κ₁ = 3 α₁ α₂⁴``; a horizonte T (pasos
  i.i.d.): ``κ_T = κ₁ / T``. Las colas gruesas a T grande sobreviven solo si
  los saltos son raros y grandes (λT pequeño, α₂ grande).
* Deriva ``m`` elegida para que ``E[S_T / S_0] = 1`` (martingala, μ = 0 por
  defecto): no se pronostica dirección.

Resultado nulo a horizonte overnight (v1.0.0)
--------------------------------------------
Calibrado a 1 minuto, el ajuste pide **muchos saltos pequeños**
(NVDA: α₁ ≈ 0.26/min, α₂ ≈ 1.5 → ~280 saltos en T = 1050 min). Por el
teorema central del límite esos saltos i.i.d. se promedian y a T = 1050 la
PDF es prácticamente gaussiana (κ_T ≈ 0.004): el modelo es indistinguible del
GBM con la misma σ (ratio de probabilidad de cola ≈ 1.01 para el −15.2 % de
NVDA del 11/12-nov). Detalle y consecuencias: ``docs/model_behavior.md``.

Discretización
--------------
El horizonte de ``T_minutes`` se divide en ``N_steps`` pasos. En cada paso el
incremento se muestrea de forma **exacta** (normal + suma de K ~ Poisson(λΔt)
saltos normales), así que la distribución terminal no depende de N; N solo
afecta a la resolución de las trayectorias. Además la distribución terminal
tiene forma cerrada (mezcla de normales ponderada por Poisson), que se usa en
:meth:`cdf` y :meth:`pdf` para probabilidades de cola exactas.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

import numpy as np
from scipy import stats

__all__ = ["PaolinelliArioliModel", "deterministic_seed", "jump_params_from_alphas"]

APPROXIMATIONS = ("jump_diffusion",)
# q005/q995 delimitan el intervalo central del 99 % (q01/q99 es el del 98 %).
QUANTILES = {"q005": 0.005, "q01": 0.01, "q05": 0.05, "q25": 0.25, "q50": 0.50,
             "q75": 0.75, "q95": 0.95, "q99": 0.99, "q995": 0.995}


def deterministic_seed(*parts: object) -> int:
    """Semilla reproducible a partir de cualquier combinación de valores.

    Usa SHA-256 (no ``hash()``, que en Python varía entre procesos).

    Args:
        *parts: Valores que identifican la predicción (ticker, modo, fecha...).

    Returns:
        int: Semilla de 64 bits.
    """
    key = "|".join(str(p) for p in parts).encode("utf-8")
    return int.from_bytes(hashlib.sha256(key).digest()[:8], "big")


def jump_params_from_alphas(sigma: float, alpha1: float, alpha2: float) -> dict[str, float]:
    """Deriva los parámetros de salto a partir de (σ, α₁, α₂).

    Args:
        sigma: Volatilidad total de 1 minuto (log-retorno).
        alpha1: Intensidad de saltos por minuto (λ).
        alpha2: Tamaño típico del salto en unidades de σ.

    Returns:
        dict: ``lambda_jumps_per_min``, ``mu_jump``, ``sigma_jump``.
    """
    return {"lambda_jumps_per_min": float(alpha1), "mu_jump": 0.0, "sigma_jump": float(alpha2 * sigma)}


@dataclass
class PaolinelliArioliModel:
    """PDF de log-retornos a horizonte ``T_minutes`` (aproximación jump-diffusion).

    Args:
        sigma: Volatilidad total de 1 minuto (log-retorno), p. ej. 0.0015.
        alpha1: Intensidad de saltos por minuto (λ). 0 ⇒ GBM.
        alpha2: Tamaño del salto en unidades de σ (σ_J = α₂σ). 0 ⇒ GBM.
        T_minutes: Horizonte en minutos.
        N_steps: Pasos de discretización del horizonte.
        approximation: Solo ``"jump_diffusion"``.
        jump_params: Opcional; si se pasa debe ser coherente con (σ, α₁, α₂).
        drift_per_min: Deriva μ del precio por minuto (0 = martingala).
    """

    sigma: float
    alpha1: float
    alpha2: float
    T_minutes: int
    N_steps: int
    approximation: str = "jump_diffusion"
    jump_params: dict | None = None
    drift_per_min: float = 0.0
    _derived: dict = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if self.approximation not in APPROXIMATIONS:
            raise ValueError(f"approximation debe ser uno de {APPROXIMATIONS}, no {self.approximation!r}")
        if not (self.sigma > 0 and self.T_minutes > 0 and self.N_steps > 0):
            raise ValueError("sigma, T_minutes y N_steps deben ser > 0")
        if self.alpha1 < 0 or self.alpha2 < 0:
            raise ValueError("alpha1 y alpha2 deben ser >= 0")
        if self.alpha1 * self.alpha2 ** 2 >= 1:
            raise ValueError(f"alpha1·alpha2² = {self.alpha1 * self.alpha2 ** 2:.4f} >= 1: "
                             "los saltos explicarían más varianza que la total")
        derivados = jump_params_from_alphas(self.sigma, self.alpha1, self.alpha2)
        if self.jump_params is not None:
            for k, v in derivados.items():
                if not np.isclose(float(self.jump_params.get(k, np.nan)), v, rtol=1e-6, atol=1e-12):
                    raise ValueError(f"jump_params[{k!r}]={self.jump_params.get(k)} incoherente con "
                                     f"(sigma, alpha1, alpha2) → {v}")
        lam, mu_j, sig_j = derivados["lambda_jumps_per_min"], derivados["mu_jump"], derivados["sigma_jump"]
        sig_d = self.sigma * np.sqrt(1.0 - self.alpha1 * self.alpha2 ** 2)
        compensador = lam * (np.exp(mu_j + 0.5 * sig_j ** 2) - 1.0)
        self._derived = {"lam": lam, "mu_j": mu_j, "sig_j": sig_j, "sig_d": sig_d,
                         "m": self.drift_per_min - 0.5 * sig_d ** 2 - compensador}

    # ------------------------------------------------------------------
    @property
    def is_gbm(self) -> bool:
        """True si no hay saltos (α₁ = 0 o α₂ = 0)."""
        return self.alpha1 == 0 or self.alpha2 == 0

    def gbm_counterpart(self) -> "PaolinelliArioliModel":
        """GBM con la misma σ total, T y N (baseline del test)."""
        return PaolinelliArioliModel(self.sigma, 0.0, 0.0, self.T_minutes, self.N_steps,
                                     self.approximation, None, self.drift_per_min)

    def excess_kurtosis_1min(self) -> float:
        """Curtosis en exceso teórica de un retorno de 1 minuto: 3 α₁ α₂⁴."""
        return 3.0 * self.alpha1 * self.alpha2 ** 4

    def excess_kurtosis_horizon(self) -> float:
        """Curtosis en exceso teórica a horizonte T: κ₁ / T."""
        return self.excess_kurtosis_1min() / self.T_minutes

    # ------------------------------------------------------------------
    def simulate_pdf(self, n_samples: int = 100_000, seed: int | None = None) -> np.ndarray:
        """Muestrea log-retornos ``ln(S_T / S_0)`` a horizonte T.

        Args:
            n_samples: Número de trayectorias.
            seed: Semilla (misma semilla ⇒ mismas muestras).

        Returns:
            np.ndarray: ``n_samples`` log-retornos.
        """
        rng = np.random.default_rng(seed)
        d = self._derived
        dt = self.T_minutes / self.N_steps
        x = np.zeros(n_samples)
        for _ in range(self.N_steps):
            x += d["m"] * dt + d["sig_d"] * np.sqrt(dt) * rng.standard_normal(n_samples)
            if d["lam"] > 0 and d["sig_j"] > 0:
                k = rng.poisson(d["lam"] * dt, n_samples)
                x += k * d["mu_j"] + np.sqrt(k) * d["sig_j"] * rng.standard_normal(n_samples)
        return x

    # ------------------------------------------------------------------
    def _mixture(self, tol: float = 1e-14) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Pesos, medias y desviaciones de la mezcla Poisson-normal terminal."""
        d, T = self._derived, self.T_minutes
        if self.is_gbm:
            return np.array([1.0]), np.array([d["m"] * T]), np.array([d["sig_d"] * np.sqrt(T)])
        L = d["lam"] * T
        kmax = int(stats.poisson.isf(tol, L)) + 1
        k = np.arange(kmax + 1)
        w = stats.poisson.pmf(k, L)
        mu = d["m"] * T + k * d["mu_j"]
        sd = np.sqrt(d["sig_d"] ** 2 * T + k * d["sig_j"] ** 2)
        return w / w.sum(), mu, sd

    def cdf(self, x: float | np.ndarray) -> float | np.ndarray:
        """CDF exacta del log-retorno a horizonte T (mezcla Poisson-normal)."""
        w, mu, sd = self._mixture()
        x = np.asarray(x, dtype=float)
        return (w * stats.norm.cdf((x[..., None] - mu) / sd)).sum(-1)

    def pdf(self, x: float | np.ndarray) -> float | np.ndarray:
        """Densidad exacta del log-retorno a horizonte T."""
        w, mu, sd = self._mixture()
        x = np.asarray(x, dtype=float)
        return (w * stats.norm.pdf((x[..., None] - mu) / sd) / sd).sum(-1)

    def ppf(self, q: float | np.ndarray) -> np.ndarray:
        """Cuantiles exactos (inversión numérica de :meth:`cdf`)."""
        from scipy.optimize import brentq
        w, mu, sd = self._mixture()
        lo, hi = float((mu - 40 * sd).min()), float((mu + 40 * sd).max())
        q = np.atleast_1d(np.asarray(q, dtype=float))
        return np.array([brentq(lambda v: float(self.cdf(v)) - qi, lo, hi, xtol=1e-12) for qi in q])

    # ------------------------------------------------------------------
    @staticmethod
    def summary(samples: np.ndarray) -> dict[str, float]:
        """Resumen de una muestra de log-retornos.

        Returns:
            dict: ``mean``, ``std``, ``q01``…``q99``, ``skew``, ``kurtosis``
            (curtosis **en exceso**, Fisher).
        """
        s = np.asarray(samples, dtype=float)
        out = {"mean": float(s.mean()), "std": float(s.std(ddof=1))}
        out.update({k: float(v) for k, v in zip(QUANTILES, np.quantile(s, list(QUANTILES.values())))})
        out["skew"] = float(stats.skew(s))
        out["kurtosis"] = float(stats.kurtosis(s))
        return out

    def analytic_summary(self) -> dict[str, float]:
        """Mismo esquema que :meth:`summary`, pero exacto (sin Monte Carlo)."""
        w, mu, sd = self._mixture()
        m1 = float((w * mu).sum())
        c = mu - m1
        m2 = float((w * (c ** 2 + sd ** 2)).sum())
        m3 = float((w * (c ** 3 + 3 * c * sd ** 2)).sum())
        m4 = float((w * (c ** 4 + 6 * c ** 2 * sd ** 2 + 3 * sd ** 4)).sum())
        out = {"mean": m1, "std": float(np.sqrt(m2))}
        out.update({k: float(v) for k, v in zip(QUANTILES, self.ppf(list(QUANTILES.values())))})
        out["skew"] = m3 / m2 ** 1.5
        out["kurtosis"] = m4 / m2 ** 2 - 3.0
        return out


# ----------------------------------------------------------------------
# Tests inline:  python -m src.paolinelli_arioli
# ----------------------------------------------------------------------
if __name__ == "__main__":
    np.set_printoptions(precision=5)
    ok = True

    def check(nombre: str, cond: bool, detalle: str = "") -> None:
        global ok
        ok &= bool(cond)
        print(f"[{'OK ' if cond else 'FAIL'}] {nombre} {detalle}")

    sigma, T, N, n = 0.0015, 1050, 80, 200_000

    # 1) alpha1 = alpha2 = 0  →  GBM exacto
    gbm = PaolinelliArioliModel(sigma, 0.0, 0.0, T, N)
    s = gbm.simulate_pdf(n, seed=1)
    mu_teo, sd_teo = -0.5 * sigma ** 2 * T, sigma * np.sqrt(T)
    ks = stats.kstest(s, lambda v: stats.norm.cdf(v, mu_teo, sd_teo))
    check("GBM: KS vs N(-σ²T/2, σ²T)", ks.pvalue > 0.01, f"(D={ks.statistic:.4f}, p={ks.pvalue:.3f})")
    check("GBM: std MC ≈ σ√T", abs(s.std() / sd_teo - 1) < 0.01, f"({s.std():.5f} vs {sd_teo:.5f})")
    check("GBM: curtosis ≈ 0", abs(stats.kurtosis(s)) < 0.05, f"({stats.kurtosis(s):+.4f})")
    xs = np.linspace(-0.2, 0.2, 9)
    check("GBM: cdf analítica == norm.cdf",
          np.allclose(gbm.cdf(xs), stats.norm.cdf(xs, mu_teo, sd_teo), atol=1e-14))
    check("GBM: E[S_T/S_0] = 1 (martingala)", abs(np.exp(s).mean() - 1) < 1e-3, f"({np.exp(s).mean():.5f})")
    for a1, a2 in [(0.0, 10.0), (0.01, 0.0)]:
        m = PaolinelliArioliModel(sigma, a1, a2, T, N)
        check(f"α=({a1},{a2}) ⇒ GBM idéntico", m.is_gbm and np.array_equal(
            m.simulate_pdf(1000, seed=7), gbm.simulate_pdf(1000, seed=7)))

    # 2) Con saltos: Monte Carlo vs mezcla analítica
    jd = PaolinelliArioliModel(sigma, 0.002, 20.0, T, N)
    sj = jd.simulate_pdf(n, seed=2)
    ks = stats.kstest(sj, jd.cdf)
    check("JD: KS(MC, cdf analítica)", ks.pvalue > 0.01, f"(D={ks.statistic:.4f}, p={ks.pvalue:.3f})")
    a = jd.analytic_summary()
    check("JD: std analítica == σ√T (misma varianza que GBM)", np.isclose(a["std"], sd_teo, rtol=1e-3),
          f"({a['std']:.5f})")
    check("JD: curtosis analítica == 3α₁α₂⁴/T", np.isclose(a["kurtosis"], jd.excess_kurtosis_horizon(), rtol=1e-3),
          f"({a['kurtosis']:.4f} vs {jd.excess_kurtosis_horizon():.4f})")
    check("JD: E[S_T/S_0] = 1", abs(np.exp(sj).mean() - 1) < 2e-3, f"({np.exp(sj).mean():.5f})")
    q = -0.15
    check("JD: cola izquierda > GBM en −15%", jd.cdf(q) > gbm.cdf(q),
          f"(JD={float(jd.cdf(q)):.2e}, GBM={float(gbm.cdf(q)):.2e})")

    # 3) Convergencia en N (40/80/160)
    qs = [0.01, 0.05, 0.5, 0.95, 0.99]
    ref = np.quantile(PaolinelliArioliModel(sigma, 0.002, 20.0, T, 160).simulate_pdf(n, seed=3), qs)
    for Nn in (40, 80):
        qq = np.quantile(PaolinelliArioliModel(sigma, 0.002, 20.0, T, Nn).simulate_pdf(n, seed=3), qs)
        check(f"Convergencia N={Nn} vs 160 (cuantiles)", np.allclose(qq, ref, atol=0.003),
              f"(máx |Δ|={np.abs(qq - ref).max():.5f})")

    # 4) Determinismo
    sd1 = deterministic_seed("NVDA", "daily_close_overnight", "2026-11-11")
    check("Determinismo: misma semilla ⇒ mismas muestras",
          np.array_equal(jd.simulate_pdf(5000, seed=sd1), jd.simulate_pdf(5000, seed=sd1)))
    check("Determinismo: seed estable entre procesos", sd1 == deterministic_seed("NVDA", "daily_close_overnight", "2026-11-11"))

    # 5) Validaciones
    try:
        PaolinelliArioliModel(sigma, 0.01, 20.0, T, N)
        check("Rechaza α₁α₂² >= 1", False)
    except ValueError:
        check("Rechaza α₁α₂² >= 1", True)
    try:
        PaolinelliArioliModel(sigma, 0.002, 20.0, T, N, jump_params={"lambda_jumps_per_min": 0.5,
                                                                     "mu_jump": 0, "sigma_jump": 0.03})
        check("Rechaza jump_params incoherentes", False)
    except ValueError:
        check("Rechaza jump_params incoherentes", True)

    print("\nTODOS LOS TESTS OK" if ok else "\nHAY TESTS FALLIDOS")
    raise SystemExit(0 if ok else 1)
