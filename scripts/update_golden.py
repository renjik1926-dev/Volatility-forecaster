"""Regenerate tests/fixtures/golden/forecasts.json.

Run this only together with a model version bump (§11, §17): if a code or dependency change
alters any model's output, the golden test fails until the new outputs are accepted here.

Usage: python scripts/update_golden.py
"""

from __future__ import annotations

import json
from pathlib import Path

from volforecast.golden import golden_forecasts

OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "golden" / "forecasts.json"

if __name__ == "__main__":
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(golden_forecasts(), indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"wrote {OUT}")
