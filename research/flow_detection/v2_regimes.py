from __future__ import annotations

from pathlib import Path

import pandas as pd

from alpha_stack.regime.state_machine import classify_trend


def build_regime_frame(panel: pd.DataFrame) -> pd.DataFrame:
    dates = pd.DataFrame({"date": pd.to_datetime(panel["date"]).sort_values().unique()})
    artifact = _load_regime_artifact()
    proxy = _build_proxy_regimes(panel)
    merged = dates.merge(artifact, on="date", how="left").merge(proxy, on="date", how="left", suffixes=("", "_proxy"))
    merged["trend_state"] = merged["trend_state"].fillna(merged["trend_state_proxy"])
    merged["vol_bucket"] = merged["vol_bucket"].fillna(merged["vol_bucket_proxy"])
    merged["regime_source"] = merged["trend_state_proxy"].notna().map(lambda _: "hybrid")
    merged["regime_source"] = merged.apply(
        lambda row: "artifact" if pd.notna(row.get("trend_state")) and pd.notna(row.get("vol_bucket")) and pd.isna(row.get("trend_state_proxy")) is False else row["regime_source"],
        axis=1,
    )
    merged["regime_source"] = merged.apply(
        lambda row: "proxy" if pd.isna(row.get("trend_state")) or pd.isna(row.get("vol_bucket")) else row["regime_source"],
        axis=1,
    )
    merged["trend_state"] = merged["trend_state"].fillna("neutral")
    merged["vol_bucket"] = merged["vol_bucket"].fillna("normal")
    return merged[["date", "trend_state", "vol_bucket", "regime_source"]].sort_values("date").reset_index(drop=True)


def _load_regime_artifact() -> pd.DataFrame:
    candidates = [
        Path("outputs/regime_validation/regime_history.csv"),
        Path("outputs/regime_matrix/regime_history.csv"),
    ]
    for path in candidates:
        if not path.exists():
            continue
        df = pd.read_csv(path)
        if "date" not in df.columns:
            continue
        df["date"] = pd.to_datetime(df["date"]).dt.normalize()
        if "volatility_state" in df.columns:
            vol_col = "volatility_state"
        elif "vol_state" in df.columns:
            vol_col = "vol_state"
        else:
            vol_col = None
        out = pd.DataFrame({"date": df["date"]})
        out["trend_state"] = df.get("trend_state")
        if vol_col is not None:
            out["vol_bucket"] = df[vol_col].map(_collapse_vol_bucket)
        else:
            out["vol_bucket"] = None
        out = out.drop_duplicates("date", keep="last")
        return out
    return pd.DataFrame(columns=["date", "trend_state", "vol_bucket"])


def _build_proxy_regimes(panel: pd.DataFrame) -> pd.DataFrame:
    spy = panel[panel["ticker"] == "SPY"].sort_values("date").copy()
    if spy.empty:
        return pd.DataFrame(columns=["date", "trend_state_proxy", "vol_bucket_proxy"])
    close = spy["close"]
    ema50 = close.ewm(span=50, adjust=False).mean()
    ema200 = close.ewm(span=200, adjust=False).mean()
    t1 = (close / ema200) - 1.0
    t2 = (ema50 / ema200) - 1.0
    realized_vol_20 = close.pct_change().rolling(20, min_periods=20).std(ddof=0) * (252 ** 0.5)
    out = pd.DataFrame({"date": spy["date"]})
    out["trend_state_proxy"] = [
        classify_trend(t1_i if pd.notna(t1_i) else None, t2_i if pd.notna(t2_i) else None).value
        for t1_i, t2_i in zip(t1, t2)
    ]
    out["vol_bucket_proxy"] = realized_vol_20.apply(lambda x: "high_vol" if pd.notna(x) and x >= 0.20 else "normal")
    return out.drop_duplicates("date", keep="last")


def _collapse_vol_bucket(value: object) -> str | None:
    text = str(value).strip().lower()
    if not text or text == "nan":
        return None
    if text in {"elevated", "crisis", "high", "high_vol"}:
        return "high_vol"
    return "normal"
