"""Canonical accounting/reporting helpers used across paper + email layers."""
from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from typing import Any

import pandas as pd


DEFAULT_CASH_TICKERS = {"CASH"}


def _truthy_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}


def _safe_df(value: Any) -> pd.DataFrame:
    return value if isinstance(value, pd.DataFrame) else pd.DataFrame()


def _coerce_price_map(prices: Any) -> dict[str, float]:
    if prices is None:
        return {}

    if isinstance(prices, pd.Series):
        out: dict[str, float] = {}
        for ticker, value in prices.items():
            try:
                out[str(ticker).upper()] = float(value)
            except Exception:
                continue
        return out

    if isinstance(prices, Mapping):
        out = {}
        for ticker, value in prices.items():
            try:
                out[str(ticker).upper()] = float(value)
            except Exception:
                continue
        return out

    df = _safe_df(prices)
    if df.empty:
        return {}

    cols = [str(c) for c in df.columns]
    lower_cols = {str(c).lower(): str(c) for c in cols}
    ticker_col = lower_cols.get("ticker")
    preferred_price_cols = [
        "price",
        "mtm_price",
        "last_price",
        "prev_close",
        "close",
        "open",
        "fill_price",
    ]

    if ticker_col:
        price_col = None
        for candidate in preferred_price_cols:
            if candidate in lower_cols:
                price_col = lower_cols[candidate]
                break
        if price_col is None:
            numeric_cols = [c for c in cols if pd.api.types.is_numeric_dtype(df[c])]
            if numeric_cols:
                price_col = numeric_cols[0]
        if price_col:
            tmp = df[[ticker_col, price_col]].dropna(subset=[ticker_col]).copy()
            tmp[ticker_col] = tmp[ticker_col].astype(str).str.upper()
            out = {}
            for _, row in tmp.iterrows():
                try:
                    out[str(row[ticker_col]).upper()] = float(row[price_col])
                except Exception:
                    continue
            return out

    # Wide frame fallback: first numeric row indexed by ticker column names.
    numeric_cols = [c for c in cols if pd.api.types.is_numeric_dtype(df[c])]
    if not numeric_cols or df.empty:
        return {}
    row = df[numeric_cols].iloc[-1]
    out = {}
    for ticker, value in row.items():
        try:
            out[str(ticker).upper()] = float(value)
        except Exception:
            continue
    return out


def compute_exposure(
    weights_dict: Mapping[str, float] | Iterable[tuple[str, float]] | None,
    *,
    cash_tickers: Iterable[str] = ("CASH",),
    leverage_enabled: bool | None = None,
    tolerance: float = 1e-6,
    enforce_bounds: bool = True,
) -> dict[str, float | bool]:
    """Compute canonical gross/net exposure from non-cash position weights."""
    if leverage_enabled is None:
        leverage_enabled = _truthy_env("ALLOW_LEVERAGE", default=False)

    cash_set = {str(t).upper() for t in cash_tickers}
    if not cash_set:
        cash_set = set(DEFAULT_CASH_TICKERS)

    if weights_dict is None:
        gross = 0.0
        net = 0.0
    else:
        items: Iterable[tuple[Any, Any]]
        if isinstance(weights_dict, Mapping):
            items = weights_dict.items()
        else:
            items = list(weights_dict)
        gross = 0.0
        net = 0.0
        for ticker, raw_weight in items:
            if str(ticker).upper() in cash_set:
                continue
            try:
                weight = float(raw_weight)
            except Exception:
                continue
            if pd.isna(weight):
                continue
            gross += abs(weight)
            net += weight

    gross = float(gross)
    net = float(net)
    leverage_violation = bool((not leverage_enabled) and gross > (1.0 + float(tolerance)))
    if enforce_bounds and leverage_violation:
        raise AssertionError(
            f"gross_exposure={gross:.6f} exceeds long-only bound (1.0 + {float(tolerance):.6f})"
        )
    return {
        "gross_exposure": gross,
        "net_exposure": net,
        "gross_exposure_pct": gross * 100.0,
        "net_exposure_pct": net * 100.0,
        "leverage_enabled": bool(leverage_enabled),
        "leverage_violation": leverage_violation,
    }


def compute_nav(ledger: Any, prices: Any, cash: float) -> tuple[pd.DataFrame, dict[str, Any]]:
    """
    Canonical NAV computation.

    Parameters:
    - ledger: holdings-like frame with at least `ticker`, `shares`.
    - prices: map/Series/DataFrame with ticker -> price.
    - cash: cash dollars.
    """
    holdings = _safe_df(ledger).copy()
    if holdings.empty:
        nav = {
            "equity": float(cash),
            "cash": float(cash),
            "gross_exposure": 0.0,
            "net_exposure": 0.0,
            "totals": {"market_value": 0.0, "unrealized_pnl": 0.0},
        }
        return pd.DataFrame(
            columns=[
                "ticker",
                "shares",
                "price",
                "market_value",
                "avg_cost",
                "unrealized_pnl",
                "sleeve",
            ]
        ), nav

    if "ticker" not in holdings.columns or "shares" not in holdings.columns:
        raise ValueError("compute_nav requires holdings columns: ticker, shares")

    price_map = _coerce_price_map(prices)
    missing_prices: list[str] = []
    rows: list[dict[str, Any]] = []
    for _, row in holdings.iterrows():
        ticker = str(row.get("ticker", "")).upper()
        if not ticker:
            continue
        try:
            shares = float(row.get("shares", 0.0))
        except Exception:
            continue
        if abs(shares) <= 0:
            continue
        px = price_map.get(ticker)
        if px is None or pd.isna(px):
            missing_prices.append(ticker)
            continue
        avg_cost = row.get("avg_cost")
        avg_cost_val = None
        try:
            if avg_cost is not None and not pd.isna(avg_cost):
                avg_cost_val = float(avg_cost)
        except Exception:
            avg_cost_val = None
        market_value = float(shares * float(px))
        unrealized = (
            float((float(px) - avg_cost_val) * shares)
            if avg_cost_val is not None
            else 0.0
        )
        rows.append(
            {
                "ticker": ticker,
                "shares": float(shares),
                "price": float(px),
                "market_value": market_value,
                "avg_cost": avg_cost_val,
                "unrealized_pnl": unrealized,
                "sleeve": row.get("sleeve", ""),
            }
        )

    if missing_prices:
        unique = sorted(set(missing_prices))
        raise ValueError(f"Missing asof close for tickers: {', '.join(unique)}")

    mtm = pd.DataFrame(rows)
    if not mtm.empty:
        mtm = mtm.sort_values("ticker").reset_index(drop=True)
    market_value_total = float(mtm["market_value"].sum()) if not mtm.empty else 0.0
    equity = float(float(cash) + market_value_total)

    if equity > 0 and not mtm.empty:
        weights = {
            str(r["ticker"]).upper(): float(r["market_value"]) / equity
            for _, r in mtm.iterrows()
        }
        has_shorts = bool((mtm["shares"] < 0).any())
        exposure = compute_exposure(
            weights,
            leverage_enabled=has_shorts,
            enforce_bounds=not has_shorts,
        )
        gross_exposure = float(exposure["gross_exposure"])
        net_exposure = float(exposure["net_exposure"])
    else:
        gross_exposure = 0.0
        net_exposure = 0.0

    nav = {
        "equity": equity,
        "cash": float(cash),
        "gross_exposure": gross_exposure,
        "net_exposure": net_exposure,
        "totals": {
            "market_value": market_value_total,
            "unrealized_pnl": float(mtm["unrealized_pnl"].sum()) if not mtm.empty else 0.0,
        },
    }
    return mtm, nav


def determine_sleeve_state(
    sleeve_object: Any,
    allocation_weight: float = 0.0,
    *,
    weight_tolerance: float = 1e-9,
) -> dict[str, Any]:
    """Canonical ACTIVE/INACTIVE determination shared by allocator + reporting."""
    equity_df = pd.DataFrame()
    target_weights = pd.DataFrame()

    if isinstance(sleeve_object, Mapping):
        equity_df = _safe_df(sleeve_object.get("equity_df"))
        target_weights = _safe_df(sleeve_object.get("target_weights"))
    elif isinstance(sleeve_object, pd.DataFrame):
        equity_df = _safe_df(sleeve_object)

    try:
        alloc_weight = float(allocation_weight)
    except Exception:
        alloc_weight = 0.0

    has_equity = not equity_df.empty
    has_target_weights = not target_weights.empty
    has_allocation = alloc_weight > float(weight_tolerance)
    active = bool(has_equity and has_target_weights and has_allocation)

    missing: list[str] = []
    if not has_equity:
        missing.append("equity_df empty")
    if not has_target_weights:
        missing.append("target_weights empty")
    if not has_allocation:
        missing.append("allocation <= 0")

    return {
        "active": active,
        "allocation_weight": alloc_weight,
        "has_equity_df": has_equity,
        "has_target_weights": has_target_weights,
        "reason": "active" if active else ", ".join(missing),
    }
