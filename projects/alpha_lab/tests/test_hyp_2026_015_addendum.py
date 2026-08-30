from __future__ import annotations

import hashlib
from pathlib import Path


ALPHA_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ALPHA_ROOT / "templates/HYPOTHESIS.md"
SPEC = (
    ALPHA_ROOT
    / "hypotheses/HYP-2026-015_industry_earnings_information_diffusion.md"
)
ADDENDUM = (
    ALPHA_ROOT
    / "hypotheses/"
    "HYP-2026-015-ADDENDUM-001_source_materiality_and_evaluator_determinism.md"
)
SPEC_SHA256 = "3ca51f2f477c548d0b9ad266f004b4f61ba532f1d23961847c05db1e5fd033d6"
ADDENDUM_SHA256 = "6a3747d98e89efdb3f73e0f7a3587992b38804789e43534a7ec03842ee5e3c8e"


def _frozen_hash(path: Path, marker: bytes) -> str:
    body = path.read_bytes()
    assert marker in body
    return hashlib.sha256(body.split(marker, 1)[0]).hexdigest()


def test_original_hypothesis_remains_byte_identical_before_freeze_record():
    assert _frozen_hash(SPEC, b"## Freeze record\n") == SPEC_SHA256


def test_owner_addendum_hash_and_binding_are_immutable():
    text = ADDENDUM.read_text(encoding="utf-8")

    assert _frozen_hash(ADDENDUM, b"## Addendum record\n") == ADDENDUM_SHA256
    assert SPEC_SHA256 in text
    assert "OWNER_APPROVED_FROZEN_ADDENDUM" in text
    assert f"Addendum SHA-256: `{ADDENDUM_SHA256}`" in text


def test_materiality_and_included_lineage_gates_are_explicit():
    text = ADDENDUM.read_text(encoding="utf-8")

    assert "source_hydrated_count / source_candidate_count >= 0.999" in text
    assert "reporter_source_ready_count / reporter_candidate_count >= 0.999" in text
    assert "potential_peer_observation_count >= 0.99" in text
    assert "every included reporter, peer, and control observation has `100%`" in text
    assert "SELECTION_RELATED_MISSINGNESS" in text
    assert "minimum corresponding net incremental return" in text


def test_structural_floors_are_not_mislabeled_as_realized_signal_counts():
    text = ADDENDUM.read_text(encoding="utf-8")
    compact = " ".join(text.split())

    assert "structural pre-signal eligibility potential" in text
    assert "Applying those two conditions would access market outcomes" in compact
    assert "actually qualifying 2019-2024 validation clusters" in compact
    assert "Structural potential is not represented as an achieved validation sample" in compact


def test_multi_reporter_and_event_capital_rules_are_deterministic():
    text = ADDENDUM.read_text(encoding="utf-8")
    compact = " ".join(text.split())

    assert "reporter_set_id" in text
    assert "One cluster produces one observation" in compact
    assert "normalized capital `1.0`" in compact
    assert "corresponding to `$1,000,000`" in compact
    assert "leave the residual as cash; never redistribute it" in compact
    assert "net_return_s(c) = gross_return_s - 2 * c * G_s" in text
    assert "identical costs cancel in their difference by construction" in text


def test_one_sided_cluster_inference_is_fully_determined():
    text = ADDENDUM.read_text(encoding="utf-8")
    compact = " ".join(text.split())

    assert "reporter_set_id`, four-digit SIC, and reaction-session calendar quarter" in compact
    assert "SE = sample_sd / sqrt(n)" in text
    assert "1 - StudentT_CDF(t, df=n-1)" in text
    assert "StudentT_PPF(0.90, df=n-1)" in text
    assert "adjusted and raw p-values coincide" in text
    assert "primary mean to be at least `+0.50%`" in compact


def test_addendum_preserves_outcome_and_production_boundaries():
    text = ADDENDUM.read_text(encoding="utf-8")

    assert "Outcome access at freeze: `NONE`" in text
    assert "challenge period remains sealed" in text
    assert "LOCAL_PREREGISTRATION_PENDING_AUTHENTICATED_LEDGER_IMPORT" in text
    assert "NON_DECISION_GRADE" in text
    assert "FAMILY-2026-0015-T001" in text
    assert "outcome_access_started" in text
    assert "No term in this addendum authorizes Shadow, Paper, Live" in text


def test_hypothesis_template_requires_a_preoutcome_coverage_policy():
    text = TEMPLATE.read_text(encoding="utf-8")
    compact = " ".join(text.split())

    assert "Aggregate source/universe coverage tolerance and exact denominator" in text
    assert "Included-row causal lineage requirement" in text
    assert "Deterministic pre-outcome exclusion rule" in text
    assert "Missingness diagnostics and concentration gates" in text
    assert "Absolute `100%` source-universe gate owner sign-off" in text
    assert "These rules may not be invented or relaxed after outcome access" in compact
