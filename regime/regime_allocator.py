"""
regime_allocator.py
-------------------
Maps composite regime labels to sleeve target weights, then applies
EWM transition smoothing to prevent large turnover spikes on regime shifts.

Usage:
    from regime.regime_allocator import RegimeAllocator

    allocator = RegimeAllocator()
    weights = allocator.compute_weights(regime_series)
    # weights: DataFrame with daily smoothed weight for each sleeve

The allocator is stateless between calls — feed it the full regime
history and it produces the full weight history in one pass.
"""

import logging
import numpy as np
import pandas as pd

from regime.regime_config import REGIME_WEIGHTS, WEIGHT_COLS, TRANSITION

logger = logging.getLogger(__name__)


class RegimeAllocator:
    """
    Converts a daily Series of composite regime labels into daily sleeve weights.

    Parameters
    ----------
    regime_weights : dict, optional
        Override the default REGIME_WEIGHTS from regime_config.py.
        Keys = regime labels; values = tuples of weights in WEIGHT_COLS order.
    blend_alpha : float, optional
        Override TRANSITION['blend_alpha']. Controls convergence speed.
        alpha=1.0 = instant switch; alpha=0.2 = ~5-day transition.
    min_rebalance_threshold : float, optional
        Override TRANSITION['min_rebalance_threshold'].
        Weight changes smaller than this are zeroed out (no trade).
    """

    def __init__(
        self,
        regime_weights: dict | None = None,
        blend_alpha: float | None = None,
        min_rebalance_threshold: float | None = None,
    ):
        self._target_weights = self._build_target_table(
            regime_weights or REGIME_WEIGHTS
        )
        self._alpha = blend_alpha if blend_alpha is not None else TRANSITION["blend_alpha"]
        self._min_threshold = (
            min_rebalance_threshold
            if min_rebalance_threshold is not None
            else TRANSITION["min_rebalance_threshold"]
        )

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def compute_weights(self, regime_series: pd.Series) -> pd.DataFrame:
        """
        Compute daily smoothed sleeve weights from a regime label series.

        Parameters
        ----------
        regime_series : pd.Series
            DatetimeIndex, values = composite regime labels (strings).
            Produced by regime_classifier.classify()["composite_regime"].

        Returns
        -------
        pd.DataFrame
            Indexed by date, columns = WEIGHT_COLS.
            Values are smoothed weights (0.0–1.0), summing to 1.0 per row.
            Also includes:
                composite_regime  : the input regime label for that day
                regime_changed    : bool, True when regime label changed
        """
        if regime_series.empty:
            return pd.DataFrame(columns=WEIGHT_COLS + ["composite_regime", "regime_changed"])

        # Map regime labels → target weights (raw, unsmoothed)
        target_df = self._map_targets(regime_series)

        # Apply EWM blending row-by-row (must be sequential — not vectorisable)
        smoothed = self._apply_transition_smoothing(target_df)

        # Apply minimum rebalance threshold: zero out tiny changes vs prior day
        smoothed = self._apply_min_threshold(smoothed)

        # Renormalize so weights always sum to 1.0 (threshold zeroing can break this)
        weight_cols = [c for c in WEIGHT_COLS if c in smoothed.columns]
        row_sums = smoothed[weight_cols].sum(axis=1)
        smoothed[weight_cols] = smoothed[weight_cols].div(row_sums, axis=0)

        # Attach regime metadata
        smoothed["composite_regime"] = regime_series.reindex(smoothed.index)
        smoothed["regime_changed"] = (
            smoothed["composite_regime"] != smoothed["composite_regime"].shift(1)
        )
        smoothed.iloc[0, smoothed.columns.get_loc("regime_changed")] = False

        logger.info(
            "Regime allocator: %d days, %d regime transitions",
            len(smoothed),
            smoothed["regime_changed"].sum(),
        )
        return smoothed

    def target_weights_table(self) -> pd.DataFrame:
        """Return the static regime → target weight table (for inspection)."""
        return self._target_weights.copy()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _build_target_table(self, regime_weights: dict) -> pd.DataFrame:
        """Convert REGIME_WEIGHTS dict to a lookup DataFrame."""
        rows = {}
        for regime_label, weight_tuple in regime_weights.items():
            if len(weight_tuple) != len(WEIGHT_COLS):
                raise ValueError(
                    f"Regime '{regime_label}' has {len(weight_tuple)} weights "
                    f"but WEIGHT_COLS has {len(WEIGHT_COLS)}: {WEIGHT_COLS}"
                )
            total = sum(weight_tuple)
            if abs(total - 100) > 0.1:
                raise ValueError(
                    f"Regime '{regime_label}' weights sum to {total}, expected 100"
                )
            # Store as fractions (0.0–1.0)
            rows[regime_label] = {col: w / 100.0 for col, w in zip(WEIGHT_COLS, weight_tuple)}
        return pd.DataFrame(rows).T  # shape: (n_regimes, n_sleeves)

    def _map_targets(self, regime_series: pd.Series) -> pd.DataFrame:
        """Map each regime label to its target weight row."""
        known_regimes = set(self._target_weights.index)
        unknown = set(regime_series.unique()) - known_regimes
        if unknown:
            logger.warning(
                "Unknown regime labels (defaulting to neutral_mixed): %s", unknown
            )

        rows = []
        for date, regime in regime_series.items():
            if regime not in known_regimes:
                regime = "neutral_mixed"
            rows.append(self._target_weights.loc[regime].rename(date))
        return pd.DataFrame(rows)

    def _apply_transition_smoothing(self, target_df: pd.DataFrame) -> pd.DataFrame:
        """
        Apply EWM-style blending: each day's weight = alpha * target + (1-alpha) * prior.
        This prevents abrupt jumps when the regime label changes.
        Uses numpy arrays internally to avoid pandas CoW issues in row loops.
        """
        alpha = self._alpha
        weight_cols = [c for c in WEIGHT_COLS if c in target_df.columns]

        target_arr = target_df[weight_cols].to_numpy(dtype=float)
        smoothed_arr = target_arr.copy()

        for i in range(1, len(smoothed_arr)):
            smoothed_arr[i] = (
                alpha * target_arr[i]
                + (1 - alpha) * smoothed_arr[i - 1]
            )

        result = target_df.copy()
        result[weight_cols] = smoothed_arr
        return result

    def _apply_min_threshold(self, smoothed: pd.DataFrame) -> pd.DataFrame:
        """
        Zero out weight changes below min_rebalance_threshold.
        If the change in a sleeve's weight is < threshold, keep prior weight.
        Prevents small drift from triggering unnecessary trades.
        Uses numpy arrays internally to avoid pandas CoW issues in row loops.
        """
        threshold = self._min_threshold
        weight_cols = [c for c in WEIGHT_COLS if c in smoothed.columns]

        arr = smoothed[weight_cols].to_numpy(dtype=float)
        result_arr = arr.copy()

        for i in range(1, len(result_arr)):
            prior   = result_arr[i - 1]
            current = arr[i]
            delta   = np.abs(current - prior)
            # Where delta is below threshold, keep prior weight
            result_arr[i] = np.where(delta < threshold, prior, current)

        result = smoothed.copy()
        result[weight_cols] = result_arr
        return result
