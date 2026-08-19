import copy
import json
from pathlib import Path

import pytest

from core.generic_paper_live_rehearsal import (
    GenericPaperLiveRehearsalError,
    build_generic_paper_live_rehearsal,
    validate_generic_paper_live_rehearsal,
)
from core.scheduled_v2_factual_pipeline import run_scheduled_v2_factual_pipeline
from Tests.test_scheduled_v2_factual_pipeline import _inputs


ROOT = Path(__file__).resolve().parents[1]


def _receipt():
    return run_scheduled_v2_factual_pipeline(**_inputs())


def test_rehearsal_is_derived_from_exact_sealed_pipeline_receipt():
    result = build_generic_paper_live_rehearsal(
        created_at="2026-08-19T02:15:00+00:00", pipeline_receipt=_receipt()
    )
    assert result["source_artifact_hash"] == "c7686e4f2b8f8d4a615b2a30b726334a977040e9d7be3478f17d1a0bc6410fe0"
    assert result["operational_receipt_hash"] == "98e5e10a4addc42b733ed6813d5bca713234af81fd09fe1aab279355cca38e3f"
    assert result["classification"] == "STRUCTURAL_REHEARSAL_NOT_BROKER_FACTUAL"
    assert [row["lane_kind"] for row in result["rehearsals"]] == ["LIVE", "PAPER"]
    assert all(row["status"] == "VALIDATED_NO_WRITE" for row in result["rehearsals"])
    assert result["broker_factual"] is False
    assert result["execution_authority"] is False


def test_published_summary_exactly_matches_source_bound_builder():
    observed = json.loads(
        (ROOT / "docs/evidence/generic_paper_live_no_write_rehearsal_2026-08-18.json").read_text()
    )
    expected = build_generic_paper_live_rehearsal(
        created_at="2026-08-19T02:15:00+00:00", pipeline_receipt=_receipt()
    )
    assert observed == expected
    assert validate_generic_paper_live_rehearsal(observed) == observed


def test_pipeline_or_nested_execution_tamper_fails_closed():
    receipt = _receipt()
    receipt["artifact"]["factual_lanes"][0]["execution_rehearsal"]["status"] = "BLOCKED"
    with pytest.raises(GenericPaperLiveRehearsalError, match="receipt content_hash"):
        build_generic_paper_live_rehearsal(
            created_at="2026-08-19T02:15:00+00:00", pipeline_receipt=receipt
        )


def test_summary_cannot_claim_broker_factual_or_authority():
    result = build_generic_paper_live_rehearsal(
        created_at="2026-08-19T02:15:00+00:00", pipeline_receipt=_receipt()
    )
    for field in ("broker_factual", "execution_authority", "activation_authority"):
        tampered = copy.deepcopy(result)
        tampered[field] = True
        with pytest.raises(GenericPaperLiveRehearsalError):
            validate_generic_paper_live_rehearsal(tampered)
