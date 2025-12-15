# sleeve2/sleeve2_engine.py
import math
import pandas as pd
import numpy as np
import yfinance as yf

# ===== Config =====
UNIVERSE_PATH = "data/universe.csv"
TICKER_COL = "ticker"
BUCKET_COL = "bucket"

TREASURY_TICKER = "IEF"   # Recommendation: intermediate Treasuries (less whippy than TLT)
REB_FREQ = "W-FRI"        # rebalance weekly (Friday close -> next open in backtest)
TOP_LONGS = 3             # number of equity positions when "risk on"
MIN_BUCKET_SIZE = 3       # need enough names in a bucket to compute stable z-scores

# Risk / sizing
MAX_EQUITY_WEIGHT = 1.0   # 1.0 means 100% equities when we like them
EQUAL_WEIGHT = True       # equal weight among selected equities

# P/E stress rules (accelerates exits / forces Treasury)
PE_Z_STRESS = 2.0         # "significant deviations" threshold (configurable)
MOM_LOOKBACK = 20         # momentum lookback (trading days)
MOM_MIN = 0.00            # require non-negative momentum unless deeply cheap
CHEAP_Z = -1.0            # allow negative mom if very cheap


def load_universe(path: str = UNIVERSE_PATH) -> pd.DataFrame:
    df = pd.read_csv(path)
    assert TICKER_COL in df.columns, f"Universe missing '{TICKER_COL}' column"
    assert BUCKET_COL in df.columns, f"Universe missing '{BUCKET_COL}' column"
    df[TICKER_COL] = df[TICKER_COL].astype(str).str.upper().str.strip()
    df[BUCKET_COL] = df[BUCKET_COL].astype(str).str.strip()
    df = df.dropna(subset=[TICKER_COL, BUCKET_COL]).drop_duplicates(subset=[TICKER_COL])
    return df


def download_prices(tickers: list[str], start: str = "2020-01-01") -> pd.DataFrame:
    px = yf.download(
        tickers=tickers,
        start=start,
        auto_adjust=True,
        progress=False,
        group_by="ticker",
        threads=True,
    )

    # Normalize output to a simple wide frame of adj close
    if isinstance(px.columns, pd.MultiIndex):
        closes = {}
        for t in tickers:
            if (t, "Close") in px.columns:
                closes[t] = px[(t, "Close")]
        close = pd.DataFrame(closes)
    else:
        close = px[["Close"]].rename(columns={"Close": tickers[0]})

    close = close.dropna(how="all")
    return close


def fetch_trailing_pe_snapshot(tickers: list[str]) -> pd.Series:
    """
    V1 uses a *snapshot* trailing P/E (not historical).
    This is enough to implement the ranking + stress logic now.
    V2 will replace this with a historical fundamentals series.
    """
    pe = {}
    for t in tickers:
        try:
            info = yf.Ticker(t).fast_info
            # fast_info doesn't include PE; use info as fallback
            info2 = yf.Ticker(t).info
            val = info2.get("trailingPE", np.nan)
            pe[t] = float(val) if val is not None else np.nan
        except Exception:
            pe[t] = np.nan
    return pd.Series(pe, name="trailingPE").astype(float)


def compute_bucket_zscores(univ: pd.DataFrame, pe: pd.Series) -> pd.DataFrame:
    """
    Returns a df with ticker, bucket, pe, pe_z (within bucket).
    """
    df = univ.copy()
    df["pe"] = df[TICKER_COL].map(pe).astype(float)

    # Remove nonsense values
    df.loc[(df["pe"] <= 0) | (df["pe"] > 500), "pe"] = np.nan

    # compute z-scores within bucket
    out_rows = []
    for bucket, g in df.groupby(BUCKET_COL, dropna=False):
        g = g.copy()
        valid = g["pe"].dropna()
        if len(valid) < MIN_BUCKET_SIZE:
            g["pe_z"] = np.nan
        else:
            mu = valid.mean()
            sd = valid.std(ddof=0)
            if sd == 0 or np.isnan(sd):
                g["pe_z"] = np.nan
            else:
                g["pe_z"] = (g["pe"] - mu) / sd
        out_rows.append(g)

    out = pd.concat(out_rows, axis=0).reset_index(drop=True)
    return out[[TICKER_COL, BUCKET_COL, "pe", "pe_z"]]


def compute_momentum(close: pd.DataFrame, lookback: int = MOM_LOOKBACK) -> pd.Series:
    # simple momentum: % return over lookback
    mom = close.pct_change(lookback).iloc[-1]
    return mom.rename("momentum")


def build_equity_candidates(pe_z_df: pd.DataFrame, mom: pd.Series) -> pd.DataFrame:
    df = pe_z_df.copy()
    df["momentum"] = df[TICKER_COL].map(mom)

    # Valuation stress: if PE is extremely high vs bucket AND momentum not strong, we avoid/exit
    df["stress"] = (df["pe_z"] >= PE_Z_STRESS) & (df["momentum"].fillna(-1) <= MOM_MIN)

    # Core eligibility:
    # - prefer low pe_z (cheap vs bucket)
    # - require momentum >= 0 unless very cheap
    df["eligible"] = False
    df.loc[df["stress"] == True, "eligible"] = False
    df.loc[
        (df["stress"] == False)
        & (
            (df["momentum"].fillna(-1) >= MOM_MIN)
            | (df["pe_z"].fillna(0) <= CHEAP_Z)
        ),
        "eligible"
    ] = True

    # Score: cheaper + some momentum (tunable)
    # lower pe_z is better, higher momentum is better
    df["score"] = (-1.0 * df["pe_z"].fillna(0)) + (0.5 * df["momentum"].fillna(0))

    return df


def pick_positions(cands: pd.DataFrame, top_n: int = TOP_LONGS) -> list[str]:
    pick = (
        cands[cands["eligible"] == True]
        .sort_values("score", ascending=False)
        .head(top_n)[TICKER_COL]
        .tolist()
    )
    return pick


def build_target_weights(selected: list[str]) -> dict[str, float]:
    """
    If no equities selected -> 100% Treasuries.
    If equities selected -> equities get MAX_EQUITY_WEIGHT total, rest goes to Treasuries.
    """
    w = {}
    if len(selected) == 0:
        w[TREASURY_TICKER] = 1.0
        return w

    eq_total = float(MAX_EQUITY_WEIGHT)
    tr_total = 1.0 - eq_total

    if EQUAL_WEIGHT:
        per = eq_total / len(selected)
        for t in selected:
            w[t] = per
    else:
        # default to equal in V1
        per = eq_total / len(selected)
        for t in selected:
            w[t] = per

    if tr_total > 0:
        w[TREASURY_TICKER] = tr_total

    return w
