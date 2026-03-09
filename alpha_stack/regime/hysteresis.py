"""
Alpha Stack — Hysteresis Controller
======================================
Anti-whipsaw logic for regime state transitions.

Rules implemented (per spec):
  1. Minimum dwell time: 5 trading days before any state downgrade/upgrade.
  2. Two-close confirmation: threshold breach must persist for 2 consecutive closes.
  3. Crisis bypass: crisis volatility can force immediate risk reduction
     even if dwell time is not met.
  4. Max state jump: no jump of more than 1 state in a single transition
     (e.g., strong_up → neutral is allowed, strong_up → strong_down is not).

Implementation notes:
  - Each HysteresisController is stateful and tracks the current confirmed
    state, the number of bars in that state, and any pending transition.
  - In a backtest loop, call .update(raw_state, is_crisis) on each bar.
  - The confirmed_state property returns the current hysteresis-filtered state.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Generic, List, Optional, TypeVar

from alpha_stack.regime.state_machine import (
    TrendState, VolatilityState, BreadthState, MacroState,
    transition_distance,
)

logger = logging.getLogger(__name__)

# Type variable for state enum members
S = TypeVar("S")


@dataclass
class _PendingTransition:
    """Tracks a proposed state change awaiting confirmation."""
    proposed_state: object
    consecutive_bars: int = 0
    required_bars: int = 2


class HysteresisController:
    """
    Hysteresis controller for a single regime dimension.

    Parameters
    ----------
    initial_state
        Starting confirmed state.
    min_dwell_days : int
        Minimum bars to remain in a state before any transition is allowed.
    confirmation_bars : int
        Number of consecutive bars the new signal must persist before
        a transition is confirmed.
    max_state_jump : int
        Maximum ordinal distance allowed in a single transition.
        Ignored for VolatilityState.CRISIS transitions.
    dimension_name : str
        For logging only.
    """

    def __init__(
        self,
        initial_state,
        min_dwell_days: int = 5,
        confirmation_bars: int = 2,
        max_state_jump: int = 1,
        dimension_name: str = "unknown",
    ) -> None:
        self._confirmed = initial_state
        self._min_dwell = min_dwell_days
        self._confirmation_bars = confirmation_bars
        self._max_jump = max_state_jump
        self._name = dimension_name

        self._bars_in_state: int = 0
        self._pending: Optional[_PendingTransition] = None
        self._history: List[dict] = []

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    @property
    def confirmed_state(self):
        """The current hysteresis-confirmed state."""
        return self._confirmed

    @property
    def bars_in_state(self) -> int:
        """Number of bars (trading days) in the current confirmed state."""
        return self._bars_in_state

    def update(self, raw_state, as_of_date=None, is_crisis: bool = False) -> bool:
        """
        Process a new raw state signal and update confirmed state.

        Parameters
        ----------
        raw_state
            The raw (pre-hysteresis) state signal for this bar.
        as_of_date : date or str, optional
            Date for logging.
        is_crisis : bool
            If True (crisis vol), bypass dwell time and confirm immediately.

        Returns
        -------
        bool
            True if the confirmed state changed on this bar.
        """
        changed = False
        self._bars_in_state += 1

        if raw_state == self._confirmed:
            # No change — reset any pending transition
            self._pending = None
            return False

        # Check for crisis bypass (immediate transition allowed)
        if is_crisis and self._name == "volatility":
            if raw_state != self._confirmed:
                old = self._confirmed
                self._confirmed = raw_state
                self._bars_in_state = 1
                self._pending = None
                logger.info(
                    "[HYSTERESIS/%s] CRISIS bypass: %s → %s on %s",
                    self._name, old, raw_state, as_of_date or "?",
                )
                self._history.append({
                    "date": str(as_of_date or ""),
                    "from": str(old),
                    "to": str(raw_state),
                    "reason": "crisis_bypass",
                })
                return True

        # Check dwell time
        if self._bars_in_state < self._min_dwell:
            logger.debug(
                "[HYSTERESIS/%s] Dwell not met (%d/%d); ignoring signal %s",
                self._name, self._bars_in_state, self._min_dwell, raw_state,
            )
            return False

        # Check max state jump
        if hasattr(self._confirmed, "numeric") and hasattr(raw_state, "numeric"):
            dist = abs(self._confirmed.numeric() - raw_state.numeric())  # type: ignore
            if dist > self._max_jump:
                # Clip to adjacent state instead of jumping
                raw_state = self._adjacent_state(self._confirmed, raw_state)
                logger.debug(
                    "[HYSTERESIS/%s] Jump capped: %s → %s (dist=%d)",
                    self._name, self._confirmed, raw_state, dist,
                )

        # Confirmation logic
        if self._pending is None or self._pending.proposed_state != raw_state:
            self._pending = _PendingTransition(
                proposed_state=raw_state,
                consecutive_bars=1,
                required_bars=self._confirmation_bars,
            )
        else:
            self._pending.consecutive_bars += 1

        if self._pending.consecutive_bars >= self._pending.required_bars:
            old = self._confirmed
            self._confirmed = self._pending.proposed_state
            self._bars_in_state = 1
            self._pending = None
            changed = True
            logger.info(
                "[HYSTERESIS/%s] Transition confirmed: %s → %s on %s",
                self._name, old, self._confirmed, as_of_date or "?",
            )
            self._history.append({
                "date": str(as_of_date or ""),
                "from": str(old),
                "to": str(self._confirmed),
                "reason": "confirmed",
            })

        return changed

    def transition_history(self) -> List[dict]:
        """Return list of confirmed transitions as dicts."""
        return list(self._history)

    def reset(self, state) -> None:
        """Reset to a specific state (used for backtest initialisation)."""
        self._confirmed = state
        self._bars_in_state = 0
        self._pending = None

    # ------------------------------------------------------------------ #
    # Helpers                                                             #
    # ------------------------------------------------------------------ #

    def _adjacent_state(self, current, target):
        """
        Return the state adjacent to current in the direction of target.
        Used to enforce max_state_jump == 1.
        """
        if not hasattr(current, "numeric"):
            return target
        curr_val = current.numeric()  # type: ignore
        tgt_val = target.numeric()  # type: ignore
        step = 1 if tgt_val > curr_val else -1
        adj_val = curr_val + step

        # Find the state enum with numeric() == adj_val
        for member in type(current):
            try:
                if member.numeric() == adj_val:  # type: ignore
                    return member
            except Exception:
                pass
        return target  # fallback
