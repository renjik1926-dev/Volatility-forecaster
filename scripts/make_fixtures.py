"""Write the recorded API responses used by tests and `run-daily --recorded` (synthetic values).

The files mimic Alpha Vantage and FRED response bodies, so the real parsers and message checks
run on them. Values come from `volforecast.synthetic`, never from a provider.

Usage: python scripts/make_fixtures.py [out_dir]
"""

from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

from volforecast.calendar import TradingCalendar
from volforecast.config import TICKERS
from volforecast.synthetic import simulate, simulate_vix

LAST_SESSION = dt.date(2026, 10, 16)
SESSIONS = 100  # the free tier's compact window


def daily_payload(ticker: str, bars) -> dict:  # type: ignore[no-untyped-def]
    series = {}
    for row in bars.sort_values("date", ascending=False).itertuples(index=False):
        series[row.date.date().isoformat()] = {
            "1. open": f"{row.open:.4f}",
            "2. high": f"{row.high:.4f}",
            "3. low": f"{row.low:.4f}",
            "4. close": f"{row.close:.4f}",
            "5. volume": str(row.volume),
        }
    return {
        "Meta Data": {
            "1. Information": "Daily Prices (open, high, low, close) and Volumes",
            "2. Symbol": ticker,
            "3. Last Refreshed": LAST_SESSION.isoformat(),
            "4. Output Size": "Compact",
            "5. Time Zone": "US/Eastern",
        },
        "Time Series (Daily)": series,
    }


def main(out: Path) -> None:
    cal = TradingCalendar()
    sessions = cal.sessions(LAST_SESSION - dt.timedelta(days=200), LAST_SESSION)[-SESSIONS:]
    av, fred = out / "alphavantage", out / "fred"
    av.mkdir(parents=True, exist_ok=True)
    fred.mkdir(parents=True, exist_ok=True)
    for k, ticker in enumerate(TICKERS):
        bars, true_h = simulate(sessions, seed=1000 + k)
        (av / f"TIME_SERIES_DAILY_{ticker}.json").write_text(
            json.dumps(daily_payload(ticker, bars), indent=1), encoding="utf-8"
        )
        ex_date = sessions[40].isoformat()
        dividends = {
            "symbol": ticker,
            "data": [
                {
                    "ex_dividend_date": ex_date,
                    "declaration_date": "None",
                    "record_date": "None",
                    "payment_date": "None",
                    "amount": "0.25",
                }
            ],
        }
        (av / f"DIVIDENDS_{ticker}.json").write_text(
            json.dumps(dividends, indent=1), encoding="utf-8"
        )
        (av / f"SPLITS_{ticker}.json").write_text(
            json.dumps({"symbol": ticker, "data": []}, indent=1), encoding="utf-8"
        )
        if ticker == "SPY":
            vix = simulate_vix(bars, true_h, seed=77)
            obs = [{"date": d.date().isoformat(), "value": f"{v:.2f}"} for d, v in vix.items()]
            obs[-1]["value"] = "."  # FRED marks a missing value with "."
            (fred / "VIXCLS.json").write_text(
                json.dumps({"observations": obs}, indent=1), encoding="utf-8"
            )
    messages = {
        "RATE_LIMIT.json": {
            "Information": "Thank you for using Alpha Vantage! Our standard API rate limit is 25 "
            "requests per day. Please subscribe to any of the premium plans to remove limits."
        },
        "PREMIUM.json": {
            "Information": "Thank you for using Alpha Vantage! This is a premium endpoint. You may "
            "subscribe to any of the premium plans to instantly unlock all premium endpoints."
        },
        "ERROR.json": {
            "Error Message": "Invalid API call. Please retry or visit the documentation."
        },
        "BAD_KEY.json": {"Error Message": "the parameter apikey is invalid or missing."},
    }
    for name, body in messages.items():
        (av / name).write_text(json.dumps(body, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else Path("tests/fixtures"))
