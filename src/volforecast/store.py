"""Append-only public CSVs, input hashing and the private raw-data store (§9, §14).

Public files (`data/` on the `forecasts` branch) hold derived values only. The private store
(`volforecast-data`) holds raw OHLCV, VIX and index levels, corporate actions, the revision log,
model parameters and the API call budget.
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
import math
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

import pandas as pd

from volforecast.providers.base import (
    BAR_COLUMNS,
    empty_bars,
    empty_dividends,
    empty_splits,
)
from volforecast.validate import REVISION_COLUMNS

FORECAST_COLUMNS = [
    "forecast_id",
    "ticker",
    "made_after_close_of",
    "target_date",
    "model",
    "model_version",
    "fit_status",  # ok | fallback | warming_up | failed
    "variance",
    "vol_annualized",
    "var95",
    "var99",
    "dist",
    "code_version",
    "input_hash",
    "created_utc",
    "on_time",
    "notes",  # ensemble members, VIX date used, sessions still needed
]
SCORE_COLUMNS = [
    "forecast_id",
    "proxy_rs",
    "proxy_r2",
    "proxy_gk",
    "qlike",
    "mse",
    "viol95",
    "viol99",
    "early_close",
    "scored_utc",
]
RUN_COLUMNS = [
    "run_id",
    "started_utc",
    "finished_utc",
    "status",
    "session_date",
    "api_calls",
    "notes",
]
NUMERIC_FORECAST = ["model_version", "variance", "vol_annualized", "var95", "var99"]
NUMERIC_SCORE = ["proxy_rs", "proxy_r2", "proxy_gk", "qlike", "mse", "viol95", "viol99"]


class StoreError(RuntimeError):
    pass


def format_value(value: Any) -> str:
    """Stable text form: the same row always serializes to the same bytes (proofs, hashes)."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        if math.isnan(value):
            return ""
        return format(value, ".12g")
    if isinstance(value, int):
        return str(value)
    if isinstance(value, dt.datetime):
        return value.isoformat(timespec="seconds")
    if isinstance(value, dt.date):
        return value.isoformat()
    return str(value)


class AppendOnlyCSV:
    """A CSV that only ever grows. Appending an existing key is an error (§14 idempotency)."""

    def __init__(self, path: Path, columns: Sequence[str], key: str | None = None) -> None:
        self.path = path
        self.columns = list(columns)
        self.key = key

    def exists(self) -> bool:
        return self.path.is_file()

    def _check_header(self) -> None:
        with self.path.open(newline="", encoding="utf-8") as handle:
            header = next(csv.reader(handle), [])
        if header != self.columns:
            raise StoreError(f"{self.path.name}: header {header} != schema {self.columns}")

    def read(self) -> pd.DataFrame:
        if not self.exists():
            return pd.DataFrame(columns=self.columns, dtype=str)
        self._check_header()
        return pd.read_csv(self.path, dtype=str, keep_default_na=False)

    def keys(self) -> set[str]:
        if self.key is None:
            raise StoreError(f"{self.path.name} has no key column")
        return set(self.read()[self.key])

    def append(self, rows: Iterable[dict[str, Any]]) -> int:
        rows = list(rows)
        if not rows:
            return 0
        for row in rows:
            if set(row) != set(self.columns):
                missing = set(self.columns) - set(row)
                extra = set(row) - set(self.columns)
                raise StoreError(f"{self.path.name}: missing {missing}, unexpected {extra}")
        if self.key is not None:
            new_keys = [str(r[self.key]) for r in rows]
            if len(set(new_keys)) != len(new_keys):
                raise StoreError(f"{self.path.name}: duplicate {self.key} within the new rows")
            clash = set(new_keys) & (self.keys() if self.exists() else set())
            if clash:
                raise StoreError(
                    f"{self.path.name}: {self.key} already written: {sorted(clash)[:3]}"
                )
        new_file = not self.exists()
        if not new_file:
            self._check_header()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle, lineterminator="\n")
            if new_file:
                writer.writerow(self.columns)
            for row in rows:
                writer.writerow([format_value(row[c]) for c in self.columns])
        return len(rows)


def write_rows(path: Path, columns: Sequence[str], rows: Iterable[dict[str, Any]]) -> None:
    """Write a whole CSV (used for proof snapshots and backtest outputs, never for live files)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(list(columns))
        for row in rows:
            writer.writerow([format_value(row.get(c)) for c in columns])


class PublicStore:
    """`data/` on the `forecasts` branch."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.forecasts = AppendOnlyCSV(root / "forecasts.csv", FORECAST_COLUMNS, "forecast_id")
        self.scores = AppendOnlyCSV(root / "scores.csv", SCORE_COLUMNS, "forecast_id")
        self.runs = AppendOnlyCSV(root / "runs.csv", RUN_COLUMNS, "run_id")
        self.proofs_dir = root / "proofs"
        self.backtest_dir = root / "backtest"
        self.no_session_path = root / "no_session.csv"

    def no_session_dates(self) -> set[dt.date]:
        """Confirmed unscheduled closures (marked by hand per the runbook)."""
        if not self.no_session_path.is_file():
            return set()
        frame = pd.read_csv(self.no_session_path, dtype=str, keep_default_na=False)
        return {dt.date.fromisoformat(d) for d in frame.get("date", []) if d}


def read_numeric(frame: pd.DataFrame, columns: Iterable[str]) -> pd.DataFrame:
    out = frame.copy()
    for col in columns:
        if col in out:
            out[col] = pd.to_numeric(out[col].replace("", None), errors="coerce")
    return out


def input_hash(*frames: pd.DataFrame | pd.Series) -> str:
    """SHA-256 of the exact inputs a forecast used (NFR-2). Order and formatting are canonical."""
    digest = hashlib.sha256()
    for frame in frames:
        data = frame.to_frame() if isinstance(frame, pd.Series) else frame
        data = data.reset_index()
        digest.update(",".join(map(str, data.columns)).encode())
        for row in data.itertuples(index=False):
            digest.update(("\n" + ",".join(format_value(_plain(v)) for v in row)).encode())
        digest.update(b"\x00")
    return digest.hexdigest()


def _plain(value: Any) -> Any:
    if isinstance(value, pd.Timestamp):
        return value.date() if value == value.normalize() else value.to_pydatetime()
    if hasattr(value, "item"):
        return value.item()
    return value


class PrivateStore:
    """The private `volforecast-data` checkout. Never published (§6 licensing rules)."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.raw = root / "raw"
        self.actions_dir = root / "corporate_actions"
        self.params_dir = root / "params"
        self.revisions = AppendOnlyCSV(root / "revisions.csv", REVISION_COLUMNS)
        self.budget_path = root / "budget.json"

    # --- bars ------------------------------------------------------------------------------
    def bars(self, ticker: str) -> pd.DataFrame:
        path = self.raw / f"{ticker}.parquet"
        if not path.is_file():
            return empty_bars()
        frame = pd.read_parquet(path)
        frame["date"] = pd.to_datetime(frame["date"]).astype("datetime64[ns]")
        return frame[BAR_COLUMNS].sort_values("date").reset_index(drop=True)

    def save_bars(self, ticker: str, frame: pd.DataFrame) -> None:
        self.raw.mkdir(parents=True, exist_ok=True)
        frame[BAR_COLUMNS].to_parquet(self.raw / f"{ticker}.parquet", index=False)

    def has_bar(self, ticker: str, day: dt.date) -> bool:
        return bool((self.bars(ticker)["date"] == pd.Timestamp(day)).any())

    # --- series (VIX, S&P 500 snapshot) ------------------------------------------------------
    def series(self, name: str) -> pd.Series:
        path = self.raw / f"{name}.parquet"
        if not path.is_file():
            return pd.Series(dtype="float64", index=pd.DatetimeIndex([], name="date"))
        frame = pd.read_parquet(path)
        index = pd.DatetimeIndex(pd.to_datetime(frame["date"]), name="date").astype(
            "datetime64[ns]"
        )
        return pd.Series(frame["value"].to_numpy(), index=index, dtype="float64").sort_index()

    def save_series(self, name: str, values: pd.Series) -> None:
        self.raw.mkdir(parents=True, exist_ok=True)
        frame = pd.DataFrame({"date": values.index, "value": values.to_numpy()})
        frame.to_parquet(self.raw / f"{name}.parquet", index=False)

    def merge_series(self, name: str, values: pd.Series) -> pd.Series:
        stored = self.series(name)
        merged = values.combine_first(stored).sort_index()
        self.save_series(name, merged)
        return merged

    # --- corporate actions -----------------------------------------------------------------
    def dividends(self, ticker: str) -> pd.DataFrame:
        frame = self._actions(ticker)
        out = frame[frame["kind"] == "dividend"].rename(columns={"value": "amount"})
        return out[["date", "amount"]].reset_index(drop=True) if len(out) else empty_dividends()

    def splits(self, ticker: str) -> pd.DataFrame:
        frame = self._actions(ticker)
        out = frame[frame["kind"] == "split"].rename(columns={"value": "factor"})
        return out[["date", "factor"]].reset_index(drop=True) if len(out) else empty_splits()

    def _actions(self, ticker: str) -> pd.DataFrame:
        path = self.actions_dir / f"{ticker}.parquet"
        if not path.is_file():
            return pd.DataFrame(
                {
                    "date": pd.Series(dtype="datetime64[ns]"),
                    "kind": pd.Series(dtype="str"),
                    "value": pd.Series(dtype="float64"),
                }
            )
        frame = pd.read_parquet(path)
        frame["date"] = pd.to_datetime(frame["date"]).astype("datetime64[ns]")
        return frame

    def save_actions(
        self, ticker: str, dividends: pd.DataFrame, splits: pd.DataFrame, refreshed: dt.date
    ) -> None:
        self.actions_dir.mkdir(parents=True, exist_ok=True)
        frame = pd.concat(
            [
                pd.DataFrame(
                    {"date": dividends["date"], "kind": "dividend", "value": dividends["amount"]}
                ),
                pd.DataFrame({"date": splits["date"], "kind": "split", "value": splits["factor"]}),
            ],
            ignore_index=True,
        )
        frame.to_parquet(self.actions_dir / f"{ticker}.parquet", index=False)
        meta = self._refresh_log()
        meta[ticker] = refreshed.isoformat()
        (self.actions_dir / "refreshed.json").write_text(
            json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

    def _refresh_log(self) -> dict[str, str]:
        path = self.actions_dir / "refreshed.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}

    def actions_refreshed_on(self, ticker: str) -> dt.date | None:
        value = self._refresh_log().get(ticker)
        return dt.date.fromisoformat(value) if value else None

    # --- model parameters ------------------------------------------------------------------
    def params(self, ticker: str) -> dict[str, Any]:
        path = self.params_dir / f"{ticker}.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}

    def save_params(self, ticker: str, state: dict[str, Any]) -> None:
        self.params_dir.mkdir(parents=True, exist_ok=True)
        (self.params_dir / f"{ticker}.json").write_text(
            json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
