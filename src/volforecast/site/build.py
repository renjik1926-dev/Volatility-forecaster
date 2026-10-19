"""Static site builder (§15): Today, Scoreboard, Backtest, Methodology.

Reads only public, derived files (`data/`). Charts plot variances, proxies, VaR levels and
violation dates; never prices or returns. Every statistic is shown with its sample size, and
the "early" / "low power" labels follow the reporting rules in §12.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from jinja2 import Environment, PackageLoader, select_autoescape
from plotly.offline import get_plotlyjs

from volforecast.calendar import TradingCalendar
from volforecast.config import OOS_START, STUDY_END, TICKERS, TRADING_DAYS_PER_YEAR
from volforecast.evaluate import BASELINE, loss_table, var_table
from volforecast.store import (
    NUMERIC_FORECAST,
    NUMERIC_SCORE,
    PublicStore,
    read_numeric,
)

BACKTEST_LABEL = "S&P 500 index, closing prices, no dividends: a stand-in for SPY"
CONSENSUS_ORDER = ("ensemble", "garch_t_sh", BASELINE)
STATIC = Path(__file__).parent / "static"


# --- formatting ---------------------------------------------------------------------------------
def _missing(x: Any) -> bool:
    return x is None or x is pd.NA or (isinstance(x, float) and math.isnan(x))


def pct(x: Any, digits: int = 2) -> str:
    return "–" if _missing(x) else f"{float(x) * 100:.{digits}f}%"


def num(x: Any, digits: int = 3) -> str:
    return "–" if _missing(x) else f"{float(x):.{digits}f}"


def pval(x: Any) -> str:
    if _missing(x):
        return "–"
    x = float(x)
    return "<0.001" if x < 0.001 else f"{x:.3f}"


def yesno(x: Any) -> str:
    return "–" if _missing(x) else ("yes" if bool(x) else "no")


# --- data ---------------------------------------------------------------------------------------
@dataclass
class SiteData:
    forecasts: pd.DataFrame
    scores: pd.DataFrame
    runs: pd.DataFrame
    backtest_forecasts: pd.DataFrame
    backtest_scores: pd.DataFrame
    short_history: pd.DataFrame


def _read_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, dtype=str, keep_default_na=False) if path.is_file() else pd.DataFrame()


def load(public: PublicStore) -> SiteData:
    bt = public.backtest_dir
    return SiteData(
        forecasts=read_numeric(public.forecasts.read(), NUMERIC_FORECAST),
        scores=read_numeric(public.scores.read(), NUMERIC_SCORE),
        runs=public.runs.read(),
        backtest_forecasts=read_numeric(_read_csv(bt / "spx_forecasts.csv"), NUMERIC_FORECAST),
        backtest_scores=read_numeric(_read_csv(bt / "spx_scores.csv"), NUMERIC_SCORE),
        short_history=_read_csv(bt / "short_history.csv"),
    )


def joined(forecasts: pd.DataFrame, scores: pd.DataFrame, live_only: bool) -> pd.DataFrame:
    if forecasts.empty or scores.empty:
        return pd.DataFrame()
    f = forecasts
    if live_only:
        f = f[f["on_time"] == "true"]
    cols = ["forecast_id", "ticker", "model", "target_date", "variance", "var95", "var99"]
    return f[cols].merge(scores, on="forecast_id", how="inner")


# --- page sections ------------------------------------------------------------------------------
def today_section(data: SiteData) -> dict[str, Any]:
    f = data.forecasts
    if f.empty:
        return {"made": None, "target": None, "tickers": []}
    made = f["made_after_close_of"].max()
    latest = f[f["made_after_close_of"] == made]
    tickers = []
    for ticker in [t for t in TICKERS if t in set(latest["ticker"])]:
        rows = latest[latest["ticker"] == ticker]
        live = rows[rows["variance"].notna()]
        consensus = None
        for name in CONSENSUS_ORDER:
            hit = live[live["model"] == name]
            if not hit.empty:
                consensus = {"model": name, "vol": pct(hit["vol_annualized"].iloc[0], 1)}
                break
        tickers.append(
            {
                "ticker": ticker,
                "consensus": consensus,
                "rows": [
                    {
                        "model": r.model,
                        "vol": pct(r.vol_annualized, 1),
                        "var95": pct(r.var95),
                        "var99": pct(r.var99),
                        "dist": r.dist,
                        "status": r.fit_status,
                        "notes": r.notes,
                    }
                    for r in live.itertuples()
                ],
                "waiting": [
                    {"model": r.model, "status": r.fit_status, "notes": r.notes}
                    for r in rows[rows["variance"].isna()].itertuples()
                ],
            }
        )
    return {"made": made, "target": latest["target_date"].iloc[0], "tickers": tickers}


def status_section(data: SiteData, calendar: TradingCalendar, now: dt.datetime) -> dict[str, Any]:
    runs = data.runs
    last_ok = runs[runs["status"] == "ok"]["finished_utc"].max() if not runs.empty else None
    latest_made = data.forecasts["made_after_close_of"].max() if not data.forecasts.empty else None
    completed = calendar.latest_completed_session(now)
    stale = True
    if latest_made:
        stale = dt.date.fromisoformat(latest_made) < calendar.previous_session(completed)
    f = data.forecasts
    return {
        "last_updated": last_ok or "never",
        "latest_forecast_made": latest_made or "none yet",
        "stale": stale,
        "late": int((f["on_time"] == "false").sum()) if not f.empty else 0,
        "fallbacks": int((f["fit_status"] == "fallback").sum()) if not f.empty else 0,
        "failed": int((f["fit_status"] == "failed").sum()) if not f.empty else 0,
        "gaps": int(runs["notes"].str.contains("gap:").sum()) if not runs.empty else 0,
        "built_utc": now.isoformat(timespec="minutes"),
    }


def _loss_rows(table: pd.DataFrame) -> list[dict[str, str]]:
    out = []
    for r in table.itertuples():
        out.append(
            {
                "model": r.model,
                "n": str(r.n),
                "qlike": f"{num(r.mean)} [{num(r.mean_low)}, {num(r.mean_high)}]",
                "skill": "baseline"
                if r.model == BASELINE
                else f"{pct(r.skill, 1)} [{pct(r.skill_low, 1)}, {pct(r.skill_high, 1)}]",
                "dm_p": pval(r.dm_p),
                "dm_p_bh": pval(r.dm_p_bh),
                "in_mcs": yesno(r.in_mcs),
                "label": "best" if r.best else ("early" if r.early else ""),
            }
        )
    return out


def _var_rows(table: pd.DataFrame) -> list[dict[str, str]]:
    out = []
    for r in table.itertuples():
        out.append(
            {
                "model": r.model,
                "n": str(r.n_95),
                "rate95": f"{pct(r.rate_95, 1)} ({r.violations_95})",
                "binom95": pval(r.binomial_p_95),
                "kupiec95": pval(r.kupiec_p_95),
                "cc95": "not enough violations" if _missing(r.cc_p_95) else pval(r.cc_p_95),
                "rate99": f"{pct(r.rate_99, 1)} ({r.violations_99})",
                "binom99": pval(r.binomial_p_99),
                "kupiec99": pval(r.kupiec_p_99),
                "cc99": "not enough violations" if _missing(r.cc_p_99) else pval(r.cc_p_99),
                "light": f"{r.traffic_light} ({r.tl_violations} in {r.tl_sessions})",
                "light_class": str(r.traffic_light),
                "low_power": "low power" if (r.low_power_95 or r.low_power_99) else "",
            }
        )
    return out


def scoreboard(joined_frame: pd.DataFrame, tickers: list[str]) -> list[dict[str, Any]]:
    sections = []
    for ticker in tickers:
        j = (
            joined_frame[joined_frame["ticker"] == ticker]
            if not joined_frame.empty
            else joined_frame
        )
        if j.empty:
            sections.append({"ticker": ticker, "loss": [], "var": [], "n": 0})
            continue
        sections.append(
            {
                "ticker": ticker,
                "n": int(j["target_date"].nunique()),
                "loss": _loss_rows(loss_table(j)),
                "var": _var_rows(var_table(j)),
            }
        )
    return sections


OOS_LABEL = "Out-of-sample (2022 on)"
STUDY_LABEL = "Study period (to 2021)"


def backtest_sections(bt: pd.DataFrame) -> list[dict[str, Any]]:
    """The backtest reported by period, out-of-sample first (§13): the study period was used
    for modeling choices, so only the out-of-sample table is a clean test."""
    if bt.empty:
        return []
    oos = bt[bt["target_date"] >= OOS_START.isoformat()].assign(ticker=OOS_LABEL)
    study = bt[bt["target_date"] <= STUDY_END.isoformat()].assign(ticker=STUDY_LABEL)
    return scoreboard(pd.concat([oos, study], ignore_index=True), [OOS_LABEL, STUDY_LABEL])


# --- charts -------------------------------------------------------------------------------------
def _chart_html(fig: go.Figure, series: list[str]) -> str:
    fig.update_layout(
        template="simple_white",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        margin={"l": 50, "r": 20, "t": 40, "b": 40},
        height=320,
        font={"family": "Georgia, serif", "size": 13},
        legend={"orientation": "h", "y": -0.2},
    )
    manifest = json.dumps({"series": series})
    body = fig.to_html(full_html=False, include_plotlyjs=False, config={"displaylogo": False})
    return f'<script type="application/json" data-volforecast-chart>{manifest}</script>\n{body}'


def forecast_vs_proxy_chart(j: pd.DataFrame, ticker: str, model: str) -> str | None:
    if j.empty:
        return None
    rows = j[(j["ticker"] == ticker) & (j["model"] == model)].sort_values("target_date")
    if len(rows) < 2:
        return None
    ann = math.sqrt(TRADING_DAYS_PER_YEAR)
    proxy = rows["proxy_rs"].where(rows["proxy_rs"].notna(), rows["proxy_r2"])
    fig = go.Figure()
    fig.add_scatter(
        x=rows["target_date"],
        y=np.sqrt(proxy) * ann * 100,
        mode="markers",
        name="Realized (proxy, annualized %)",
        marker={"size": 4, "color": "#888"},
    )
    fig.add_scatter(
        x=rows["target_date"],
        y=np.sqrt(rows["variance"]) * ann * 100,
        mode="lines",
        name=f"Forecast: {model} (annualized %)",
        line={"color": "#1f3a5f"},
    )
    fig.update_layout(title=f"{ticker}: forecast vs realized volatility")
    return _chart_html(fig, ["forecast_vol", "proxy_vol"])


def violations_chart(j: pd.DataFrame, ticker: str, model: str) -> str | None:
    if j.empty:
        return None
    rows = j[(j["ticker"] == ticker) & (j["model"] == model)].sort_values("target_date")
    if len(rows) < 2:
        return None
    fig = go.Figure()
    fig.add_scatter(
        x=rows["target_date"],
        y=-rows["var99"] * 100,
        mode="lines",
        name="−VaR 99% (%)",
        line={"color": "#1f3a5f"},
    )
    hits = rows[rows["viol99"] == 1]
    fig.add_scatter(
        x=hits["target_date"],
        y=-hits["var99"] * 100,
        mode="markers",
        name="99% violation (date only)",
        marker={"symbol": "x", "size": 9, "color": "#a33"},
    )
    fig.update_layout(title=f"{ticker}: 99% VaR and violation dates")
    return _chart_html(fig, ["var99", "violation_dates"])


def rolling_qlike_chart(j: pd.DataFrame, title: str, window: int = 250) -> str | None:
    if j.empty:
        return None
    wide = j.pivot_table(index="target_date", columns="model", values="qlike", aggfunc="first")
    if len(wide) < 20:
        return None
    w = min(window, len(wide))
    fig = go.Figure()
    for model in wide.columns:
        fig.add_scatter(
            x=wide.index,
            y=wide[model].rolling(w, min_periods=w // 2).mean(),
            mode="lines",
            name=model,
        )
    fig.update_layout(title=f"{title}: rolling {w}-session mean QLIKE (lower is better)")
    return _chart_html(fig, ["rolling_qlike"])


# --- build --------------------------------------------------------------------------------------
def build_site(
    public: PublicStore,
    out_dir: Path,
    calendar: TradingCalendar | None = None,
    now: dt.datetime | None = None,
) -> list[Path]:
    calendar = calendar or TradingCalendar()
    now = now or dt.datetime.now(tz=dt.UTC)
    data = load(public)
    env = Environment(
        loader=PackageLoader("volforecast.site", "templates"),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    live = joined(data.forecasts, data.scores, live_only=True)
    bt = joined(data.backtest_forecasts, data.backtest_scores, live_only=False)
    tickers = [
        t for t in TICKERS if not data.forecasts.empty and t in set(data.forecasts["ticker"])
    ]
    chart_model = "garch_t_sh"
    charts = []
    for t in tickers:
        for chart in (
            forecast_vs_proxy_chart(live, t, chart_model),
            violations_chart(live, t, chart_model),
        ):
            if chart:
                charts.append(chart)
    common = {"status": status_section(data, calendar, now)}
    pages = {
        "index.html": env.get_template("index.html").render(
            page="today", today=today_section(data), **common
        ),
        "scoreboard.html": env.get_template("scoreboard.html").render(
            page="scoreboard",
            sections=scoreboard(live, tickers),
            charts=charts,
            rolling=[
                c
                for t in tickers
                if (
                    c := rolling_qlike_chart(
                        live[live["ticker"] == t] if not live.empty else live, t
                    )
                )
            ],
            **common,
        ),
        "backtest.html": env.get_template("backtest.html").render(
            page="backtest",
            label=BACKTEST_LABEL,
            sections=backtest_sections(bt),
            rolling=rolling_qlike_chart(bt, "S&P 500 index backtest") if not bt.empty else None,
            short_history=data.short_history.to_dict("records"),
            **common,
        ),
        "methodology.html": env.get_template("methodology.html").render(
            page="methodology", **common
        ),
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    assets = out_dir / "assets"
    assets.mkdir(exist_ok=True)
    (assets / "plotly.min.js").write_text(get_plotlyjs(), encoding="utf-8")
    shutil.copyfile(STATIC / "style.css", assets / "style.css")
    written = []
    for name, html in pages.items():
        path = out_dir / name
        path.write_text(html, encoding="utf-8")
        written.append(path)
    (out_dir / ".nojekyll").write_text("", encoding="utf-8")
    return written
