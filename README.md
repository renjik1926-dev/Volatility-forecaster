# Volatility & Risk Forecaster

Every trading evening this bot forecasts **tomorrow's volatility** and **1-day Value-at-Risk (VaR)** for five ETFs (SPY, QQQ, IWM, TLT, GLD). It commits the forecasts before the market opens, scores them the next evening, and keeps a permanent public track record.

It is **not** a trading bot and not a price predictor. It never trades and never says "buy" or "sell". Research project, not investment advice.

> **Status:** first draft (v0.1.0.dev0). The code follows [`BUILD_PLAN.md`](BUILD_PLAN.md) (plan v1.6). Not deployed yet. See "What's left" below.

## Why volatility, not returns

Daily returns are close to unpredictable. Volatility is not: calm days follow calm days and turbulent days cluster. A forecaster can show honest, measurable skill there, so every model here is judged on whether it beats simple baselines, with the statistics to back it up.

## What it forecasts

For each ETF, after the close of day *t*:

- the variance (and annualized volatility) of the next session's close-to-close log return;
- VaR at 95% and 99%: the 1-day loss that should be exceeded on 5% or 1% of sessions.

For SPY it also compares against the VIX, the options market's own forecast.

## How forecasts are protected from hindsight

- **Append-only branch.** The bot commits only to a `forecasts` branch. Force-pushes and deletion are blocked, so nobody (including the author) can quietly rewrite a past forecast.
- **Timestamp proofs.** Each evening's forecast rows go into `data/proofs/{date}.csv`, stamped with [OpenTimestamps](https://opentimestamps.org). Only the file's hash leaves the machine. Once the proof is anchored in Bitcoin, anyone can check that the file existed before the market opened. `volforecast verify-proofs` checks that every published row appears in a stamped file.
- **Same code for backtest and live.** Both call `forecast_next(history ≤ t)` and `score(forecast, realized)`. A leak test checks that truncating the data after *t* never changes a forecast.
- **Late forecasts don't count.** A forecast committed after the target session's open is kept but marked `on_time = false` and excluded from the live record.

## Models

| Kind | Models |
|---|---|
| Baselines | `rw` (yesterday's proxy), `hist21`, `yz21` (Yang–Zhang), `ewma_094` (RiskMetrics) |
| GARCH family | `garch_n`, `garch_t` (+ `garch_t_fhs` filtered-historical-simulation VaR), `gjr_t`, `egarch_t` |
| Short history | `garch_t_sh`: GARCH(1,1)-t by penalized likelihood, shrunk toward typical parameters so it can forecast from ~100 sessions |
| Other | `har_log` (HAR on log proxies), `ensemble` (equal-weight average), `vix` and `vix_mz` (SPY only) |

Models switch on as their minimum history is met (22, 100, 150 or 500 sessions). The site lists the ones still waiting.

## Results

The **live record** is the headline: QLIKE loss with bootstrap intervals, skill vs EWMA, Diebold–Mariano tests with Benjamini–Hochberg adjustment, the Model Confidence Set, and VaR backtests (exact binomial, Kupiec, Christoffersen, Basel traffic light). Every number shows its sample size. "Early" and "low power" labels stay on until there's enough data.

The **backtest** comes second. It runs on about 10 years of the S&P 500 index from FRED, labeled everywhere as "S&P 500 index, closing prices, no dividends: a stand-in for SPY". It has a fixed study period (through 2021) and an out-of-sample period (2022 on) that is scored once, after every modeling choice is frozen.

Live scoreboard: https://renjik1926-dev.github.io/volatility-forecaster/ (goes live after the first successful evening run)

## Data and licensing

- Daily prices: [Alpha Vantage](https://www.alphavantage.co) free tier (personal, non-commercial use; see their [terms](https://www.alphavantage.co/terms_of_service/)).
- VIX: Chicago Board Options Exchange, CBOE Volatility Index: VIX [VIXCLS], retrieved from FRED, Federal Reserve Bank of St. Louis.
- S&P 500 (backtest only): S&P Dow Jones Indices LLC, S&P 500 [SP500], retrieved from FRED, Federal Reserve Bank of St. Louis.

Raw prices, volumes and returns are **never published**. They live in a private repository. This repo and the site contain only derived values: forecasts, variance proxies, losses, violation flags and test statistics. A privacy scanner (`volforecast privacy-check`) runs in CI and before every publish. The project is non-commercial. Forks must use their own API keys and follow the providers' terms.

## Run it yourself

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
cp .env.example .env    # then add your own ALPHAVANTAGE_API_KEY and FRED_API_KEY
uv run volforecast smoke          # at most 3 real API calls: checks keys and formats
uv run volforecast run-daily      # one evening's run against data-private/ and data/
uv run volforecast site           # build site/ from data/
uv run volforecast health
```

Offline, with recorded synthetic responses (no keys, no network):

```bash
uv run volforecast run-daily --recorded tests/fixtures --now 2026-10-16T23:37:00+00:00 --no-proofs
```

Other commands: `fetch`, `forecast`, `score`, `snapshot` (one-time FRED snapshot), `backtest` (`--tune` for the study period only), `verify-proofs`, `upgrade-proofs`, `privacy-check`.

Development:

```bash
uv run pytest            # fast tests
uv run pytest -m slow    # simulation tests (nightly)
uv run ruff check . && uv run ruff format --check . && uv run mypy
```

## Repository layout

- `src/volforecast/`: `calendar`, `providers/` (Alpha Vantage, FRED, recorded), `validate`, `estimators`, `models/`, `forecast` (the shared `forecast_next`), `var`, `scoring`, `evaluate`, `backtest`, `live`, `store`, `proofs`, `privacy`, `health`, `site/`, `cli`.
- `tests/`: `unit/`, `property/` (Hypothesis), `simulation/` (slow), `fixtures/` (synthetic recorded responses, golden outputs).
- `.github/workflows/`: `ci`, `nightly`, `daily` (the evening job and deploy), `proofs`, `backtest`, `pages`.
- `data/` lives on the `forecasts` branch, not `main`.

## What's left (from the build plan)

- **Week 0 setup:** pick the final name, create the repos, set up branch protection and secrets, set up the heartbeat.
- **Week 1 spike:** confirm Alpha Vantage response formats, dividend ex-date alignment and the meaning of `split_factor` (Q1).
- **Week 4:** tune the `garch_t_sh` prior strength on the study period only (`backtest --tune`), freeze it, then score the out-of-sample period once.
- **Docs:** add checked sources for the prior centers to METHODOLOGY.md.
- **Revisions:** the site should show how many scores used a bar that was later revised.

## Limitations

See [LIMITATIONS.md](LIMITATIONS.md).

## License

MIT. See [LICENSE](LICENSE).
