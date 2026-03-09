"""
Alpha Stack — Config Loader
===========================
Loads and caches the alpha_stack.yaml configuration file.
Provides a typed access helper.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Path relative to this file
_CONFIG_PATH = Path(__file__).parent / "config" / "alpha_stack.yaml"

_CACHE: dict | None = None


def load_alpha_stack_config(reload: bool = False) -> dict:
    """
    Load alpha_stack.yaml and return as a nested dict.

    Parameters
    ----------
    reload : bool
        Force reload from disk even if cached.

    Returns
    -------
    dict
    """
    global _CACHE
    if _CACHE is not None and not reload:
        return _CACHE

    try:
        import yaml  # type: ignore
    except ImportError:
        logger.warning(
            "[ALPHA_STACK] PyYAML not installed; returning empty config. "
            "Install with: pip install pyyaml"
        )
        return {}

    if not _CONFIG_PATH.exists():
        logger.error("[ALPHA_STACK] Config file not found: %s", _CONFIG_PATH)
        return {}

    try:
        with open(_CONFIG_PATH) as fh:
            cfg = yaml.safe_load(fh) or {}
        _CACHE = cfg
        logger.debug("[ALPHA_STACK] Config loaded from %s", _CONFIG_PATH)
        return cfg
    except Exception as exc:
        logger.error("[ALPHA_STACK] Failed to load config: %s", exc)
        return {}


def get_flag(flag_name: str, default: bool = False) -> bool:
    """Return a boolean feature flag value."""
    cfg = load_alpha_stack_config()
    return bool(cfg.get("feature_flags", {}).get(flag_name, default))


def get_section(section: str, default: Any = None) -> Any:
    """Return a top-level config section."""
    cfg = load_alpha_stack_config()
    return cfg.get(section, default)
