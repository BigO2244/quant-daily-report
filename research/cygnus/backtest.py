"""FR-051 Cygnus Stage 2 — walk-forward backtest + A4 pass/fail table.

RESEARCH_ONLY / NON_EXECUTIONAL. Builds the cygnus_v0_event_reaction backtest
over the EDGAR event tape and the repo's adjusted-close matrix, and reports the
pre-registered A4 criteria. Component weights are frozen (strategy.py); nothing
is re-tuned to pass. The 2025+ holdout is excluded by slicing all price/event
data at `holdout_start` — this module never reads holdout data.

Walk-forward (A4): tune <= 2021-12-31, validate 2022-01-01..2024-12-31. The
A3 composite has no free parameters, so "tune" is a confirmation pass; the
reported headline is the validation-window A4 table.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from research.cygnus import EXECUTION_IMPACT, GOVERNANCE_LABEL, STRATEGY_ID
from research.cygnus import features as F
from research.cygnus.strategy import compute_v0_scores, select_basket

HOLDOUT_START = pd.Timestamp("2025-01-01")
TUNE_END = pd.Timestamp("2021-12-31")
VALIDATE_START = pd.Timestamp("2022-01-01")
VALIDATE_END = pd.Timestamp("2024-12-31")
HOLD_DAYS = 10
TOP_N = 10
DEFAULT_COST_BPS = 25.0

# A4 pre-registered thresholds (frozen).
A4 = {
    "rank_ic_10d_min": 0.02,
    "rank_ic_tstat_min": 2.0,
    "net_ir_vs_spy_min": 0.30,
    "polaris_excess_corr_max": 0.50,
    "event_coverage_min": 0.60,
}


# --------------------------------------------------------------------------- #
# Statistics primitives (pure; unit-tested)
# --------------------------------------------------------------------------- #
def spearman_rank_ic(scores: list[float], fwd: list[float]) -> tuple[float | None, float | None, int]:
    """Spearman rank IC and its t-stat across the event panel."""
    pairs = [(s, f) for s, f in zip(scores, fwd) if s is not None and f is not None]
    n = len(pairs)
    if n < 3:
        return None, None, n
    s_rank = _ranks([p[0] for p in pairs])
    f_rank = _ranks([p[1] for p in pairs])
    rho = _pearson(s_rank, f_rank)
    if rho is None:
        return None, None, n
    denom = 1.0 - rho * rho
    t = rho * math.sqrt((n - 2) / denom) if denom > 1e-12 else math.inf
    return rho, t, n


def _ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def _pearson(a: list[float], b: list[float]) -> float | None:
    n = len(a)
    if n < 2:
        return None
    ma, mb = sum(a) / n, sum(b) / n
    cov = sum((a[i] - ma) * (b[i] - mb) for i in range(n))
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((x - mb) ** 2 for x in b)
    if va <= 0 or vb <= 0:
        return None
    return cov / math.sqrt(va * vb)


def annualized_ir(daily_excess: list[float]) -> float | None:
    n = len(daily_excess)
    if n < 2:
        return None
    mean = sum(daily_excess) / n
    var = sum((x - mean) ** 2 for x in daily_excess) / (n - 1)
    sd = math.sqrt(var)
    if sd <= 0:
        return None
    return (mean / sd) * math.sqrt(252)


# --------------------------------------------------------------------------- #
# Data loading
# --------------------------------------------------------------------------- #
def load_price_matrix(repo_root: Path | str = ".", *, holdout_start: pd.Timestamp = HOLDOUT_START) -> pd.DataFrame:
    path = Path(repo_root) / "alpha_stack_cache" / "prices" / "_matrix_prices_2007_2026.parquet"
    prices = pd.read_parquet(path)
    prices = prices[prices.index < holdout_start]  # holdout never visible
    return prices


def load_event_tape(repo_root: Path | str, trade_date: str) -> list[dict[str, Any]]:
    path = Path(repo_root) / "outputs" / "research" / "cygnus" / trade_date / "cygnus_event_tape.csv"
    if not path.exists():
        return []
    df = pd.read_csv(path)
    return df.to_dict("records")


def _fundamentals_loader(repo_root: Path | str) -> Callable[[str], pd.DataFrame | None]:
    cache: dict[str, pd.DataFrame | None] = {}
    root = Path(repo_root)

    def load(ticker: str) -> pd.DataFrame | None:
        if ticker not in cache:
            p = root / "data" / "fundamental" / f"{ticker}.parquet"
            cache[ticker] = pd.read_parquet(p) if p.exists() else None
        return cache[ticker]

    return load


# --------------------------------------------------------------------------- #
# Panel construction
# --------------------------------------------------------------------------- #
def build_event_panel(
    events: list[dict[str, Any]],
    prices: pd.DataFrame,
    *,
    fundamentals_loader: Callable[[str], pd.DataFrame | None],
    spy_symbol: str = "SPY",
) -> list[dict[str, Any]]:
    """One row per event with A3 features, monthly-cohort score, forward returns."""
    if spy_symbol not in prices.columns:
        raise ValueError(f"price matrix missing {spy_symbol}")
    index = prices.index
    spy = prices[spy_symbol]

    rows: list[dict[str, Any]] = []
    for ev in events:
        ticker = str(ev.get("ticker") or "").strip().upper()
        avail = pd.to_datetime(ev.get("availability_date"), errors="coerce")
        if ticker not in prices.columns or pd.isna(avail) or avail >= prices.index[-1]:
            continue
        t_pos = F._pos_on_or_before(index, avail)
        if t_pos is None or t_pos < 25:
            continue
        series = prices[ticker]
        reaction = F.event_reaction_abnormal_return(series, spy, t_pos)
        if reaction is None:
            continue  # the 0.40 driver is required
        rows.append(
            {
                "ticker": ticker,
                "availability_date": avail.date().isoformat(),
                "cohort_month": f"{avail.year}-{avail.month:02d}",
                "t_pos": t_pos,
                "event_reaction_abnormal_return": reaction,
                "revenue_yoy_acceleration": F.revenue_yoy_acceleration(fundamentals_loader(ticker), avail),
                "drift_confirmation": F.drift_confirmation(series, t_pos),
                "filing_quality_bonus": 1.0 if (ev.get("has_financial_exhibit_item") in (True, "True", "true", 1)
                                                 and ev.get("acceptance_timestamp_present") in (True, "True", "true", 1)) else 0.0,
                "pre_event_runup": F.pre_event_runup(series, t_pos),
                "fwd_10d": F.forward_return(series, t_pos, 10),
                "fwd_20d": F.forward_return(series, t_pos, 20),
                "fwd_60d": F.forward_return(series, t_pos, 60),
            }
        )

    # Score within monthly cohorts (contemporaneous, no future info).
    scored: list[dict[str, Any]] = []
    by_month: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_month.setdefault(r["cohort_month"], []).append(r)
    for cohort in by_month.values():
        scored.extend(compute_v0_scores(cohort))
    return scored


def _window(panel: list[dict[str, Any]], start: pd.Timestamp, end: pd.Timestamp) -> list[dict[str, Any]]:
    out = []
    for r in panel:
        d = pd.to_datetime(r["availability_date"])
        if start <= d <= end:
            out.append(r)
    return out


# --------------------------------------------------------------------------- #
# Tradeable NAV + Polaris proxy (for IR and correlation criteria)
# --------------------------------------------------------------------------- #
def _strategy_daily_excess(
    panel: list[dict[str, Any]],
    prices: pd.DataFrame,
    *,
    start: pd.Timestamp,
    end: pd.Timestamp,
    cost_bps: float,
    spy_symbol: str = "SPY",
) -> list[float]:
    """Equal-weight basket of selected events held HOLD_DAYS, net of cost,
    expressed as daily excess return vs SPY over [start, end]."""
    daily = prices.pct_change()
    index = prices.index
    window_mask = (index >= start) & (index <= end)
    window_positions = [i for i, m in enumerate(window_mask) if m]
    if not window_positions:
        return []

    # Per-day active position weights from selected events (top-N per month).
    selected: list[dict[str, Any]] = []
    by_month: dict[str, list[dict[str, Any]]] = {}
    for r in panel:
        by_month.setdefault(r["cohort_month"], []).append(r)
    for cohort in by_month.values():
        selected.extend(select_basket(cohort, top_n=TOP_N))

    holdings_by_pos: dict[int, list[str]] = {}
    entry_days: dict[int, int] = {}
    exit_days: dict[int, int] = {}
    for ev in selected:
        t = ev["t_pos"]
        for h in range(1, HOLD_DAYS + 1):
            p = t + h
            if p < len(index):
                holdings_by_pos.setdefault(p, []).append(ev["ticker"])
        entry_days[t + 1] = entry_days.get(t + 1, 0) + 1
        exit_days[t + HOLD_DAYS] = exit_days.get(t + HOLD_DAYS, 0) + 1

    cost_rate = cost_bps / 10_000.0
    excess: list[float] = []
    for p in window_positions:
        names = holdings_by_pos.get(p, [])
        if names:
            rets = [daily.iloc[p][n] for n in names if n in daily.columns and not pd.isna(daily.iloc[p][n])]
            port = sum(rets) / len(rets) if rets else 0.0
            # cost drag: each entry/exit turns over 1/len(names) of the book
            n_book = len(names)
            turnover = (entry_days.get(p, 0) + exit_days.get(p, 0)) / n_book if n_book else 0.0
            port -= cost_rate * turnover
        else:
            port = 0.0
        spy_ret = daily.iloc[p][spy_symbol]
        excess.append(port - (0.0 if pd.isna(spy_ret) else float(spy_ret)))
    return excess


def _polaris_proxy_daily_excess(
    prices: pd.DataFrame, *, start: pd.Timestamp, end: pd.Timestamp, spy_symbol: str = "SPY", top_n: int = 10
) -> list[float]:
    """Monthly-rebalanced top-N 12-1 momentum basket daily excess vs SPY — a
    labeled PROXY for Polaris (live Polaris paper history is only 2026+)."""
    daily = prices.pct_change()
    index = prices.index
    universe = [c for c in prices.columns if c not in (spy_symbol, "^VIX", "TLT")]
    excess: list[float] = []
    held: list[str] = []
    cur_month = None
    for p in range(len(index)):
        d = index[p]
        if not (start <= d <= end):
            continue
        month = (d.year, d.month)
        if month != cur_month and p > 252:
            cur_month = month
            mom = {}
            for c in universe:
                r = F.close_to_close_return(prices[c], p - 21, 231)  # ~12m-1m
                if r is not None:
                    mom[c] = r
            held = [c for c, _ in sorted(mom.items(), key=lambda kv: kv[1], reverse=True)[:top_n]]
        if held:
            rets = [daily.iloc[p][n] for n in held if not pd.isna(daily.iloc[p][n])]
            port = sum(rets) / len(rets) if rets else 0.0
        else:
            port = 0.0
        spy_ret = daily.iloc[p][spy_symbol]
        excess.append(port - (0.0 if pd.isna(spy_ret) else float(spy_ret)))
    return excess


# --------------------------------------------------------------------------- #
# A4 table
# --------------------------------------------------------------------------- #
def a4_table(
    panel: list[dict[str, Any]],
    prices: pd.DataFrame,
    *,
    window_name: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
    expected_events: int | None = None,
) -> dict[str, Any]:
    win = _window(panel, start, end)
    scores = [r.get("cygnus_v0_score") for r in win]
    f10 = [r.get("fwd_10d") for r in win]
    f20 = [r.get("fwd_20d") for r in win]
    f60 = [r.get("fwd_60d") for r in win]

    ic10, t10, n10 = spearman_rank_ic(scores, f10)
    ic20, _, _ = spearman_rank_ic(scores, f20)
    ic60, _, _ = spearman_rank_ic(scores, f60)

    excess_25 = _strategy_daily_excess(panel, prices, start=start, end=end, cost_bps=25.0)
    excess_50 = _strategy_daily_excess(panel, prices, start=start, end=end, cost_bps=50.0)
    ir_25 = annualized_ir(excess_25)
    ir_50 = annualized_ir(excess_50)

    pol_excess = _polaris_proxy_daily_excess(prices, start=start, end=end)
    corr = _pearson(excess_25, pol_excess) if len(excess_25) == len(pol_excess) and len(excess_25) > 2 else None

    coverage = (len(win) / expected_events) if expected_events else None

    def verdict(ok: bool | None) -> str:
        return "UNAVAILABLE" if ok is None else ("PASS" if ok else "FAIL")

    monotone = (ic10 is not None and ic20 is not None and ic60 is not None
                and ic20 > 0 and ic60 > 0 and ic10 >= ic60)

    criteria = {
        "rank_ic_10d": {
            "value": ic10, "t_stat": t10, "n": n10,
            "threshold": f">= {A4['rank_ic_10d_min']}, t>= {A4['rank_ic_tstat_min']}",
            "verdict": verdict(ic10 is not None and t10 is not None
                               and ic10 >= A4["rank_ic_10d_min"] and t10 >= A4["rank_ic_tstat_min"]),
        },
        "rank_ic_20d_60d_decay": {
            "ic_20d": ic20, "ic_60d": ic60,
            "threshold": "positive, monotone-ish decay (ic10 >= ic60 > 0, ic20 > 0)",
            "verdict": verdict(monotone),
        },
        "net_ir_vs_spy_25bps": {
            "value": ir_25, "threshold": f">= {A4['net_ir_vs_spy_min']}",
            "verdict": verdict(ir_25 is not None and ir_25 >= A4["net_ir_vs_spy_min"]),
        },
        "polaris_excess_correlation": {
            "value": corr, "basis": "PROXY (12-1 momentum top-10; live Polaris is 2026+)",
            "threshold": f"<= {A4['polaris_excess_corr_max']}",
            "verdict": verdict(corr is not None and corr <= A4["polaris_excess_corr_max"]),
        },
        "event_coverage": {
            "value": coverage, "captured": len(win), "expected": expected_events,
            "threshold": f">= {A4['event_coverage_min']}",
            "verdict": verdict(coverage is not None and coverage >= A4["event_coverage_min"]),
        },
        "cost_sensitivity_50bps": {
            "ir_25bps": ir_25, "ir_50bps": ir_50,
            "threshold": "thesis survives at 50 bps (IR_50 > 0)",
            "verdict": verdict(ir_50 is not None and ir_50 > 0),
        },
    }
    passed = sum(1 for c in criteria.values() if c["verdict"] == "PASS")
    return {
        "strategy_id": STRATEGY_ID,
        "governance_label": GOVERNANCE_LABEL,
        "execution_impact": EXECUTION_IMPACT,
        "window": window_name,
        "window_range": [start.date().isoformat(), end.date().isoformat()],
        "events_in_window": len(win),
        "criteria": criteria,
        "criteria_passed": passed,
        "criteria_total": len(criteria),
        "overall": "PASS" if passed == len(criteria) else "FAIL",
        "note": "Component weights frozen (A3); no re-tuning. Holdout (2025+) excluded from all data.",
    }
