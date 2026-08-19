import json
from pathlib import Path

from core.lifecycle_recommendation import validate_lifecycle_recommendation
from core.owner_decision import parse_owner_decision


ROOT = Path(__file__).resolve().parents[1]


def test_owner_decision_binds_exact_generic_live_v1_terms():
    recommendation = validate_lifecycle_recommendation(
        json.loads((ROOT / "docs/evidence/generic_live_v1_lyra_recommendation_2026-08-19.json").read_text())
    )
    decision = parse_owner_decision(
        json.loads((ROOT / "docs/evidence/generic_live_v1_lyra_owner_decision_2026-08-19.json").read_text())
    )
    terms = decision.approved_policy_patch
    assert decision.approved is True
    assert decision.recommendation_id == recommendation["recommendation_id"]
    assert decision.recommendation_hash == recommendation["content_hash"]
    assert decision.effective_session == "2026-08-19"
    assert decision.capital_ceiling == 460.0
    assert terms["adapter_contract"] == "CAERUS_GENERIC_LANE_V4"
    assert terms["eligible_sleeve_ids"] == ("caerus_lyra",)
    assert terms["decision_schema"] == "caerus.sleeve_decision.v2"
    assert terms["capital_ceiling_usd"] == 460.0
    assert terms["minimum_trade_usd"] == 100.0
    assert terms["maximum_orders_per_session"] == 1
    assert terms["maximum_gross_fraction"] == 0.95
    assert terms["whole_share_only"] is True
    assert terms["long_only"] is True
    assert terms["leverage_allowed"] is False
    assert terms["shorting_allowed"] is False
    assert terms["generic_paper_cutover_allowed"] is False
    assert terms["legacy_live_executor_allowed"] is False
    assert terms["opportunistic_test_orders_allowed"] is False
    assert set(terms["automatic_rearm_and_rollback_triggers"]) == {
        "PREFLIGHT_BREAK", "SUBMISSION_BREAK", "ORDER_BREAK",
        "RECONCILIATION_BREAK", "ACCOUNTING_BREAK", "REPORTING_BREAK",
    }
    assert decision.execution_authority is False
