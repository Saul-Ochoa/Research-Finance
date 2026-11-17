# Limitations

What this study **cannot** claim. The file is append-only; each item has an ID (L-xxx) and a date.
For decisions, see [`decisions.md`](decisions.md); for findings, see
[`model_behavior.md`](model_behavior.md).

## Methodological

**L-001 — Short calibration window (2026-11-16).**
The window is 06-Nov 04:00 to 11-Nov 16:00 ET: 4 trading days, about 3.5 of them usable (D-002),
giving ≈ 1,558 regular-session returns per ticker. Fourth moments are very unstable at this
size. In notebook 01 the block-bootstrap 95 % intervals for excess kurtosis span roughly
[2.1, 6.6] for NVDA in the regular session. α₁ and α₂ carry similar uncertainty, which is **not**
propagated into the predictions.

**L-002 — Single event, so no statistical power (2026-11-16).**
The predictive test rests on one overnight move (n = 1). A probability of 1.1 × 10⁻³ cannot
falsify a model by itself, and a model/GBM ratio cannot be estimated from one observation. The
shadow-production design exists to collect many forecast–outcome pairs.

**L-003 — The null result means we tested GBM, not full Paolinelli–Arioli (2026-11-16).**
At T = 1050 the calibrated model is GBM to within ~1 % in the tail (F-004, F-005). The predictive
test is therefore effectively **GBM vs reality**. It says nothing about whether the paper's exact
coherent-state kernel (which was not implemented, L-012) would behave differently overnight.

**L-004 — The model saw "normal" NVDA by design (2026-11-16).**
The calibration cutoff (11-Nov 16:00, regular close) was deliberate: it keeps the test out of
sample. It also means the model never saw the after-hours shock (−5.5 % at 17:28) or anything
like it. The question "did pre-event dynamics contain the seed?" is answered for this window only.
A different pre-event window, for example one including the 06-Nov → 09-Nov gap of −8.3 %, could
change σ but not the structural conclusion (F-005).

**L-005 — The i.i.d. jump assumption is strong (2026-11-16).**
Real 1-minute returns show volatility clustering and intraday seasonality (a U-shaped volatility
profile). The model has neither. It is this assumption, not the data, that makes overnight tails
collapse to Gaussian (F-003). The conclusion "no overnight fat tails" belongs to the **model
class**, not to NVDA.

## Data

**L-006 — Yahoo Finance is the only source, with no cross-verification (2026-11-16).**
No second vendor or consolidated tape was used to check prices, timestamps or bad ticks. The bad
ticks detected (D-005) show that the feed has errors. Others subtler than the filter can catch
may remain.

**L-007 — Extended-hours data is thin (2026-11-16).**
Volume is reported as 0 in every pre-market and after-hours bar, and minutes with no trades are
missing (SPY coverage is 86–95 %). Extended-hours returns were excluded from calibration (D-008),
yet the overnight horizon consists mostly of extended hours and closed market.

**L-008 — Event narrative not independently verified (2026-11-16).**
The brief describes the event as the CEO's resignation, announced before the 12-Nov open. The
price data place the start of the shock in 11-Nov after-hours (D-003). Neither the news nor its
exact timing was checked against an independent source. Conclusions rely only on prices.

**L-009 — σ over wall-clock minutes is an assumption (2026-11-16).**
T = 1050 counts every minute from 16:00 to 09:30, including about 8 hours of closed market, and
applies regular-session σ to all of them. The overnight standard deviation σ_T = 4.94 % (NVDA) is
therefore a modeling choice. It happens to be consistent with the three in-window NVDA gaps
(−8.3 %, −2.6 %, +2.3 %). For SPY it implies 1.23 %, against observed gaps of at most 0.91 % in absolute value. The
absolute probabilities in F-004 inherit this assumption; the model/GBM **ratio** does not.

**L-010 — Intraday seasonality not removed (2026-11-16).**
With only 4 days, the U-shaped volatility profile cannot be estimated reliably. Part of the
1-minute kurtosis is likely due to it (notebook 01, section 6), so α₁ and α₂ may partly fit
seasonality rather than order-driven jumps.

**L-011 — Rolling data availability (2026-11-16).**
Yahoo serves 1-minute bars for about 30 days only. Anyone trying to reproduce this work after
that period needs the archived CSVs in `data/raw/yahoo_1m/`, which must be kept (L-006).
*Update 2026-11-16:* under the git policy (D-024), these CSVs are **not** in the repository. Unless they are
backed up elsewhere, losing this disk makes the calibration window unreproducible.
*Update 2026-11-16 (later):* D-025 supersedes D-024. The raw CSVs are now versioned in git;
an external backup remains recommended.

## Scope and implementation

**L-012 — Paper not available; kernel not implemented (2026-11-16).**
The full text of Paolinelli & Arioli (2018) was not accessible. The description of the model, the
reported parameters (M = 100, β̃ = 2.5) and the claim that it was validated at 1 minute come from
the project brief. The model used is a documented jump-diffusion approximation (D-007,
[`approximation.md`](approximation.md)). α₁ and α₂ in this repository are **not comparable** with
the paper's coupling constants.

**L-013 — Partial documentation and notebooks (2026-11-16).**
Notebooks 02–04 have not been written yet; calibration v1.0.0 comes from
`scripts/calibrate_params.py` (D-013). `docs/data_pipeline.md` and `docs/data_format.md` are
still in Spanish (D-012). The shadow-production blocks (predictor, logger, evaluator and
scripts) are designed but not implemented; see
[`shadow_production.md`](shadow_production.md).
*Update 2026-11-16:* the shadow library modules are now implemented (`src/shadow_logger.py`,
`src/shadow_predictor.py`, `src/shadow_evaluator.py`, `config/shadow_config.yaml`). The CLI
scripts and the `.bat` are still pending, so shadow production does not run yet.
*Update 2026-11-16 (later):* `scripts/shadow_predict.py` is implemented and tested. Evaluation,
backfill and report scripts and the `.bat` are still pending, so the daily loop is not scheduled yet.
*Update 2026-11-16 (output infrastructure):* `output/`, `src/metrics_aggregator.py`,
`scripts/aggregate_metrics.py` and `scripts/export_csv.py` are implemented and tested (D-022, D-023).

## Shadow production

**L-014 — Weekend and holiday horizons do not match the frozen T (2026-11-16). Decided: option A (D-020).**
The frozen horizon is T = 1050 min (16:00 → 09:30 on consecutive weekdays). A Friday prediction
resolves on Monday's open, 3,930 min later (3.7 × T), and a long weekend with a holiday is longer
still. The predictor currently forecasts on Fridays with T = 1050 (option A) and records
`horizon_minutes_actual` in each evaluation. Under the model's own wall-clock assumption (L-009),
those forecasts are too narrow by about √3.7 ≈ 1.9× and will over-produce tail misses on Mondays.
Until this is decided, shadow coverage and PIT must be reported separately for 1050-minute and
longer horizons. The alternatives are B (skip Fridays and holiday eves, losing about 20 % of
days) and C (adjust T per prediction), and C is ruled out because it breaks D-006.
*Update 2026-11-16:* the owner chose **option A** (D-020). The limitation itself remains: Friday and
holiday-eve forecasts are too narrow for their real horizon. It is managed by splitting reports by
`horizon_minutes_actual`, not removed. Friday predictions carry a note saying so.
