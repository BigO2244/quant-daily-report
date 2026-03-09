"""
Alpha Stack v1 — Parallel Research & Shadow Framework
======================================================

Alpha Stack is a modular, regime-aware multi-sleeve alpha engine built
as a PARALLEL namespace alongside the production strategy.

SAFETY CONTRACT
---------------
- All feature flags default to FALSE (off by default).
- This package does NOT import from or modify any production execution path.
- The master switch is ENABLE_ALPHA_STACK in alpha_stack/config/alpha_stack.yaml.
- Nothing in this package is called by the production workflow unless you
  explicitly wire it into a separate shadow/research entrypoint.

Usage (research / backtest):
    from alpha_stack.research.backtest import AlphaStackBacktest
    bt = AlphaStackBacktest()
    results = bt.run(start_date="2022-01-01", end_date="2024-12-31")

Usage (shadow runner):
    from alpha_stack.research.shadow_runner import ShadowRunner
    runner = ShadowRunner()
    runner.run_daily()

Version: 1.0.0 (initial parallel build)
Status: Research / Shadow only — not wired to production execution.
"""

__version__ = "1.0.0"
__status__ = "research"

# Expose top-level convenience imports (lazy — only import when actually used)
__all__ = [
    "load_config",
    "is_enabled",
]


def load_config() -> dict:
    """
    Load the Alpha Stack YAML config.

    Returns
    -------
    dict
        Parsed configuration dictionary.
    """
    from alpha_stack._config_loader import load_alpha_stack_config
    return load_alpha_stack_config()


def is_enabled() -> bool:
    """
    Return True if the master ENABLE_ALPHA_STACK flag is set.

    This is a lightweight check that does NOT fetch data or run any computation.
    """
    try:
        cfg = load_config()
        return bool(cfg.get("feature_flags", {}).get("ENABLE_ALPHA_STACK", False))
    except Exception:
        return False
