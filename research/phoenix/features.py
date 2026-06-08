from __future__ import annotations

import pandas as pd


def _rsi(close: pd.Series, window: int) -> pd.Series:
    delta = close.diff()
    gains = delta.clip(lower=0.0)
    losses = (-delta).clip(lower=0.0)
    avg_gain = gains.rolling(window, min_periods=window).mean()
    avg_loss = losses.rolling(window, min_periods=window).mean()
    rs = avg_gain / avg_loss.replace(0.0, pd.NA)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return rsi.fillna(50.0)


def _percentile_rank(series: pd.Series) -> pd.Series:
    if series.empty:
        return series
    return series.rank(pct=True, method="average").fillna(0.0)


def build_phoenix_feature_frame(panel: pd.DataFrame) -> pd.DataFrame:
    """Build point-in-time Phoenix features from adjusted daily OHLCV.

    Features at date ``t`` use only rows with timestamps ``<= t``. Forward
    returns are included for backtest evaluation only and are never used by
    selection helpers.
    """
    if panel is None or panel.empty:
        return pd.DataFrame(
            columns=[
                "date",
                "ticker",
                "close",
                "forward_return",
                "phoenix_score",
                "eligible",
                "signal_ready",
            ]
        )

    frame = panel.copy()
    frame["date"] = pd.to_datetime(frame["date"]).dt.normalize()
    frame["ticker"] = frame["ticker"].astype(str).str.upper().str.strip()
    for col in ("open", "high", "low", "close", "volume"):
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame = frame.dropna(subset=["date", "ticker", "close"])
    frame = frame.sort_values(["ticker", "date"]).drop_duplicates(["ticker", "date"], keep="last")

    pieces: list[pd.DataFrame] = []
    for ticker, group in frame.groupby("ticker", sort=True):
        g = group.sort_values("date").copy()
        close = g["close"].astype(float)
        high = g["high"].astype(float)
        low = g["low"].astype(float)
        volume = g["volume"].astype(float)
        prev_close = close.shift(1)
        true_range = pd.concat(
            [
                (high - low).abs(),
                (high - prev_close).abs(),
                (low - prev_close).abs(),
            ],
            axis=1,
        ).max(axis=1)
        g["history_count"] = range(1, len(g) + 1)
        g["return_1d"] = close.pct_change(1)
        g["return_3d"] = close.pct_change(3)
        g["return_5d"] = close.pct_change(5)
        g["return_10d"] = close.pct_change(10)
        g["return_20d"] = close.pct_change(20)
        g["forward_return"] = close.pct_change().shift(-1)
        g["volume_avg_20d"] = volume.rolling(20, min_periods=20).mean()
        g["volume_shock_20d"] = volume / g["volume_avg_20d"].replace(0.0, pd.NA)
        g["dollar_volume"] = close * volume
        g["median_dollar_volume_20d"] = g["dollar_volume"].rolling(20, min_periods=20).median()
        g["atr_20d"] = true_range.rolling(20, min_periods=20).mean()
        g["atr_pct_20d"] = g["atr_20d"] / close.replace(0.0, pd.NA)
        g["range_pct"] = true_range / close.replace(0.0, pd.NA)
        g["atr_range_shock"] = true_range / g["atr_20d"].replace(0.0, pd.NA)
        g["rsi_2"] = _rsi(close, 2)
        g["rsi_5"] = _rsi(close, 5)
        g["ma_10d"] = close.rolling(10, min_periods=10).mean()
        g["ma_20d"] = close.rolling(20, min_periods=20).mean()
        g["ma_60d"] = close.rolling(60, min_periods=60).mean()
        g["distance_below_20d_ma"] = ((g["ma_20d"] - close) / g["ma_20d"]).clip(lower=0.0)
        g["prior_uptrend"] = close.shift(5) > g["ma_60d"].shift(5)
        g["lower_low_streak"] = (
            (close < close.shift(1)).astype(int)
            + (close.shift(1) < close.shift(2)).astype(int)
            + (close.shift(2) < close.shift(3)).astype(int)
        )
        pieces.append(g)

    out = pd.concat(pieces, ignore_index=True) if pieces else pd.DataFrame()
    if out.empty:
        return out

    scored: list[pd.DataFrame] = []
    for date, daily in out.groupby("date", sort=True):
        d = daily.copy()
        d["drawdown_5d_rank"] = _percentile_rank((-d["return_5d"]).clip(lower=0.0))
        d["drawdown_10d_rank"] = _percentile_rank((-d["return_10d"]).clip(lower=0.0))
        d["volume_shock_rank"] = _percentile_rank(d["volume_shock_20d"])
        d["range_shock_rank"] = _percentile_rank(d["atr_range_shock"])
        oversold = ((50.0 - d["rsi_2"]).clip(lower=0.0) + (50.0 - d["rsi_5"]).clip(lower=0.0)) / 100.0
        d["oversold_rank"] = _percentile_rank(oversold)
        d["ma_distance_rank"] = _percentile_rank(d["distance_below_20d_ma"])
        d["falling_knife_penalty"] = 0.0
        d.loc[d["lower_low_streak"] >= 3, "falling_knife_penalty"] += 0.10
        d.loc[d["return_1d"] <= -0.25, "falling_knife_penalty"] += 0.25
        d["market_stress_bonus"] = 0.0
        spy = d[d["ticker"] == "SPY"]
        if not spy.empty:
            spy_ret5 = float(spy["return_5d"].iloc[0] or 0.0)
            spy_ret10 = float(spy["return_10d"].iloc[0] or 0.0)
            if spy_ret5 <= -0.02 or spy_ret10 <= -0.03:
                d["market_stress_bonus"] = 0.05
        d["phoenix_score"] = (
            0.30 * d["drawdown_5d_rank"]
            + 0.20 * d["drawdown_10d_rank"]
            + 0.15 * d["volume_shock_rank"]
            + 0.15 * d["range_shock_rank"]
            + 0.10 * d["oversold_rank"]
            + 0.10 * d["ma_distance_rank"]
            - d["falling_knife_penalty"]
            + d["market_stress_bonus"]
        ).fillna(0.0)
        scored.append(d)

    return pd.concat(scored, ignore_index=True).sort_values(["date", "ticker"]).reset_index(drop=True)
