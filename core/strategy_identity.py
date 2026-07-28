from __future__ import annotations

from typing import Any, Mapping


LIVE_STRATEGY_ID = "growth_engine_v4"
EXECUTION_TARGET_STRATEGY_ID = LIVE_STRATEGY_ID
EXECUTION_TARGET_TYPE = "precompute_signals"
SHADOW_BASELINE_STRATEGY = "caerus_polaris"
LIVE_TRACKS_SHADOW_BASELINE = False
PAPER_GOVERNED_STRATEGY_ID = "caerus_polaris"
LIVE_PILOT_GOVERNED_STRATEGY_ID = "caerus_orion"


def _canonical_strategy_id(value: object) -> str:
    raw = str(value or "").strip().lower()
    aliases = {
        "polaris": "caerus_polaris",
        "orion": "caerus_orion",
        "growth_engine_v4": "growth_engine_v4",
    }
    return aliases.get(raw, raw)


def validate_lane_strategy_identity(
    *,
    identity: Mapping[str, Any] | None,
    approved_strategy: str,
    lane: str,
) -> dict[str, Any]:
    """Fail-closed lane-to-target identity validation."""
    identity = identity or {}
    lane_id = str(lane or "").strip().lower()
    approved = _canonical_strategy_id(approved_strategy)
    target = _canonical_strategy_id(
        identity.get("execution_target_strategy_id")
        or identity.get("live_strategy_id")
    )
    result = {
        "lane": lane_id,
        "approved_strategy_id": approved,
        "execution_target_strategy_id": target or None,
        "status": "BLOCK",
        "reason_code": "strategy_identity_unverified",
    }
    if lane_id == "paper":
        governed = _canonical_strategy_id(
            identity.get("paper_governed_strategy_id") or PAPER_GOVERNED_STRATEGY_ID
        )
        mapping = str(identity.get("paper_mapping_status") or "").upper()
        legacy_alias = target == "growth_engine_v4" and approved == "caerus_polaris"
        if approved == governed and (
            mapping == "ENGINE_BASELINE_ALIAS" or legacy_alias
        ):
            result.update(
                {
                    "status": "PASS",
                    "reason_code": "paper_baseline_identity_verified",
                    "governed_strategy_id": governed,
                }
            )
        else:
            result.update(
                {
                    "reason_code": "paper_approved_strategy_target_mismatch",
                    "governed_strategy_id": governed,
                }
            )
        return result

    if lane_id == "live_pilot":
        governed = _canonical_strategy_id(
            identity.get("live_pilot_governed_strategy_id")
            or LIVE_PILOT_GOVERNED_STRATEGY_ID
        )
        tracks = identity.get("live_pilot_tracks_approved_strategy")
        if approved == governed and target == governed and tracks is True:
            result.update(
                {
                    "status": "PASS",
                    "reason_code": "live_pilot_identity_verified",
                    "governed_strategy_id": governed,
                }
            )
        else:
            result.update(
                {
                    "reason_code": "live_pilot_approved_strategy_target_mismatch",
                    "governed_strategy_id": governed,
                    "tracks_approved_strategy": tracks,
                }
            )
        return result

    result["reason_code"] = "strategy_identity_lane_unknown"
    return result


def strategy_identity_metadata(trade_date: str) -> dict[str, object]:
    return {
        "live_strategy_id": LIVE_STRATEGY_ID,
        "execution_target_strategy_id": EXECUTION_TARGET_STRATEGY_ID,
        "execution_target_source": f"outputs/precompute/{trade_date}/signals.json",
        "execution_target_type": EXECUTION_TARGET_TYPE,
        "paper_governed_strategy_id": PAPER_GOVERNED_STRATEGY_ID,
        "paper_mapping_status": "ENGINE_BASELINE_ALIAS",
        "live_pilot_governed_strategy_id": LIVE_PILOT_GOVERNED_STRATEGY_ID,
        "live_pilot_mapping_status": "NOT_TRACKING_GOVERNED_STRATEGY",
        "live_pilot_tracks_approved_strategy": False,
        "shadow_baseline_strategy": SHADOW_BASELINE_STRATEGY,
        "shadow_baseline_source": f"outputs/shadow_candidates/{trade_date}/caerus_polaris.json",
        "live_tracks_shadow_baseline": LIVE_TRACKS_SHADOW_BASELINE,
    }
