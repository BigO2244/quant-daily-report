from __future__ import annotations

from .engine import StrategySpec


def build_strategy_specs(top_n: int) -> list[StrategySpec]:
    return [
        StrategySpec(
            name="baseline_top10_daily",
            hypothesis_id="BASELINE",
            description="Momentum baseline: daily rebalance, equal-weight top N.",
            selection_mode="momentum",
            top_n=top_n,
            rebalance_mode="daily",
        ),
        StrategySpec(
            name="h1_twice_weekly",
            hypothesis_id="H1",
            description="Slower rebalance: rebalance Monday and Thursday only.",
            selection_mode="slower_rebalance",
            top_n=top_n,
            rebalance_mode="twice_weekly",
        ),
        StrategySpec(
            name="h1_weekly",
            hypothesis_id="H1",
            description="Slower rebalance: weekly Monday rebalance.",
            selection_mode="slower_rebalance",
            top_n=top_n,
            rebalance_mode="weekly",
        ),
        StrategySpec(
            name="h2_rank_decay_exit",
            hypothesis_id="H2",
            description="Rank decay exit: hold unless momentum rank falls below top N*2.",
            selection_mode="rank_decay_exit",
            top_n=top_n,
            rebalance_mode="daily",
            exit_rank_multiple=2.0,
        ),
        StrategySpec(
            name="h3_regime_gating",
            hypothesis_id="H3",
            description="Regime gating: 100% allocation above SPY 200DMA, otherwise 50%.",
            selection_mode="regime_gating",
            top_n=top_n,
            rebalance_mode="daily",
            reduced_allocation=0.5,
        ),
        StrategySpec(
            name="h4_mean_reversion_5d",
            hypothesis_id="H4",
            description="Mean reversion: bottom decile of 3-day returns, hold 5 days.",
            selection_mode="mean_reversion",
            top_n=top_n,
            rebalance_mode="daily",
            fixed_hold_days=5,
        ),
        StrategySpec(
            name="h5_post_move_drift_5d",
            hypothesis_id="H5",
            description="Post-move drift: top-decile 1-day move with 2-day follow-through, hold 5 days.",
            selection_mode="post_move_drift",
            top_n=top_n,
            rebalance_mode="daily",
            fixed_hold_days=5,
        ),
        StrategySpec(
            name="h6_concentration_top5",
            hypothesis_id="H6",
            description="Concentration: top 5 momentum names.",
            selection_mode="concentration",
            top_n=5,
            rebalance_mode="daily",
        ),
        StrategySpec(
            name="h6_concentration_top20",
            hypothesis_id="H6",
            description="Concentration: top 20 momentum names.",
            selection_mode="concentration",
            top_n=20,
            rebalance_mode="daily",
        ),
    ]


HYPOTHESIS_ORDER = ["H1", "H2", "H3", "H4", "H5", "H6"]
