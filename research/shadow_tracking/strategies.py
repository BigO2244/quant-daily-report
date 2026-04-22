from __future__ import annotations

from dataclasses import dataclass

from research.alpha_lab_v2.engine import StrategySpec


@dataclass(frozen=True)
class ShadowStrategyDefinition:
    strategy_name: str
    strategy_slug: str
    source_variant: str
    spec: StrategySpec


def build_shadow_definitions() -> list[ShadowStrategyDefinition]:
    return [
        ShadowStrategyDefinition(
            strategy_name="Caerus Polaris",
            strategy_slug="caerus_polaris",
            source_variant="baseline_top10_daily",
            spec=StrategySpec(
                name="baseline_top10_daily",
                hypothesis_id="CONTROL",
                description="Caerus Polaris: current paper baseline / operational control.",
                top_n=10,
                rebalance_mode="daily",
            ),
        ),
        ShadowStrategyDefinition(
            strategy_name="Caerus Orion",
            strategy_slug="caerus_orion",
            source_variant="h2_rank_decay_exit_h6_top5",
            spec=StrategySpec(
                name="h2_rank_decay_exit_h6_top5",
                hypothesis_id="COMBO",
                description="Caerus Orion: H2 rank-decay exit + H6 top-5 concentration.",
                top_n=5,
                rebalance_mode="daily",
                use_rank_decay_exit=True,
            ),
        ),
        ShadowStrategyDefinition(
            strategy_name="Caerus Lyra",
            strategy_slug="caerus_lyra",
            source_variant="h1_weekly_h6_top5",
            spec=StrategySpec(
                name="h1_weekly_h6_top5",
                hypothesis_id="COMBO",
                description="Caerus Lyra: H1 weekly rebalance + H6 top-5 concentration.",
                top_n=5,
                rebalance_mode="weekly",
            ),
        ),
    ]


def build_strategy_lookup() -> dict[str, ShadowStrategyDefinition]:
    return {item.strategy_slug: item for item in build_shadow_definitions()}

