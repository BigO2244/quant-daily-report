"""Research-only forward options proxy infrastructure.

This package is deliberately isolated from broker, execution, allocation,
scheduler, paper, live, and production runtime modules.
"""

from .config import ProxyConfig, load_config
from .evaluation import evaluate_signal
from .features import build_feature_rows, build_signal
from .pipeline import build_from_snapshot, collect_and_build, collect_snapshot

__all__ = [
    "ProxyConfig",
    "build_feature_rows",
    "build_from_snapshot",
    "build_signal",
    "collect_and_build",
    "collect_snapshot",
    "evaluate_signal",
    "load_config",
]
