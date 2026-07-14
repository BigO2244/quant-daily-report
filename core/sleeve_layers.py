"""Canonical runtime sleeve classifications used by live fail-closed checks.

This intentionally contains classification only. Per-layer sector-cap behavior
remains deferred and is not imported into the pilot readiness change.
"""

from __future__ import annotations

SLEEVE_LAYERS: dict[str, str] = {
    "sleeve_1": "alpha",
    "sleeve_trend": "alpha",
    "sleeve_2": "alpha",
    "sleeve_quality": "alpha",
    "sleeve_mean_reversion": "alpha",
    "sleeve_defensive_etf": "protection",
    "sleeve_c1_cross_asset": "diversifier",
    "charlie_munger": "alpha",
}


def unresolved_sleeve_labels(raw: object) -> list[str]:
    """Return normalized labels that are empty or absent from the canonical map."""
    labels = [part.strip() for part in str(raw or "").split(",")]
    return [label or "<missing>" for label in labels if not label or label not in SLEEVE_LAYERS]
