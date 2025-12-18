import numpy as np
import pandas as pd

from sleeves.sleeve_2.config import (
    MIN_INDUSTRY_COUNT,
    PE_PREFER_FORWARD,
    PE_ABS_CAP,
    W_Z,
    W_TREND,
)

def _pick_pe_column(df: pd.DataFrame) -> pd.Series:
    cols = df.columns

    pe = None
    if PE_PREFER_FORWARD and "forward_pe" in cols:
        pe = df["forward_pe"]
    if pe is None and "trailing_pe" in cols:
        pe = df["trailing_pe"]
    if pe is None and "pe" in cols:
        pe = df["pe"]

    if pe is None:
        raise ValueError("No P/E column found. Expected: forward_pe, trailing_pe, or pe")

    pe = pd.to_numeric(pe, errors="coerce")
    pe = pe.where((pe > 0) & (pe <= PE_ABS_CAP))
    return pe

def _rank_to_0_100(x: pd.Series) -> pd.Series:
    r = x.rank(pct=True, method="average")
    return (r * 100.0).clip(0, 100)

def build_signals(factor_df: pd.DataFrame) -> pd.DataFrame:
    df = factor_df.copy()

    required = {"date", "ticker"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"factor_df missing required columns: {missing}")

    if "industry" not in df.columns:
        raise ValueError("factor_df missing required column: industry")

    df["date"] = pd.to_datetime(df["date"])
    df["pe"] = _pick_pe_column(df)

    grp = df.groupby(["date", "industry"], dropna=False)

    df["ind_n"] = grp["pe"].transform("count")
    df["ind_mu"] = grp["pe"].transform("mean")
    df["ind_sigma"] = grp["pe"].transform("std").replace(0, np.nan)

    df["z_pe"] = (df["pe"] - df["ind_mu"]) / df["ind_sigma"]
    df.loc[df["ind_n"] < MIN_INDUSTRY_COUNT, "z_pe"] = np.nan

    df = df.sort_values(["ticker", "date"])
    df["pe_lag20"] = df.groupby("ticker")["pe"].shift(20)
    df["pe_change_20d"] = (df["pe"] / df["pe_lag20"]) - 1.0

    by_date = df.groupby("date")

    df["rank_long_z"] = by_date["z_pe"].transform(lambda s: _rank_to_0_100(-s))
    df["rank_long_trend"] = by_date["pe_change_20d"].transform(lambda s: _rank_to_0_100(-s))
    df["score_long"] = (W_Z * df["rank_long_z"] + W_TREND * df["rank_long_trend"]).clip(0, 100)

    df["score_short"] = by_date["z_pe"].transform(lambda s: _rank_to_0_100(s))

    keep = ["date", "ticker", "industry", "pe", "z_pe", "pe_change_20d", "score_long", "score_short"]
    return df[keep]
