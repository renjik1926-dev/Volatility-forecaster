"""Build a demo site from synthetic data: N simulated evenings of the live job, then the site.

Useful while working on the site. Everything is synthetic; nothing touches a real provider.

Usage: python scripts/demo_site.py [out_dir] [evenings]
"""

from __future__ import annotations

import datetime as dt
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from volforecast.calendar import TradingCalendar
from volforecast.config import TICKERS, Settings
from volforecast.live import LiveContext, run_daily
from volforecast.proofs import Prover
from volforecast.providers.recorded import FrameProvider
from volforecast.site.build import build_site
from volforecast.store import PrivateStore, PublicStore
from volforecast.synthetic import simulate, simulate_vix


def fake_ots(args: Sequence[str]) -> subprocess.CompletedProcess[str]:
    path = Path(args[2])
    if args[1] == "stamp":
        path.with_name(path.name + ".ots").write_bytes(b"demo")
    return subprocess.CompletedProcess(list(args), 0, "PendingAttestation", "")


def main(out: Path, evenings: int) -> None:
    cal = TradingCalendar()
    sessions = cal.sessions(dt.date(2026, 1, 2), dt.date(2026, 10, 16))
    bars, vix = {}, None
    for k, ticker in enumerate(TICKERS):
        frame, true_h = simulate(sessions, seed=500 + k)
        bars[ticker] = frame
        if ticker == "SPY":
            vix = simulate_vix(frame, true_h, seed=600)
    provider = FrameProvider(bars, vix=vix)
    settings = Settings(
        public_dir=out / "data",
        private_dir=out / "private",
        site_dir=out / "site",
        data_wait_minutes=0.0,
    )
    public, private = PublicStore(settings.public_dir), PrivateStore(settings.private_dir)
    clock = [dt.datetime(2026, 1, 1, tzinfo=dt.UTC)]
    for day in sessions[-evenings:]:
        clock[0] = dt.datetime(day.year, day.month, day.day, 23, 37, tzinfo=dt.UTC)
        provider.available_through = day
        ctx = LiveContext(
            settings,
            cal,
            provider,
            provider,
            private,
            public,
            now=lambda: clock[0],
            sleep=lambda s: None,
            run_kind="primary",
            code_version="demo",
            prover=Prover(public.proofs_dir, runner=fake_ots),
        )
        result = run_daily(ctx)
        print(day, result.status, result.forecasts_written, result.scores_written)
    build_site(public, settings.site_dir, cal, clock[0] + dt.timedelta(minutes=10))
    print(f"site: {settings.site_dir / 'index.html'}")


if __name__ == "__main__":
    main(
        Path(sys.argv[1]) if len(sys.argv) > 1 else Path("demo"),
        int(sys.argv[2]) if len(sys.argv) > 2 else 40,
    )
