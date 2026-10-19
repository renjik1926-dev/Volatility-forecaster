"""The evening job end to end on synthetic data (§14, §16 live-job, budget, proofs, privacy)."""

from __future__ import annotations

import datetime as dt
import subprocess
from collections.abc import Sequence
from pathlib import Path

import pandas as pd
import pytest

from tests.conftest import World, utc
from volforecast.live import (
    ALREADY_DONE,
    BUDGET_EXHAUSTED,
    DATA_NOT_READY,
    OK,
    PARTIAL,
    LiveContext,
    RunResult,
    run_daily,
)
from volforecast.privacy import scan
from volforecast.proofs import Prover, verify_forecasts
from volforecast.site.build import build_site


class FakeOts:
    """Stands in for the `ots` command: no network, records what it was asked to stamp."""

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.stamped: list[Path] = []

    def __call__(self, args: Sequence[str]) -> subprocess.CompletedProcess[str]:
        cmd, path = args[1], Path(args[2])
        if cmd == "stamp":
            if self.fail:
                return subprocess.CompletedProcess(args, 1, "", "calendar servers unreachable")
            self.stamped.append(path)
            path.with_name(path.name + ".ots").write_bytes(b"fake-ots")
            return subprocess.CompletedProcess(args, 0, "", "")
        if cmd == "info":
            return subprocess.CompletedProcess(args, 0, "PendingAttestation", "")
        return subprocess.CompletedProcess(args, 0, "", "")


def evening(
    world: World,
    day: dt.date,
    *,
    at: dt.datetime | None = None,
    kind: str = "primary",
    ots: FakeOts | None = None,
) -> RunResult:
    world.clock[0] = at or utc(day.year, day.month, day.day, 23, 37)
    world.provider.available_through = day
    ctx = LiveContext(
        settings=world.settings,
        calendar=world.calendar,
        prices=world.provider,
        vix=world.provider,
        private=world.private,
        public=world.public,
        now=world.now,
        sleep=lambda s: None,
        run_kind=kind,
        code_version="test",
        prover=Prover(world.public.proofs_dir, runner=ots or FakeOts()),
    )
    return run_daily(ctx)


MON, TUE, WED, THU = (dt.date(2026, 10, d) for d in (12, 13, 14, 15))


def test_first_two_evenings(world: World) -> None:
    ots = FakeOts()
    first = evening(world, MON, ots=ots)
    assert first.status == OK, first.notes
    forecasts = world.public.forecasts.read()
    assert set(forecasts["ticker"]) == {"SPY", "QQQ", "IWM", "TLT", "GLD"}
    assert set(forecasts["target_date"]) == {TUE.isoformat()}
    assert (forecasts["on_time"] == "true").all()
    spy = forecasts[forecasts["ticker"] == "SPY"].set_index("model")
    assert spy.loc["garch_t_sh", "fit_status"] == "ok"
    assert spy.loc["garch_t", "fit_status"] == "warming_up"
    # The first run fetches VIX back to the start of the price history, so vix_mz has enough
    # past pairs to fit on day one.
    assert spy.loc["vix_mz", "fit_status"] == "ok", spy.loc["vix_mz", "notes"]
    assert "vix" in spy.index and "vix" not in set(forecasts[forecasts["ticker"] == "QQQ"]["model"])
    assert ots.stamped == [world.public.proofs_dir / "2026-10-12.csv"]

    second = evening(world, TUE, ots=ots)
    assert second.status == OK, second.notes
    scores = world.public.scores.read()
    scored = forecasts[forecasts["variance"] != ""]
    assert set(scores["forecast_id"]) == set(scored["forecast_id"])
    assert not any("gap" in n for n in second.notes)
    assert verify_forecasts(world.public.forecasts.read(), world.public.proofs_dir) == []


def test_rerun_is_idempotent(world: World) -> None:
    evening(world, MON)
    before = len(world.public.forecasts.read())
    again = evening(world, MON, at=utc(2026, 10, 13, 3, 37), kind="backup")
    assert again.status == ALREADY_DONE
    assert len(world.public.forecasts.read()) == before
    assert world.public.runs.read()["status"].tolist() == [OK, ALREADY_DONE]


def test_late_bar_primary_stops_backup_publishes_partial(world: World) -> None:
    evening(world, MON)
    world.provider.missing_tickers = {"TLT"}
    primary = evening(world, TUE)
    assert primary.status == DATA_NOT_READY
    backup = evening(world, TUE, at=utc(2026, 10, 14, 3, 37), kind="backup")
    assert backup.status == PARTIAL
    assert "TLT" not in backup.ready_tickers
    # The bar arrives; a manual run fetches only TLT and completes the session.
    world.provider.missing_tickers = set()
    world.provider.calls.clear()
    manual = evening(world, TUE, at=utc(2026, 10, 14, 4, 30), kind="manual")
    assert manual.status == OK, manual.notes
    assert [c for c in world.provider.calls if c.startswith("TIME_SERIES")] == [
        "TIME_SERIES_DAILY TLT"
    ]
    proof_files = sorted(p.name for p in world.public.proofs_dir.glob("2026-10-13*.csv"))
    assert proof_files == ["2026-10-13.2.csv", "2026-10-13.csv"]
    assert verify_forecasts(world.public.forecasts.read(), world.public.proofs_dir) == []


def test_corporate_actions_refresh_only_on_the_first_session_of_the_week(world: World) -> None:
    evening(world, MON)
    monday_calls = [c for c in world.provider.calls if c.startswith(("DIVIDENDS", "SPLITS"))]
    assert len(monday_calls) == 10
    world.provider.calls.clear()
    evening(world, TUE)
    assert not [c for c in world.provider.calls if c.startswith(("DIVIDENDS", "SPLITS"))]
    assert world.budget.used_today() <= 15


def test_first_run_mid_week_fetches_corporate_actions_once(world: World) -> None:
    evening(world, WED)  # the very first run, and not the first session of the week
    first = [c for c in world.provider.calls if c.startswith(("DIVIDENDS", "SPLITS"))]
    assert len(first) == 10
    world.provider.calls.clear()
    evening(world, THU)
    assert not [c for c in world.provider.calls if c.startswith(("DIVIDENDS", "SPLITS"))]


def test_budget_guard_stops_the_run(world: World) -> None:
    from volforecast.providers.budget import CallBudget

    world.provider.budget = CallBudget(world.private.budget_path, 3, today=lambda: MON)
    result = evening(world, MON)
    assert result.status == BUDGET_EXHAUSTED
    assert world.public.forecasts.read().empty


def test_late_forecasts_are_marked(world: World) -> None:
    evening(world, MON)
    # Tuesday's run is delayed until after Wednesday's open (13:30 UTC in October).
    late = evening(world, TUE, at=utc(2026, 10, 14, 14, 0), kind="manual")
    assert late.session == TUE
    rows = world.public.forecasts.read()
    assert (rows[rows["made_after_close_of"] == TUE.isoformat()]["on_time"] == "false").all()


def test_missed_day_is_recorded_as_a_gap(world: World) -> None:
    evening(world, MON)
    result = evening(world, WED)  # Tuesday's run never happened
    assert result.status == OK
    assert any(n.startswith("gap: no SPY forecasts targeted 2026-10-14") for n in result.notes)


def test_proof_outage_still_publishes_and_next_run_stamps_late(world: World) -> None:
    evening(world, MON, ots=FakeOts(fail=True))
    run = world.public.runs.read().iloc[-1]
    assert run["status"] == OK and "stamping failed" in run["notes"]
    ots = FakeOts()
    tuesday = evening(world, TUE, ots=ots)
    assert any("stamped 2026-10-12.csv late" in n for n in tuesday.notes)
    index = pd.read_csv(world.public.proofs_dir / "index.csv", dtype=str)
    assert index.set_index("file").loc["2026-10-12.csv", "late"] == "true"


def test_tampering_with_a_proof_file_is_detected(world: World) -> None:
    evening(world, MON)
    path = world.public.proofs_dir / "2026-10-12.csv"
    text = path.read_text(encoding="utf-8").replace(",ok,", ",fallback,", 1)
    path.write_text(text, encoding="utf-8")
    problems = verify_forecasts(world.public.forecasts.read(), world.public.proofs_dir)
    assert any("changed after stamping" in p for p in problems)


def test_public_outputs_and_site_pass_the_privacy_scan(world: World) -> None:
    for day in (MON, TUE, WED, THU):
        assert evening(world, day).status == OK
    build_site(world.public, world.settings.site_dir, world.calendar, utc(2026, 10, 15, 23, 50))
    assert scan([world.public.root, world.settings.site_dir]) == []
    html = (world.settings.site_dir / "index.html").read_text(encoding="utf-8")
    assert "2026-10-15" in html and "Stale" not in html


def test_privacy_scan_catches_a_raw_column(tmp_path: Path) -> None:
    (tmp_path / "leak.csv").write_text("date,close\n2026-10-16,100\n", encoding="utf-8")
    (tmp_path / "ok.csv").write_text("forecast_id,variance\nx,1e-4\n", encoding="utf-8")
    (tmp_path / "raw.parquet").write_bytes(b"")
    findings = {(f.path.name, f.detail) for f in scan([tmp_path])}
    assert ("leak.csv", "forbidden column 'close'") in findings
    assert any(name == "raw.parquet" for name, _ in findings)
    assert not any(name == "ok.csv" for name, _ in findings)


@pytest.mark.parametrize("kind", ["primary", "backup"])
def test_session_detection_never_uses_the_runner_date(world: World, kind: str) -> None:
    evening(world, MON)
    hour = (23, 37) if kind == "primary" else (3, 37)
    when = utc(2026, 10, 13, *hour) if kind == "primary" else utc(2026, 10, 14, *hour)
    result = evening(world, TUE, at=when, kind=kind)
    assert result.session == TUE
