"""
Alpha Stack — Sleeve Base Contract
=====================================
Formal abstract base class that every sleeve must implement.

Each sleeve exposes:
    name                    — string identifier
    required_features()     — list of feature column names needed
    eligibility_filter()    — hard-pass/fail data quality and signal gates
    score_universe()        — produce normalized score [0, 100] per ticker
    select_candidates()     — apply eligibility + threshold + select top-N
    target_weights()        — convert candidates to target weights
    diagnostics()           — reason codes and gating outcomes

SleeveOutput is the standardised output dataclass consumed by the allocator.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

import pandas as pd


class HoldState(str, Enum):
    """Position lifecycle state emitted by each sleeve."""
    ENTER = "enter"   # Newly passes entry threshold
    HOLD  = "hold"    # Remains above hold threshold
    TRIM  = "trim"    # Fell from enter zone but still holdable
    EXIT  = "exit"    # Below exit threshold; close position


@dataclass
class SleeveOutput:
    """
    Standardised output from a sleeve for a single as_of_date.

    Fields
    ------
    sleeve_name : str
        Identifier matching SleeveBase.name.
    as_of_date : str
        ISO date string.
    scores : DataFrame
        Per-ticker scores with columns:
        [ticker, score, rank, candidate_flag, provisional_weight,
         hold_state, raw_signals (dict as JSON str), diagnostics]
    candidates : DataFrame
        Subset of scores where candidate_flag == True.
    active : bool
        False if sleeve is disabled or has insufficient data.
    reason : str
        Human-readable explanation if active=False.
    meta : dict
        Additional diagnostic metadata.
    """
    sleeve_name:        str
    as_of_date:         str
    scores:             pd.DataFrame = field(default_factory=pd.DataFrame)
    candidates:         pd.DataFrame = field(default_factory=pd.DataFrame)
    active:             bool = True
    reason:             str = ""
    meta:               Dict[str, Any] = field(default_factory=dict)

    @property
    def has_candidates(self) -> bool:
        return self.active and self.candidates is not None and len(self.candidates) > 0

    def __repr__(self) -> str:
        n = len(self.candidates) if self.has_candidates else 0
        return (
            f"SleeveOutput(sleeve={self.sleeve_name}, "
            f"date={self.as_of_date}, "
            f"candidates={n}, "
            f"active={self.active})"
        )


# ================================================================== #
# Abstract base class                                                  #
# ================================================================== #

class SleeveBase(ABC):
    """
    Abstract base class for all Alpha Stack sleeves.

    Subclasses must implement all abstract methods and should call
    super().__init__() to initialise the config.
    """

    def __init__(self, config: Optional[dict] = None) -> None:
        self._cfg = config or {}

    # ------------------------------------------------------------------ #
    # Identity                                                             #
    # ------------------------------------------------------------------ #

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique string identifier for this sleeve (e.g. 'trend')."""

    @abstractmethod
    def required_features(self) -> List[str]:
        """
        List of feature column names that this sleeve requires.
        The backtest runner uses this to ensure all features are computed.
        """

    # ------------------------------------------------------------------ #
    # Core pipeline                                                        #
    # ------------------------------------------------------------------ #

    @abstractmethod
    def eligibility_filter(self, data: pd.DataFrame) -> pd.DataFrame:
        """
        Apply hard pass/fail eligibility gates.

        Parameters
        ----------
        data : DataFrame
            Feature data for the universe with required_features() columns.

        Returns
        -------
        DataFrame
            Subset of data that passes all gates.
        """

    @abstractmethod
    def score_universe(self, data: pd.DataFrame) -> pd.DataFrame:
        """
        Score the eligible universe on a [0, 100] scale.

        Parameters
        ----------
        data : DataFrame
            Eligible tickers (output of eligibility_filter).

        Returns
        -------
        DataFrame
            Same DataFrame with 'score' column added.
        """

    @abstractmethod
    def select_candidates(self, scores: pd.DataFrame) -> pd.DataFrame:
        """
        Apply entry threshold and select top-N candidates.

        Parameters
        ----------
        scores : DataFrame
            Output of score_universe.

        Returns
        -------
        DataFrame
            Candidate tickers with candidate_flag=True, hold_state set.
        """

    @abstractmethod
    def target_weights(
        self,
        candidates: pd.DataFrame,
        regime_context,
        risk_budget: float = 1.0,
    ) -> pd.DataFrame:
        """
        Convert candidates to normalised target weights.

        Parameters
        ----------
        candidates : DataFrame
        regime_context : RegimeContext
            Current regime (for regime-specific sizing adjustments).
        risk_budget : float
            Fraction of sleeve budget allocated by the allocator [0, 1].

        Returns
        -------
        DataFrame with 'provisional_weight' column summing to <= risk_budget.
        """

    @abstractmethod
    def diagnostics(self) -> Dict[str, Any]:
        """
        Return diagnostic information about the latest run.

        Returns
        -------
        dict with keys: last_run_date, n_eligible, n_candidates,
                        gate_failures (dict), warnings (list)
        """

    # ------------------------------------------------------------------ #
    # Convenience orchestration                                            #
    # ------------------------------------------------------------------ #

    def run(
        self,
        data: pd.DataFrame,
        regime_context,
        risk_budget: float = 1.0,
        as_of_date: Optional[str] = None,
    ) -> SleeveOutput:
        """
        Full sleeve pipeline: filter → score → select → weight.

        Parameters
        ----------
        data : DataFrame
            Universe feature data for as_of_date.
        regime_context : RegimeContext
        risk_budget : float
        as_of_date : str, optional
            For output labelling.

        Returns
        -------
        SleeveOutput
        """
        date_str = as_of_date or (
            str(data["as_of_date"].iloc[0])[:10]
            if "as_of_date" in data.columns and len(data) > 0
            else "unknown"
        )

        try:
            eligible = self.eligibility_filter(data)
            if eligible.empty:
                return SleeveOutput(
                    sleeve_name=self.name,
                    as_of_date=date_str,
                    active=True,
                    reason="no_eligible_tickers",
                    meta={"n_total": len(data), "n_eligible": 0},
                )

            scored = self.score_universe(eligible)
            candidates = self.select_candidates(scored)
            weighted = self.target_weights(candidates, regime_context, risk_budget)

            # Assemble full scores DataFrame
            full_scores = scored.copy()
            full_scores["candidate_flag"] = full_scores["ticker"].isin(
                candidates["ticker"].tolist()
            )
            if "provisional_weight" not in full_scores.columns:
                full_scores["provisional_weight"] = 0.0
                for _, row in weighted.iterrows():
                    mask = full_scores["ticker"] == row["ticker"]
                    full_scores.loc[mask, "provisional_weight"] = row.get("provisional_weight", 0.0)

            return SleeveOutput(
                sleeve_name=self.name,
                as_of_date=date_str,
                scores=full_scores,
                candidates=weighted,
                active=True,
                meta=self.diagnostics(),
            )

        except Exception as exc:
            import traceback
            return SleeveOutput(
                sleeve_name=self.name,
                as_of_date=date_str,
                active=False,
                reason=f"exception: {exc}",
                meta={"traceback": traceback.format_exc()},
            )

    # ------------------------------------------------------------------ #
    # Shared utilities                                                     #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _iterative_cap_weights(
        weights: "pd.Series",
        cap: float = 0.10,
        floor: float = 0.02,
        max_iterations: int = 20,
    ) -> "pd.Series":
        """
        Iteratively cap weights while preserving relative ordering.
        Shared implementation available to all sleeve subclasses.
        """
        import numpy as np
        w = weights.values.copy().astype(float)
        n = len(w)
        if n == 0:
            return weights

        s = w.sum()
        if s <= 0:
            return pd.Series(1.0 / n, index=weights.index)
        w = w / s

        # Feasibility check
        if cap * n < 1.0 - 1e-12:
            return pd.Series(w, index=weights.index)

        # Apply floor
        below = w < floor
        if below.any() and not below.all():
            w[below] = floor

        for _ in range(max_iterations):
            over = w > cap + 1e-9
            if not over.any():
                break
            excess = (w[over] - cap).sum()
            w[over] = cap
            free = ~over & (w > 0)
            if not free.any():
                break
            free_total = w[free].sum()
            if free_total > 0:
                w[free] += excess * (w[free] / free_total)

        s = w.sum()
        if s > 0:
            w = w / s

        return pd.Series(np.minimum(w, cap), index=weights.index)
