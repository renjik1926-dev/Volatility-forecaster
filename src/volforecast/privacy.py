"""Privacy-of-data scanner (§16, NFR-5, D7, D13).

Fails on any raw price, volume or return column in files that are about to be published. The
same scanner runs in CI on fixture outputs and as the pre-publish step of `daily.yml` and
`backtest.yml` on the real files.
"""

from __future__ import annotations

import csv
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

FORBIDDEN = frozenset(
    {
        "open",
        "high",
        "low",
        "close",
        "adj_close",
        "adjusted_close",
        "price",
        "prices",
        "volume",
        "return",
        "returns",
        "ret",
        "r",
        "log_return",
        "overnight",
        "overnight_return",
        "open_close",
        "vix_close",
        "vix_level",
        "sp500",
        "sp500_level",
        "dividend",
        "amount",
        "split_factor",
    }
)
FORBIDDEN_SUFFIXES = (".parquet", ".feather", ".pkl", ".pickle")
# Everything published must be a type this scanner reads (csv, json, html) or one known to hold no
# data. Any other type fails, so a new output format can't publish raw data unchecked: add it here
# once it's known to be safe.
SAFE_SUFFIXES = (".ots", ".md", ".css")  # timestamp proofs, docs, the site's stylesheet
SAFE_NAMES = frozenset({".gitkeep", ".nojekyll", "plotly.min.js"})
SAFE_ENDINGS = (".ots.bak",)  # `ots upgrade` keeps the previous proof as a backup
TH_RE = re.compile(r"<th[^>]*>(.*?)</th>", re.IGNORECASE | re.DOTALL)
TAG_RE = re.compile(r"<[^>]+>")
CHART_RE = re.compile(r"<script[^>]*data-volforecast-chart[^>]*>(.*?)</script>", re.DOTALL)


@dataclass(frozen=True)
class Finding:
    path: Path
    detail: str

    def __str__(self) -> str:
        return f"{self.path}: {self.detail}"


def normalize(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")


def _check_names(path: Path, names: Iterable[str], where: str) -> list[Finding]:
    return [
        Finding(path, f"forbidden {where} {name!r}")
        for name in names
        if normalize(name) in FORBIDDEN
    ]


def _json_keys(value: Any) -> Iterable[str]:
    if isinstance(value, dict):
        for k, v in value.items():
            yield str(k)
            yield from _json_keys(v)
    elif isinstance(value, list):
        for item in value:
            yield from _json_keys(item)


def scan_file(path: Path) -> list[Finding]:
    suffix = path.suffix.lower()
    if suffix in FORBIDDEN_SUFFIXES:
        return [Finding(path, "raw data file type must not be published")]
    if suffix == ".csv":
        with path.open(newline="", encoding="utf-8") as handle:
            header = next(csv.reader(handle), [])
        return _check_names(path, header, "column")
    if suffix == ".json":
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, UnicodeDecodeError):
            return [Finding(path, "unreadable JSON")]
        return _check_names(path, set(_json_keys(data)), "key")
    if suffix in (".html", ".htm"):
        text = path.read_text(encoding="utf-8")
        headers = [TAG_RE.sub("", h) for h in TH_RE.findall(text)]
        findings = _check_names(path, headers, "table header")
        # Charts declare the derived series they plot; raw series names are forbidden.
        for block in CHART_RE.findall(text):
            try:
                spec = json.loads(block)
            except ValueError:
                findings.append(Finding(path, "unreadable chart manifest"))
                continue
            findings += _check_names(path, spec.get("series", []), "chart series")
        return findings
    if suffix in SAFE_SUFFIXES or path.name in SAFE_NAMES or path.name.endswith(SAFE_ENDINGS):
        return []
    return [
        Finding(
            path,
            f"file type {suffix or path.name!r} isn't on the privacy allowlist "
            "(add it to privacy.py only if it can't hold raw data)",
        )
    ]


def scan(paths: Iterable[Path]) -> list[Finding]:
    findings: list[Finding] = []
    for root in paths:
        if root.is_file():
            findings += scan_file(root)
        elif root.is_dir():
            for path in sorted(root.rglob("*")):
                if path.is_file() and ".git" not in path.parts:
                    findings += scan_file(path)
    return findings
