# Changelog

All notable changes to the code. Model versions are tracked separately, in the forecasts themselves.

## [Unreleased]

### Security
- Workflows no longer keep the forecasts-branch write token or the data-repo deploy key on disk while the project's code runs. Checkouts use `persist-credentials: false`, and each push step receives its credential for that push only: the deploy key through an `ssh-agent` with GitHub's pinned host keys, the token as a one-off header.
- The privacy scanner fails on any file type it can't read and that isn't known to be safe, so a new output format can't publish raw data unchecked.

### Fixed
- A malformed FRED observation raises a provider error instead of crashing the evening job with `KeyError` or `ValueError`.

### Added
- First draft of the full pipeline from `BUILD_PLAN.md` v1.6: Alpha Vantage and FRED providers with a persistent call budget; XNYS calendar; validation and revision log; range estimators; baselines, GARCH family, short-history GARCH (`garch_t_sh`), HAR, ensemble and VIX models; VaR (normal, Student-t, FHS); scoring; evaluation (bootstrap, DM + BH, MCS, MZ, binomial, Kupiec, Christoffersen, traffic light); walk-forward backtest, prior tuning and the short-history experiment; the evening job with OpenTimestamps proofs; privacy scanner; health check; static site; CLI; workflows.
