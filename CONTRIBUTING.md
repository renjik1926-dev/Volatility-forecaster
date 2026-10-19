# Contributing

Thanks for your interest. A few rules keep the public record honest and the data licensing clean:

- **Never add raw data.** No prices, volumes, returns, VIX levels or index levels in this repo or on the site. The privacy scanner (`uv run volforecast privacy-check`) runs in CI and will fail a pull request that adds a raw column.
- **Model changes need a new version.** If a change could alter any model's output, bump that model's `version` and regenerate the golden file with `scripts/update_golden.py` in the same pull request.
- **Use your own keys.** Forks don't receive this repo's secrets, and scheduled workflows are off on forks by default. To run your own copy, get your own Alpha Vantage and FRED keys and follow their terms (non-commercial).
- **Before opening a PR:** `uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest`.

Tests use synthetic or recorded data only: no network, no API keys.
