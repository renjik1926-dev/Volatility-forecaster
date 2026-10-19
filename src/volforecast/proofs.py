"""OpenTimestamps proofs for each evening's forecasts (§14, D17).

Each evening's new forecast rows are written to `data/proofs/{date}.csv`, and `ots stamp`
creates `{date}.csv.ots`. Only the file's hash goes to the public calendar servers. The weekly
`proofs.yml` job runs `ots upgrade` to complete proofs once they are anchored in Bitcoin.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from volforecast.store import FORECAST_COLUMNS, AppendOnlyCSV

Runner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]
INDEX_COLUMNS = ["file", "sha256", "stamped_utc", "late"]


def run_command(args: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(args), capture_output=True, text=True, timeout=120, check=False)


def snapshot_path(proofs_dir: Path, session: dt.date) -> Path:
    """`{date}.csv`, or `{date}.2.csv`, `{date}.3.csv` … if earlier files exist for the date."""
    path = proofs_dir / f"{session.isoformat()}.csv"
    k = 2
    while path.exists():
        path = proofs_dir / f"{session.isoformat()}.{k}.csv"
        k += 1
    return path


def proof_files(proofs_dir: Path, made: str) -> list[Path]:
    return sorted(proofs_dir.glob(f"{made}.csv")) + sorted(proofs_dir.glob(f"{made}.*.csv"))


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@dataclass(frozen=True)
class StampOutcome:
    file: Path
    ok: bool
    detail: str = ""


class Prover:
    def __init__(self, proofs_dir: Path, runner: Runner = run_command) -> None:
        self.dir = proofs_dir
        self.runner = runner
        self.index = AppendOnlyCSV(proofs_dir / "index.csv", INDEX_COLUMNS, "file")

    def stamp(self, path: Path, now_utc: dt.datetime, late: bool = False) -> StampOutcome:
        try:
            result = self.runner(["ots", "stamp", str(path)])
        except (OSError, subprocess.SubprocessError) as exc:
            return StampOutcome(path, False, f"ots not runnable ({type(exc).__name__})")
        ots = path.with_name(path.name + ".ots")
        if result.returncode != 0 or not ots.is_file():
            return StampOutcome(path, False, (result.stderr or result.stdout or "").strip()[:200])
        if path.name not in (self.index.keys() if self.index.exists() else set()):
            self.index.append(
                [
                    {
                        "file": path.name,
                        "sha256": sha256_file(path),
                        "stamped_utc": now_utc.isoformat(timespec="seconds"),
                        "late": late,
                    }
                ]
            )
        return StampOutcome(path, True)

    def unstamped(self) -> list[Path]:
        if not self.dir.is_dir():
            return []
        return sorted(
            p
            for p in self.dir.glob("*.csv")
            if p.name != "index.csv" and not p.with_name(p.name + ".ots").is_file()
        )

    def stamp_missing(self, now_utc: dt.datetime, today: Path | None = None) -> list[StampOutcome]:
        """Stamp files a previous run couldn't; those proofs show a later time (marked late)."""
        return [self.stamp(p, now_utc, late=p != today) for p in self.unstamped() if p != today]

    def status(self, ots: Path) -> str:
        """complete | pending | unknown (from `ots info`)."""
        try:
            result = self.runner(["ots", "info", str(ots)])
        except (OSError, subprocess.SubprocessError):
            return "unknown"
        text = result.stdout or ""
        if "BitcoinBlockHeaderAttestation" in text:
            return "complete"
        if "PendingAttestation" in text:
            return "pending"
        return "unknown"

    def upgrade_all(self) -> dict[str, str]:
        """Run `ots upgrade` on every proof that isn't complete yet; return each file's status."""
        out: dict[str, str] = {}
        for ots in sorted(self.dir.glob("*.csv.ots")):
            if self.status(ots) != "complete":
                with contextlib.suppress(OSError, subprocess.SubprocessError):
                    self.runner(["ots", "upgrade", str(ots)])
            out[ots.name] = self.status(ots)
        return out


def verify_forecasts(forecasts: pd.DataFrame, proofs_dir: Path) -> list[str]:
    """Every forecast row must appear, unchanged, in a stamped proof file (§14).

    Returns a list of problems; empty means every row is covered.
    """
    problems: list[str] = []
    if forecasts.empty:
        return problems
    index_path = proofs_dir / "index.csv"
    hashes: dict[str, str] = {}
    if index_path.is_file():
        idx = pd.read_csv(index_path, dtype=str, keep_default_na=False)
        hashes = dict(zip(idx["file"], idx["sha256"], strict=True))
    for made, group in forecasts.groupby("made_after_close_of"):
        files = proof_files(proofs_dir, str(made))
        if not files:
            problems.append(f"{made}: no proof file")
            continue
        snaps = []
        for path in files:
            if not path.with_name(path.name + ".ots").is_file():
                problems.append(f"{path.name}: proof file has no .ots timestamp")
            recorded = hashes.get(path.name)
            if recorded and recorded != sha256_file(path):
                problems.append(f"{path.name}: proof file changed after stamping")
            snap = pd.read_csv(path, dtype=str, keep_default_na=False)
            if list(snap.columns) != FORECAST_COLUMNS:
                problems.append(f"{path.name}: proof file has the wrong columns")
                continue
            snaps.append(snap)
        if not snaps:
            continue
        stamped = pd.concat(snaps).set_index("forecast_id")
        for row in group.itertuples(index=False):
            fid = row.forecast_id
            if fid not in stamped.index:
                problems.append(f"{fid}: not in any proof file for {made}")
                continue
            stored = stamped.loc[fid]
            for col in FORECAST_COLUMNS[1:]:
                if str(getattr(row, col)) != str(stored[col]):
                    problems.append(f"{fid}: {col} differs from the stamped file")
                    break
    return problems
