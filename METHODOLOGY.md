# Methodology

The formulas are in `BUILD_PLAN.md` §3 and §11. This page explains the choices and where they come from.

## Returns and the variance proxy

- **Returns.** Log returns from unadjusted prices plus Alpha Vantage's dividend and split records: rₜ = ln((Cₜ + Dₜ) / (Cₜ₋₁/sₜ)). The overnight return oₜ uses the open in place of the close.
- **Primary proxy.** v̂ₜ = oₜ² + σ²_RS,ₜ: the overnight jump plus the Rogers–Satchell intraday variance. Rogers–Satchell is unaffected by drift.
- **Robustness proxies.** rₜ² and oₜ² + σ²_GK,ₜ.
- **Primary loss.** QLIKE, L = v̂/h − ln(v̂/h) − 1. Rankings under QLIKE stay valid with a noisy proxy (Patton, 2011). Zero proxies are floored at 1e-10 and counted.

## Zero-mean convention

Every model assumes a zero daily mean. Daily means of these ETFs are tiny next to their volatility, and a common zero mean keeps every model's VaR comparable.

## Short-history GARCH (`garch_t_sh`)

Plain maximum-likelihood GARCH is unreliable with a few hundred observations; persistence in particular is badly estimated. `garch_t_sh` maximizes the log-likelihood plus a log-prior:

- **Variance targeting:** ω = σ̄²(1 − α − β), with σ̄² the mean squared return in the window.
- **Parameterization:** p = α + β, a = α/p, ν > 2. Normal priors on logit p, logit a and ln(ν − 2).
- **Prior centers:** p₀ = 0.97, α₀ = 0.08 (β₀ = 0.89), ν₀ = 7. These were fixed before any backtest.
- **Prior strength.** One number, n₀ (`prior_sessions`). Prior precision is n₀ times the per-observation Fisher information at the prior centers. That information is estimated by simulation in `scripts/unit_information.py` and hard-coded as `UNIT_INFORMATION`. At n = n₀ sessions, the data and the prior carry about equal information. n₀ = 250, tuned on the study period and frozen (below).
- **Checks (tests):** with n₀ = 0 the estimates match `arch`'s maximum likelihood; with a huge n₀ they equal the prior centers; as the sample grows they move to the truth.

### Prior strength tuning (study period only, frozen 2026-10-19)

`volforecast backtest --tune` ran a walk-forward on the S&P 500 index study period only (FRED `SP500`, 2016-10-17 to 2021-12-31; closing prices, no dividends; a stand-in for SPY). It used a 250-session window, re-estimated every 21 sessions, and scored 1,060 forecasts with QLIKE on squared returns. No out-of-sample data was read.

| n₀ | 0 (plain ML) | 50 | 100 | **250** | 500 | 1000 |
|---|---|---|---|---|---|---|
| Mean QLIKE | 1.636 | 1.601 | 1.595 | **1.591** | 1.591 | 1.594 |

The procedure picks the lowest mean QLIKE, so n₀ = 250 is frozen.

A follow-up sensitivity check used the same study data, starting later (810 forecasts):
- **100-session window:** n₀ = 100 was slightly better than 250, and both beat plain maximum likelihood.
- **250- and 500-session windows:** plain maximum likelihood was slightly better.
- **Significance:** none of the differences between 0, 100 and 250 were significant. Diebold–Mariano p-values for 250 vs 0 were 0.20, 0.88 and 0.79.

So shrinkage helps most when history is short, which is exactly when `garch_t_sh` matters, and fades as history grows. The value from the pre-specified procedure was kept rather than re-picked after seeing the sensitivity results.

**TODO before v0.1.0:** cite checked sources for the prior centers, i.e. published daily equity-index GARCH(1,1)-t estimates. Do not cite a paper for a specific number without checking it.

## Model versions

Each model has a version (`garch_t@1`). Any change that could alter outputs creates a new version, which starts its own live record. That includes formula, window or distribution changes, and dependency upgrades that change results. The golden-output test (`tests/fixtures/golden/forecasts.json`) catches such changes. Regenerate it with `scripts/update_golden.py` only together with a version bump.

## Evaluation

- **Losses:** mean QLIKE and MSE with stationary-bootstrap 95% intervals (fixed seed).
- **Skill vs EWMA:** with a bootstrap interval.
- **Diebold–Mariano:** one-sided, Newey–West variance with lag ⌊T^(1/3)⌋, Harvey–Leybourne–Newbold correction, t(T−1) reference.
- **Multiple testing:** Benjamini–Hochberg adjustment within each ticker.
- **Model Confidence Set:** 10% size.
- **Mincer–Zarnowitz:** on levels and logs, with HAC errors.
- **VaR:** exact binomial, Kupiec, Christoffersen independence and conditional coverage (0·ln 0 = 0; "not enough violations" below two), and the Basel traffic light for 99% VaR over 250 sessions.
- **"Best":** a model is called best only if it is in the MCS and beats EWMA after the adjustment.

## References

- Bollerslev (1986), generalized ARCH.
- Christoffersen (1998), evaluating interval forecasts.
- Corsi (2009), the HAR model.
- Diebold and Mariano (1995), comparing predictive accuracy.
- Garman and Klass (1980), Parkinson (1980), Rogers and Satchell (1991), Yang and Zhang (2000): range-based variance estimators.
- Glosten, Jagannathan and Runkle (1993), GJR-GARCH.
- Hansen, Lunde and Nason (2011), the Model Confidence Set.
- Harvey, Leybourne and Newbold (1997), the small-sample DM correction.
- Kupiec (1995), proportion-of-failures test.
- Nelson (1991), EGARCH.
- Patton (2011), robust loss functions for volatility forecasts.
- Politis and Romano (1994), the stationary bootstrap.
- J.P. Morgan/Reuters (1996), RiskMetrics technical document.
- Benjamini and Hochberg (1995), false discovery rate.
- Mincer and Zarnowitz (1969), forecast evaluation regressions.
