from __future__ import annotations

import pandas as pd

from .signals import build_flow_signals
from .v2_regimes import build_regime_frame


def build_flow_signals_v2(panel: pd.DataFrame) -> pd.DataFrame:
    df = build_flow_signals(panel, use_efficiency_filter=False).sort_values(["ticker", "date"]).reset_index(drop=True)
    grouped = df.groupby("ticker", group_keys=False)

    df["signed_participation"] = df["volume_z"] * df["r1"]
    df["positive_signed_participation"] = (df["signed_participation"] > 0).astype(int)
    df["volume_z_3d_avg"] = grouped["volume_z"].transform(lambda s: s.rolling(3, min_periods=3).mean())
    df["volume_z_5d_avg"] = grouped["volume_z"].transform(lambda s: s.rolling(5, min_periods=5).mean())
    df["accumulation_3d"] = grouped["signed_participation"].transform(lambda s: s.rolling(3, min_periods=3).sum())
    df["accumulation_5d"] = grouped["signed_participation"].transform(lambda s: s.rolling(5, min_periods=5).sum())
    df["participation_positive_count_3d"] = grouped["positive_signed_participation"].transform(lambda s: s.rolling(3, min_periods=3).sum())
    df["participation_positive_count_5d"] = grouped["positive_signed_participation"].transform(lambda s: s.rolling(5, min_periods=5).sum())
    df["persistent_participation_3d"] = df["participation_positive_count_3d"] >= 2
    df["persistent_participation_5d"] = df["participation_positive_count_5d"] >= 3

    df["recent_5d_return"] = grouped["close"].pct_change(5)
    df["recent_20d_return"] = grouped["close"].pct_change(20)
    df["extended_momentum"] = (df["momentum_rank_pct"] >= 0.80) & (df["recent_5d_return"] > 0)
    df["exhaustion_flow"] = df["extended_momentum"] & (
        (df["volume_z"] > 1.5)
        | (df["volume_z_3d_avg"] > 1.0)
        | ((df["accumulation_3d"] > 0) & df["persistent_participation_3d"])
    )

    df["slower_participation_3d"] = (
        (df["volume_z_3d_avg"] > 0)
        & (df["accumulation_3d"] > 0)
        & df["persistent_participation_3d"]
    )
    df["slower_participation_5d"] = (
        (df["volume_z_5d_avg"] > 0)
        & (df["accumulation_5d"] > 0)
        & df["persistent_participation_5d"]
    )
    df["participation_entry_signal"] = df["momentum_only"] & (df["slower_participation_3d"] | df["slower_participation_5d"])

    regime = build_regime_frame(panel)
    df = df.merge(regime, on="date", how="left")
    df["regime_conditional_entry_signal"] = df["participation_entry_signal"] & df["trend_state"].isin(["strong_up", "weak_up"]) & (df["vol_bucket"] == "normal")
    df["signal_ready_v2"] = (
        df["signal_ready"]
        & df["volume_z_3d_avg"].notna()
        & df["accumulation_3d"].notna()
        & df["trend_state"].notna()
        & df["vol_bucket"].notna()
    )
    return df
