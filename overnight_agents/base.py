"""
Overnight Agents — Base Class
================================
All overnight research agents inherit from BaseAgent.

Contract:
    - run(as_of_date) → dict of signal values
    - Agents are stateless: no memory between runs
    - Each agent owns one key in the nightly signals JSON
    - Failures return a neutral stub, never crash the orchestrator

Signal output schema (each agent returns):
    {
        "status": "ok" | "error" | "partial",
        "regime": str,          # agent-specific regime label
        "score": float,         # normalized [-1.0, +1.0]; 0 = neutral
        ...                     # agent-specific fields
    }
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import date, timedelta
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class BaseAgent(ABC):
    """Base class for all overnight research agents."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique key used in the nightly signals JSON."""

    @abstractmethod
    def _fetch(self, as_of_date: date) -> Dict[str, Any]:
        """
        Fetch and compute signals for as_of_date.
        Must return a dict. Raise on unrecoverable errors.
        """

    def run(self, as_of_date: Optional[date] = None) -> Dict[str, Any]:
        """
        Run the agent. Returns signal dict; returns neutral stub on any failure.
        """
        target = as_of_date or date.today()
        try:
            result = self._fetch(target)
            result.setdefault("status", "ok")
            logger.info("[%s] run complete: regime=%s score=%.3f",
                        self.name, result.get("regime", "?"), result.get("score", 0.0))
            return result
        except Exception as exc:
            logger.warning("[%s] run failed: %s — returning neutral stub", self.name, exc)
            return self._neutral_stub(str(exc))

    def _neutral_stub(self, reason: str = "") -> Dict[str, Any]:
        return {"status": "error", "regime": "unknown", "score": 0.0, "error": reason}

    @staticmethod
    def _clamp(value: float, lo: float = -1.0, hi: float = 1.0) -> float:
        return max(lo, min(hi, value))

    @staticmethod
    def _prior_trading_days(as_of: date, n: int) -> date:
        """Approximate n trading days back (calendar-day proxy, good enough for FRED)."""
        return as_of - timedelta(days=int(n * 1.45))
