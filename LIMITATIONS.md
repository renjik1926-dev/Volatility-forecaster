# Limitations

- **Short live history.** The free data tier gives the latest 100 sessions only, so history builds up one day at a time. Until about 60 live sessions, loss comparisons are labeled "early". VaR tests stay "low power" until 10 violations are expected: 200 sessions at 95% and 1,000 at 99%.
- **The backtest is a stand-in.** It uses FRED's S&P 500 index: closing prices only, no dividends, about 10 years. It is not SPY and not the other four ETFs. The range-based baseline (`yz21`) can't run on it, and squared returns are a noisier proxy than the range-based one.
- **Daily data only.** "Realized" variance is a range-based proxy from daily OHLC, not intraday realized volatility.
- **Unadjusted prices plus our own adjustment.** Returns are adjusted with Alpha Vantage's dividend and split records. A record that arrives late is logged as a revision, and scores already published are not changed.
- **The VIX is not like-for-like.** It is a risk-neutral 30-day expectation for the S&P 500 index, usually above realized volatility. `vix_mz` recalibrates it. If day *t*'s VIX isn't published yet at run time, the latest earlier value is used and its date is recorded.
- **Optimizer-based models** (GARCH family, `garch_t_sh`) can differ in the last digits between machines. Golden tests use a relative tolerance for them.
- **Timing proofs need time.** An OpenTimestamps proof is "pending" until it is anchored in Bitcoin, usually within hours. Full independent verification with the official client needs a Bitcoin node.
- **Not investment advice.** Forecasts of volatility say nothing about direction.
