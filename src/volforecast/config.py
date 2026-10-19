"""Settings: tickers, windows, paths, budgets and the frozen backtest split (§6, §11, §13).

Secrets come from the environment. A local `.env` file is read if present, but values already
set in the environment win, so GitHub Secrets override anything on disk.
"""

from __future__ import annotations

import datetime as dt
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

TICKERS: tuple[str, ...] = ("SPY", "QQQ", "IWM", "TLT", "GLD")
VIX_TICKER = "SPY"  # the only ticker compared against the VIX (§1)
BACKTEST_TICKER = "SPX"  # label for the FRED S&P 500 index stand-in (§13)

TRADING_DAYS_PER_YEAR = 252
PROXY_FLOOR = 1e-10  # floor for zero proxies before QLIKE and logs (§10.6, §11)
MAX_ABS_RETURN = 0.25  # validation threshold for a single day's return (§10.3)

# Backtest split, written into config before any results are looked at (§13). Do not move.
STUDY_END = dt.date(2021, 12, 31)
OOS_START = dt.date(2022, 1, 1)

# Models changed after the out-of-sample period was first scored (2026-10-19). Their 2022-on
# backtest results are not a clean holdout, and the site labels them so. model -> new version.
POST_HOLDOUT_REVISIONS: dict[str, int] = {"vix_mz": 2, "har_log": 2, "ensemble": 2}


def _read_dotenv(path: Path) -> dict[str, str]:
    """Parse KEY=VALUE lines. Comments and blank lines are skipped; quotes are stripped."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _env(name: str, dotenv: dict[str, str], default: str | None = None) -> str | None:
    value = os.environ.get(name)
    if value is None or value == "":
        value = dotenv.get(name) or default
    return value


@dataclass(frozen=True)
class Settings:
    """Everything a run needs. Paths are resolved relative to the working directory."""

    public_dir: Path = Path("data")  # derived outputs; the `forecasts` branch checkout (§14)
    private_dir: Path = Path("data-private")  # the private `volforecast-data` checkout
    site_dir: Path = Path("site")
    tickers: tuple[str, ...] = TICKERS
    alphavantage_api_key: str | None = field(default=None, repr=False)
    fred_api_key: str | None = field(default=None, repr=False)
    heartbeat_url: str | None = field(default=None, repr=False)

    # Alpha Vantage free tier: 25/day. The guard allows at most `budget_limit` calls (§14).
    budget_limit: int = 20
    call_spacing_seconds: float = 13.0  # 5 requests/minute

    max_window: int = 1000  # rolling estimation window, sessions (§11)
    backtest_refit_every: int = 21  # backtest re-estimation interval, sessions (§11)
    data_wait_minutes: float = 30.0  # how long run-daily waits for a late bar (§14 step 2)

    def secret_status(self) -> dict[str, bool]:
        """Which secrets are present, without revealing any value."""
        return {
            "ALPHAVANTAGE_API_KEY": bool(self.alphavantage_api_key),
            "FRED_API_KEY": bool(self.fred_api_key),
            "HEARTBEAT_URL": bool(self.heartbeat_url),
        }


def load_settings(env_file: Path | None = Path(".env")) -> Settings:
    dotenv = _read_dotenv(env_file) if env_file else {}
    return Settings(
        public_dir=Path(_env("VOLFORECAST_PUBLIC_DIR", dotenv, "data") or "data"),
        private_dir=Path(_env("VOLFORECAST_PRIVATE_DIR", dotenv, "data-private") or "data-private"),
        site_dir=Path(_env("VOLFORECAST_SITE_DIR", dotenv, "site") or "site"),
        alphavantage_api_key=_env("ALPHAVANTAGE_API_KEY", dotenv),
        fred_api_key=_env("FRED_API_KEY", dotenv),
        heartbeat_url=_env("HEARTBEAT_URL", dotenv),
    )


def code_version() -> str:
    """The git commit SHA of the code that is running (NFR-2)."""
    sha = os.environ.get("GITHUB_SHA")
    if sha:
        return sha
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True, timeout=10
        )
        return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"
