"""SEM-002 truth-surface compatibility enforcement."""

from __future__ import annotations

from dataclasses import dataclass

from research_registry.models.enums import SurfaceType


@dataclass(frozen=True)
class SurfaceCompatibility:
    compatibility: str
    permitted: bool
    requires_annotation: bool = False


COMPATIBILITY_MATRIX: dict[tuple[str, str], str] = {
    (SurfaceType.LIVE_BROKER_PAPER_NAV.value, SurfaceType.LIVE_BROKER_PAPER_NAV.value): "COMPATIBLE",
    (SurfaceType.OPERATIONAL_SHADOW_NAV.value, SurfaceType.OPERATIONAL_SHADOW_NAV.value): "COMPATIBLE",
    (SurfaceType.RESEARCH_BACKTEST_NAV.value, SurfaceType.RESEARCH_BACKTEST_NAV.value): "COMPATIBLE",
    (SurfaceType.OPERATIONAL_SHADOW_NAV.value, SurfaceType.RESEARCH_BACKTEST_NAV.value): "CAUTIOUS_OK",
    (SurfaceType.RESEARCH_BACKTEST_NAV.value, SurfaceType.OPERATIONAL_SHADOW_NAV.value): "CAUTIOUS_OK",
    (SurfaceType.LIVE_BROKER_PAPER_NAV.value, SurfaceType.OPERATIONAL_SHADOW_NAV.value): "INCOMPATIBLE",
    (SurfaceType.OPERATIONAL_SHADOW_NAV.value, SurfaceType.LIVE_BROKER_PAPER_NAV.value): "INCOMPATIBLE",
    (SurfaceType.LIVE_BROKER_PAPER_NAV.value, SurfaceType.RESEARCH_BACKTEST_NAV.value): "INCOMPATIBLE",
    (SurfaceType.RESEARCH_BACKTEST_NAV.value, SurfaceType.LIVE_BROKER_PAPER_NAV.value): "INCOMPATIBLE",
}


def surface_compatibility(left: str, right: str) -> SurfaceCompatibility:
    compatibility = COMPATIBILITY_MATRIX[(left, right)]
    return SurfaceCompatibility(
        compatibility=compatibility,
        permitted=compatibility in {"COMPATIBLE", "CAUTIOUS_OK"},
        requires_annotation=compatibility == "CAUTIOUS_OK",
    )


def assert_surface_operation_allowed(
    left: str,
    right: str,
    *,
    operation: str,
    override_rationale: str | None = None,
    override_audit_ref: str | None = None,
) -> SurfaceCompatibility:
    compatibility = surface_compatibility(left, right)
    if compatibility.compatibility == "INCOMPATIBLE":
        if not override_rationale or not override_audit_ref:
            raise ValueError(f"surface operation {operation} refused: {left} and {right} are INCOMPATIBLE")
        return SurfaceCompatibility("INCOMPATIBLE_OVERRIDE", permitted=True, requires_annotation=True)
    return compatibility
