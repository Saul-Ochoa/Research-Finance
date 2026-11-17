# Model behavior: findings

This file is an **append-only** record of what the model does. Each finding has an ID (F-xxx), a date
and the parameter version it applies to. Numbers come from `config/params_calibrated.yaml` v1.0.0
and from the exact (closed-form) distribution in `src/paolinelli_arioli.py`, not from Monte Carlo.

**Central result (v1.0.0):** calibrated on 1-minute returns, the jump-diffusion approximation of
Paolinelli–Arioli reproduces 1-minute fat tails (KS 4.6× lower than GBM for NVDA and 3.0× lower for
SPY, kurtosis matched exactly). At the overnight horizon (T = 1050 min) it is **indistinguishable
from GBM**: the model/GBM tail-probability ratio for NVDA's −15.2 % event is **1.01**. This is
not specific to the fitted parameters. **No** i.i.d. jump specification that matches the observed
1-minute variance and kurtosis can do materially better (F-005).

---

## F-001 — Calibrated parameters and fit quality (v1.0.0, 2026-11-16)

Calibration data: regular-session 1-minute log returns from 06-Nov to 11-Nov 16:00 ET, Δt = 1,
with bad ticks removed (D-002, D-005, D-008). Method: profile search (D-009).

| | NVDA | SPY |
|---|---|---|
| σ (1 min, total) | 0.001524 (15.2 bp) | 0.000378 (3.8 bp) |
| α₁ = λ (jumps / min) | 0.2637 | 0.3851 |
| α₂ = σ_J / σ | 1.493 | 1.331 |
| σ_J (jump size, log) | 0.00228 (22.8 bp) | 0.00050 (5.0 bp) |
| Share of 1-min variance from jumps, α₁α₂² | 0.59 | 0.68 |
| n returns | 1,558 | 1,556 |
| **KS at 1 min: model / GBM** | **0.0126 / 0.0585** (4.6× better) | **0.0260 / 0.0774** (3.0× better) |
| Excess kurtosis at 1 min: empirical / model | 3.93 / 3.93 | 3.63 / 3.63 |
| Expected jumps over T = 1050 min (λT) | 277 | 404 |
| **Excess kurtosis at T = 1050: model** | **0.0037** | **0.0035** |
| σ_T = σ√T (horizon standard deviation) | 4.94 % | 1.23 % |
| q01 / q99 at T: model | −11.62 % / +11.37 % | −2.86 % / +2.84 % |
| q01 / q99 at T: GBM | −11.61 % / +11.37 % | −2.86 % / +2.84 % |

At 1 minute the model clearly beats GBM. At the horizon, the quantiles agree to within 4×10⁻⁵ in
log return.

## F-002 — The 1-minute data prefer frequent, small jumps (v1.0.0, 2026-11-16)

Along the curve that reproduces the empirical kurtosis exactly (α₁ = κ_emp / 3α₂⁴), the KS
distance at 1 minute is:

| α₂ (σ_J/σ) | λT (jumps per horizon) | KS NVDA | KS SPY |
|---|---|---|---|
| 1.3 | ≈ 480 / 440 | 0.040 | **0.029** |
| 1.5 | ≈ 270 / 250 | **0.013** | 0.041 |
| 2 | ≈ 86 / 79 | 0.032 | 0.059 |
| 3 | ≈ 17 / 16 | 0.045 | 0.069 |
| 6 | ≈ 1.1 / 1.0 | 0.055 | 0.075 |
| 20 | ≈ 0.01 | 0.058 | 0.077 |
| GBM (no jumps) | 0 | 0.059 | 0.077 |

The minimum is sharp and unique. Rare, large jumps fit the 1-minute distribution **no better than
GBM**. Over 3.5 trading days, the clean 1-minute sample contains no evidence of rare, large
jumps.

## F-003 — Why i.i.d. jumps average out: central limit theorem (v1.0.0, 2026-11-16)

The horizon log return is a sum of T = 1050 i.i.d. one-minute increments. Cumulants add, so the
excess kurtosis of the sum (the 4th cumulant divided by the variance squared) falls as 1/T:

$$
\kappa_T \;=\; \frac{T\,c_4}{(T\,c_2)^2} \;=\; \frac{\kappa_{1\text{min}}}{T}
\;=\; \frac{3\,\alpha_1\alpha_2^4}{T} \;=\; \frac{3.93}{1050} \;\approx\; 0.0037 .
$$

There is an equivalent way to see it. The jump count over the horizon is Poisson with mean
λT ≈ 277. Its relative dispersion is 1/√277 ≈ 6 %, so the jump component is itself a sum of
roughly 277 small Gaussian kicks, which is Gaussian to excellent precision. The higher cumulants
fall even faster (c₆ relative to σ⁶ goes as 1/T²).

**Contrast:**

| Horizon | Model vs GBM |
|---|---|
| 1 minute | Clearly different: KS 3–5× lower, kurtosis ≈ 4 vs 0. |
| 1050 minutes | Indistinguishable: kurtosis 0.004 vs 0; quantiles equal to 4×10⁻⁵. |

## F-004 — Consequence for the predictive test: it becomes "GBM vs reality" (v1.0.0, 2026-11-16)

Overnight log returns, from the last regular-session bar of 11-Nov to the 12-Nov 09:30 open:

| | NVDA | SPY |
|---|---|---|
| Reference price (11-Nov, regular close) | 173.85 | 759.79 |
| 12-Nov 09:30 open | 149.29 | 753.98 |
| Observed log return | **−15.23 %** | −0.77 % |
| In units of σ_T | **−3.08** | −0.63 |
| P(r ≤ observed): model | **1.121 × 10⁻³** | 0.2675 |
| P(r ≤ observed): GBM (same σ, same T) | **1.109 × 10⁻³** | 0.2675 |
| P(\|r\| ≥ \|observed\|): model (two-sided) | 2.24 × 10⁻³ | 0.535 |
| **Ratio model / GBM (left tail)** | **1.01** | 1.00 |

- **The ratio is far below the threshold of 10** set in the original protocol. Within the model,
  pre-event order dynamics give no extra overnight tail probability beyond GBM.
- **The absolute probability is not negligible:** 1.1 × 10⁻³ is roughly one night in 900, or about
  once in 3.5 years of trading days. This comes from the GBM part. Applying regular-session σ over
  1050 wall-clock minutes gives a horizon standard deviation of 4.94 %, which is consistent with
  the three overnight gaps NVDA had inside the calibration window (−8.27 %, −2.62 %, +2.30 %). That
  σ_T is a modeling assumption (L-009), not a finding of the jump component.

## F-005 — The null result is structural, not a calibration artifact (v1.0.0, 2026-11-16)

F-002 might suggest that a different split, with rare large jumps, would have kept fat tails at
the horizon. **It would not.** Holding 1-minute σ and excess kurtosis fixed at NVDA's values and
moving along the whole kurtosis curve:

| α₂ | σ_J | λT | One jump every … sessions | P(r ≤ −15.23 %) | Ratio vs GBM |
|---|---|---|---|---|---|
| 1.49 (fitted) | 0.23 % | 277 | — | 1.121 × 10⁻³ | 1.01 |
| 6 | 0.9 % | 1.06 | ~2.5 | 1.121 × 10⁻³ | 1.01 |
| 12 | 1.8 % | 0.066 | 41 | 1.121 × 10⁻³ | 1.01 |
| 32 | 4.9 % (= σ_T) | 1.3 × 10⁻³ | 2,051 | 1.120 × 10⁻³ | 1.01 |
| 100 | 15.2 % (= the event) | 1.4 × 10⁻⁵ | 195,638 | 1.111 × 10⁻³ | 1.00 |

Two mechanisms explain this:

- **Jumps small relative to σ_T** affect the horizon tail only through κ_T, which is fixed at
  κ₁/T no matter how the kurtosis is split. The leading Edgeworth correction is therefore the same
  everywhere on the curve.
- **Jumps large enough to matter** (σ_J ≳ σ_T) must be extremely rare to stay within a 1-minute
  kurtosis budget of ≈ 4. Their tail contribution, ≈ λT·P(J ≤ x), is then negligible.

**How much 1-minute kurtosis would a ratio ≥ 10 need?** Solving numerically gives:

| σ_J | λT needed | Excess kurtosis needed at 1 min |
|---|---|---|
| 7.6 % | 0.35 | ≈ 6,200 |
| 10.7 % | 0.13 | ≈ 8,800 |
| 15.2 % | 0.07 | ≈ 19,500 |

For σ_J ≤ 4.9 %, a ratio of 10 cannot be reached at all. The observed value is 3.9, **three to
four orders of magnitude short**.

**Conclusion:** with i.i.d. jumps, 1-minute data from a calm 3.5-day window **cannot** produce
overnight fat tails. The calibration sample would have to contain minutes like the 11-Nov 17:28
print (−5.5 %, roughly −96σ of after-hours volatility), and that is exactly what the
out-of-sample design excludes.

## F-006 — The bad-tick filter does not drive the null result (v1.0.0, 2026-11-16)

Without the filter, NVDA's pre-event excess kurtosis would be 370 (regular session) or 538 (all
sessions). Both values are still more than an order of magnitude below the ≈ 6,000 F-005 requires
for a ratio of 10. The filter changes the calibrated parameters, but it cannot change the
qualitative conclusion.

## F-007 — Consistency with the original paper (v1.0.0, 2026-11-16)

The project brief describes Paolinelli & Arioli (2018) as validating the model on **1-minute**
return distributions of Apple and the S&P 500 (the full text was not available to us; see
L-012). Our replication agrees at that horizon: the model captures 1-minute fat tails that GBM
misses (F-001). As far as we know, the paper makes no claim about multi-hour or overnight
horizons, and with i.i.d. perturbations the central limit theorem rules out such a claim (F-003,
F-005).

**This null result is therefore an extension, not a contradiction.** It locates the model's
domain of validity: the time scale at which order-driven fat tails are visible before
aggregation erases them.

## F-008 — What would be needed to capture overnight fat tails (future work, v2.0.0)

To produce overnight fat tails, a model has to break at least one of the assumptions behind F-005:

| Route | Assumption broken | Status |
|---|---|---|
| (b) Calibrate jumps directly on overnight gaps (or on a horizon-matched return series) | The calibration scale differs from the forecast scale | Rejected for v1 (D-011): 3 gaps in the window, about 10 since 28-Oct. Needs months of archive. |
| (c1) Self-exciting jumps (Hawkes) | Independence: clustered jumps keep kurtosis from shrinking as 1/T | Future work, a different model |
| (c2) Stochastic or regime-switching volatility | Identical distribution: a mixture of σ over the horizon creates horizon kurtosis | Future work, a different model |
| (d) Faithful Metropolis–Hastings kernel (paper Sec. 4) | Possibly the i.i.d. structure itself, if the coherent-state action induces memory | Blocked on access to the paper (see `approximation.md`) |
| (e) Event or news-arrival component (earnings, governance shocks) | Stationarity: a separate jump process tied to information arrival, calibrated on cross-sectional event data | Out of scope |

## F-009 — Production path reproduces the offline event result (v1.0.0, 2026-11-16)

The event day was replayed through the shadow-production code (`generar_prediccion` with
`timestamp_utc` = 11-Nov-2026 16:05 ET → `find_realized_outcome` → `evaluar_prediccion`). It
reproduces F-004 exactly:

| | Reference → realized | Log return | P model | P GBM | Ratio | Actual horizon |
|---|---|---|---|---|---|---|
| NVDA | 173.85 (15:59) → 149.29 (12-Nov 09:30) | −15.23 % | 1.121 × 10⁻³ | 1.110 × 10⁻³ | 1.010 | 1050 min |
| SPY | 759.79 → 753.98 | −0.77 % | 0.2675 | 0.2675 | 1.000 | 1050 min |

The NVDA forecast's 99 % interval was [−12.85 %, +12.61 %], so the move fell outside it
(`in_99_interval = false`). Log score was −2.58 for NVDA and +3.29 for SPY. The offline analysis
and the production code therefore share one implementation, with no divergence.
