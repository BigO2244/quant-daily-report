from __future__ import annotations

from research.evidence_hardening import classify_sleeve_gate


def test_sleeve_gate_routes_orion_lyra_to_merge_review() -> None:
    assert (
        classify_sleeve_gate("orion", lifecycle_stage="shadow_observed", operational_full=False)
        == "RETIRE_OR_MERGE_REVIEW"
    )
    assert (
        classify_sleeve_gate("lyra", lifecycle_stage="shadow_observed", operational_full=False)
        == "RETIRE_OR_MERGE_REVIEW"
    )


def test_sleeve_gate_blocks_polaris_without_operational_full_evidence() -> None:
    assert (
        classify_sleeve_gate("polaris", lifecycle_stage="paper_observed", operational_full=False)
        == "PILOT_CAPITAL_BLOCKED"
    )


def test_sleeve_gate_keeps_cassiopeia_research_directional() -> None:
    assert (
        classify_sleeve_gate("cassiopeia", lifecycle_stage="spec_only", operational_full=False)
        == "RESEARCH_DIRECTIONAL"
    )

