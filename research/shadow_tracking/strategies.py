from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from core.strategy_registry import StrategyRegistryEntry, load_strategy_registry
from research.alpha_lab_v2.engine import StrategySpec


@dataclass(frozen=True)
class ShadowStrategyDefinition:
    strategy_name: str
    strategy_slug: str
    source_variant: str
    spec: StrategySpec


def build_shadow_definitions(*, trade_date: str | None = None) -> list[ShadowStrategyDefinition]:
    registry = load_strategy_registry()
    return [
        _definition_from_registry_entry(entry)
        for entry in registry.active_shadow_security_selection_entries()
        if shadow_tracking_active_on(entry, trade_date=trade_date)
    ]


def shadow_tracking_observation_start(entry: StrategyRegistryEntry) -> str | None:
    value = (entry.shadow_tracking or {}).get("observation_start_date")
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)).isoformat()
    except ValueError as exc:
        raise ValueError(f"{entry.strategy_id}: invalid shadow observation_start_date {value!r}") from exc


def shadow_tracking_active_on(entry: StrategyRegistryEntry, *, trade_date: str | None) -> bool:
    start_date = shadow_tracking_observation_start(entry)
    if trade_date is None or start_date is None:
        return True
    try:
        normalized_trade_date = date.fromisoformat(str(trade_date)).isoformat()
    except ValueError as exc:
        raise ValueError(f"invalid shadow trade_date {trade_date!r}") from exc
    return normalized_trade_date >= start_date


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
        "polaris_alpha_top4_cap20_daily": StrategySpec(
            name="polaris_alpha_top4_cap20_daily",
            hypothesis_id="CONCENTRATION_ALPHA",
            description="Polaris_Alpha: official shadow concentration variant; top-4 daily momentum with 20% max position and residual cash.",
            top_n=4,
            rebalance_mode="daily",
            max_position_weight=0.20,
        ),
        "h2_rank_decay_exit_h6_top5": StrategySpec(
            name="h2_rank_decay_exit_h6_top5",
            hypothesis_id="COMBO",
            description="Caerus Orion: H2 rank-decay exit + H6 top-5 concentration.",
            top_n=5,
            rebalance_mode="daily",
            use_rank_decay_exit=True,
        ),
        "orion_alpha_rank_decay_top3_cap25": StrategySpec(
            name="orion_alpha_rank_decay_top3_cap25",
            hypothesis_id="CONCENTRATION_ALPHA",
            description="Orion_Alpha: official shadow concentration variant; top-3 Orion rank-decay exit with 25% max position and residual cash.",
            top_n=3,
            rebalance_mode="daily",
            use_rank_decay_exit=True,
            max_position_weight=0.25,
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
