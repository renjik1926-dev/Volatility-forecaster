"""Backtest loop, study-period discipline, CLI commands and site build (§13, §16)."""

from __future__ import annotations

import datetime as dt
import shutil
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from typer.testing import CliRunner

from tests.conftest import FIXTURES, utc
from volforecast.backtest import summarize_by_period, tune_prior_sessions, walk_forward
from volforecast.calendar import TradingCalendar
from volforecast.cli import app
from volforecast.estimators import close_only_frame
from volforecast.forecast import build_model_data
from volforecast.models import select
from volforecast.privacy import scan
from volforecast.site.build import build_site
from volforecast.store import PublicStore
from volforecast.synthetic import simulate


def index_closes(calendar: TradingCalendar, start: dt.date, end: dt.date, seed: int) -> pd.Series:
    bars, _ = simulate(calendar.sessions(start, end), seed=seed)
    return pd.Series(bars["close"].to_numpy(), index=pd.DatetimeIndex(bars["date"]))


def test_walk_forward_scores_every_forecast_with_values(calendar: TradingCalendar) -> None:
    closes = index_closes(calendar, dt.date(2021, 1, 4), dt.date(2022, 3, 31), seed=3)
    data = build_model_data(close_only_frame(closes))
    out = walk_forward(
        data, select(["rw", "ewma_094", "garch_t_sh"]), start_index=22, refit_every=21
    )
    with_values = [f for f in out.forecasts if f["variance"] is not None]
    assert len(out.scores) == len(with_values)
    assert {f["model"] for f in out.forecasts} == {"rw", "ewma_094", "garch_t_sh"}
    summary = summarize_by_period(out)
    assert set(summary["period"]) == {"study", "out_of_sample"}
    assert summary["label"].str.contains("stand-in for SPY").all()
    assert "r" not in out.scores[0]


def test_prior_tuning_never_reads_out_of_sample_data(calendar: TradingCalendar) -> None:
    closes = index_closes(calendar, dt.date(2020, 6, 1), dt.date(2022, 6, 30), seed=4)
    altered = closes.copy()
    oos = altered.index > pd.Timestamp("2021-12-31")
    altered[oos] = altered[oos] * np.exp(np.random.default_rng(9).normal(0, 0.05, oos.sum()))
    a = tune_prior_sessions(build_model_data(close_only_frame(closes)), grid=(0.0, 250.0))
    b = tune_prior_sessions(build_model_data(close_only_frame(altered)), grid=(0.0, 250.0))
    pd.testing.assert_frame_equal(a, b)
    assert (a["last_study_date"] <= "2021-12-31").all()


@pytest.fixture
def cli_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("VOLFORECAST_PUBLIC_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("VOLFORECAST_PRIVATE_DIR", str(tmp_path / "private"))
    monkeypatch.setenv("VOLFORECAST_SITE_DIR", str(tmp_path / "site"))
    monkeypatch.delenv("ALPHAVANTAGE_API_KEY", raising=False)
    shutil.copytree(FIXTURES / "alphavantage", tmp_path / "recorded" / "alphavantage")
    shutil.copytree(FIXTURES / "fred", tmp_path / "recorded" / "fred")
    return tmp_path


def test_cli_run_daily_on_recorded_responses(cli_env: Path) -> None:
    runner = CliRunner()
    out_file = cli_env / "gh_output.txt"
    result = runner.invoke(
        app,
        [
            "run-daily",
            "--recorded",
            str(cli_env / "recorded"),
            "--now",
            "2026-10-16T23:37:00+00:00",
            "--no-proofs",
            "--github-output",
            str(out_file),
        ],
    )
    assert result.exit_code == 0, result.output
    assert '"status": "ok"' in result.output
    assert "status=ok" in out_file.read_text() and "publish=true" in out_file.read_text()
    assert (cli_env / "site" / "index.html").is_file()
    assert scan([cli_env / "data", cli_env / "site"]) == []
    # Five daily series, plus dividends and splits for five tickers on this first run.
    budget = (cli_env / "private" / "budget.json").read_text()
    assert '"2026-10-16": 15' in budget

    again = runner.invoke(
        app,
        [
            "run-daily",
            "--recorded",
            str(cli_env / "recorded"),
            "--now",
            "2026-10-17T03:37:00+00:00",
            "--run-kind",
            "backup",
            "--no-proofs",
        ],
    )
    assert again.exit_code == 0 and '"status": "already_done"' in again.output

    proofs = runner.invoke(app, ["verify-proofs"])
    assert proofs.exit_code == 1  # stamping was off, so the proof file has no .ots
    assert runner.invoke(app, ["privacy-check", str(cli_env / "data")]).exit_code == 0


def test_cli_privacy_check_fails_on_raw_columns(cli_env: Path) -> None:
    (cli_env / "bad.csv").write_text("date,volume\n2026-10-16,1\n", encoding="utf-8")
    result = CliRunner().invoke(app, ["privacy-check", str(cli_env / "bad.csv")])
    assert result.exit_code == 2


def test_site_shows_stale_banner_and_empty_states(
    tmp_path: Path, calendar: TradingCalendar
) -> None:
    public = PublicStore(tmp_path / "data")
    build_site(public, tmp_path / "site", calendar, utc(2026, 10, 16, 23, 50))
    index = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    assert "No forecasts yet" in index and "Stale" in index
    assert "not investment advice" in index
    assert scan([tmp_path / "site"]) == []
