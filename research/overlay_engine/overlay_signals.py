from __future__ import annotations

import pandas as pd


def derive_overlay_signal_frame(canonical_df: pd.DataFrame) -> pd.DataFrame:
    if canonical_df.empty:
        return pd.DataFrame(columns=["date", "overlay_signal", "active_overlay", "overlay_multiplier"])
    df = canonical_df.copy().sort_values("date")
    signal = df.get("overlay_signal")
    active = df.get("active_overlay")
    if active is None:
        active = signal.astype(str).str.upper().isin({"PARTIAL", "LOCK", "ELEVATED", "HIGH", "CRISIS"}) if signal is not None else False
    multiplier = pd.Series(1.0, index=df.index)
    if signal is not None:
        signal_u = signal.astype(str).str.upper()
        multiplier = multiplier.where(~signal_u.isin(["PARTIAL", "ELEVATED"]), 0.5)
        multiplier = multiplier.where(~signal_u.isin(["LOCK", "HIGH", "CRISIS"]), 0.0)
    out = pd.DataFrame(
        {
            "date": df["date"],
            "overlay_signal": signal if signal is not None else pd.NA,
            "active_overlay": active,
            "overlay_multiplier": multiplier,
        }
    )
    return out
