"""Evaluation and statistics (§12).

Volatility: mean losses with stationary-bootstrap intervals, skill vs EWMA, Diebold–Mariano
(one-sided, Newey–West, Harvey–Leybourne–Newbold), Benjamini–Hochberg, the Model Confidence
Set and Mincer–Zarnowitz regressions. VaR: exact binomial, Kupiec, Christoffersen and the Basel
traffic light. All bootstraps use fixed seeds so reports are reproducible.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
import pandas as pd
import statsmodels.api as sm
from arch.bootstrap import MCS, StationaryBootstrap
from scipy import stats

SEED = 20261018
BASELINE = "ewma_094"
MIN_LIVE_SESSIONS = 60  # loss comparisons are "early" before this (§12)
MIN_EXPECTED_VIOLATIONS = 10  # VaR tests are "low power" before this (§12)

ArrayLike = npt.ArrayLike


def _clean(x: ArrayLike) -> npt.NDArray[np.float64]:
    a = np.asarray(x, dtype=np.float64)
    return a[np.isfinite(a)]


def block_size(n: int) -> int:
    """Expected block length for the stationary bootstrap: about n^(1/3), at least 2."""
    return max(2, round(n ** (1 / 3)))


# --- intervals ------------------------------------------------------------------------------
@dataclass(frozen=True)
class Interval:
    estimate: float
    low: float
    high: float
    n: int


def mean_ci(losses: ArrayLike, reps: int = 1000, seed: int = SEED) -> Interval:
    x = _clean(losses)
    if x.size < 10:
        return Interval(float(np.mean(x)) if x.size else math.nan, math.nan, math.nan, int(x.size))
    bs = StationaryBootstrap(block_size(x.size), x, seed=seed)
    ci = bs.conf_int(lambda a: np.array([a.mean()]), reps=reps, method="percentile")
    return Interval(float(x.mean()), float(ci[0, 0]), float(ci[1, 0]), int(x.size))


def skill_ci(
    model_losses: ArrayLike, base_losses: ArrayLike, reps: int = 1000, seed: int = SEED
) -> Interval:
    """1 − mean(L_model)/mean(L_base), bootstrapping the two loss series jointly."""
    m = np.asarray(model_losses, dtype=np.float64)
    b = np.asarray(base_losses, dtype=np.float64)
    ok = np.isfinite(m) & np.isfinite(b)
    m, b = m[ok], b[ok]
    if m.size == 0 or b.mean() <= 0:
        return Interval(math.nan, math.nan, math.nan, int(m.size))
    est = 1.0 - m.mean() / b.mean()
    if m.size < 10:
        return Interval(float(est), math.nan, math.nan, int(m.size))
    bs = StationaryBootstrap(block_size(m.size), m, b, seed=seed)
    ci = bs.conf_int(
        lambda mm, bb: np.array([1.0 - mm.mean() / bb.mean()]), reps=reps, method="percentile"
    )
    return Interval(float(est), float(ci[0, 0]), float(ci[1, 0]), int(m.size))


# --- Diebold–Mariano --------------------------------------------------------------------------
@dataclass(frozen=True)
class DMResult:
    statistic: float
    pvalue: float  # one-sided: H1 is "the model has lower expected loss than the baseline"
    n: int
    lag: int


def newey_west_variance(d: npt.NDArray[np.float64], lag: int) -> float:
    """Long-run variance of the mean of d (Bartlett kernel)."""
    n = d.size
    u = d - d.mean()
    gamma0 = float(u @ u) / n
    total = gamma0
    for k in range(1, lag + 1):
        weight = 1.0 - k / (lag + 1)
        total += 2.0 * weight * float(u[k:] @ u[:-k]) / n
    return total / n


def diebold_mariano(model_losses: ArrayLike, base_losses: ArrayLike, horizon: int = 1) -> DMResult:
    """dₜ = L_model − L_base; Newey–West lag ⌊T^(1/3)⌋; HLN correction; t(T−1) reference."""
    m = np.asarray(model_losses, dtype=np.float64)
    b = np.asarray(base_losses, dtype=np.float64)
    ok = np.isfinite(m) & np.isfinite(b)
    d = m[ok] - b[ok]
    n = d.size
    lag = math.floor(n ** (1 / 3)) if n else 0
    if n < 10:
        return DMResult(math.nan, math.nan, n, lag)
    var = newey_west_variance(d, lag)
    if not var > 0:
        return DMResult(math.nan, math.nan, n, lag)
    dm = d.mean() / math.sqrt(var)
    h = horizon
    hln = math.sqrt((n + 1 - 2 * h + h * (h - 1) / n) / n)
    stat = dm * hln
    return DMResult(float(stat), float(stats.t.cdf(stat, df=n - 1)), n, lag)


def benjamini_hochberg(pvalues: Sequence[float]) -> list[float]:
    """Adjusted p-values (step-up). NaNs stay NaN and are not counted."""
    p = np.asarray(pvalues, dtype=np.float64)
    out = np.full(p.size, np.nan)
    idx = np.where(np.isfinite(p))[0]
    m = idx.size
    if m == 0:
        return out.tolist()
    order = idx[np.argsort(p[idx])]
    ranked = p[order] * m / np.arange(1, m + 1)
    adjusted = np.minimum.accumulate(ranked[::-1])[::-1]
    out[order] = np.minimum(adjusted, 1.0)
    return out.tolist()


# --- Model Confidence Set ---------------------------------------------------------------------
def model_confidence_set(
    losses: pd.DataFrame, size: float = 0.10, reps: int = 1000, seed: int = SEED
) -> pd.DataFrame:
    """Columns: model, mcs_pvalue, in_mcs. `losses` has one column per model, aligned rows.

    Never raises: if the MCS can't be computed, every model gets NaN / NA ("–" on the site).
    """
    empty = pd.DataFrame({"model": list(losses.columns), "mcs_pvalue": math.nan, "in_mcs": pd.NA})
    clean = losses.replace([np.inf, -np.inf], np.nan).dropna()
    if clean.shape[0] < 30 or clean.shape[1] < 2:
        return empty
    # Models with identical losses (garch_t and garch_t_fhs share one variance forecast) make
    # the MCS's pairwise statistics divide by zero. Test one model of each identical group and
    # give the others the same result.
    representative: dict[str, str] = {}
    unique: list[str] = []
    for col in clean.columns:
        values = clean[col].to_numpy()
        match = next((u for u in unique if np.array_equal(values, clean[u].to_numpy())), None)
        if match is None:
            unique.append(col)
        representative[col] = match or col
    if len(unique) < 2:
        return empty
    try:
        mcs = MCS(
            clean[unique], size=size, reps=reps, block_size=block_size(clean.shape[0]), seed=seed
        )
        mcs.compute()
    except (ValueError, IndexError, ZeroDivisionError, np.linalg.LinAlgError):
        return empty
    pvals = mcs.pvalues.iloc[:, 0]
    included = set(mcs.included)
    return pd.DataFrame(
        {
            "model": list(clean.columns),
            "mcs_pvalue": [float(pvals.get(representative[c], math.nan)) for c in clean.columns],
            "in_mcs": [representative[c] in included for c in clean.columns],
        }
    )


# --- Mincer–Zarnowitz -------------------------------------------------------------------------
@dataclass(frozen=True)
class MZResult:
    a: float
    b: float
    pvalue: float  # joint test a = 0, b = 1 (HAC)
    r2: float
    n: int


def mincer_zarnowitz(proxy: ArrayLike, forecast: ArrayLike, log: bool = False) -> MZResult:
    y = np.asarray(proxy, dtype=np.float64)
    x = np.asarray(forecast, dtype=np.float64)
    ok = np.isfinite(y) & np.isfinite(x) & (x > 0)
    if log:
        ok &= y > 0
    y, x = y[ok], x[ok]
    n = y.size
    if n < 20:
        return MZResult(math.nan, math.nan, math.nan, math.nan, n)
    if log:
        y, x = np.log(y), np.log(x)
    lag = math.floor(n ** (1 / 3))
    fit = sm.OLS(y, sm.add_constant(x)).fit(cov_type="HAC", cov_kwds={"maxlags": lag})
    test = fit.wald_test((np.eye(2), np.array([0.0, 1.0])), use_f=False, scalar=True)
    a, b = (float(v) for v in fit.params)
    return MZResult(a, b, float(test.pvalue), float(fit.rsquared), n)


# --- VaR backtests ----------------------------------------------------------------------------
def _xlogy(x: float, y: float) -> float:
    """x·ln y with the convention 0·ln 0 = 0."""
    return 0.0 if x == 0 else x * math.log(y)


@dataclass(frozen=True)
class VarTests:
    n: int
    violations: int
    rate: float
    expected_rate: float
    binomial_p: float
    kupiec_lr: float
    kupiec_p: float
    ind_lr: float | None  # None: not enough violations
    ind_p: float | None
    cc_lr: float | None
    cc_p: float | None
    low_power: bool


def kupiec(violations: int, n: int, p: float) -> tuple[float, float]:
    x = violations
    pi = x / n
    ll0 = _xlogy(n - x, 1 - p) + _xlogy(x, p)
    ll1 = _xlogy(n - x, 1 - pi) + _xlogy(x, pi)
    lr = max(-2.0 * (ll0 - ll1), 0.0)
    return lr, float(stats.chi2.sf(lr, 1))


def christoffersen_independence(hits: npt.NDArray[np.int_]) -> tuple[float, float] | None:
    if hits.sum() < 2 or hits.size < 3:
        return None
    prev, cur = hits[:-1], hits[1:]
    n00 = int(np.sum((prev == 0) & (cur == 0)))
    n01 = int(np.sum((prev == 0) & (cur == 1)))
    n10 = int(np.sum((prev == 1) & (cur == 0)))
    n11 = int(np.sum((prev == 1) & (cur == 1)))
    pi0 = n01 / (n00 + n01) if n00 + n01 else 0.0
    pi1 = n11 / (n10 + n11) if n10 + n11 else 0.0
    pi = (n01 + n11) / (n00 + n01 + n10 + n11)
    ll_restricted = _xlogy(n00 + n10, 1 - pi) + _xlogy(n01 + n11, pi)
    ll_unrestricted = (
        _xlogy(n00, 1 - pi0) + _xlogy(n01, pi0) + _xlogy(n10, 1 - pi1) + _xlogy(n11, pi1)
    )
    lr = max(-2.0 * (ll_restricted - ll_unrestricted), 0.0)
    return lr, float(stats.chi2.sf(lr, 1))


def var_tests(hits: ArrayLike, level: float) -> VarTests:
    """Tests for a 0/1 violation series of VaR at `level` (0.95 or 0.99)."""
    raw = np.asarray(hits, dtype=np.float64)
    h = raw[np.isfinite(raw)].astype(np.int_)
    n = int(h.size)
    p = 1.0 - level
    if n == 0:
        return VarTests(
            0, 0, math.nan, p, math.nan, math.nan, math.nan, None, None, None, None, True
        )
    x = int(h.sum())
    binom_p = float(stats.binomtest(x, n, p, alternative="two-sided").pvalue)
    k_lr, k_p = kupiec(x, n, p)
    ind = christoffersen_independence(h)
    if ind is None:
        ind_lr = ind_p = cc_lr = cc_p = None
    else:
        ind_lr, ind_p = ind
        cc_lr = k_lr + ind_lr
        cc_p = float(stats.chi2.sf(cc_lr, 2))
    return VarTests(
        n,
        x,
        x / n,
        p,
        binom_p,
        k_lr,
        k_p,
        ind_lr,
        ind_p,
        cc_lr,
        cc_p,
        low_power=n * p < MIN_EXPECTED_VIOLATIONS,
    )


def traffic_light(hits99: ArrayLike, window: int = 250) -> tuple[str, int, int]:
    """Basel zones for 99% VaR over the last `window` sessions: (zone, violations, sessions)."""
    h = np.asarray(hits99, dtype=np.float64)
    h = h[np.isfinite(h)][-window:]
    x = int(h.sum())
    zone = "green" if x <= 4 else "yellow" if x <= 9 else "red"
    return zone, x, int(h.size)


# --- scoreboard -------------------------------------------------------------------------------
def loss_table(
    joined: pd.DataFrame, loss: str = "qlike", baseline: str = BASELINE, seed: int = SEED
) -> pd.DataFrame:
    """Per model for one ticker: n, mean loss [CI], skill vs baseline [CI], DM p, BH-adjusted p,
    MCS membership, and the 'best' and 'early' labels (§12 reporting rules).

    `joined` has one row per scored forecast with columns target_date, model, and `loss`.
    """
    wide = joined.pivot_table(index="target_date", columns="model", values=loss, aggfunc="first")
    rows = []
    base = wide.get(baseline)
    for model in wide.columns:
        series = wide[model].dropna()
        ci = mean_ci(series.to_numpy(), seed=seed)
        row = {
            "model": model,
            "n": ci.n,
            "mean": ci.estimate,
            "mean_low": ci.low,
            "mean_high": ci.high,
            "skill": math.nan,
            "skill_low": math.nan,
            "skill_high": math.nan,
            "dm_stat": math.nan,
            "dm_p": math.nan,
        }
        if base is not None and model != baseline:
            pair = wide[[model, baseline]].dropna()
            sk = skill_ci(pair[model].to_numpy(), pair[baseline].to_numpy(), seed=seed)
            dm = diebold_mariano(pair[model].to_numpy(), pair[baseline].to_numpy())
            row.update(
                skill=sk.estimate,
                skill_low=sk.low,
                skill_high=sk.high,
                dm_stat=dm.statistic,
                dm_p=dm.pvalue,
            )
        rows.append(row)
    table = pd.DataFrame(rows)
    if table.empty:
        return table
    table["dm_p_bh"] = benjamini_hochberg(table["dm_p"].tolist())
    # The MCS needs one common sample. Models with much shorter records (e.g. ones that went
    # live later) would shrink it for everyone, so they are left out of the MCS and shown as "–".
    counts = wide.notna().sum()
    eligible = [c for c in wide.columns if counts[c] >= 0.8 * counts.max()]
    common = wide[eligible].dropna()
    mcs = model_confidence_set(common, seed=seed) if len(common) else None
    if mcs is not None:
        table = table.merge(mcs, on="model", how="left")
    else:
        table["mcs_pvalue"], table["in_mcs"] = math.nan, pd.NA
    table["early"] = table["n"] < MIN_LIVE_SESSIONS
    table["best"] = (
        (table["in_mcs"] == True)  # noqa: E712 - pandas NA-aware comparison
        & (table["dm_p_bh"] < 0.05)
        & (table["skill"] > 0)
        & ~table["early"]
    )
    return table.sort_values("mean").reset_index(drop=True)


def var_table(joined: pd.DataFrame) -> pd.DataFrame:
    """Per model for one ticker: VaR tests at both levels and the 99% traffic light."""
    rows = []
    for model, group in joined.sort_values("target_date").groupby("model"):
        row: dict[str, object] = {"model": model}
        for level, col in ((0.95, "viol95"), (0.99, "viol99")):
            t = var_tests(group[col].to_numpy(dtype=np.float64), level)
            tag = col[-2:]
            row.update(
                {
                    f"n_{tag}": t.n,
                    f"violations_{tag}": t.violations,
                    f"rate_{tag}": t.rate,
                    f"binomial_p_{tag}": t.binomial_p,
                    f"kupiec_p_{tag}": t.kupiec_p,
                    f"ind_p_{tag}": t.ind_p,
                    f"cc_p_{tag}": t.cc_p,
                    f"low_power_{tag}": t.low_power,
                }
            )
        zone, x, n = traffic_light(group["viol99"].to_numpy(dtype=np.float64))
        row.update({"traffic_light": zone, "tl_violations": x, "tl_sessions": n})
        rows.append(row)
    return pd.DataFrame(rows)
