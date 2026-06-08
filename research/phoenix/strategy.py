from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from alpha_stack.research.metrics import summarise_performance
from research.phoenix.features import build_phoenix_feature_frame


PHOENIX_STRATEGY_ID = "caerus_phoenix"
PHOENIX_DISPLAY_NAME = "Caerus Phoenix"
BENCHMARK_SYMBOL = "SPY"


@dataclass(frozen=True)
class PhoenixConfig:
    top_n: int = 10
    max_gross: float = 0.80
    max_weight: float = 0.10
    min_price: float = 5.0
    min_median_dollar_volume_20d: float = 20_000_000.0
    min_history_days: int = 252
    local_stress_return_5d: float = -0.07
    local_stress_volume_shock: float = 1.5
    market_stress_spy_return_5d: float = -0.02
    max_position_vol_ann: float = 0.80
    max_single_day_loss: float = -0.25
    min_holding_period_days: int = 5
    max_holding_period_days: int = 20
    transaction_cost_bps: float = 25.0
    weighting: str = "equal"


def _effective_trade_date(features: pd.DataFrame, trade_date: str) -> pd.Timestamp | None:
    if features.empty or "date" not in features.columns:
        return None
    dates = pd.DatetimeIndex(pd.to_datetime(features["date"]).dropna().unique()).sort_values()
    eligible = dates[dates <= pd.Timestamp(trade_date)]
    if len(eligible) == 0:
        return None
    return pd.Timestamp(eligible[-1])


def _eligibility_frame(features: pd.DataFrame, config: PhoenixConfig) -> pd.DataFrame:
    frame = features.copy()
    spy_ret5 = frame.loc[frame["ticker"] == BENCHMARK_SYMBOL, "return_5d"]
    market_stress = bool(not spy_ret5.empty and float(spy_ret5.iloc[0] or 0.0) <= config.market_stress_spy_return_5d)
    local_stress = (
        (frame["return_5d"] <= config.local_stress_return_5d)
        & (frame["volume_shock_20d"] >= config.local_stress_volume_shock)
    )
    vol_ok = (
        frame["atr_pct_20d"].fillna(0.0) * (252.0 ** 0.5)
    ) <= config.max_position_vol_ann
    frame["market_stress_active"] = market_stress
    frame["local_stress_active"] = local_stress.fillna(False)
    frame["eligible"] = (
        (frame["ticker"] != BENCHMARK_SYMBOL)
        & (frame["close"] >= config.min_price)
        & (frame["history_count"] >= config.min_history_days)
        & (frame["median_dollar_volume_20d"] >= config.min_median_dollar_volume_20d)
        & (frame["volume"] > 0)
        & (frame["return_1d"] > config.max_single_day_loss)
        & vol_ok.fillna(False)
        & (market_stress | local_stress.fillna(False))
    )
    frame["signal_ready"] = frame["eligible"]
    return frame


def _weights_for_candidates(candidates: pd.DataFrame, config: PhoenixConfig) -> pd.Series:
    if candidates.empty:
        return pd.Series(dtype=float)
    selected = candidates.sort_values(
        ["phoenix_score", "volume_shock_20d", "ticker"],
        ascending=[False, False, True],
    ).head(int(config.top_n))
    if selected.empty:
        return pd.Series(dtype=float)
    gross = min(float(config.max_gross), float(config.max_weight) * len(selected))
    if config.weighting == "inverse_vol":
        inv = 1.0 / selected.set_index("ticker")["atr_pct_20d"].clip(lower=0.01)
        weights = inv / inv.sum() * gross
        weights = weights.clip(upper=float(config.max_weight))
        if float(weights.sum()) > 0:
            weights = weights / float(weights.sum()) * min(gross, float(weights.sum()))
    else:
        weights = pd.Series(
            gross / len(selected),
            index=pd.Index(selected["ticker"].astype(str), dtype=str),
            dtype=float,
        )
    return weights.round(10)


def build_phoenix_snapshot(
    panel: pd.DataFrame,
    *,
    trade_date: str,
    config: PhoenixConfig | None = None,
) -> dict[str, Any]:
    cfg = config or PhoenixConfig()
    features = build_phoenix_feature_frame(panel)
    effective = _effective_trade_date(features, trade_date)
    if effective is None:
        return _empty_snapshot(trade_date=trade_date, reason="NO_PRICE_DATA", config=cfg)
    daily = features[features["date"] == effective].copy()
    ranked = _eligibility_frame(daily, cfg)
    candidates = ranked[ranked["eligible"]].copy()
    weights = _weights_for_candidates(candidates, cfg)
    selected = set(weights.index)
    ranked["is_selected"] = ranked["ticker"].isin(selected)
    reason_codes = _snapshot_reason_codes(ranked, cfg)
    rank_table = ranked.sort_values(
        ["eligible", "phoenix_score", "volume_shock_20d", "ticker"],
        ascending=[False, False, False, True],
    ).reset_index(drop=True)
    holdings = _holdings_from_weights(rank_table=rank_table, weights=weights, config=cfg)
    if holdings:
        reason_codes = ["ok"]
    return {
        "schema_version": "phoenix_snapshot_v1",
        "strategy_id": PHOENIX_STRATEGY_ID,
        "strategy_slug": PHOENIX_STRATEGY_ID,
        "strategy_name": PHOENIX_DISPLAY_NAME,
        "trade_date": str(pd.Timestamp(trade_date).date()),
        "effective_trade_date": str(effective.date()),
        "governance_label": "RESEARCH_ONLY",
        "execution_impact": "NON_EXECUTIONAL",
        "status": "OK" if holdings else "NO_ELIGIBLE_NAMES",
        "reason_code": None if holdings else reason_codes[0],
        "reason_codes": reason_codes,
        "benchmark_symbol": BENCHMARK_SYMBOL,
        "target_weights": {ticker: round(float(weight), 10) for ticker, weight in weights.items()},
        "cash_weight": round(float(1.0 - weights.sum()), 10),
        "holdings": holdings,
        "holdings_count": int(len(holdings)),
        "rank_table": _json_rank_table(rank_table),
        "signal_diagnostics": _signal_diagnostics(rank_table),
        "data_coverage": _data_coverage(rank_table=rank_table, trade_date=trade_date, effective_trade_date=effective),
        "expected_turnover": None,
        "estimated_holding_period_days": _holding_period_label(cfg),
        "weight_concentration": _weight_concentration(weights),
        "config": cfg.__dict__,
    }


def _empty_snapshot(*, trade_date: str, reason: str, config: PhoenixConfig) -> dict[str, Any]:
    return {
        "schema_version": "phoenix_snapshot_v1",
        "strategy_id": PHOENIX_STRATEGY_ID,
        "strategy_slug": PHOENIX_STRATEGY_ID,
        "strategy_name": PHOENIX_DISPLAY_NAME,
        "trade_date": str(pd.Timestamp(trade_date).date()),
        "effective_trade_date": None,
        "governance_label": "RESEARCH_ONLY",
        "execution_impact": "NON_EXECUTIONAL",
        "status": "NO_DATA",
        "reason_code": reason,
        "reason_codes": [reason],
        "benchmark_symbol": BENCHMARK_SYMBOL,
        "target_weights": {},
        "cash_weight": 1.0,
        "holdings": [],
        "holdings_count": 0,
        "rank_table": [],
        "signal_diagnostics": {"selected": [], "top_rejected": []},
        "data_coverage": {
            "input_rows_used": 0,
            "input_symbols_used": 0,
            "trade_date": str(pd.Timestamp(trade_date).date()),
            "effective_trade_date": None,
            "reason_codes": [reason],
        },
        "expected_turnover": None,
        "estimated_holding_period_days": _holding_period_label(config),
        "weight_concentration": _weight_concentration(pd.Series(dtype=float)),
        "config": config.__dict__,
    }


def _holdings_from_weights(
    *,
    rank_table: pd.DataFrame,
    weights: pd.Series,
    config: PhoenixConfig,
) -> list[dict[str, Any]]:
    holdings: list[dict[str, Any]] = []
    for ticker, weight in weights.sort_values(ascending=False).items():
        row = rank_table[rank_table["ticker"] == ticker]
        rec = row.iloc[0].to_dict() if not row.empty else {}
        holdings.append(
            {
                "ticker": str(ticker),
                "target_weight": round(float(weight), 10),
                "phoenix_score": round(float(rec.get("phoenix_score") or 0.0), 10),
                "return_5d": _round_or_none(rec.get("return_5d")),
                "return_10d": _round_or_none(rec.get("return_10d")),
                "volume_shock_20d": _round_or_none(rec.get("volume_shock_20d")),
                "atr_range_shock": _round_or_none(rec.get("atr_range_shock")),
                "rsi_2": _round_or_none(rec.get("rsi_2")),
                "rsi_5": _round_or_none(rec.get("rsi_5")),
                "selection_reason": "crisis_reversal_dislocation",
                "reason_codes": ["crisis_reversal_dislocation"],
                "estimated_holding_period_days": _holding_period_label(config),
            }
        )
    return holdings


def _snapshot_reason_codes(rank_table: pd.DataFrame, config: PhoenixConfig) -> list[str]:
    if rank_table.empty:
        return ["NO_PRICE_DATA"]
    reasons: list[str] = []
    if not bool(rank_table.get("market_stress_active", pd.Series(dtype=bool)).any()) and not bool(
        rank_table.get("local_stress_active", pd.Series(dtype=bool)).any()
    ):
        reasons.append("NO_CRISIS_REGIME")
    if "history_count" in rank_table.columns and int(rank_table["history_count"].fillna(0).max()) < int(config.min_history_days):
        reasons.append("INSUFFICIENT_HISTORY")
    if "median_dollar_volume_20d" in rank_table.columns and rank_table["median_dollar_volume_20d"].dropna().empty:
        reasons.append("MISSING_LIQUIDITY_HISTORY")
    if not bool(rank_table.get("eligible", pd.Series(dtype=bool)).any()):
        reasons.append("NO_ELIGIBLE_NAMES")
    return sorted(set(reasons)) or ["NO_ELIGIBLE_NAMES"]


def _data_coverage(
    *,
    rank_table: pd.DataFrame,
    trade_date: str,
    effective_trade_date: pd.Timestamp,
) -> dict[str, Any]:
    symbols = sorted({str(symbol) for symbol in rank_table.get("ticker", pd.Series(dtype=str)).dropna().astype(str)})
    missing_close = 0
    missing_volume = 0
    if "close" in rank_table.columns:
        missing_close = int(rank_table["close"].isna().sum())
    if "volume" in rank_table.columns:
        missing_volume = int(rank_table["volume"].isna().sum())
    reasons: list[str] = []
    if pd.Timestamp(effective_trade_date).date() > pd.Timestamp(trade_date).date():
        reasons.append("LOOKAHEAD_DATE_REJECTED")
    if missing_close:
        reasons.append("MISSING_CLOSE_VALUES")
    if missing_volume:
        reasons.append("MISSING_VOLUME_VALUES")
    return {
        "input_rows_used": int(len(rank_table)),
        "input_symbols_used": int(len(symbols)),
        "trade_date": str(pd.Timestamp(trade_date).date()),
        "effective_trade_date": str(pd.Timestamp(effective_trade_date).date()),
        "symbols": symbols[:50],
        "missing_close_values": missing_close,
        "missing_volume_values": missing_volume,
        "reason_codes": sorted(set(reasons)) or ["ok"],
    }


def _holding_period_label(config: PhoenixConfig) -> str:
    return f"{int(config.min_holding_period_days)}-{int(config.max_holding_period_days)}"


def _json_rank_table(rank_table: pd.DataFrame, limit: int = 25) -> list[dict[str, Any]]:
    cols = [
        "ticker",
        "eligible",
        "is_selected",
        "phoenix_score",
        "return_5d",
        "return_10d",
        "volume_shock_20d",
        "atr_range_shock",
        "rsi_2",
        "rsi_5",
        "falling_knife_penalty",
        "market_stress_active",
        "local_stress_active",
        "close",
        "median_dollar_volume_20d",
        "history_count",
    ]
    rows = []
    for raw in rank_table[[c for c in cols if c in rank_table.columns]].head(limit).to_dict("records"):
        rows.append({k: _json_value(v) for k, v in raw.items()})
    return rows


def _signal_diagnostics(rank_table: pd.DataFrame) -> dict[str, Any]:
    selected = rank_table[rank_table.get("is_selected", False) == True] if not rank_table.empty else pd.DataFrame()
    rejected = rank_table[rank_table.get("is_selected", False) == False] if not rank_table.empty else pd.DataFrame()
    return {
        "selected": _json_rank_table(selected, limit=20),
        "top_rejected": _json_rank_table(rejected, limit=20),
        "diagnostic_fields": [
            "phoenix_score",
            "return_5d",
            "return_10d",
            "volume_shock_20d",
            "atr_range_shock",
            "rsi_2",
            "rsi_5",
            "falling_knife_penalty",
            "market_stress_active",
            "local_stress_active",
        ],
    }


def _weight_concentration(weights: pd.Series) -> dict[str, Any]:
    w = weights[weights > 0].sort_values(ascending=False)
    return {
        "holdings_count": int(len(w)),
        "max_weight": round(float(w.max()), 10) if not w.empty else 0.0,
        "top3_concentration": round(float(w.head(3).sum()), 10) if not w.empty else 0.0,
        "top5_concentration": round(float(w.head(5).sum()), 10) if not w.empty else 0.0,
    }


def run_phoenix_backtest(
    panel: pd.DataFrame,
    *,
    start_date: str,
    end_date: str,
    config: PhoenixConfig | None = None,
) -> dict[str, Any]:
    cfg = config or PhoenixConfig()
    features = build_phoenix_feature_frame(panel)
    if features.empty:
        return _empty_backtest(start_date=start_date, end_date=end_date, config=cfg)
    features = features[
        (features["date"] >= pd.Timestamp(start_date))
        & (features["date"] <= pd.Timestamp(end_date))
    ].copy()
    dates = pd.DatetimeIndex(features["date"].dropna().unique()).sort_values()
    nav = 1.0
    nav_rows: list[dict[str, Any]] = []
    daily_rows: list[dict[str, Any]] = []
    previous_weights = pd.Series(dtype=float)
    weight_rows: list[pd.Series] = []
    for date in dates:
        daily = features[features["date"] == date].copy()
        ranked = _eligibility_frame(daily, cfg)
        weights = _weights_for_candidates(ranked[ranked["eligible"]], cfg)
        returns = pd.Series(
            ranked.set_index("ticker")["forward_return"],
            dtype=float,
        )
        gross_return = float(weights.mul(returns.reindex(weights.index).fillna(0.0), fill_value=0.0).sum()) if not weights.empty else 0.0
        turnover = float(previous_weights.sub(weights, fill_value=0.0).abs().sum())
        net_return = gross_return - turnover * (cfg.transaction_cost_bps / 10000.0)
        nav *= 1.0 + net_return
        nav_rows.append({"date": date, "nav": round(float(nav), 10)})
        daily_rows.append(
            {
                "date": date,
                "gross_return": gross_return,
                "net_return": net_return,
                "turnover": turnover,
                "holdings_count": int(len(weights)),
            }
        )
        weight_rows.append(weights.rename(date))
        previous_weights = weights

    nav_df = pd.DataFrame(nav_rows)
    daily_df = pd.DataFrame(daily_rows)
    weights_df = pd.DataFrame(weight_rows).fillna(0.0) if weight_rows else pd.DataFrame()
    returns_series = pd.Series(daily_df["net_return"].values, index=pd.to_datetime(daily_df["date"]), name="return") if not daily_df.empty else pd.Series(dtype=float)
    nav_series = pd.Series(nav_df["nav"].values, index=pd.to_datetime(nav_df["date"]), name="nav") if not nav_df.empty else pd.Series(dtype=float)
    benchmark_returns = _benchmark_forward_returns(features, dates)
    summary = summarise_performance(
        nav_series,
        returns=returns_series,
        benchmark_returns=benchmark_returns.reindex(returns_series.index) if benchmark_returns is not None else None,
        label=PHOENIX_DISPLAY_NAME,
    ) if not nav_series.empty else {}
    summary.update(
        {
            "strategy_id": PHOENIX_STRATEGY_ID,
            "strategy_slug": PHOENIX_STRATEGY_ID,
            "strategy_name": PHOENIX_DISPLAY_NAME,
            "governance_label": "RESEARCH_ONLY",
            "execution_impact": "NON_EXECUTIONAL",
            "start_date": str(pd.Timestamp(start_date).date()),
            "end_date": str(pd.Timestamp(end_date).date()),
            "avg_turnover": round(float(daily_df["turnover"].mean()), 10) if not daily_df.empty else 0.0,
            "avg_holdings_count": round(float(daily_df["holdings_count"].mean()), 4) if not daily_df.empty else 0.0,
            "config": cfg.__dict__,
        }
    )
    return {
        "schema_version": "phoenix_backtest_v1",
        "strategy_id": PHOENIX_STRATEGY_ID,
        "strategy_slug": PHOENIX_STRATEGY_ID,
        "strategy_name": PHOENIX_DISPLAY_NAME,
        "governance_label": "RESEARCH_ONLY",
        "execution_impact": "NON_EXECUTIONAL",
        "summary": summary,
        "nav": nav_df,
        "daily": daily_df,
        "weights": weights_df,
    }


def _benchmark_forward_returns(features: pd.DataFrame, dates: pd.DatetimeIndex) -> pd.Series | None:
    spy = features[features["ticker"] == BENCHMARK_SYMBOL].copy()
    if spy.empty:
        return None
    series = pd.Series(spy["forward_return"].values, index=pd.to_datetime(spy["date"]), name=BENCHMARK_SYMBOL)
    return series.reindex(dates)


def _empty_backtest(*, start_date: str, end_date: str, config: PhoenixConfig) -> dict[str, Any]:
    return {
        "schema_version": "phoenix_backtest_v1",
        "strategy_id": PHOENIX_STRATEGY_ID,
        "strategy_name": PHOENIX_DISPLAY_NAME,
        "governance_label": "RESEARCH_ONLY",
        "execution_impact": "NON_EXECUTIONAL",
        "summary": {
            "strategy_id": PHOENIX_STRATEGY_ID,
            "strategy_slug": PHOENIX_STRATEGY_ID,
            "strategy_name": PHOENIX_DISPLAY_NAME,
            "start_date": str(pd.Timestamp(start_date).date()),
            "end_date": str(pd.Timestamp(end_date).date()),
            "status": "NO_DATA",
            "config": config.__dict__,
        },
        "nav": pd.DataFrame(columns=["date", "nav"]),
        "daily": pd.DataFrame(columns=["date", "gross_return", "net_return", "turnover", "holdings_count"]),
        "weights": pd.DataFrame(),
    }


def _round_or_none(value: object, digits: int = 10) -> float | None:
    try:
        if pd.isna(value):
            return None
        return round(float(value), digits)
    except Exception:
        return None


def _json_value(value: object) -> Any:
    if isinstance(value, pd.Timestamp):
        return str(value.date())
    if pd.isna(value):
        return None
    if isinstance(value, (bool, str, int)):
        return value
    try:
        return round(float(value), 10)
    except Exception:
        return str(value)
