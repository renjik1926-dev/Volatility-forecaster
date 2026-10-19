# forecasts branch

The public, append-only record written by the evening job (`daily.yml`). Only the bot commits here;
force-pushes and deletion are blocked. Code lives on `main`.

- `data/forecasts.csv`: every forecast, committed before the target session's open
- `data/scores.csv`: how each forecast did
- `data/runs.csv`: one row per job run
- `data/proofs/`: each evening's forecast rows with their OpenTimestamps proof
- `data/backtest/`: S&P 500 index backtest results (a stand-in for SPY)

Only derived values are published: no prices, volumes or returns.
