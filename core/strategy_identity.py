from __future__ import annotations


LIVE_STRATEGY_ID = "growth_engine_v4"
EXECUTION_TARGET_TYPE = "precompute_signals"
SHADOW_BASELINE_STRATEGY = "caerus_polaris"
LIVE_TRACKS_SHADOW_BASELINE = False


def strategy_identity_metadata(trade_date: str) -> dict[str, object]:
    return {
        "live_strategy_id": LIVE_STRATEGY_ID,
        "execution_target_source": f"outputs/precompute/{trade_date}/signals.json",
        "execution_target_type": EXECUTION_TARGET_TYPE,
        "shadow_baseline_strategy": SHADOW_BASELINE_STRATEGY,
        "shadow_baseline_source": f"outputs/shadow_candidates/{trade_date}/caerus_polaris.json",
        "live_tracks_shadow_baseline": LIVE_TRACKS_SHADOW_BASELINE,
    }
