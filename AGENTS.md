# AGENTS.md

Notes for AI coding tools working in this repository. People should start with the
[README](README.md) and [CONTRIBUTING.md](CONTRIBUTING.md).

## AI tools are not contributors

This file gives AI tools the context to work here. It doesn't make them contributors. The project
is written and maintained by renjik1926-dev, and no AI tool is credited for the work.

- Don't add `Co-Authored-By:` trailers for an AI, "Generated with …" lines, or any other AI credit
  to commits, pull requests, code comments, the changelog or the site.
- Commit with the git identity that's already configured. Don't change `user.name` or
  `user.email`.

## What this is

Every trading evening the bot forecasts next-session volatility and 1-day VaR for SPY, QQQ, IWM,
TLT and GLD. It commits the forecasts before the open, scores them the next evening and publishes a
static scoreboard. It is not a trading bot. Never add buy/sell signals or anything that reads as
investment advice.

- [BUILD_PLAN.md](BUILD_PLAN.md) is the spec. Build on it rather than redesigning.
- [METHODOLOGY.md](METHODOLOGY.md) explains the models and tests, [RUNBOOK.md](RUNBOOK.md) covers
  operations, and [LIMITATIONS.md](LIMITATIONS.md) lists the known limits.

## Layout

- `src/volforecast/`: the package. `forecast.py` holds `forecast_next`, which backtest and live
  both call. Models are in `models/`, and settings and frozen dates are in `config.py`.
- `tests/`: `unit/`, `property/` (Hypothesis), `simulation/` (slow), and `fixtures/` (synthetic
  recorded responses and golden outputs).
- `scripts/`: `update_golden.py`, `demo_site.py` (a synthetic site in `demo/`), `make_fixtures.py`.
- `.github/workflows/`: `ci`, `nightly`, `daily` (the evening job and deploy), `proofs`,
  `backtest`, `pages`.
- Public outputs (`data/`) live on the `forecasts` branch, not `main`. Raw provider data lives in
  the private `volforecast-data` repository.

## Commands

```bash
uv sync
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest   # the same as CI
uv run pytest -m slow                                                                # simulation tests

# Offline run with recorded synthetic responses (no keys, no network)
uv run volforecast run-daily --recorded tests/fixtures --now 2026-10-16T23:37:00+00:00 --no-proofs
```

CI runs Python 3.12 (pinned by `.python-version` and `uv.lock`). A local venv may be newer, so
don't use features newer than 3.12.

## Rules

- **Raw data is never public.** No prices, volumes, returns, VIX levels or index levels in this
  repository or on the site. `uv run volforecast privacy-check` runs in CI and before every
  publish. Fix the code if it fails, and never bypass it.
- **Changed output means a new model version.** If a change could alter any model's output, bump
  that model's `version` and regenerate the golden file with `scripts/update_golden.py` in the same
  pull request.
- **The holdout stays honest.** `STUDY_END` and `OOS_START` in `config.py` don't move. Tune only on
  the study period (`volforecast backtest --tune`). The out-of-sample period was first scored on
  2026-10-19, so any model change after that date must also be listed in
  `config.POST_HOLDOUT_REVISIONS`, which the site uses to label those rows.
- **One code path.** Backtest and live both go through `forecast_next(history ≤ t)` and `score`.
  Don't give either one its own logic. The leak test must keep passing.
- **The record is append-only.** Only the bot commits to the `forecasts` branch. Never rewrite,
  force-push or edit past forecasts.
- **No real API calls without asking.** Tests use fixtures only. `smoke`, `fetch`, `run-daily`
  and `snapshot` with real keys use the small free Alpha Vantage and FRED quotas, so ask the owner
  first. The project stays at $0, so don't propose paid data.
- **Secrets stay in `.env`.** It is git-ignored. Never write keys into code, docs, logs or commits.
- **Branches and pull requests.** Work on a branch, open a pull request, and let `ci` pass. Commit
  messages are short imperative sentences ("Fix MCS crash on identical models"), and a model change
  starts with its new version (`vix_mz@2: ...`).
- **Plain, editorial site.** No gradients, glows, hover-lift cards or fade-in animations.
