"""`volforecast health` (§14 monitoring): exits non-zero on any problem."""

from __future__ import annotations

import datetime as dt
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from volforecast.calendar import TradingCalendar
from volforecast.proofs import Prover
from volforecast.providers.budget import CallBudget
from volforecast.store import PrivateStore, PublicStore

PROOF_DATE = re.compile(r"^(\d{4}-\d{2}-\d{2})")


@dataclass(frozen=True)
class Check:
    level: str  # ok | warning | problem
    name: str
    detail: str

    def __str__(self) -> str:
        return f"[{self.level.upper():7}] {self.name}: {self.detail}"


def _git(args: list[str], cwd: Path) -> str | None:
    try:
        out = subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True, timeout=30, check=True
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip()


def run_checks(
    public: PublicStore,
    private: PrivateStore,
    calendar: TradingCalendar,
    budget: CallBudget,
    now: dt.datetime,
    prover: Prover | None = None,
) -> list[Check]:
    checks: list[Check] = []
    completed = calendar.latest_completed_session(now)
    previous = calendar.previous_session(completed)

    runs = public.runs.read()
    ok_runs = runs[runs["status"] == "ok"] if not runs.empty else runs
    if ok_runs.empty:
        checks.append(Check("problem", "last successful run", "none recorded"))
    else:
        last = ok_runs.iloc[-1]
        day = dt.date.fromisoformat(last["session_date"])
        level = "ok" if day >= previous else "problem"
        checks.append(Check(level, "last successful run", f"{last['run_id']} for session {day}"))

    forecasts = public.forecasts.read()
    if forecasts.empty:
        checks.append(Check("problem", "last forecast", "no forecasts yet"))
    else:
        made = dt.date.fromisoformat(forecasts["made_after_close_of"].max())
        level = "ok" if made >= previous else "problem"
        checks.append(Check(level, "last forecast", f"made after the close of {made}"))

    for path in sorted(private.params_dir.glob("*.json")) if private.params_dir.is_dir() else []:
        state = private.params(path.stem)
        repeated = [k for k, v in state.items() if int(v.get("fallbacks", 0)) >= 2]
        if repeated:
            checks.append(
                Check(
                    "problem", f"fit fallbacks {path.stem}", f"2+ in a row: {', '.join(repeated)}"
                )
            )
    checks.append(
        Check("ok", "API calls today", f"{budget.used_today()} of {budget.limit} allowed")
    )

    if (private.root / ".git").exists():
        dirty = _git(["status", "--porcelain"], private.root)
        ahead = _git(["rev-list", "--count", "@{u}..HEAD"], private.root)
        if dirty:
            checks.append(Check("problem", "private repo", "uncommitted changes"))
        elif ahead not in (None, "0"):
            checks.append(Check("problem", "private repo", f"{ahead} commit(s) not pushed"))
        else:
            checks.append(Check("ok", "private repo", "clean and pushed"))
    else:
        checks.append(Check("warning", "private repo", f"{private.root} is not a git checkout"))

    if prover is not None and prover.dir.is_dir():
        cutoff = now.date() - dt.timedelta(days=7)
        stale = []
        for ots in sorted(prover.dir.glob("*.csv.ots")):
            match = PROOF_DATE.match(ots.name)
            if match and dt.date.fromisoformat(match.group(1)) < cutoff:
                status = prover.status(ots)
                if status != "complete":
                    stale.append(f"{ots.name} ({status})")
        if stale:
            checks.append(Check("problem", "proofs", f"not complete after 7 days: {stale[:5]}"))
        else:
            checks.append(Check("ok", "proofs", "no proof pending for more than 7 days"))
        missing = prover.unstamped()
        if missing:
            checks.append(
                Check("problem", "proofs", f"unstamped files: {[p.name for p in missing]}")
            )
    return checks
