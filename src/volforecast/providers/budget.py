"""Persistent daily call budget for Alpha Vantage (§14).

The counter lives in the private data repo, so it survives across runs (primary, backup and
manual). Retries count. The day is the New York calendar date, so the evening run and the
backup run share one count.
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Callable
from pathlib import Path
from zoneinfo import ZoneInfo

from volforecast.providers.base import BudgetExceededError

NEW_YORK = ZoneInfo("America/New_York")


def _new_york_today() -> dt.date:
    return dt.datetime.now(tz=NEW_YORK).date()


class CallBudget:
    def __init__(
        self, path: Path, limit: int, today: Callable[[], dt.date] = _new_york_today
    ) -> None:
        self.path = path
        self.limit = limit
        self._today = today

    def _load(self) -> dict[str, int]:
        if not self.path.is_file():
            return {}
        data = json.loads(self.path.read_text(encoding="utf-8"))
        return {str(k): int(v) for k, v in data.get("calls_by_day", {}).items()}

    def _save(self, counts: dict[str, int]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Keep the last 60 days so the file stays small but the history is still useful.
        recent = dict(sorted(counts.items())[-60:])
        self.path.write_text(
            json.dumps({"limit": self.limit, "calls_by_day": recent}, indent=2) + "\n",
            encoding="utf-8",
        )

    def used_today(self) -> int:
        return self._load().get(self._today().isoformat(), 0)

    def remaining(self) -> int:
        return max(self.limit - self.used_today(), 0)

    def charge(self, label: str = "") -> None:
        """Record one call before it is made; refuse once the limit is reached."""
        counts = self._load()
        key = self._today().isoformat()
        used = counts.get(key, 0)
        if used >= self.limit:
            raise BudgetExceededError(
                f"daily call budget reached ({used}/{self.limit}); not calling {label or 'API'}"
            )
        counts[key] = used + 1
        self._save(counts)
