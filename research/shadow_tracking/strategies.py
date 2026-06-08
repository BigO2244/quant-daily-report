from __future__ import annotations

from dataclasses import dataclass

from core.strategy_registry import StrategyRegistryEntry, load_strategy_registry
from research.alpha_lab_v2.engine import StrategySpec


@dataclass(frozen=True)
class ShadowStrategyDefinition:
    strategy_name: str
    strategy_slug: str
    source_variant: str
    spec: StrategySpec


def build_shadow_definitions() -> list[ShadowStrategyDefinition]:
    registry = load_strategy_registry()
    return [
        _definition_from_registry_entry(entry)
        for entry in registry.active_shadow_security_selection_entries()
    ]


def _definition_from_registry_entry(entry: StrategyRegistryEntry) -> ShadowStrategyDefinition:
    source_variant = str((entry.shadow_tracking or {}).get("source_variant") or "")
    if not source_variant:
        raise ValueError(f"{entry.strategy_id}: active shadow strategy missing source_variant")
    return ShadowStrategyDefinition(
        strategy_name=entry.display_name,
        strategy_slug=entry.strategy_id,
        source_variant=source_variant,
        spec=_strategy_spec_for_variant(entry=entry, source_variant=source_variant),
    )


def _strategy_spec_for_variant(*, entry: StrategyRegistryEntry, source_variant: str) -> StrategySpec:
    specs = {
        "baseline_top10_daily": StrategySpec(
            name="baseline_top10_daily",
            hypothesis_id="CONTROL",
            description="Caerus Polaris: current paper baseline / operational control.",
            top_n=10,
            rebalance_mode="daily",
        ),
        "h2_rank_decay_exit_h6_top5": StrategySpec(
            name="h2_rank_decay_exit_h6_top5",
            hypothesis_id="COMBO",
            description="Caerus Orion: H2 rank-decay exit + H6 top-5 concentration.",
            top_n=5,
            rebalance_mode="daily",
            use_rank_decay_exit=True,
        ),
        "h1_weekly_h6_top5": StrategySpec(
            name="h1_weekly_h6_top5",
            hypothesis_id="COMBO",
            description="Caerus Lyra: H1 weekly rebalance + H6 top-5 concentration.",
            top_n=5,
            rebalance_mode="weekly",
        ),
    }
    try:
        return specs[source_variant]
    except KeyError as exc:
        raise ValueError(f"{entry.strategy_id}: unsupported shadow source_variant {source_variant!r}") from exc


def build_strategy_lookup() -> dict[str, ShadowStrategyDefinition]:
    return {item.strategy_slug: item for item in build_shadow_definitions()}
