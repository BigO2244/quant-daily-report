"""Runtime validation package.

Package initialization is intentionally side-effect free so module
entrypoints such as `python -m research_registry.runtime.readiness` are
not preloaded before execution.
"""

from __future__ import annotations

__all__ = ["RuntimeReadinessCheck", "RuntimeReadinessReport"]


def __getattr__(name: str):
    if name in __all__:
        from research_registry.runtime.readiness import RuntimeReadinessCheck, RuntimeReadinessReport

        exports = {
            "RuntimeReadinessCheck": RuntimeReadinessCheck,
            "RuntimeReadinessReport": RuntimeReadinessReport,
        }
        return exports[name]
    raise AttributeError(name)
