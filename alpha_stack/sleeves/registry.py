"""
Alpha Stack — Sleeve Registry
================================
Central registry of all Alpha Stack sleeves.

Provides:
    SleeveRegistry.all_sleeves()   — list of all registered sleeves
    SleeveRegistry.active_sleeves() — sleeves that are currently enabled
    SleeveRegistry.get(name)       — retrieve a specific sleeve by name

Usage:
    registry = SleeveRegistry()
    active = registry.active_sleeves()
    for sleeve in active:
        output = sleeve.run(data, regime_ctx)
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional, Type

from alpha_stack.sleeves.base import SleeveBase
from alpha_stack._config_loader import get_flag

logger = logging.getLogger(__name__)

# Map of sleeve name → class (lazy imports to avoid circular deps)
_SLEEVE_REGISTRY: Dict[str, str] = {
    "trend":          "alpha_stack.sleeves.trend:TrendSleeve",
    "value":          "alpha_stack.sleeves.value:ValueSleeve",
    "quality":        "alpha_stack.sleeves.quality:QualitySleeve",
    "mean_reversion": "alpha_stack.sleeves.mean_reversion:MeanReversionSleeve",
}

# Feature flags that gate each sleeve (in addition to sleeve.enabled in config)
_SLEEVE_FLAGS: Dict[str, Optional[str]] = {
    "trend":          None,                    # Controlled by master ENABLE_ALPHA_STACK
    "value":          "ENABLE_VALUE_SLEEVE",
    "quality":        "ENABLE_QUALITY_SLEEVE",
    "mean_reversion": "ENABLE_MEAN_REVERSION",
}


class SleeveRegistry:
    """
    Registry and factory for Alpha Stack sleeves.

    Parameters
    ----------
    config_overrides : dict, optional
        Per-sleeve config overrides.
    """

    def __init__(self, config_overrides: Optional[Dict[str, dict]] = None) -> None:
        self._overrides = config_overrides or {}
        self._instances: Dict[str, SleeveBase] = {}

    def get(self, name: str) -> Optional[SleeveBase]:
        """
        Return a sleeve instance by name.
        Returns None if the sleeve is not registered.
        """
        if name not in _SLEEVE_REGISTRY:
            logger.warning("[REGISTRY] Unknown sleeve: %s", name)
            return None

        if name not in self._instances:
            self._instances[name] = self._build(name)

        return self._instances[name]

    def all_sleeves(self) -> List[SleeveBase]:
        """Return all registered sleeves (including disabled ones)."""
        return [self.get(name) for name in _SLEEVE_REGISTRY if self.get(name) is not None]  # type: ignore

    def active_sleeves(self) -> List[SleeveBase]:
        """
        Return only sleeves that are currently enabled via feature flags.
        The master ENABLE_ALPHA_STACK flag is NOT checked here — callers
        are responsible for that gate.
        """
        active = []
        for name in _SLEEVE_REGISTRY:
            flag = _SLEEVE_FLAGS[name]
            if flag is not None and not get_flag(flag, default=False):
                logger.debug("[REGISTRY] %s is disabled (flag=%s)", name, flag)
                continue
            sleeve = self.get(name)
            if sleeve is not None:
                active.append(sleeve)
        return active

    def sleeve_names(self) -> List[str]:
        """Return all registered sleeve names."""
        return list(_SLEEVE_REGISTRY.keys())

    def active_sleeve_names(self) -> List[str]:
        """Return names of currently active sleeves."""
        return [s.name for s in self.active_sleeves()]

    # ------------------------------------------------------------------ #
    # Internal                                                             #
    # ------------------------------------------------------------------ #

    def _build(self, name: str) -> Optional[SleeveBase]:
        """Lazily import and instantiate a sleeve class."""
        import importlib
        spec = _SLEEVE_REGISTRY[name]
        module_path, class_name = spec.split(":")
        try:
            module = importlib.import_module(module_path)
            cls: Type[SleeveBase] = getattr(module, class_name)
            override = self._overrides.get(name, {})
            instance = cls(config=override if override else None)
            logger.debug("[REGISTRY] Instantiated sleeve: %s", name)
            return instance
        except Exception as exc:
            logger.error("[REGISTRY] Failed to instantiate sleeve %s: %s", name, exc)
            return None
