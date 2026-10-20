# Volatility & Risk Forecaster — Build Plan

**Status:** Plan v1.6 — 2026-9-05; v1.0 plus review passes, the no-full-history update, a production pass and a full re-read (change notes in §23). API keys are in the local `.env` (git-ignored).
**Goal:** A public, open-source Python bot that every trading evening forecasts tomorrow's volatility and 1-day Value-at-Risk (VaR) for a few ETFs, publishes the forecasts before the market opens, scores them the next evening, and keeps an honest, permanent track record.

**What it is not:** not a trading bot, not a price predictor, not investment advice. It never places trades and never says "buy" or "sell".

**Cost target:** $0 (free data tier, GitHub Actions and GitHub Pages on a public repo, open-source libraries; no AI API).


## Contents
1. [Scope](#1-scope)
2. [Requirements](#2-requirements)
3. [Key definitions](#3-key-definitions)
4. [Architecture](#4-architecture)
5. [Key decisions](#5-key-decisions)
6. [Data sources and licensing](#6-data-sources-and-licensing)
7. [Tech stack](#7-tech-stack)
8. [Repository layout](#8-repository-layout)
9. [Data model and files](#9-data-model-and-files)
10. [Module specifications](#10-module-specifications)
11. [Models](#11-models)
12. [Evaluation and statistics](#12-evaluation-and-statistics)
13. [Backtest protocol](#13-backtest-protocol)
14. [Live operation](#14-live-operation)
15. [Public site](#15-public-site)
16. [Testing strategy](#16-testing-strategy)
17. [CI/CD and automation](#17-cicd-and-automation)
18. [Security](#18-security)
19. [Milestones](#19-milestones)
20. [Risks](#20-risks)
21. [Definition of done](#21-definition-of-done)
22. [Open questions](#22-open-questions)
23. [Verification notes](#23-verification-notes)

---

## 1. Scope

### In scope (v0.1.0)
- Five liquid US ETFs covering different kinds of volatility: **SPY** (S&P 500), **QQQ** (Nasdaq-100), **IWM** (small caps), **TLT** (long-term Treasuries), **GLD** (gold).
- Daily end-of-day data only (open, high, low, close, volume, plus dividend and split records).
- Forecast horizon: the **next trading session** only.
- Two forecast types per ETF per day:
  1. the variance (and volatility) of tomorrow's close-to-close return;
  2. tomorrow's 1-day VaR at 95% and 99%.
- Baselines plus statistical models (§11), a historical backtest (§13), live forecasting (§14), scoring and a public site (§15).
- For SPY only: comparison against the VIX, the market's own implied-volatility forecast.

### Out of scope
- Predicting price direction or returns.
- Intraday data and true realized volatility from intraday prices (range-based estimators are used instead; see §3).
- Multi-day horizons, options pricing, portfolios of many assets.
- Accounts, user input, a mobile app, alerts to the public.
- Any commercial use (ads, paid tiers) — required by the data provider's terms (§6).

---

## 2. Requirements

### Functional
| ID | Requirement |
|---|---|
| FR-1 | Each trading evening, fetch the day's data for every ticker and validate it. |
| FR-2 | Each trading evening, produce next-session variance forecasts from every model and baseline, and VaR at 95% and 99%. |
| FR-3 | Commit the forecasts to the append-only `forecasts` branch before the next session opens, with a timestamp proof; forecasts are never edited afterwards. |
| FR-4 | Each trading evening, score the previous session's forecasts against what happened. |
| FR-5 | Maintain running scoreboards: loss metrics, model comparisons, VaR backtests. |
| FR-6 | Run a historical walk-forward backtest with the same code path as live forecasting. |
| FR-7 | Publish a static site with the latest forecasts, the scoreboard and charts. |
| FR-8 | Handle holidays, early closes, unscheduled closures and missing data without crashing or producing wrong scores. |
| FR-9 | Provide a CLI to fetch, forecast, score, backtest, rebuild the site, check health, verify timestamp proofs and smoke-test the real APIs. |

### Non-functional
| ID | Requirement | Target |
|---|---|---|
| NFR-1 Honesty | No forecast may use information from after its forecast time. | Automated leak test (§16). |
| NFR-2 Reproducibility | Anyone with their own access to the same data can reproduce any past forecast from the committed code version; the recorded input hash confirms they used identical inputs. | Each forecast records code version, model version and input hash. |
| NFR-3 Reliability | The daily job succeeds on ≥ 99% of trading days; failures are noticed within 24 hours. | Health check + failure notifications (§14). |
| NFR-4 Cost | $0 per month. | Free tiers only; API calls logged. |
| NFR-5 Licensing | Raw provider data is never published; only derived statistics. | Repo and site review in release checklist. |
| NFR-6 Correctness | Estimators and tests match textbook values. | Simulation and known-value tests (§16). |
| NFR-7 Maintainability | Lint, format, type checks and tests pass on every change. | CI gate. |
| NFR-8 Security | Secrets never exposed; least-privilege workflows; no known high-severity vulnerable dependencies. | §18 checks + `pip-audit` in CI. |
| NFR-9 Statistical honesty | No claim on the site without its sample size, uncertainty and multiple-testing adjustment. | Reporting rules (§12) enforced by the site builder. |

### Traceability
| Req | Built in | Tested by | Week |
|---|---|---|---|
| FR-1 | `providers/`, `validate.py`, `calendar.py` | Provider tests with recorded responses; validation and calendar tests | 1 |
| FR-2 | `models/`, `var.py`, `live.py` | Golden-output tests; simulation tests; end-to-end | 2–3 |
| FR-3 | `store.py`, `daily.yml` | Append-only and `on_time` tests | 2 |
| FR-4 | `scoring.py`, `live.py` | Known-value loss tests; gap recording test | 2 |
| FR-5 | `evaluate.py`, `site/` | Statistical-test unit and simulation tests | 4–5 |
| FR-6 | `backtest.py` | Leak test; same-function check (backtest and live call identical code) | 4 |
| FR-7 | `site/`, `pages.yml` | Site build test; privacy-of-data test | 5 |
| FR-8 | `calendar.py`, `live.py` | Holiday, early close, DST, unscheduled-closure and missing-data tests | 1–2 |
| FR-9 | `cli.py` | CLI tests (Typer's test runner) | 1–5 |
| NFR-1 | `backtest.py`, `models/` | Leak test | 4 |
| NFR-2 | `store.py` | Input-hash and golden-output tests | 2–3 |
| NFR-3 | `daily.yml`, heartbeat, `health` | Forced-failure drill (§21) | 2, 5 |
| NFR-4 | `providers/` call budget | `runs.csv` call counts; budget test | 1–2 |
| NFR-5 | `store.py`, `site/` | Privacy-of-data test | 5 |
| NFR-6 | `estimators.py`, `evaluate.py` | Known-value and simulation tests | 1, 4 |
| NFR-7 | CI | `ci.yml` | 0+ |
| NFR-8 | Workflows | actionlint, `pip-audit`, permissions review | 0, 5 |
| NFR-9 | `site/` | Site-builder test: every statistic rendered with n, interval and adjustment | 5 |

---

## 3. Key definitions

All returns are log returns computed from **unadjusted** prices plus the provider's dividend and split records (the adjusted price series is a premium feature; §6). For trading day *t*: open Oₜ, high Hₜ, low Lₜ, close Cₜ; previous close Cₜ₋₁; dividend Dₜ if day *t* is an ex-dividend day (else 0); split factor sₜ if a split takes effect on day *t* (new shares per old share, e.g. 2 for a 2-for-1 split; else 1).

**Split-adjusted previous close:** C̃ₜ₋₁ = Cₜ₋₁ / sₜ

**Close-to-close return** (dividend- and split-adjusted):
  rₜ = ln((Cₜ + Dₜ) / C̃ₜ₋₁)

**Overnight return** (dividend- and split-adjusted):
  oₜ = ln((Oₜ + Dₜ) / C̃ₜ₋₁)

**Range-based variance estimators** for the open-to-close part of the day:
- Parkinson (1980): σ²ₚ = (ln(Hₜ/Lₜ))² / (4 ln 2)
- Garman–Klass (1980): σ²_GK = ½ (ln(Hₜ/Lₜ))² − (2 ln 2 − 1)(ln(Cₜ/Oₜ))²
- Rogers–Satchell (1991): σ²_RS = ln(Hₜ/Cₜ)·ln(Hₜ/Oₜ) + ln(Lₜ/Cₜ)·ln(Lₜ/Oₜ)

**Daily variance proxy ("what actually happened")** — the primary scoring target:
  v̂ₜ = oₜ² + σ²_RS,ₜ
(overnight jump plus Rogers–Satchell intraday variance, which is robust to drift). Secondary proxies for robustness checks: rₜ² (squared return) and oₜ² + σ²_GK,ₜ. Yang–Zhang is defined over multi-day windows and is used only for the rolling-window baseline (§11).

**Forecast target:** hₜ₊₁ = forecast of the variance of rₜ₊₁, made after the close of day *t*. Volatility = √h. For display, annualize with √252.

**1-day VaR at level α** (α = 0.95 or 0.99), as a positive loss number:
  VaRₜ₊₁(α) = −√hₜ₊₁ · q₁₋α
where q₁₋α is the (1−α) quantile of the model's standardized (unit-variance) return distribution. **All models assume a zero daily mean.** The average daily return of these ETFs is tiny next to their daily volatility, a zero mean is standard practice in 1-day risk forecasting, and it keeps every model's VaR directly comparable. For a Student-t with ν degrees of freedom, the unit-variance quantile is q = t_ν⁻¹(1−α) · √((ν−2)/ν). A **violation** occurs when rₜ₊₁ < −VaRₜ₊₁(α).

**VIX-implied daily variance** (SPY only): the VIX is the market's expected volatility of the S&P 500 over the next 30 calendar days, annualized and in percent. Converted to a per-session variance:
  h_VIX,ₜ₊₁ = (VIXₜ/100)² · (30/365) / Nₜ
where Nₜ is the number of trading sessions in the 30 calendar days after day *t* (about 21). The simpler (VIXₜ/100)²/252 is within a few percent and is shown in the methodology for comparison. The VIX is a risk-neutral, forward-looking measure that usually sits above realized volatility, which is why `vix_mz` (§11) recalibrates it.

---

## 4. Architecture

```
          ┌──────────────────────── daily job (GitHub Actions, after the close) ─────────────────────────┐
provider ─▶ fetch ─▶ validate ─▶ store inputs ─▶ score yesterday ─▶ forecast tomorrow ─▶ write ─▶ stamp ─▶ commit ─▶ site
  API       (retry,   (rules)     (private data   (proxies, losses,   (all models,          (append-  (Open-    (push to   (static
            budget)                repo first)     VaR violations)     VaR levels)            only CSV) Timestamps forecasts  HTML)
                                                                                                          proof)    branch)
          └────────────────────────────────────────────────────────────────────────────────────────────┘
backtest ─▶ same forecast + score functions, replayed day by day over history (walk-forward)
```

**Core rule:** live forecasting and backtesting call the exact same `forecast_next(history_up_to_t)` and `score(forecast, realized)` functions. The backtest is a loop over dates; live is one iteration.

---

## 5. Key decisions

| # | Decision | Why |
|---|---|---|
| D1 | Forecast volatility, not returns | Volatility clusters and is genuinely predictable; returns are close to unpredictable. Honest skill is possible. |
| D2 | Range-based daily proxy (overnight² + Rogers–Satchell) instead of intraday realized volatility | Free daily OHLC data is enough; well-established estimators; squared returns are kept as a secondary proxy. |
| D3 | QLIKE as the primary loss | Robust to noise in the volatility proxy (Patton 2011), so rankings stay valid with an imperfect proxy. |
| D4 | Always include simple baselines | A model is only interesting if it beats "rolling 21-day variance" and RiskMetrics EWMA. |
| D5 | Same code for backtest and live | Prevents the classic backtest/live mismatch. |
| D6 | Forecasts are append-only, committed by GitHub Actions before the next open | The timing is proven by the protected branch (D16) and permanent timestamp proofs (D17); GitHub's own run logs help only for their first 90 days. |
| D7 | Never publish raw prices | Provider terms; publish forecasts, proxies and scores only. |
| D8 | Static site, no server | $0, nothing to keep running, nothing to attack. |
| D9 | Pluggable data provider | Free tiers and terms change; swapping provider must be a config change. |
| D10 | Raw data in a private Git repo, not the Actions cache | The cache expires after 7 days unused; warm-up history can't be re-downloaded on the free tier. |
| D11 | Zero-mean convention for all models | Daily means are negligible next to volatility; keeps VaR comparable across models. |
| D12 | Warm-up mode: models switch on when their minimum history is met | Lets the project go live on day one with baselines, whatever history source is available. |
| D13 | Never publish returns | A return series rebuilds the price path; variance proxies and violation flags are enough to verify results. |
| D14 | Short-history GARCH by penalized likelihood (`garch_t_sh`) | Full ETF history isn't available for free; shrinking toward typical parameter values lets GARCH run live early and is a defensible small-sample technique. |
| D15 | Backtest on FRED's S&P 500 index as a SPY stand-in | Free, about 10 years long; the limits (index not ETF, closes only, no dividends) are labeled everywhere it appears. |
| D16 | Bot commits only to a `forecasts` branch with force-push and deletion blocked | Keeps `main` protected for code, and makes the public record append-only. |
| D17 | OpenTimestamps proof for each day's forecasts | GitHub's logs expire after at most 90 days and commit dates can be set freely; a hash anchored in a public blockchain is a free, permanent, independent proof of timing. |

---

## 6. Data sources and licensing

| Data | Source | Notes |
|---|---|---|
| Daily OHLCV for the ETFs (live feed) | **Alpha Vantage `TIME_SERIES_DAILY`** | Free key: 25 requests/day (5 per minute reported). On the free tier only `outputsize=compact` (latest 100 sessions) is available; `outputsize=full` (25+ years) and `TIME_SERIES_DAILY_ADJUSTED` are premium. Prices are unadjusted. |
| Dividends and splits | Alpha Vantage `DIVIDENDS` and `SPLITS` | Free per the documentation. Used to adjust returns ourselves (§3). Fetched weekly. |
| S&P 500 index history (backtest stand-in for SPY) | FRED series `SP500` | About 10 years of daily **closing** values (FRED's agreement with S&P Dow Jones Indices limits it to 10 years). A price index, so no dividends. S&P prohibits reproducing it without permission: snapshot it once into the private data repo, use it only to compute results, publish derived statistics only, and cite it as FRED suggests. |
| VIX daily close | FRED series `VIXCLS` (free FRED API key) | Copyright Cboe; cite as FRED requires. Use for computation; publish only derived comparison metrics. |
| Trading calendar | `exchange_calendars` (XNYS) | Holidays and early closes. |

**Daily API budget:** 5 tickers × 1 `TIME_SERIES_DAILY` call = 5 calls per evening, plus 10 dividend/split calls on the first session of each week, so at most 15 on the busiest day. The budget guard (§14) stops at 20, leaving room for a backup run that fetches only what's missing and for a manual `smoke` run (3 calls), all under the free limit of 25. Calls are spaced at least 13 seconds apart to respect 5 per minute.

### History: the default path (no full-history access)
Full ETF history on the free tier isn't expected, so the plan is built to work without it:
1. **Start live with 100 sessions.** The first compact fetch already gives about 100 sessions per ETF, enough for every baseline (they need 22).
2. **Accumulate.** Every evening's fetch is appended to the private data repo, so each ETF's history grows by one session per trading day.
3. **Short-history GARCH.** `garch_t_sh` (§11) pulls its parameters toward typical values when history is short, so a GARCH model can forecast live from about 100 sessions instead of 500. The plain maximum-likelihood GARCH models switch on later, when their own minimum history is met.
4. **Backtest on the S&P 500 index.** FRED's roughly 10 years of daily S&P 500 closes give a historical walk-forward backtest for the close-to-close models, as a stand-in for SPY (§13). It also hosts the "how much history does GARCH need?" experiment.

**Optional upgrades:**
- **Alpha Vantage's open-source/educational access:** the free service advertises unlimited requests for verified open-source or educational projects. Worth one email; if it includes `outputsize=full`, the ETF backtests get longer. Nothing in the plan depends on it.
- **Paid data:** one month of premium data would provide full ETF history at once. It's a documented last resort, not part of the plan.

**Rejected sources:**
- **Twelve Data free plan:** "internal non-display usage… cannot be displayed to users, shared externally, or used in production systems." Incompatible with a public bot.
- **yfinance (Yahoo scraping):** no official API, can break without notice, conflicts with Yahoo's terms for commercial use.
- **Stooq:** now requires a CAPTCHA-issued key and has a daily quota — not automatable.

**Licensing rules for this project:**
1. Raw OHLCV and VIX values are stored only in a private data repository (§14), never committed to the public repo and never shown on the site.
2. The repo and site contain only derived values: forecasts, variance proxies, losses, test statistics.
3. The project stays non-commercial: no ads, sponsorships tied to the data, or paid features.
4. README states the data source, links its terms, and explains that forks must use their own API key and follow the provider's terms.

---

## 7. Tech stack

| Purpose | Choice |
|---|---|
| Language | Python 3.12 |
| Packages | `uv` with `uv.lock` |
| Data | `pandas`, `numpy`, `pyarrow` (Parquet cache) |
| Models | `arch` (GARCH family, HAR, Student-t, EWMA; bootstrap and multiple-comparison tests: SPA, StepM, MCS), `statsmodels` (OLS with HAC errors) |
| Stats | `scipy` |
| Calendar | `exchange_calendars` |
| HTTP | `httpx` with retries |
| Timestamp proofs | `opentimestamps-client` (the `ots` command) |
| CLI | `typer` |
| Site | `jinja2` templates + `plotly` (bundled JS, no CDN) |
| Tests | `pytest`, `hypothesis` |
| Quality | `ruff`, `mypy`, `pip-audit` |

---

## 8. Repository layout

```
volatility-forecaster/
├── README.md  LICENSE  CHANGELOG.md  BUILD_PLAN.md
├── METHODOLOGY.md  LIMITATIONS.md  RUNBOOK.md  CONTRIBUTING.md  SECURITY.md
├── pyproject.toml  uv.lock  .env.example  .gitignore  .pre-commit-config.yaml
├── .env                   # local secrets (git-ignored; never committed)
├── .github/
│   ├── dependabot.yml     # Python packages and GitHub Actions
│   └── workflows/
│       ├── ci.yml         # lint, types, fast tests, actionlint, pip-audit (main and PRs only)
│       ├── nightly.yml    # slow simulation tests
│       ├── daily.yml      # the evening job, including the site deploy
│       ├── proofs.yml     # weekly OpenTimestamps upgrades
│       ├── backtest.yml   # manual: run the backtest and publish its results
│       └── pages.yml      # manual: redeploy the site after code changes
├── src/volforecast/
│   ├── config.py          # tickers, model list, windows, paths
│   ├── calendar.py        # trading sessions, early closes
│   ├── providers/         # base.py, alphavantage.py, fred.py, recorded.py (tests)
│   ├── validate.py        # data quality rules
│   ├── estimators.py      # returns, Parkinson, GK, RS, Yang–Zhang
│   ├── models/            # baselines.py, garch.py, har.py, ensemble.py, vix.py
│   ├── var.py             # VaR from variance + distribution
│   ├── scoring.py         # losses, VaR violations
│   ├── evaluate.py        # DM, MZ, MCS, Kupiec, Christoffersen, traffic light
│   ├── backtest.py        # walk-forward loop
│   ├── live.py            # one evening's run
│   ├── store.py           # append-only CSV writing, input hashing
│   ├── site/              # build.py, templates/
│   └── cli.py
├── data/                  # PUBLIC derived outputs only — lives on the `forecasts` branch, not `main` (§14)
│   ├── forecasts.csv      # append-only
│   ├── scores.csv
│   ├── backtest/          # backtest forecasts and scores
│   ├── proofs/            # one daily snapshot + its OpenTimestamps .ots proof (§14)
│   └── runs.csv           # one row per job run
├── tests/
│   ├── fixtures/          # small synthetic OHLC sets, recorded API responses (synthetic values)
│   ├── unit/  property/  simulation/
└── site/                  # generated (git-ignored; deployed by pages.yml)
```

---

## 9. Data model and files

### `data/forecasts.csv` (append-only)
| Column | Meaning |
|---|---|
| `forecast_id` | `{ticker}-{target_date}-{model}` (unique) |
| `ticker` | e.g. SPY |
| `made_after_close_of` | date *t* |
| `target_date` | next session *t+1* |
| `model` | e.g. `garch_t`, `ewma_094` |
| `model_version` | e.g. `1` (§11) |
| `fit_status` | `ok`, `fallback` (reused last good parameters) or `warming_up` (not enough history; no forecast values) |
| `variance` | hₜ₊₁ |
| `vol_annualized` | √h · √252 |
| `var95`, `var99` | VaR as positive numbers |
| `dist` | normal / t / fhs |
| `code_version` | git commit SHA |
| `input_hash` | SHA-256 of the input history used |
| `created_utc` | timestamp |
| `on_time` | true if committed before the target session's open (§14); late forecasts are kept but excluded from the live record |

### `data/scores.csv` (append-only)
`forecast_id`, `proxy_rs` (v̂), `proxy_r2`, `proxy_gk`, `qlike`, `mse`, `viol95`, `viol99`, `early_close` (bool), `scored_utc`.

**Daily returns are deliberately not published.** A series of daily log returns lets anyone rebuild the price path, which would amount to redistributing the provider's data. Variance proxies (squared, so the sign is lost) and 0/1 violation flags are enough to verify every published statistic. Returns stay in the private data repository.

### `data/runs.csv`
`run_id`, `started_utc`, `finished_utc`, `status`, `session_date`, `api_calls`, `notes`.

### Private data repository (never in the public repo)
`volforecast-data` (private): `raw/{ticker}.parquet` (OHLCV), `raw/vix.parquet`, `corporate_actions/{ticker}.parquet` (dividends, splits), `revisions.csv` (any change to an already-stored bar), `params/` (last good model parameters per model version). See §14 for how it is written.

---

## 10. Module specifications

### 10.1 `calendar.py`
- `sessions(start, end)`, `next_session(date)`, `is_early_close(date)` from `exchange_calendars` (XNYS).
- **Unscheduled closures** (e.g. a national day of mourning) may not be in the calendar yet. If both the primary and backup runs find no bar for an expected session, the run ends as `data_not_ready` and the session is flagged for review. Once confirmed as a closure (manually, per the runbook), it is recorded as `no_session` and never scored.

### 10.2 `providers/`
- Protocol: `daily_bars(ticker, start, end) -> DataFrame[date, open, high, low, close, volume]`, `dividends(ticker)`, `splits(ticker)`.
- **Corporate actions:** dividends and splits are refreshed on the first session of each week, and immediately for any ticker whose computed return breaks the 25% validation threshold (a missed split is the usual cause). A dividend or split that appears later for an already-processed date is logged as a revision; published scores are not changed (§14).
- Alpha Vantage implementation: `TIME_SERIES_DAILY` with `outputsize=compact` (latest 100 sessions) on the free tier; `DIVIDENDS` and `SPLITS` weekly. Spaces calls ≥ 13 seconds apart; retries with exponential backoff on network errors. Alpha Vantage reports rate limits and premium-only requests as JSON messages inside a normal HTTP 200 response, so the client inspects every response body for those messages and fails with a clear error instead of treating them as data.
- Each compact fetch is merged into the private history: new dates are appended; dates already stored are compared and any change is logged as a revision.
- FRED implementation for `VIXCLS` (daily) and a one-time snapshot of `SP500` into the private data repo. The snapshot is taken once in week 1 and never refreshed, so the backtest stays fixed even as FRED's 10-year window moves forward.
- `recorded.py`: replays saved synthetic responses for tests.

### 10.3 `validate.py`
Rejects or flags:
- High < max(open, close), low > min(open, close), non-positive prices, zero volume on a session day.
- Duplicate or missing dates vs the calendar.
- Absolute daily return > 25% (likely a data error or an unrecorded split) → refresh that ticker's corporate actions (§10.2) and recompute; if still above 25%, flag and stop for that ticker until reviewed.
- A previously fetched bar that changed → log as a revision and keep the latest.

### 10.4 `estimators.py`
Pure functions for rₜ, oₜ, Parkinson, GK, RS, Yang–Zhang (window), with input checks. No pandas magic inside the formulas, so they are easy to test.

### 10.5 `var.py`
Given variance h and a distribution (normal, unit-variance Student-t with ν degrees of freedom, or an empirical quantile of standardized residuals for filtered historical simulation), return VaR(0.95) and VaR(0.99) under the zero-mean convention (§3).

### 10.6 `scoring.py`
For a forecast h and realized proxy v̂:
- **QLIKE:** L = v̂/h − ln(v̂/h) − 1 (0 at a perfect forecast). Proxies of exactly 0 (no price movement at all) are floored at 1e-10, the same floor as in §11, and counted in the report.
- **MSE:** (v̂ − h)².
- **VaR violation:** 1 if rₜ₊₁ < −VaR, else 0.

### 10.7 `evaluate.py` — see §12.

### 10.8 `live.py` — see §14.

### 10.9 `cli.py`
`fetch`, `forecast`, `score`, `backtest`, `site`, `health`, `run-daily` (the whole evening job), `verify-proofs` (checks every committed forecast file against its timestamp proof), and `smoke` (makes at most 3 real API calls to confirm the keys and response formats work; run after setup and after any key change, never in CI).

---

## 11. Models

Every model produces hₜ₊₁ from data up to and including day *t*.

### Baselines
| Name | Definition |
|---|---|
| `hist21` | Mean of the last 21 daily proxies v̂ |
| `yz21` | Yang–Zhang variance over the last 21 days |
| `ewma_094` | RiskMetrics: hₜ₊₁ = λhₜ + (1−λ)rₜ², λ = 0.94 |
| `rw` | Random walk: hₜ₊₁ = v̂ₜ |

### Statistical models
| Name | Definition |
|---|---|
| `garch_n` | GARCH(1,1), normal errors, zero mean (`arch` `mean="Zero"`, as for every GARCH variant below) |
| `garch_t` | GARCH(1,1), Student-t errors, maximum likelihood (`arch`) |
| `garch_t_sh` | GARCH(1,1), Student-t errors, **short-history version**: penalized likelihood that pulls parameters toward typical values when history is short (details below). Own implementation with `scipy.optimize` |
| `gjr_t` | GJR-GARCH(1,1,1), Student-t (bad news raises volatility more than good news) |
| `egarch_t` | EGARCH(1,1), Student-t |
| `har_log` | HAR on log proxy, fitted by OLS on the rolling window: ln v̂ₜ₊₁ = β₀ + β_d ln v̂ₜ + β_w avg₅(ln v̂) + β_m avg₂₂(ln v̂) + ε. Converted back with the log-normal correction: h = exp(ŷ + s²/2), where s² is the residual variance |
| `ensemble` | Equal-weight average of the variances of the statistical models that produced a forecast that evening (models still warming up are left out; the members are recorded) |
| `vix` (SPY only) | σ²_VIX from §3 |
| `vix_mz` (SPY only) | VIX recalibrated by a rolling regression of past proxies on past VIX variance (removes the typical gap between implied and realized volatility) |

**Estimation:** returns are scaled by 100 before fitting (`arch` works best on percent returns), then scaled back. Rolling window of the last 1,000 sessions. In the backtest, parameters are re-estimated every 21 sessions and the variance recursion is updated daily with fixed parameters; live runs refit every evening.

**Minimum history before a model goes live** (§6):
| Model | Minimum sessions | When it goes live (live start ≈ 100 sessions) |
|---|---|---|
| `rw`, `hist21`, `yz21`, `ewma_094`, `vix` | 22 | Day one |
| `garch_t_sh`, `vix_mz` | 100 | Day one |
| `har_log`, `ensemble` (needs at least two live statistical models) | 150 | About 2½ months in |
| `garch_n`, `garch_t`, `gjr_t`, `egarch_t` (plain maximum likelihood) | 500 | About 1½ years in |

Windows grow with the history up to 1,000 sessions. The site lists models that are still waiting, with the sessions they still need.

### Short-history GARCH (`garch_t_sh`)
Plain GARCH estimates are unreliable with a few hundred observations. Persistence in particular is badly estimated. `garch_t_sh` fixes this with **penalized likelihood**: it maximizes the log-likelihood plus a log-prior that pulls the parameters toward values typical for daily ETF returns. With little data the prior dominates. As history grows the data take over, and the estimates converge to the plain maximum-likelihood ones.

- **Variance targeting:** ω = σ̄²(1 − α − β), where σ̄² is the sample variance of the estimation window (data up to *t* only). This leaves three parameters: α, β and the Student-t degrees of freedom ν.
- **Reparameterization**, so the priors respect the constraints: persistence p = α + β ∈ (0, 1), share a = α/p ∈ (0, 1), and ν > 2. Normal priors are placed on logit(p), logit(a) and ln(ν − 2).
- **Prior centers:** p₀ = 0.97, a₀ ≈ 0.08 (so α₀ ≈ 0.08, β₀ ≈ 0.89) and ν₀ = 7. These are in the range commonly reported for daily equity-index GARCH models. They are fixed before any backtest and documented, with sources, in METHODOLOGY.md.
- **Prior strength:** one scale parameter, set so that at about 250 sessions the prior and the data have roughly equal influence. It is tuned only on the S&P 500 study period (§13), then frozen.
- **Fitting:** `scipy.optimize.minimize` (L-BFGS-B) from several starting points; the best is kept. Fit failures follow the fallback rule below.
- **Check:** with the prior strength set to zero, the estimates must match `arch`'s maximum-likelihood fit within tolerance (§16).

**Fit failures:** if a fit fails to converge or produces invalid parameters (e.g. α + β ≥ 1 for GARCH), reuse the last good parameters, record `fit_status = fallback` on the forecast, and flag it on the site. Two consecutive fallbacks raise a health warning.

**Logs of zero:** before taking logs for `har_log`, proxies are floored at 1e-10 (and the floor is counted and reported).

**Model versioning:** each model has a version (`garch_t@1`). Any change that could alter its outputs — formula, window, distribution, or a dependency upgrade that changes results — creates a new version that starts its own live record. Past forecasts are never recomputed or rescored.

**VaR distributions:** normal for `garch_n`, `ewma_094`, baselines, HAR, ensemble and VIX models; fitted Student-t (unit-variance quantile, §3) for `*_t` models, including `garch_t_sh`; filtered historical simulation as an extra VaR variant for `garch_t`: standardize past returns by their fitted volatility (zₛ = rₛ/√hₛ), take the empirical (1−α) quantile of the last 1,000 values of z, and multiply by √hₜ₊₁.

---

## 12. Evaluation and statistics

### Volatility forecasts
| Method | Purpose |
|---|---|
| Mean QLIKE and MSE per model and ticker, with 95% confidence intervals from a stationary block bootstrap (`arch.bootstrap.StationaryBootstrap`, fixed seed) | Headline accuracy, with honest uncertainty |
| Skill vs baseline: 1 − QLIKE_model / QLIKE_ewma, with bootstrap interval | Readable improvement |
| Diebold–Mariano test on the loss difference dₜ = L_model − L_ewma: one-sided, Newey–West variance with lag ⌊T^(1/3)⌋, Harvey–Leybourne–Newbold small-sample correction, compared with a t distribution | Is the model significantly better than the baseline? |
| Benjamini–Hochberg adjustment of the DM p-values within each ticker | Many models are tested, so some would "win" by luck |
| Model Confidence Set (`arch.bootstrap.MCS`, 10% size, block bootstrap, fixed seed) | Which models are statistically tied for best |
| Mincer–Zarnowitz regression on variances, v̂ = a + b·h, and on logs, ln v̂ = a + b·ln h (HAC errors); test a = 0, b = 1 | Is the forecast biased, or does it over- or under-react? The log version is less dominated by a few extreme days |
| Robustness: repeat rankings with r² and GK proxies | Does the conclusion depend on the proxy? |
| Rolling 250-session QLIKE chart | Does skill change across calm and stressed periods? |

### VaR
| Method | Purpose |
|---|---|
| Violation rate vs expected (5% / 1%) | Headline calibration |
| Exact two-sided binomial test of the violation count | Correct even with few violations, where chi-square approximations are poor |
| Kupiec proportion-of-failures test: LR_pof ~ χ²(1) | Is the violation rate right? (Reported alongside the exact test) |
| Christoffersen independence test: LR_ind ~ χ²(1), using the convention 0·ln 0 = 0; reported as "not enough violations" if there are fewer than two | Do violations cluster? |
| Christoffersen conditional coverage: LR_cc = LR_pof + LR_ind ~ χ²(2) | Both at once |
| Basel traffic light for 99% VaR over the last 250 sessions: green 0–4 violations, yellow 5–9, red 10+ | Familiar, readable summary |

### Reporting rules
- The backtest and the live record are reported separately; the live record is the headline.
- Every statistic is shown with its number of observations.
- **Low-power labels:** a VaR test is marked "low power" until the expected number of violations reaches 10 (200 sessions at 95%, 1,000 sessions at 99%). Loss comparisons are marked "early" before 60 live sessions. The site says plainly what can't be concluded yet.
- No model is described as "best" unless it is in the MCS **and** beats the EWMA baseline after the Benjamini–Hochberg adjustment.
- All bootstraps use fixed, recorded seeds so reports are reproducible.
- Robustness: rankings are recomputed excluding early-close sessions and using each alternative proxy; any ranking that flips is reported.

---

## 13. Backtest protocol
There are three kinds of evidence, always reported separately and in this order of importance:
1. **The live record** for all five ETFs (§14): the headline.
2. **The S&P 500 index backtest:** about 10 years from FRED's `SP500`, a stand-in for SPY.
3. **The short-history experiment** on the same index data.

### S&P 500 index backtest
- **Data:** the one-time `SP500` snapshot (§10.2). Closing values only, price index (no dividends).
- **Models included:** everything that needs only closing prices: `rw` and `hist21` on squared returns, `ewma_094`, all GARCH variants including `garch_t_sh`, `har_log` on squared returns, the ensemble, and both VIX models (VIX history from FRED). Range-based baselines (`yz21`) are left out, because highs and lows aren't available.
- **Scoring:** squared returns as the volatility proxy (noisier than the range-based proxy, but QLIKE rankings stay valid with a noisy proxy) plus VaR violations.
- **Walk-forward:** for each session *t*, call `forecast_next(history ≤ t)` and score against *t+1*. No parameter, window, prior or threshold may be chosen using data after *t*. Parameters are re-estimated every 21 sessions, with the variance recursion updated daily.
- **Holdout discipline:** the first part of the snapshot, from its start through the end of 2021, is the **study period**. It's used to set the `garch_t_sh` prior strength and to make any other modeling choices. From January 2022 to the live start is the **out-of-sample period**, scored once after every choice is frozen. These dates are written into the config in week 1, before any results are looked at.
- **Labels:** every table and chart from this backtest says "S&P 500 index, closing prices, no dividends: a stand-in for SPY".

### Short-history experiment ("how much history does GARCH need?")
On the index data, at every session from the first point where 1,000 sessions of history exist (roughly late 2020, given a snapshot starting around late 2016) onward, fit models using only the last *n* sessions, for *n* ∈ {100, 150, 250, 500, 1,000}. Results are reported separately for the study and out-of-sample periods. Compare QLIKE and VaR calibration for `ewma_094`, `garch_t` (plain) and `garch_t_sh` at each *n*, using the same tests as §12. It answers two questions the live project depends on: does shrinkage beat plain GARCH with little data, and when does plain GARCH catch up? It also makes a clear chart for the README.

### Shared rules
- **Leak test:** for a sample of dates, truncate all data after *t* and check that the forecast equals the one produced with the full data available (§16).
- **As ETF history accumulates** in the private data repo, the same backtest code can be rerun on each ETF's own history. It's labeled with its (short) length.
- **Output:** `data/backtest/spx_forecasts.csv`, `data/backtest/spx_scores.csv` (same schemas as live) and `data/backtest/short_history.csv`, plus a summary report. All derived values, no index levels or returns.
- **How it's run and published:** the `backtest.yml` workflow (manual trigger) checks out the code, reads the private snapshot with a read-only deploy key, runs `volforecast backtest`, passes the privacy check, and commits the results to the `forecasts` branch. You can also run it locally while developing, but only the workflow publishes, so every published result comes with a run log and a known code version.

---

## 14. Live operation

### Evening job (`run-daily`)
**Schedule:** a primary run at 23:37 UTC and a backup run at 03:37 UTC (the next calendar day), Monday–Friday. 23:37 UTC is 7:37 p.m. EDT / 6:37 p.m. EST, always after the 4 p.m. ET close. The odd minute avoids the top and half of the hour, when GitHub's scheduler is busiest. GitHub says scheduled runs can be delayed under heavy load and, at very heavy load, some queued jobs can be dropped. The backup run does nothing if the primary run already finished; it exists for exactly those cases and for a provider's end-of-day bar arriving late. Scheduled workflows only run on the default branch (`main`).

**Which session is "today":** the job never trusts the runner's clock date. It asks the calendar for the most recent completed XNYS session as of the current time in America/New_York, so a delayed run or the 03:37 UTC backup still processes the right day.

Steps:
1. Determine the latest completed session *t*. If it is already processed → exit (idempotent).
2. Fetch the compact series for each ticker that doesn't already have a bar dated *t* in the private repo (so a backup run after a partial failure only fetches what's missing). On the first session of the week, primary run only, also refresh dividends and splits. If a bar dated *t* isn't available yet, retry with backoff for up to 30 minutes, then exit with status `data_not_ready` (the backup run tries again).
3. Fetch VIX from FRED. If day *t*'s VIX is not published yet, the SPY VIX models use the latest available value and record its date; the comparison treats that as a known handicap.
4. Validate (§10.3).
5. Score the forecasts that targeted session *t*. If none exist (e.g. a missed day), record the gap; nothing is backfilled into the live record.
6. Forecast session *t+1* with every model whose minimum history is met.
7. Append to `forecasts.csv`, `scores.csv`, `runs.csv`; rebuild the site.
8. Write the evening's new forecast rows to `data/proofs/{date}.csv` and create an OpenTimestamps proof for it (see below).
9. **Privacy check (pre-publish):** scan everything about to be committed and the built site for raw price, volume or return columns. Any hit stops the publish and fails the run (§16).
10. Commit and push to the `forecasts` branch (never to `main`).
11. Deploy the site to GitHub Pages as the last job of the same workflow. Pushes made with the workflow's built-in token don't start other workflows, so the deploy can't rely on a separate workflow reacting to the push.

**API budget guard:** the job keeps a count of Alpha Vantage calls per calendar day (in the private repo, so it survives across runs) and stops before 20 calls. Retries count toward the cap. That leaves headroom under the free limit of 25 for manual `smoke` runs.

**Revisions after use:** a bar that changes after it was used to score or forecast is logged in the revision log. Published scores are never recomputed; the site shows how many scores used a bar that was later revised.

### Where outputs are committed, and why
- **`main`** holds code only. It's protected: changes need a pull request with passing CI. The bot never pushes to it.
- **`forecasts`** holds `data/`, and only the bot commits to it. Its protection blocks force-pushes and branch deletion, which makes the published record append-only at the Git level: nobody, including you, can quietly rewrite past forecasts. GitHub's classic branch protection or rulesets can do this on a public repository; set it up in week 0.
- The daily workflow runs from `main` (where scheduled workflows run) and checks out `forecasts` alongside it to write outputs. The site is built from both.

### Permanent proof of timing
GitHub keeps workflow logs for at most 90 days on public repositories, and Git commit dates can be set by whoever makes the commit. So neither proves, years later, that a forecast existed before the market opened. The project adds an independent proof:
- Each evening the job stamps the day's forecast file with **OpenTimestamps**, a free, open-source timestamping standard. Its public calendar servers need no account or API key. The client sends only the file's hash, never the data.
- A proof starts as "pending". A weekly job runs `ots upgrade` to complete it once the timestamp is anchored in the Bitcoin blockchain, which typically takes a few hours. Completed proofs are committed next to their files.
- Anyone can verify a day's forecasts with the OpenTimestamps tools. Full verification by the official client needs a Bitcoin node, so the README also points to the project's online verifier. `volforecast verify-proofs` checks that every forecast row appears in a stamped file.
- If the calendar servers are unreachable, publishing continues, the run is flagged, and the next run stamps the missed file. The proof then shows a later time, and that day's forecasts are marked as having a weaker proof.

**Concurrency:** both schedules share one GitHub Actions `concurrency` group, so two runs can never overlap or race on the CSV files.

**Deadline:** forecasts must be committed before the target session's open (13:30 UTC during US daylight time, 14:30 UTC otherwise; computed from the calendar, including late openings if any). A forecast committed after the open gets `on_time = false` and is excluded from the live record.

**Idempotency:** re-running the job for a session already processed changes nothing; appending an existing `forecast_id` is an error.

### Private data storage
Raw prices must stay private (§6), but the history must also survive for years, because in warm-up mode it is built up one day at a time and cannot be re-downloaded on the free tier.
- **Durable store:** a separate **private** GitHub repository (`volforecast-data`) holding the raw Parquet history, the dividend/split records and the revision log. The daily job pushes to it with a deploy key that has write access to that repository only, stored as a secret in the public repo. Git history doubles as a backup.
- **Speed-up only:** GitHub Actions cache may hold a copy for faster runs, but it is never the source of truth. GitHub removes cache entries that haven't been accessed in 7 days and evicts the least recently used entries above the repository limit (10 GB by default).
- **Write order:** the job pushes raw data to the private repo **before** publishing forecasts to the public repo, so every published forecast has its inputs stored.

### Monitoring
- **Run log:** every run, successful or not, appends a row to `runs.csv` with its status and API call count, and writes one-line JSON logs to the Actions output.
- **Failure notifications:** turn on email notifications for failed workflow runs in GitHub settings, and test them once by forcing a failure.
- **Heartbeat (recommended):** after a successful run, the job pings an external heartbeat monitor. If no ping arrives within 36 hours on a trading day, the monitor emails you. Several services have free tiers; check their terms. This also catches the cases GitHub can't report: a dropped scheduled run, or scheduled workflows being turned off.
- **60-day rule:** GitHub turns off scheduled workflows in a public repository after 60 days without repository activity. The bot's daily commits to `forecasts` should count as activity, but don't rely on it. If the bot stops for a long stretch, the heartbeat alert fires long before day 60; re-enable the workflow from the Actions tab.
- **Staleness on the site:** the site shows "last updated" and turns the banner amber if the latest forecast is more than one trading session old, so visitors are never misled by stale numbers.
- **`volforecast health`:** checks the last successful run, the last forecast date, recent fit fallbacks, the API calls used today, the private-repo sync status, and any timestamp proof still pending after 7 days. Exits non-zero on any problem.

### Runbook (`RUNBOOK.md`)
| Symptom | Likely cause | Action |
|---|---|---|
| `data_not_ready` on both runs | Provider's end-of-day bar delayed or missing | Run the job manually later; if the session was an unscheduled closure, mark it `no_session` |
| Rate-limit or premium message in the response | Daily cap reached, or endpoint moved to premium | Check `runs.csv` call counts; check the provider docs; switch provider via config if needed |
| Authentication error | API key revoked or expired | Create a new key; update the secret |
| Validation failure | Bad bar from the provider, or a split/dividend not yet recorded | Inspect the revision log in the private repo; rerun after the provider corrects it |
| Repeated `fallback` fits | Model instability (e.g. extreme market day) | Check the parameters log; if it persists, release a new model version |
| Workflow shows "disabled" | 60-day inactivity rule | Re-enable in the Actions tab; investigate why the job stopped |
| Private-repo push fails | Deploy key removed or repo renamed | Re-add the deploy key; the job refuses to publish forecasts until this works |
| A holiday is missing from the calendar | Calendar package not yet updated | Mark the date `no_session`; upgrade `exchange_calendars` |
| Forecast committed after the open | Run delayed | Nothing to fix; it is marked `on_time = false` and excluded |
| Run stopped by the budget guard | Retries or manual runs used up the day's calls | Wait for tomorrow; check `runs.csv` for what used the calls |
| Proof stamping failed | OpenTimestamps calendar servers unreachable | Nothing urgent; the next run stamps the missed file (with a later time) |
| Proof still pending after 7 days | `proofs.yml` failing or not running | Run `proofs.yml` manually; check its log |
| Privacy check stopped a publish | A code change added a raw column to public output | Fix the code; never bypass the check |
| Key needs replacing | Key leaked, revoked or expired | Follow the key-rotation steps in §18 |

---

## 15. Public site
Static GitHub Pages site rebuilt each evening:
- **Today:** next-session forecasts for each ETF (annualized volatility and 95/99% VaR), the model consensus, and for SPY the VIX comparison. Models still warming up are listed with how much history they still need.
- **Scoreboard:** live-record QLIKE and skill vs EWMA per model with bootstrap intervals and sample sizes, MCS membership, violation rates with exact binomial, Kupiec and Christoffersen p-values, and the traffic-light colour. "Low power" and "early" labels per §12.
- **Charts:** forecast vs realized proxy over time (derived values only), rolling QLIKE, VaR violations timeline.
- **Backtest:** the same tables for the historical period, clearly labeled as a backtest.
- **Status:** "last updated" time, the staleness banner (§14), and counts of late forecasts, fit fallbacks and missed sessions.
- **Methodology** page, data sources with citations (FRED/Cboe for the VIX), and a visible disclaimer: research project, not investment advice.
- **Design:** plain and editorial; no gradients or decorative animation. Colors are never the only signal (the traffic light also shows the word "green", "yellow" or "red").
- **Data shown:** derived values only; the privacy-of-data test (§16) checks the built site.

---

## 16. Testing strategy
| Type | What |
|---|---|
| Known values | Each estimator on a hand-computed OHLC example |
| Property (Hypothesis) | Estimators are non-negative where theory says so; scaling all prices by a constant leaves them unchanged; RS is unchanged by adding a constant drift |
| Simulation | Simulate geometric Brownian motion with known σ (finely sampled, then aggregated to OHLC): Parkinson/GK/RS means close to σ² within tolerance (they run slightly low with discrete sampling, which the test allows for); simulate GARCH(1,1) and check `arch` recovers parameters; under a correctly specified model, Kupiec and the exact binomial test reject about 5% of the time at the 5% level; with two equally good forecasts, the DM test rejects about 5% of the time; the bootstrap intervals cover the true mean loss about 95% of the time |
| Leak test | Truncated-data forecasts equal full-data forecasts for sampled dates |
| Calendar | Holidays, early closes, DST changes, an unscheduled closure |
| Providers | Recorded synthetic responses, including rate-limit and premium-endpoint messages |
| Store | Append-only: writing an existing `forecast_id` fails; schema validation on every CSV |
| Golden outputs | Every model's forecasts on a fixed synthetic dataset are stored; any change in output (from code or a dependency upgrade) fails the test and forces a model version bump (§11) |
| Short-history GARCH | With the prior strength at zero, `garch_t_sh` matches `arch`'s maximum-likelihood estimates within tolerance; with a huge prior strength, estimates equal the prior centers; on simulated GARCH data, as the sample grows the estimates move from the prior toward the true values; the reparameterization always gives α, β ≥ 0, α + β < 1 and ν > 2 |
| Backtest config | The study/out-of-sample split dates are read from config, and a test fails if any tuning step reads out-of-sample data |
| Live-job logic | Session detection from a delayed run and from the backup run; `on_time` calculation around DST changes; idempotent reruns; gap recording when a day is missed; refusal to publish if the private-repo push fails |
| Budget guard | The call counter persists across runs; the job stops before 20 calls; retries count; a backup run after a partial failure fetches only the missing tickers; corporate actions are refreshed only on the week's first session (or on a large-return flag) |
| Proofs | Stamping is called with the right file hash (calendar servers mocked); a calendar outage still publishes and flags the run; `verify-proofs` fails if any forecast row is missing from a stamped file |
| Privacy of data | The same scanner runs in two places: in CI against outputs generated from fixtures (catches code changes that add a raw column), and as the pre-publish step of `daily.yml` and `backtest.yml` against the real files about to be committed (§14). Either one fails on any raw price, volume or return column |
| Workflows | `actionlint` checks every workflow file |
| End-to-end | `run-daily` on recorded data for a short date range produces the expected files |

All tests run on synthetic or recorded data: no network, no API key in CI.

---

## 17. CI/CD and automation

| Workflow | Trigger | Steps | Permissions |
|---|---|---|---|
| `ci.yml` | Pushes to `main` and pull requests into `main` (never the bot's `forecasts` branch) | `uv sync --frozen` → ruff → ruff format check → mypy → pytest (fast) → privacy scan on fixture outputs → actionlint → `pip-audit` | read-only |
| `nightly.yml` | Daily schedule (off-peak minute) | Slow simulation tests | read-only |
| `daily.yml` | 23:37 and 03:37 UTC Mon–Fri, plus manual trigger | `run-daily`; push raw data to the private repo; stamp, privacy-check and commit public outputs to `forecasts`; deploy the site; ping heartbeat | Job 1: `contents: write`; deploy job: `pages: write`, `id-token: write`; secrets per §18 |
| `proofs.yml` | Weekly (off-peak minute), plus manual trigger | `ots upgrade` on pending proofs; commit completed proofs to `forecasts`; `verify-proofs` | `contents: write`; no secrets |
| `backtest.yml` | Manual trigger only | Run the backtest on the private snapshot; privacy check; commit results to `forecasts` | `contents: write`; read-only data-repo key |
| `pages.yml` | Manual trigger (e.g. after a site-code change on `main`) | Build and deploy the site from `main` + `forecasts` | `pages: write`, `id-token: write` |

**Why the deploy lives inside `daily.yml`:** commits pushed with the workflow's built-in `GITHUB_TOKEN` deliberately don't start new workflow runs (GitHub's protection against loops). A separate "deploy on push" workflow would never fire for the bot's commits, so the evening job deploys the site itself. The same rule means the bot's commits to `forecasts` never trigger `ci.yml`.

Common settings: one `concurrency` group shared by `daily.yml`, `proofs.yml` and `backtest.yml` (all write to `forecasts`); `timeout-minutes` on every job (e.g. 20); runners pinned to a specific image (`ubuntu-24.04`, not `ubuntu-latest`) and Python pinned through `uv`, so numerical results don't drift when GitHub updates its default image; third-party actions pinned to full commit SHAs; Dependabot for both Python packages and GitHub Actions.

**Numerical tolerance:** optimizer-based models (GARCH, `garch_t_sh`) can differ in the last digits between machines. Golden-output tests use a relative tolerance (e.g. 1e-6) for those models and exact equality for closed-form ones (baselines, estimators).

**Dependency upgrades:** Dependabot PRs must pass the golden-output tests. If an upgrade changes any model's outputs, merge it only together with a new model version (§11), so live records never silently mix behaviors.

**Releases:** SemVer tags for the code (`v0.1.0`), with CHANGELOG entries. Model versions are tracked separately in the forecasts themselves.

---

## 18. Security
- **Secrets:** stored only in GitHub Secrets and never printed. Each workflow gets only the ones it needs:

  | Secret | Used for | Used by |
  |---|---|---|
  | `ALPHAVANTAGE_API_KEY` | Daily prices, dividends, splits | `daily.yml` |
  | `FRED_API_KEY` | VIX (daily) and the one-time S&P 500 snapshot | `daily.yml` |
  | `DATA_REPO_DEPLOY_KEY` | Write access to the private `volforecast-data` repo only | `daily.yml` |
  | `DATA_REPO_READ_KEY` | Read-only access to the private data repo | `backtest.yml` |
  | `HEARTBEAT_URL` | Success ping to the heartbeat monitor | `daily.yml` |

  The bot's built-in `GITHUB_TOKEN` can't push to `main` (it requires a pull request), so even a compromised workflow step can't change the code.

- **Key rotation:** create the new key, update `.env` and the GitHub secret, run `volforecast smoke`, then revoke the old key. The runbook lists these steps.
- **Local secrets:** on your computer the keys live in `.env` next to this plan (already created, with the Alpha Vantage and FRED keys filled in). `.gitignore` excludes `.env`, so it never reaches the public repo. This plan names the variable (`ALPHAVANTAGE_API_KEY`) but never holds the key itself, because BUILD_PLAN.md is committed to the public repo (§8). The key has also appeared in a chat transcript: Alpha Vantage keys are free, so claiming a fresh one before going public is cheap insurance.
- **Least privilege:** each workflow declares its permissions explicitly; nothing gets write access it doesn't need. The deploy key can write only to the private data repo.
- **Supply chain:** third-party actions pinned to commit SHAs; `pip-audit` in CI; Dependabot.
- **Forks:** GitHub turns off scheduled workflows on forks by default, and secrets are not given to workflows triggered by pull requests from forks, so contributors can't trigger the daily job or see keys. `CONTRIBUTING.md` explains that forks need their own API keys and must follow the provider's terms.
- **No `pull_request_target`** workflows (they run with secrets on untrusted code).
- **Site:** static files only; no forms, cookies or tracking.

---

## 19. Milestones
Weeks are milestones of roughly 10–15 hours. **Start the live record as early as possible**: every week of live forecasting is evidence that can't be produced later.

### Week 0 — Setup
- [ ] Name check: search GitHub and PyPI; pick the project and package names (`volforecast` is a placeholder).
- [ ] Public repo (MIT license), private `volforecast-data` repo, deploy key.
- [ ] Branch protection: `main` requires a PR with passing CI; create the `forecasts` branch with force-push and deletion blocked (no PR requirement, so the bot can push).
- [x] Alpha Vantage and FRED keys saved in the local `.env` (git-ignored). Add both, plus the two data-repo keys and the heartbeat URL, as GitHub Secrets (§18).
- [ ] `uv` project, ruff, mypy, pytest, pre-commit; `ci.yml` green.
- [ ] Optional: email Alpha Vantage about the open-source/educational access and publishing derived statistics (Q2, Q3).
- [ ] Turn on failure notifications; create the heartbeat monitor.
- **Done when:** CI is green and both repos exist.

### Week 1 — Data
- [ ] Provider protocol, Alpha Vantage client (daily, dividends, splits, JSON message detection), FRED client.
- [ ] Spike: confirm response formats, dividend ex-date alignment, splits format (Q1). Start with `volforecast smoke` using the keys in `.env`.
- [ ] API budget guard (stop before 20 Alpha Vantage calls per day).
- [ ] One-time `SP500` and `VIXCLS` snapshots into the private data repo; write the study/out-of-sample split dates into config before looking at any results.
- [ ] Calendar module with early closes and the "latest completed session" logic.
- [ ] Validation rules; private-repo storage with revision log.
- [ ] Estimators with known-value, property and simulation tests.
- **Done when:** five tickers' data are fetched, validated and stored privately; estimator tests pass.

### Week 2 — Baselines live
- [ ] Baselines (`rw`, `hist21`, `yz21`, `ewma_094`, `vix`), VaR for normal distributions, scoring.
- [ ] Append-only store, `run-daily`, `daily.yml` (writing to `forecasts`), heartbeat ping, `health`.
- [ ] OpenTimestamps stamping in `run-daily`, `proofs.yml` for weekly upgrades, `verify-proofs`.
- [ ] **Go live with baselines** using the 100 sessions from the first compact fetch.
- **Done when:** baseline forecasts have been committed before the open on 3 consecutive sessions.

### Week 3 — Statistical models
- [ ] `garch_t_sh` first (penalized likelihood with `scipy.optimize`, reparameterization, variance targeting, its tests), so a GARCH model goes live right away.
- [ ] Plain GARCH family via `arch`, HAR, ensemble, `vix_mz`; Student-t and FHS VaR; minimum-history switching; fit fallback; model versions; golden-output tests.
- **Done when:** `garch_t_sh` forecasts live, and the later models are listed as waiting, with the sessions they still need.

### Week 4 — Backtest and evaluation
- [ ] Walk-forward S&P 500 index backtest with the same functions; leak test; `backtest.yml` with the read-only data-repo key.
- [ ] Tune the `garch_t_sh` prior strength on the study period only, freeze it, then score the out-of-sample period once.
- [ ] Short-history experiment (§13).
- [ ] DM, MCS, MZ, Kupiec, Christoffersen, traffic light, robustness checks.
- **Done when:** the backtest and short-history reports are generated from one command.

### Week 5 — Site and docs
- [ ] Site (today, scoreboard, charts, backtest, methodology), staleness banner, disclaimer.
- [ ] README, METHODOLOGY, LIMITATIONS, RUNBOOK, CONTRIBUTING, SECURITY.
- [ ] Privacy-of-data test; licensing review of everything public.
- [ ] Tag `v0.1.0`.
- **Done when:** §21 is satisfied.

### Week 6 — Buffer
Whatever slipped. If nothing did: write a short post about why volatility is forecastable but returns aren't, using your own backtest.

**Fallback checkpoint:** at the end of week 2 the project is already live and honest (baselines with a public track record). If time runs short, ship that with a clear README and add models later.

**README outline:** what it forecasts and why volatility is predictable; live scoreboard link; how the forecasts are protected from hindsight (the append-only `forecasts` branch, plus how to verify a day's OpenTimestamps proof yourself); models and baselines; results (live first, backtest second); data sources and licensing; how to run your own copy; limitations; disclaimer.

---

## 20. Risks
| Risk | Mitigation |
|---|---|
| Free tier gives only 100 sessions, so long ETF history isn't available | Default path (§6): live start with baselines and `garch_t_sh`, daily accumulation, S&P 500 index backtest; models switch on automatically |
| The index backtest is mistaken for an SPY result | Labeled on every table and chart; reported after the live record |
| Short-history priors quietly drive results | Prior centers fixed in advance and documented; prior strength tuned only on the study period; the short-history experiment shows how much the prior matters |
| FRED's 10-year window moves or the series changes | One-time snapshot in the private repo; the backtest never re-downloads |
| Free data tier changes or endpoint becomes premium | Pluggable providers; week-1 spike; response-body checks for premium/rate-limit messages |
| Provider terms don't allow public derived results | Apply for open-source access; publish derived values only (no returns, no prices); written confirmation |
| Published returns let others rebuild prices | Returns are never published (§9) |
| Scheduled job silently stops | Health check; failure notifications |
| Model fitting fails to converge | Fall back to last good parameters; flag |
| VIX not yet published at run time | Use latest available value and record its date |
| Run delayed past the next open | Backup run; forecasts marked `on_time = false` are excluded |
| Timing evidence disappears (GitHub logs kept at most 90 days) | OpenTimestamps proofs committed with each day's file (§14) |
| Someone (even you) rewrites past forecasts | `forecasts` branch blocks force-push and deletion; daily proofs would no longer match |
| Retries burn the daily API allowance | Persistent budget guard stops before 20 calls |
| Scheduled run dropped under GitHub load, or workflow turned off after 60 days | Backup run, heartbeat alert, staleness banner |
| Accumulated private history lost | Stored in a private Git repo, not only the Actions cache; job refuses to publish if the push fails |
| Dependency upgrade silently changes model behavior | Golden-output tests; model version bump required |
| Name clashes with an existing project | Name check in week 0 |
| Small live sample makes results look better or worse than they are | Reporting rules and minimum-sample labels (§12) |

---

## 21. Definition of done
- [ ] Live forecasts committed on time on 10 consecutive sessions, with the heartbeat confirming each run.
- [ ] At least one day's OpenTimestamps proof is complete (upgraded) and verifies; `verify-proofs` passes for every published day.
- [ ] `forecasts` branch protection confirmed by trying a force-push (it must be rejected).
- [ ] `volforecast smoke` passes with the real keys.
- [ ] Every FR and NFR in §2 has its test in CI (traceability table, §2).
- [ ] Backtest and live scoreboards published, live first, each with sample sizes.
- [ ] Privacy-of-data test passes; no prices or returns anywhere public; data sources cited.
- [ ] Written answer from Alpha Vantage on publishing derived statistics is saved (or, if none arrives, the README says so and the project stays within the published terms).
- [ ] `RUNBOOK.md` tested: one forced failure handled by following it.
- [ ] README, METHODOLOGY, LIMITATIONS, RUNBOOK, CONTRIBUTING, SECURITY written.
- [ ] `pip-audit` clean; actions pinned; permissions reviewed.
- [ ] `v0.1.0` tagged with release notes.

---

## 22. Open questions
| # | Question | When |
|---|---|---|
| Q1 | Answered: `TIME_SERIES_DAILY` compact is free; full history and the adjusted series are premium; `DIVIDENDS` and `SPLITS` are free. Still to confirm in the week-1 spike: exact response formats and how dividend dates line up with ex-dates. | Week 1 |
| Q2 | Does Alpha Vantage confirm that publishing derived statistics (forecasts, variance proxies, violation flags) from a non-commercial open-source project is fine? | Week 0 (email) |
| Q3 | Optional: is Alpha Vantage's open-source/educational access granted, and does it include `outputsize=full`? Not required; the default path (§6) assumes no. | Any time |
| Q4 | Answered: the default path (§6) is used, so no full-history access or paid data is needed. | — |
| Q5 | Final project and package name | Week 0 |
| Q6 | Which heartbeat service, and do its free-tier terms fit? | Week 0 |
| Q7 | Does the site's Plotly bundle need anything beyond a static GitHub Pages deploy? (Expected: no) | Week 5 |

---

## 23. Verification notes
| Fact | Source |
|---|---|
| Twelve Data free plan is internal, non-display, non-production only | twelvedata.com/pricing |
| Alpha Vantage: 25 requests/day free; unlimited for verified open-source/educational projects | alphavantage.co/support |
| Alpha Vantage: `TIME_SERIES_DAILY` free with `outputsize=compact` (latest 100 points); `outputsize=full` requires premium; `TIME_SERIES_DAILY_ADJUSTED` is premium; `DIVIDENDS` and `SPLITS` endpoints not marked premium | alphavantage.co/documentation |
| Alpha Vantage terms: personal, non-commercial use; commercial use includes giving others access | alphavantage.co/terms_of_service |
| GitHub disables scheduled workflows in public repos after 60 days without activity | docs.github.com (disable and enable workflows) |
| `arch` provides GARCH/GJR/EGARCH, HAR, Student-t, EWMA variance, and SPA/StepM/MCS | github.com/bashtage/arch, arch.readthedocs.io |
| VIXCLS on FRED is Cboe-copyrighted; citation required | fred.stlouisfed.org/series/VIXCLS |
| Stooq requires a CAPTCHA-issued API key since early 2026 | pandas-datareader issue #1012 |
| GitHub Actions cache: entries unused for 7 days are removed; 10 GB default per repository, least-recently-used eviction | `@actions/cache` README; GitHub changelog (2025-11-20) |
| Scheduled runs can be delayed under heavy load (worst at the top of the hour) and some queued jobs can be dropped; schedules run only on the default branch | GitHub docs as quoted in Flutter's infra docs and itime.day; GitHub community discussion |
| Scheduled workflows are off by default on public forks | docs.github.com (disable and enable workflows) |
| Workflow logs and artifacts are kept 90 days by default, and at most 90 days on public repositories | docs.github.com (retention period for GitHub Actions artifacts and logs) |
| OpenTimestamps: free public calendar servers, no account or API key; proofs start pending and are completed with `ots upgrade`; full verification with the official client needs a Bitcoin node | opentimestamps.org; PyPI `opentimestamps-client` |
| Rulesets and branch protection can block force-pushes and deletions, with bypass lists | docs.github.com (about rulesets) |
| Events triggered by the built-in `GITHUB_TOKEN` (except `workflow_dispatch` and `repository_dispatch`) don't create new workflow runs | docs.github.com (automatic token authentication); stated from GitHub's documented behavior, confirm when writing the workflows |
| `arch` includes `StationaryBootstrap` and the MCS/SPA/StepM procedures | arch.readthedocs.io (bootstrap and multiple comparison) |
| FRED `SP500`: daily closing values limited to 10 years under FRED's agreement with S&P Dow Jones Indices; price index without dividends; reproduction prohibited without S&P DJI permission; suggested citation provided | fred.stlouisfed.org (SP500 series notes) |

**Not yet verified (handled by spikes or tests in the plan):** exact Alpha Vantage response formats and dividend date alignment (week 1); whether the open-source access includes full history (Q3); FRED's daily update time for `VIXCLS` (handled by the latest-available rule in §14); the heartbeat service's free-tier terms (Q6).

### Change notes
- **v1.1 (pass 1, contradictions and gaps):** Alpha Vantage free-tier limits confirmed (100-session compact window; full history and the adjusted series are premium; dividends and splits are free), leading to the history bootstrap options, warm-up mode and our own dividend/split adjustment. Returns removed from public files (they would let anyone rebuild prices). Student-t quantile scaling, minimum-history table, fit fallback, model versioning, run-time session detection, backup run, concurrency, VIX timing, `on_time` flag.
- **v1.2 (pass 2, production readiness):** durable private data repo instead of the Actions cache; monitoring (heartbeat, staleness banner, 60-day rule); runbook; off-peak schedule minutes; workflow table with permissions; supply-chain and fork protections; golden-output tests and the dependency-upgrade rule; privacy-of-data test; detailed weekly milestones with a fallback checkpoint; fuller risks and definition of done.
- **v1.3 (pass 3, statistical correctness and traceability):** zero-mean convention; exact VIX conversion; HAR log-normal correction; ensemble membership; FHS spelled out; bootstrap intervals, DM details, Benjamini–Hochberg, log Mincer–Zarnowitz; exact binomial VaR test and Christoffersen edge cases; low-power labels; simulation checks for test size and interval coverage; NFR-8/9 and the traceability table.
- **Pass 4 (consistency check):** only naming and wording fixes (status names, decision records D10–D13, site section, open questions). No further material issues found, so the review loop stopped here.
- **v1.4 (no full history):** full ETF history is assumed unavailable, so the default path is now: live start on the free 100 sessions; `garch_t_sh` (penalized-likelihood GARCH for short samples) live from day one; a 10-year S&P 500 index backtest from FRED as a stand-in for SPY, with a fixed study/out-of-sample split; and a short-history experiment. The Alpha Vantage open-source request is now optional. The local `.env` and `.gitignore` were created (key stored there, not in this file). New decisions D14–D15, tests, risks and milestone tasks.
- **v1.5 (final production pass):** bot commits only to an append-only `forecasts` branch (force-push and deletion blocked) while `main` stays PR-protected; daily OpenTimestamps proofs, a weekly `proofs.yml` upgrade job and `verify-proofs`, because GitHub keeps logs at most 90 days on public repos; persistent API budget guard (stop before 20 calls/day); `smoke` command; explicit secrets table and key-rotation steps; runners pinned to `ubuntu-24.04` and numerical tolerances for optimizer-based golden tests; revision-after-use policy; FRED key added to `.env`. Review result: no remaining material gaps; remaining unknowns are the scheduled spikes and tests (§22).
- **v1.6 (full re-read):** privacy check moved to a pre-publish step in `daily.yml`/`backtest.yml` (the data lives on `forecasts`, which CI on `main` never sees), with the same scanner kept in CI on fixture outputs; site deploy moved inside `daily.yml`, because bot pushes with `GITHUB_TOKEN` don't start other workflows; `ci.yml` limited to `main` and PRs; new manual `backtest.yml` with a read-only data-repo key; secrets table now says which workflow uses each secret; backup runs fetch only missing tickers and corporate actions refresh weekly or on a large-return flag (keeps the busiest day at 15 calls); late dividends/splits handled as revisions; health check covers stale proofs; runbook rows for budget, proofs, privacy stops and key rotation; FR-3/FR-9, D6 and leftover wording updated.
