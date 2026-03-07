from __future__ import annotations

import pandas as pd


def apply_overlay_to_returns(returns: pd.Series, multiplier: pd.Series) -> pd.Series:
    aligned_multiplier = multiplier.reindex(returns.index).fillna(1.0)
    return returns.fillna(0.0) * aligned_multiplier
