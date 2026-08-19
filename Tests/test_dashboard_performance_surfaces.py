from __future__ import annotations

import copy
import hashlib

import pytest

from core.accounting_journal import canonical_json

from core.lane_truth_status import (
    LaneTruthStatusError,
    build_daily_lane_audit,
    build_dashboard_performance_surfaces,
    validate_dashboard_performance_surfaces,
)
from Tests.lane_truth_fixtures import (
    AS_OF,
    KNOWN_SLEEVES,
    capital,
    deployment_state,
    lane,
    lineage,
    policy,
    valuation_and_performance,
)


def _audit(lane_payload: dict, governed: dict, *, reconciliation=None) -> dict:
    valuation, performance = valuation_and_performance(
        lane_payload, lane_payload["eligible_sleeves"][0]["sleeve_id"]
    )
    if reconciliation is None:
        reconciliation = (
            lineage(lane_payload, "RECONCILIATION", "NOT_APPLICABLE_MODELED")
            if lane_payload["lane_kind"] == "SHADOW"
            else lineage(lane_payload, "RECONCILIATION")
        )
    return build_daily_lane_audit(
        deployment_policy=governed,
        known_sleeve_ids=KNOWN_SLEEVES,
        lane_id=lane_payload["lane_id"],
        as_of=AS_OF,
        deployment_state=deployment_state(governed),
        capital=capital(),
        journal_status=lineage(lane_payload, "JOURNAL"),
        reconciliation_status=reconciliation,
        valuation=valuation,
        performance=performance,
    )


def test_dashboard_keeps_modeled_paper_and_live_surfaces_distinct() -> None:
    lanes = [
        lane("a_shadow", "SHADOW", "caerus_polaris"),
        lane("b_paper", "PAPER", "caerus_orion"),
        lane("c_live", "LIVE", "caerus_lyra"),
    ]
    governed = policy(*lanes)

    projection = build_dashboard_performance_surfaces(
        [_audit(row, governed) for row in lanes]
    )

    lane_rows = [row for row in projection["performance_surfaces"] if row["sleeve_id"] is None]
    assert [(row["performance_surface"], row["label"], row["claim_type"]) for row in lane_rows] == [
        ("MODELED_SHADOW_NAV", "modeled shadow return", "MODELED_RETURN"),
        ("FACTUAL_PAPER", "realized paper return", "FACTUAL_RETURN"),
        ("FACTUAL_LIVE", "realized live return", "FACTUAL_RETURN"),
    ]
    assert all(row["display_return"] is not None for row in lane_rows)
    assert projection["execution_authority"] is False
    assert projection["approval_authority"] is False


def test_dashboard_never_exposes_a_suppressed_factual_return() -> None:
    live_lane = lane("live_control", "LIVE", "caerus_lyra")
    governed = policy(live_lane)
    failed = lineage(
        live_lane,
        "RECONCILIATION",
        "FAIL",
        blockers=("positions_do_not_reconcile",),
    )

    projection = build_dashboard_performance_surfaces(
        [_audit(live_lane, governed, reconciliation=failed)]
    )

    rows = projection["performance_surfaces"]
    assert projection["status"] == "BLOCKED"
    assert all(row["claim_status"] == "SUPPRESSED" for row in rows)
    assert all(row["display_return"] is None for row in rows)
    assert all(row["blocker_codes"] == ["positions_do_not_reconcile"] for row in rows)


def test_dashboard_shows_deployment_and_capital_state_for_each_surface() -> None:
    paper_lane = lane("paper_control", "PAPER", "caerus_orion")
    governed = policy(paper_lane)
    projection = build_dashboard_performance_surfaces([_audit(paper_lane, governed)])

    row = projection["performance_surfaces"][0]
    assert row["active_deployment_version"] == "deployment-truth-v1"
    assert row["prior_deployment_version"] == "deployment-truth-v0"
    assert row["rollback_deployment_version"] == "deployment-truth-safe"
    assert row["capital_ceiling_usd"] == 500.0
    assert row["effective_deployable_capital_usd"] == 460.0


def test_dashboard_projection_is_hash_bound_against_return_relabeling() -> None:
    shadow_lane = lane("shadow_control", "SHADOW", "caerus_lyra")
    governed = policy(shadow_lane)
    projection = build_dashboard_performance_surfaces([_audit(shadow_lane, governed)])

    tampered = copy.deepcopy(projection)
    tampered["performance_surfaces"][0]["performance_surface"] = "FACTUAL_LIVE"
    with pytest.raises(LaneTruthStatusError, match="classification mismatch"):
        validate_dashboard_performance_surfaces(tampered)


def test_projection_is_deterministic() -> None:
    paper_lane = lane("paper_control", "PAPER", "caerus_orion")
    governed = policy(paper_lane)
    audit = _audit(paper_lane, governed)
    assert build_dashboard_performance_surfaces([audit]) == build_dashboard_performance_surfaces([audit])


def test_resealed_projection_cannot_relabel_or_shift_return_time() -> None:
    paper_lane = lane("paper_control", "PAPER", "caerus_orion")
    governed = policy(paper_lane)
    projection = build_dashboard_performance_surfaces([_audit(paper_lane, governed)])

    relabeled = copy.deepcopy(projection)
    relabeled["performance_surfaces"][0]["label"] = "modeled shadow return"
    relabeled.pop("content_hash")
    relabeled["content_hash"] = hashlib.sha256(
        canonical_json(relabeled).encode("utf-8")
    ).hexdigest()
    with pytest.raises(LaneTruthStatusError, match="return label mismatch"):
        validate_dashboard_performance_surfaces(relabeled)

    shifted = copy.deepcopy(projection)
    shifted["performance_surfaces"][0]["as_of"] = "2026-08-18T19:59:00Z"
    shifted.pop("content_hash")
    shifted["content_hash"] = hashlib.sha256(
        canonical_json(shifted).encode("utf-8")
    ).hexdigest()
    with pytest.raises(LaneTruthStatusError, match="as_of mismatch"):
        validate_dashboard_performance_surfaces(shifted)
