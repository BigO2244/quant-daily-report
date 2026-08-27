from __future__ import annotations

import pandas as pd


def build_alpha_lab_signal_frame(panel: pd.DataFrame) -> pd.DataFrame:
    if panel.empty:
        return pd.DataFrame()

    df = panel.copy().sort_values(["ticker", "date"]).reset_index(drop=True)
    grouped = df.groupby("ticker", group_keys=False)

    df["r1"] = grouped["close"].pct_change(1)
    df["r3"] = grouped["close"].pct_change(3)
    df["r6_1"] = grouped["close"].shift(21) / grouped["close"].shift(126) - 1.0
    df["r12_1"] = grouped["close"].shift(21) / grouped["close"].shift(252) - 1.0
    df["momentum_score"] = 0.5 * df["r12_1"] + 0.3 * df["r6_1"] + 0.2 * df["r3"]
    df["momentum_rank"] = df.groupby("date")["momentum_score"].rank(method="first", ascending=False)
    df["momentum_rank_pct"] = df.groupby("date")["momentum_score"].rank(method="average", pct=True)
    df["signal_ready"] = df["momentum_score"].notna()

    # H4: short-term mean reversion
    df["r3_rank_pct"] = df.groupby("date")["r3"].rank(method="average", pct=True)
    df["mean_reversion_signal"] = df["r3_rank_pct"] <= 0.10

    # H5: post-move drift
    df["large_up_1d"] = df.groupby("date")["r1"].rank(method="average", pct=True) >= 0.90
    df["post_move_drift_signal"] = (
        grouped["large_up_1d"].shift(2).astype("boolean").fillna(False).astype(bool)
        & (grouped["r1"].shift(1) > 0)
        & (df["r1"] > 0)
    )

    # H3: simple SPY trend regime
    spy = df[df["ticker"] == "SPY"][["date", "close"]].sort_values("date").copy()
    if spy.empty:
        regime = pd.DataFrame(columns=["date", "spy_above_200dma"])
    else:
        spy["spy_ema200"] = spy["close"].ewm(span=200, adjust=False).mean()
        spy["spy_above_200dma"] = (spy["close"] > spy["spy_ema200"]).astype(bool)
        regime = spy[["date", "spy_above_200dma"]]
    df = df.merge(regime, on="date", how="left")
    df["spy_above_200dma"] = df["spy_above_200dma"].astype("boolean").fillna(False).astype(bool)
    return df


def current_signal_readiness(
    signals: pd.DataFrame,
    *,
    effective_date: str,
    required_symbols: list[str],
) -> dict:
    required = sorted({str(symbol).upper() for symbol in required_symbols})
    if signals.empty:
        ready: set[str] = set()
    else:
        current = signals[pd.to_datetime(signals["date"]).dt.normalize() == pd.Timestamp(effective_date)]
        ready = set(
            current.loc[current["signal_ready"].fillna(False).astype(bool), "ticker"]
            .astype(str)
            .str.upper()
        )
    missing = sorted(set(required) - ready)
    return {
        "status": "OK" if not missing else "INCOMPLETE",
        "effective_date": str(pd.Timestamp(effective_date).date()),
        "symbols_required_count": len(required),
        "symbols_signal_ready_count": len(required) - len(missing),
        "missing_signal_ready_symbols": missing,
    }
