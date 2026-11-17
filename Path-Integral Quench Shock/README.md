# Path-Integral Quench Shock

A predictive replication of the Paolinelli–Arioli (2018) path-integral model of stock and order
dynamics, applied to a single shock: NVIDIA's overnight drop of −15.2 % between the 11-Nov-2026
close and the 12-Nov-2026 open. The model is calibrated strictly on pre-event 1-minute data from
NVDA, with SPY as a market control. Its frozen forecast is tested against the realized move and
compared with a geometric-Brownian-motion baseline of identical volatility. The frozen model then
runs as a daily shadow forecaster, so that its calibration can be measured over many nights
instead of one.

> **Status (2026-11-17):**
>
> | Area | State |
> |---|---|
> | Research: notebook 01, model, calibration v1.0.0 (frozen) | ✅ |
> | Shadow: `shadow_predict.py` (daily forecast) | ✅ operational |
> | Shadow: `run_daily.ps1` + `run_daily.bat` (fetch + gate + predict) | ✅ operational; Task Scheduler at 22:00 Peru, Mon–Fri |
> | Shadow: `output/` metrics, `aggregate_metrics.py`, `export_csv.py` | ✅ operational |
> | Shadow: `shadow_evaluate.py` (block 3) | **pending**: forecasts are logged but not yet scored |
> | Shadow: `shadow_report.py`, backfill | pending |
> | Research: notebooks 02–04 | planned |

## Quick start

Windows PowerShell, from the project root, Python 3.13 (details in
[`docs/getting_started.md`](docs/getting_started.md)):

```powershell
python -m venv .venv                         # create an isolated environment
.venv\Scripts\Activate.ps1                   # activate it (every new terminal)
pip install -r requirements.txt              # install pinned dependencies
python scripts\fetch_today.py                # download today's 1-min bars for NVDA and SPY into data/raw/
python -m src.paolinelli_arioli              # model self-tests; must end with "TODOS LOS TESTS OK"
python scripts\shadow_predict.py --dry-run   # compute today's forecasts, write nothing
python scripts\shadow_predict.py             # compute today's forecasts and append them to data/predictions/
scripts\run_daily.bat --DryRun               # full daily pipeline (fetch + data gate + predict), writes no predictions
scripts\run_daily.bat                        # full daily pipeline, the same thing the scheduler runs
```

The forecast commands only predict after the session has closed (16:05 ET, Mon–Fri). Earlier, or
on a weekend or holiday, they skip with a warning and exit 0. That is expected behavior, not an
error.

## The scientific question

> *Did NVDA's order dynamics before 12 November 2026 contain the seed of the fat tails that
> materialized in the event's jump?*

## TL;DR: findings

- **1-minute fat tails were present before the event.** In the regular session, excess kurtosis
  was ≈ 3.9 for NVDA and ≈ 3.6 for SPY. The tails persist after normalizing volatility by day and
  session. They are a generic market feature, not an NVDA anomaly.
- **The calibrated model reproduces them.** Paolinelli–Arioli (jump-diffusion approximation)
  matches 1-minute kurtosis exactly, and its KS distance is 3–5× lower than GBM's (NVDA 0.013 vs
  0.059; SPY 0.026 vs 0.077).
- **At the overnight horizon (T = 1050 min) the model reduces to GBM.** The fit calls for about
  280 small i.i.d. jumps per horizon, and by the central limit theorem they average out: horizon
  kurtosis is 0.004.
- **The predictive test therefore becomes "GBM vs reality".** NVDA's −15.2 % is −3.1σ of the
  horizon. Its probability is 1.12 × 10⁻³ under the model and 1.11 × 10⁻³ under GBM, a
  **model/GBM ratio of 1.01** (the protocol's threshold for evidence was 10).
- **The null result is structural.** No i.i.d. jump specification that matches the observed
  1-minute variance and kurtosis can raise the ratio materially. A ratio of 10 would need a
  1-minute kurtosis of roughly 6,000–20,000; the observed value is 3.9.

**Conclusion.** Our replication confirms that the model reproduces 1-minute fat tails, but finds
that i.i.d. jumps average out at the overnight horizon by the central limit theorem. This is
consistent with the original paper, which validated the model at the 1-minute horizon. Within
this model class, the pre-event 1-minute dynamics did **not** contain an overnight seed of the
shock, and the calm pre-event window shows no evidence of rare, large jumps. The result
delimits the model's domain of validity. It does not refute the model. Details are in
[`docs/model_behavior.md`](docs/model_behavior.md).

**Data note.** The shock began in **11-Nov after-hours** (−5.5 % in the 17:28 ET bar), not in
12-Nov pre-market as originally assumed. The calibration cutoff at the 11-Nov regular close keeps
the test out of sample (decisions D-002 to D-004).

## How to use this project

| I want to… | Command | Details |
|---|---|---|
| Forecast today | `python scripts\shadow_predict.py` | [usage guide §2.1](docs/usage_guide.md) |
| Schedule the daily pipeline | `scripts\run_daily.bat` via `Register-ScheduledTask` (22:00 Peru, Mon–Fri) | [usage guide §5](docs/usage_guide.md#5-scheduling) |
| Score yesterday's forecasts | `python scripts\shadow_evaluate.py` *(block 3, pending)* | [usage guide §2.2](docs/usage_guide.md) |
| See a report | `python scripts\shadow_report.py` *(block 4, pending)* | [usage guide §2.3](docs/usage_guide.md) |
| Rebuild the metrics | `python scripts\aggregate_metrics.py --rebuild-all` | [usage guide §3.1](docs/usage_guide.md) |
| Export to CSV | `python scripts\export_csv.py --date 2026-11-16` | [usage guide §3.2](docs/usage_guide.md) |
| Download a day of bars | `python scripts\fetch_today.py --date 2026-11-13` | [data pipeline](docs/data_pipeline.md) |
| Re-run the research notebook | `jupyter nbconvert --to notebook --execute --inplace notebooks\01_data_exploration.ipynb` | [notebook 01](notebooks/01_data_exploration.ipynb) |
| Re-calibrate (rare, governed) | `python scripts\calibrate_params.py --version 2.0.0` | [usage guide §4](docs/usage_guide.md) |
| Fix a failure | — | [troubleshooting](docs/troubleshooting.md) |

Yahoo serves 1-minute bars for only about 30 days, so the CSVs in `data/raw/yahoo_1m/` are
versioned in git and are the only copy of older history (L-011, D-025).

## Reading the outputs

**`data/predictions/` and `data/evaluations/` are the source of truth.** Each is one append-only
JSONL file per ticker and prediction date, and lines are never edited or deleted. Each prediction
carries its full frozen-parameter snapshot, so it can be audited and re-scored on its own. Record
schemas are in [`docs/shadow_production.md` §5](docs/shadow_production.md#5-records).

**`output/metrics/` is derived.** It holds daily, ISO-week, monthly and cumulative JSON:
coverage, PIT, model/GBM ratio, log score and alerts, split by horizon (overnight vs weekend). It
can be deleted at any time and regenerated byte for byte with
`python scripts\aggregate_metrics.py --rebuild-all` (D-022, D-023). Never treat it as a source of
truth.

**`output/reports/` is presentation**: Markdown reports and figures generated from the metrics
(block 4, pending). `output/exports/` holds convenience CSVs that are overwritten on every run.
`output/backtests/` holds one file per backfill run, kept as evidence.

## Repository structure

```text
Path-Integral Quench Shock/
├── README.md                       this file
├── requirements.txt                Python 3.13 dependencies
├── config/
│   ├── params_calibrated.yaml      FROZEN model parameters v1.0.0 (SHA-256 protected; never edit)
│   ├── shadow_config.yaml          shadow operations: tickers, modes, paths, alert thresholds (no model params)
│   └── archive/                    previous parameter versions (created on re-calibration; versioned)
├── data/                           SOURCE OF TRUTH, append-only, versioned
│   ├── raw/yahoo_1m/{TICKER}/      one CSV per trading day: 1-min OHLCV + session labels (ET)
│   ├── predictions/{TICKER}/       shadow forecasts, one JSONL per prediction date
│   └── evaluations/{TICKER}/       shadow scores, one JSONL filed by prediction date
├── output/                         DERIVED, regenerable from data/; never a source of truth
│   ├── metrics/                    daily/, weekly/, monthly/ JSON + cumulative.json (deterministic)
│   ├── reports/                    daily/ and weekly/ Markdown reports, figures/ (block 4, planned)
│   ├── backtests/                  one JSON per backfill run (block 5, planned; versioned as evidence)
│   └── exports/                    flat CSVs of predictions and evaluations (overwritten)
├── logs/                           structured logs of the shadow scripts (not in git)
├── docs/                           see "Documentation map" below
├── notebooks/
│   ├── 01_data_exploration.ipynb   sessions, kurtosis, bad ticks, event chronology  ✅
│   ├── 02_model_implementation     planned
│   ├── 03_calibration              planned (must reproduce v1.0.0, not re-fit it)
│   ├── 04_predictive_test          planned
│   └── figures/                    figures saved by the notebooks (not in git; regenerated by running them)
├── scripts/
│   ├── run_daily.ps1 / .bat        daily pipeline for Task Scheduler (fetch + gates + predict)
│   ├── fetch_today.py              download today's (or a given day's) 1-min bars from Yahoo
│   ├── calibrate_params.py         OFFLINE calibration: the only writer of params_calibrated.yaml
│   ├── shadow_predict.py           daily shadow forecast CLI (--dry-run, --timestamp replay; exit 0/1/2)
│   ├── aggregate_metrics.py        rebuild output/metrics/ from data/ (--rebuild-all|-date|-from)
│   └── export_csv.py               export one day's predictions/evaluations to CSV
└── src/
    ├── data_loader.py              download, save and load daily CSVs (schema-normalizing)
    ├── returns.py                  1-min log returns, bad-tick filter (detectar_bad_ticks)
    ├── paolinelli_arioli.py        PaolinelliArioliModel: sampling, exact cdf/pdf/ppf, GBM baseline, inline tests
    ├── frozen_params.py            loads the frozen YAML, raises PARAMS_VERSION_MISMATCH on tampering
    ├── shadow_config.py            loads shadow_config.yaml and resolves its paths
    ├── shadow_logger.py            append-only JSONL store, dedup by run_id, no mixing of parameter versions
    ├── shadow_predictor.py         generar_prediccion: one deterministic frozen-parameter forecast per day
    ├── shadow_evaluator.py         evaluar_prediccion / find_realized_outcome: PIT, coverage, tail ratios, log score
    ├── metrics_aggregator.py       deterministic daily/weekly/monthly/cumulative metrics, alerts
    └── structured_logging.py       log format: timestamp | level | ticker | run_id | logger | message
```

## Shadow production

The frozen model forecasts each trading day's overnight return at 16:05 ET. The forecast is
scored the next day, once the 09:30 open is known, and is **never** re-calibrated. The design is
in [`docs/shadow_production.md`](docs/shadow_production.md).

| Component | What it does | Status |
|---|---|---|
| `src/shadow_predictor.py`, `scripts/shadow_predict.py` | Forecasts with frozen parameters: deterministic seed and `run_id`, exact quantiles, GBM baseline, data-quality checks; skips weekends and holidays; exit codes 0/1/2 | ✅ |
| `src/shadow_evaluator.py` | Finds the realized 09:30 open and computes PIT, 50/90/99 % interval hits, model/GBM tail ratios and log score | ✅ library |
| `src/shadow_logger.py` | Append-only JSONL with dedup; refuses to mix parameter versions | ✅ |
| `src/metrics_aggregator.py`, `scripts/aggregate_metrics.py` | Coverage, PIT, model/GBM ratio and alerts per day, ISO week, month and cumulative, split by horizon | ✅ |
| `scripts/export_csv.py` | Flat CSV exports for external analysis | ✅ |
| `scripts/shadow_evaluate.py` | Daily CLI: scores the previous day's forecasts and rebuilds the metrics | pending (block 3) |
| `scripts/shadow_report.py` | Weekly Markdown report and alerts | pending (block 4) |
| `scripts/shadow_backfill.py` | Scores forecasts missed by the scheduler | pending (block 5) |
| `scripts/run_daily.ps1`, `scripts/run_daily.bat` | Daily pipeline for Task Scheduler (22:00 Peru, Mon–Fri): weekend gate, date guard, fetch, data gate, predict | ✅ |

Governance guarantees are enforced in code:

- Frozen parameters are verified by SHA-256 on every load.
- Predictions are reproducible bit for bit.
- Evaluations rebuild the model from each prediction's own parameter snapshot.
- Records are never rewritten.

**The full event day was replayed through this production path and reproduces the offline result
exactly** (NVDA ratio 1.010; finding F-009). Friday and holiday-eve forecasts, whose real horizon
(3,930 min or more) exceeds the frozen T = 1050, are still made with T = 1050 and reported
separately (decision D-020, limitation L-014).

## Roadmap

| Version | Content | Status |
|---|---|---|
| **v1.0.0** | Jump-diffusion calibrated on 1-minute regular-session returns (06–11 Nov). Equivalent to GBM overnight. | Frozen; baseline for the shadow |
| v2.0.0 (b) | Jump parameters calibrated on overnight gaps or horizon-matched returns | Needs months of archived data |
| v2.0.0 (c) | Clustered jumps (Hawkes) and/or stochastic volatility: breaks the i.i.d. assumption behind the null result | Future work, a different model |
| — | Exact Metropolis–Hastings kernel (paper Sec. 4, eqs. 6–7) | Blocked on access to the paper |

## Documentation governance

1. Design decision → append to `docs/decisions.md`.
2. Finding → append to `docs/model_behavior.md` or `docs/limitations.md`.
3. Parameter version change → a new decision entry plus the archived YAML in `config/archive/`
   (automatic).
4. Docs never lag the code. A gap between them is high-priority technical debt.

## Documentation map

| Doc | Read it for | Audience |
|---|---|---|
| [`getting_started.md`](docs/getting_started.md) | First setup, first forecast, checking that everything works | New user |
| [`usage_guide.md`](docs/usage_guide.md) | Daily and weekly operations, maintenance, scheduling, reading results | Operator |
| [`troubleshooting.md`](docs/troubleshooting.md) | Error messages, exit codes, recovery | Operator |
| [`shadow_production.md`](docs/shadow_production.md) | System design: components, record and metrics schemas, PIT and coverage | Architect |
| [`model_behavior.md`](docs/model_behavior.md) | Scientific findings (F-xxx), including the overnight null result | Researcher |
| [`approximation.md`](docs/approximation.md) | The jump-diffusion approximation of the paper's model | Researcher |
| [`decisions.md`](docs/decisions.md) | Why each design choice was made (D-xxx) | Auditor |
| [`limitations.md`](docs/limitations.md) | What this study cannot claim (L-xxx) | Reviewer |
| [`data_pipeline.md`](docs/data_pipeline.md) | Downloading bars and the data-loader API | Developer |
| [`data_format.md`](docs/data_format.md) | Raw CSV schema and data quirks | Developer |

## References

- Paolinelli, G. & Arioli, G. (2018). *A path integral based model for stocks and order
  dynamics.* Physica A: Statistical Mechanics and its Applications.
- Ilinski, K. (2001). *Physics of Finance: Gauge Modelling in Non-equilibrium Pricing.* Wiley.
- Merton, R. C. (1976). Option pricing when underlying stock returns are discontinuous.
  *Journal of Financial Economics*, 3(1–2), 125–144.
