from __future__ import annotations

"""Config surface for the Charlie Munger sleeve."""

from sleeves.sleeve_charlie_munger import (  # backward-compatible source of truth
    CharlieMungerConfig,
    DEFAULT_CONFIG,
    load_config,
)

__all__ = ["CharlieMungerConfig", "DEFAULT_CONFIG", "load_config"]
from sleeves.sleeve_charlie_munger import CharlieMungerConfig, load_config

__all__ = ["CharlieMungerConfig", "load_config"]

