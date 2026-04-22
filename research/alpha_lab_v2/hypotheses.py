from __future__ import annotations

from .engine import StrategySpec


def build_strategy_specs() -> list[StrategySpec]:
    return [
        StrategySpec(
            name="baseline_top10_daily",
            hypothesis_id="CONTROL",
            description="Momentum baseline: daily rebalance, equal-weight top 10.",
            top_n=10,
            rebalance_mode="daily",
        ),
        StrategySpec(
            name="h2_rank_decay_exit_top10_daily",
            hypothesis_id="SINGLE",
            description="H2 only: daily top 10 with rank-decay exit at top N*2.",
            top_n=10,
            rebalance_mode="daily",
            use_rank_decay_exit=True,
        ),
        StrategySpec(
            name="h6_top5_daily",
            hypothesis_id="SINGLE",
            description="H6 only: daily top 5 concentration.",
            top_n=5,
            rebalance_mode="daily",
        ),
        StrategySpec(
            name="h1_weekly_top10",
            hypothesis_id="SINGLE",
            description="H1 only: weekly top 10 rebalance.",
            top_n=10,
            rebalance_mode="weekly",
        ),
        StrategySpec(
            name="h2_rank_decay_exit_h6_top5",
            hypothesis_id="COMBO",
            description="H2 + H6: daily top 5 with rank-decay exit.",
            top_n=5,
            rebalance_mode="daily",
            use_rank_decay_exit=True,
        ),
        StrategySpec(
            name="h1_weekly_h2_rank_decay_exit",
            hypothesis_id="COMBO",
            description="H1 + H2: weekly top 10 with rank-decay exit.",
            top_n=10,
            rebalance_mode="weekly",
            use_rank_decay_exit=True,
        ),
        StrategySpec(
            name="h1_weekly_h6_top5",
            hypothesis_id="COMBO",
            description="H1 + H6: weekly top 5 rebalance.",
            top_n=5,
            rebalance_mode="weekly",
        ),
        StrategySpec(
            name="h1_weekly_h2_rank_decay_exit_h6_top5",
            hypothesis_id="COMBO",
            description="H1 + H2 + H6: weekly top 5 with rank-decay exit.",
            top_n=5,
            rebalance_mode="weekly",
            use_rank_decay_exit=True,
        ),
    ]


SINGLE_CHANGE_VARIANTS = {
    "h2_rank_decay_exit_top10_daily",
    "h6_top5_daily",
    "h1_weekly_top10",
}

