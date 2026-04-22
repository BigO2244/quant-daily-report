from __future__ import annotations

import pandas as pd


def build_flow_signals(
    panel: pd.DataFrame,
    *,
    use_efficiency_filter: bool = False,
    volume_z_threshold: float = 1.5,
    r1_threshold: float = 0.005,
    r3_threshold: float = 0.015,
    efficiency_floor: float = 1e-6,
) -> pd.DataFrame:
    if panel.empty:
        return pd.DataFrame()

    df = panel.copy().sort_values(["ticker", "date"]).reset_index(drop=True)
    grouped = df.groupby("ticker", group_keys=False)

    df["r1"] = grouped["close"].pct_change(1)
    df["r3"] = grouped["close"].pct_change(3)

    shifted_volume = grouped["volume"].shift(1)
    df["vol_mean_20"] = shifted_volume.groupby(df["ticker"]).rolling(20, min_periods=20).mean().reset_index(level=0, drop=True)
    df["vol_std_20"] = shifted_volume.groupby(df["ticker"]).rolling(20, min_periods=20).std(ddof=0).reset_index(level=0, drop=True)
    df["volume_z"] = (df["volume"] - df["vol_mean_20"]) / df["vol_std_20"].where(df["vol_std_20"] > 0)

    prev_close = grouped["close"].shift(1)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    df["atr20"] = tr.groupby(df["ticker"]).ewm(span=20, adjust=False).mean().reset_index(level=0, drop=True)
    df["atr20_pct"] = df["atr20"] / df["close"].where(df["close"] > 0)

    df["ema50"] = grouped["close"].transform(lambda s: s.ewm(span=50, adjust=False).mean())
    df["ema200"] = grouped["close"].transform(lambda s: s.ewm(span=200, adjust=False).mean())
    df["trend_flag"] = (df["ema50"] > df["ema200"]).astype(float)
    df["r12_1"] = grouped["close"].shift(21) / grouped["close"].shift(252) - 1.0
    df["r6_1"] = grouped["close"].shift(21) / grouped["close"].shift(126) - 1.0
    df["r3_1_skip"] = grouped["close"].shift(21) / grouped["close"].shift(63) - 1.0

    for col in ("r12_1", "r6_1", "r3_1_skip"):
        df[f"z_{col}"] = df.groupby("date")[col].transform(_zscore)

    raw = (
        0.45 * df["z_r12_1"].fillna(0.0)
        + 0.30 * df["z_r6_1"].fillna(0.0)
        + 0.15 * df["z_r3_1_skip"].fillna(0.0)
        + 0.10 * df["trend_flag"].fillna(0.0)
    )
    df["momentum_score"] = raw / df["atr20_pct"].clip(lower=0.01)
    df["momentum_rank_pct"] = df.groupby("date")["momentum_score"].rank(method="average", pct=True)

    vol_denom = df["volume_z"].clip(lower=efficiency_floor)
    df["efficiency"] = df["r1"] / vol_denom
    df["efficiency_median"] = df.groupby("date")["efficiency"].transform("median")
    df["flow_active"] = (
        (df["volume_z"] > volume_z_threshold)
        & (df["r1"] > r1_threshold)
        & (df["r3"] > r3_threshold)
    )
    df["flow_active_v1_1"] = df["flow_active"] & (df["efficiency"] > df["efficiency_median"])
    df["flow_flag"] = df["flow_active_v1_1"] if use_efficiency_filter else df["flow_active"]
    df["momentum_only"] = (df["r1"] > r1_threshold) & (df["r3"] > r3_threshold)
    df["signal_ready"] = (
        df["momentum_score"].notna()
        & df["r1"].notna()
        & df["r3"].notna()
        & df["volume_z"].notna()
    )
    return df


def _zscore(series: pd.Series) -> pd.Series:
    valid = series.dropna()
    if len(valid) < 3:
        return pd.Series(0.0, index=series.index)
    sigma = valid.std(ddof=0)
    if sigma == 0 or pd.isna(sigma):
        return pd.Series(0.0, index=series.index)
    mu = valid.mean()
    return (series - mu) / sigma
