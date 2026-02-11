from __future__ import annotations

import pandas as pd


def _to_series(values: pd.Series | dict | list | None) -> pd.Series:
    if values is None:
        return pd.Series(dtype=float)
    s = pd.Series(values).dropna()
    if s.empty:
        return pd.Series(dtype=float)
    s.index = pd.to_datetime(s.index)
    return s.sort_index()


def compute_alpha_attribution(
    portfolio_equity: pd.Series | dict | list | None,
    benchmark_prices: pd.Series | dict | list | None,
    *,
    min_overlap_days: int = 5,
    last_n: int = 10,
) -> dict:
    out = {
        "ok": False,
        "reason": "",
        "overlap_start": None,
        "overlap_end": None,
        "overlap_days": 0,
        "summary": {},
        "rows": [],
    }

    port = _to_series(portfolio_equity)
    bench = _to_series(benchmark_prices)
    if port.empty:
        out["reason"] = "Portfolio equity series is empty."
        return out
    if bench.empty:
        out["reason"] = "SPY data missing."
        return out

    port_ret = port.pct_change(fill_method=None).dropna()
    bench_ret = bench.pct_change(fill_method=None).dropna()

    aligned = pd.concat([port_ret, bench_ret], axis=1, join="inner").dropna()
    if aligned.empty:
        out["reason"] = "No overlapping dates between portfolio and SPY returns."
        return out

    aligned.columns = ["port_ret", "spy_ret"]
    overlap_days = int(len(aligned))
    out["overlap_days"] = overlap_days
    out["overlap_start"] = aligned.index.min().strftime("%Y-%m-%d")
    out["overlap_end"] = aligned.index.max().strftime("%Y-%m-%d")

    if overlap_days < int(min_overlap_days):
        out["reason"] = (
            f"Need >={int(min_overlap_days)} overlapping days; have {overlap_days} "
            f"({out['overlap_start']} → {out['overlap_end']})"
        )
        return out

    aligned["spread"] = aligned["port_ret"] - aligned["spy_ret"]
    port_cum = (1.0 + aligned["port_ret"]).prod() - 1.0
    spy_cum = (1.0 + aligned["spy_ret"]).prod() - 1.0
    alpha_cum = port_cum - spy_cum

    tail = aligned.tail(int(last_n)).reset_index()
    rows = []
    for _, r in tail.iterrows():
        rows.append(
            {
                "date": pd.to_datetime(r["index"]).strftime("%Y-%m-%d"),
                "port_ret": float(r["port_ret"]),
                "spy_ret": float(r["spy_ret"]),
                "spread": float(r["spread"]),
            }
        )

    out["ok"] = True
    out["summary"] = {
        "cumulative_port_return": float(port_cum),
        "cumulative_spy_return": float(spy_cum),
        "cumulative_alpha": float(alpha_cum),
    }
    out["rows"] = rows
    return out
