from __future__ import annotations

import copy

import pytest

from core.dashboard_truth_consumer import (
    DashboardTruthConsumerError,
    build_dashboard_truth_payload,
    consume_dashboard_truth,
    validate_dashboard_truth_consumption,
)
from core.lane_truth_status import (
    LaneTruthStatusError,
    build_dashboard_performance_surfaces,
)
from Tests.lane_truth_fixtures import KNOWN_SLEEVES, lane, lineage, policy
from Tests.test_dashboard_performance_surfaces import _audit


def test_dashboard_consumer_uses_only_validated_truth_projection() -> None:
    lanes = [
        lane("a_shadow", "SHADOW", "caerus_polaris"),
        lane("b_paper", "PAPER", "caerus_orion"),
        lane("c_live", "LIVE", "caerus_lyra"),
    ]
    governed = policy(*lanes)
    projection = build_dashboard_performance_surfaces(
        [_audit(row, governed) for row in lanes]
    )
    payload = build_dashboard_truth_payload(projection)

    assert payload["fallback_data_used"] is False
    assert [row["label"] for row in payload["cards"] if row["sleeve_id"] is None] == [
        "modeled shadow return", "realized paper return", "realized live return"
    ]
    assert payload["truth_projection_hash"] == projection["content_hash"]
    assert payload["execution_authority"] is False


def test_suppressed_factual_claim_remains_suppressed_for_ui() -> None:
    live = lane("live_control", "LIVE", "caerus_lyra")
    governed = policy(live)
    failed = lineage(
        live, "RECONCILIATION", "FAIL", blockers=("positions_do_not_reconcile",)
    )
    projection = build_dashboard_performance_surfaces(
        [_audit(live, governed, reconciliation=failed)]
    )
    payload = build_dashboard_truth_payload(projection)

    assert all(row["claim_status"] == "SUPPRESSED" for row in payload["cards"])
    assert all(row["return_value"] is None for row in payload["cards"])


def test_relabel_or_return_injection_is_rejected_before_consumption() -> None:
    paper = lane("paper_control", "PAPER", "caerus_orion")
    governed = policy(paper)
    projection = build_dashboard_performance_surfaces([_audit(paper, governed)])
    tampered = copy.deepcopy(projection)
    tampered["performance_surfaces"][0]["label"] = "live return"
    with pytest.raises(LaneTruthStatusError):
        build_dashboard_truth_payload(tampered)


def test_production_consumption_is_disabled_and_empty_by_default() -> None:
    paper = lane("paper_control", "PAPER", "caerus_orion")
    governed = policy(paper)
    projection = build_dashboard_performance_surfaces([_audit(paper, governed)])

    result = consume_dashboard_truth(truth_status_artifact=projection)

    assert result["status"] == "TRUTH_VALIDATED_NO_CONSUMPTION"
    assert result["consumer_enabled"] is False
    assert result["dashboard_truth_payload"] is None
    assert result["dashboard_write_performed"] is False
    assert result["external_call_performed"] is False


def test_explicit_consumption_preserves_canonical_available_and_suppressed_claims() -> None:
    live = lane("live_control", "LIVE", "caerus_lyra")
    governed = policy(live)
    failed = lineage(
        live, "RECONCILIATION", "FAIL", blockers=("positions_do_not_reconcile",)
    )
    projection = build_dashboard_performance_surfaces(
        [_audit(live, governed, reconciliation=failed)]
    )

    result = consume_dashboard_truth(
        truth_status_artifact=projection, consumer_enabled=True
    )

    assert result["status"] == "TRUTH_CONSUMED_NO_PUBLISH"
    assert all(
        card["claim_status"] == "SUPPRESSED"
        and card["return_value"] is None
        for card in result["dashboard_truth_payload"]["cards"]
    )
    assert result["dashboard_write_performed"] is False


def test_disabled_consumption_cannot_be_tampered_into_exposing_cards() -> None:
    paper = lane("paper_control", "PAPER", "caerus_orion")
    governed = policy(paper)
    projection = build_dashboard_performance_surfaces([_audit(paper, governed)])
    result = consume_dashboard_truth(truth_status_artifact=projection)
    result["dashboard_truth_payload"] = build_dashboard_truth_payload(projection)
    with pytest.raises(DashboardTruthConsumerError):
        validate_dashboard_truth_consumption(result)
