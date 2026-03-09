"""
Alpha Stack — Quality Features
=================================
Quality factor computation.

⚠️  STATUS: STUB — Returns empty DataFrame.
    Requires a PIT-safe fundamentals data source before activation.
    See alpha_stack/datastore/fundamentals.py for TODO instructions.

Factors (when implemented):
    roe              = Return on Equity
    roic             = Return on Invested Capital
    net_leverage     = Net Debt / EBITDA (lower = better, negated)
    margin_volatility = Std of trailing gross margins over 3yr (lower = better, negated)
    accrual_ratio    = Operating Accruals / Net Operating Assets (lower = better, negated)

Composite:
    S_quality_raw = 0.30*z(ROE) + 0.25*z(ROIC)
                  - 0.20*z(NetLeverage) - 0.15*z(MarginVol) - 0.10*z(Accruals)

Enable only when FundamentalsDataStore.pit_safe == True.
"""

from __future__ import annotations

import logging
import warnings
from datetime import date

import pandas as pd

from alpha_stack.datastore.base import DataStorePITWarning

logger = logging.getLogger(__name__)

_QUALITY_ENABLED_FLAG = "ENABLE_QUALITY_SLEEVE"


def compute_quality_features(
    fundamentals_store,
    universe_tickers: list,
    as_of_date: date | str,
) -> pd.DataFrame:
    """
    Compute quality features for the universe as of as_of_date.

    ⚠️  Currently returns empty DataFrame (stub).
    Enable ENABLE_QUALITY_SLEEVE in alpha_stack.yaml once PIT-safe
    fundamentals are wired.

    Returns
    -------
    DataFrame with columns:
        ticker, roe, roic, net_leverage, margin_volatility, accrual_ratio,
        z_roe, z_roic, z_netlev, z_margvol, z_accruals, raw_score
    """
    from alpha_stack._config_loader import get_flag
    if not get_flag(_QUALITY_ENABLED_FLAG, default=False):
        logger.debug(
            "[QUALITY_FEATURES] ENABLE_QUALITY_SLEEVE=false — returning empty."
        )
        return _empty_quality_features()

    meta = fundamentals_store.metadata()
    if not meta.get("pit_safe", False):
        warnings.warn(
            "[QUALITY_FEATURES] FundamentalsDataStore is NOT PIT-safe. "
            "DO NOT use in production backtest.",
            DataStorePITWarning,
            stacklevel=2,
        )
        return _empty_quality_features()

    # TODO: Implement once PIT fundamentals wired.
    # Skeleton:
    #
    # records = []
    # for ticker in universe_tickers:
    #     roe   = fundamentals_store.get_fundamental(ticker, "roe", as_of_date)
    #     roic  = fundamentals_store.get_fundamental(ticker, "roic", as_of_date)
    #     netlev = fundamentals_store.get_fundamental(ticker, "net_leverage", as_of_date)
    #     margvol = fundamentals_store.get_fundamental(ticker, "margin_volatility", as_of_date)
    #     accruals = fundamentals_store.get_fundamental(ticker, "accrual_ratio", as_of_date)
    #     records.append(dict(ticker=ticker, roe=roe, roic=roic,
    #                         net_leverage=netlev, margin_volatility=margvol,
    #                         accrual_ratio=accruals))
    #
    # df = pd.DataFrame(records)
    # df["z_roe"]     = _zscore(df["roe"])
    # df["z_roic"]    = _zscore(df["roic"])
    # df["z_netlev"]  = _zscore(df["net_leverage"])   # negate below
    # df["z_margvol"] = _zscore(df["margin_volatility"])  # negate below
    # df["z_accruals"] = _zscore(df["accrual_ratio"])  # negate below
    #
    # df["raw_score"] = (0.30*df["z_roe"] + 0.25*df["z_roic"]
    #                    - 0.20*df["z_netlev"] - 0.15*df["z_margvol"]
    #                    - 0.10*df["z_accruals"])
    # return df

    return _empty_quality_features()


def _empty_quality_features() -> pd.DataFrame:
    return pd.DataFrame(columns=[
        "ticker", "roe", "roic", "net_leverage", "margin_volatility", "accrual_ratio",
        "z_roe", "z_roic", "z_netlev", "z_margvol", "z_accruals", "raw_score",
    ])


def _zscore(s: pd.Series) -> pd.Series:
    mu, sigma = s.mean(), s.std()
    if sigma == 0 or pd.isna(sigma):
        return pd.Series(0.0, index=s.index)
    return (s - mu) / sigma
