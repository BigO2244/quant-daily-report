from core.strategy_identity import (
    strategy_identity_metadata,
    validate_lane_strategy_identity,
)


def test_paper_baseline_alias_is_explicitly_valid() -> None:
    result = validate_lane_strategy_identity(
        identity=strategy_identity_metadata("2026-07-29"),
        approved_strategy="caerus_polaris",
        lane="paper",
    )
    assert result["status"] == "PASS"
    assert result["reason_code"] == "paper_baseline_identity_verified"


def test_live_orion_label_blocks_growth_engine_targets() -> None:
    result = validate_lane_strategy_identity(
        identity=strategy_identity_metadata("2026-07-29"),
        approved_strategy="orion",
        lane="live_pilot",
    )
    assert result["status"] == "BLOCK"
    assert result["reason_code"] == "live_pilot_approved_strategy_target_mismatch"


def test_live_pass_requires_target_and_explicit_tracking_agreement() -> None:
    identity = strategy_identity_metadata("2026-07-29")
    identity.update(
        {
            "execution_target_strategy_id": "caerus_orion",
            "live_pilot_tracks_approved_strategy": True,
            "live_pilot_mapping_status": "TRACKING_GOVERNED_STRATEGY",
        }
    )
    result = validate_lane_strategy_identity(
        identity=identity,
        approved_strategy="caerus_orion",
        lane="live_pilot",
    )
    assert result["status"] == "PASS"
