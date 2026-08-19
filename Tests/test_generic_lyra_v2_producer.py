import copy
import json
from pathlib import Path

import pytest

from core.generic_lyra_v2_producer import (
    GenericLyraV2ProducerError,
    build_generic_lyra_v2_decision_batch,
)
from core.governed_universe_freeze import GovernedUniverseFreezeError
from core.portfolio_operating_model import content_hash


ROOT = Path(__file__).resolve().parents[1]
FREEZE = json.loads(
    (ROOT / "docs/evidence/lyra_governed_universe_freeze_2026-08-19.json").read_text()
)


def _sources(symbol="AAPL"):
    row = {
        "schema_version": "caerus.sleeve_decision.v1",
        "trade_date": "2026-08-19", "session_id": "session:2026-08-19:source",
        "session_hash": "a" * 64, "sleeve_id": "caerus_lyra",
        "outcome": "RECOMMENDATION", "target_rows": [
            {"symbol": symbol, "target_weight": 1.0, "source_target_weight": 1.0}
        ],
        "reason_codes": ["EVALUATION_ONLY", "NON_DECISION_GRADE_UNIVERSE", "OPPORTUNITY_AVAILABLE"],
    }
    # Legacy v1 permits additional fields; the producer binds every byte.
    row["content_hash"] = content_hash(row)
    polaris = {
        "schema_version": "caerus.sleeve_decision.v1",
        "trade_date": "2026-08-19", "session_id": row["session_id"],
        "session_hash": row["session_hash"], "sleeve_id": "caerus_polaris",
        "outcome": "OBSERVATION", "target_rows": [], "reason_codes": ["CONTROL"],
    }
    polaris["content_hash"] = content_hash(polaris)
    decisions = {
        "schema_version": "caerus.sleeve_decision_batch.v1",
        "trade_date": "2026-08-19", "session_id": row["session_id"],
        "session_hash": row["session_hash"], "complete_registry_coverage": True,
        "decisions": [polaris, row],
    }
    decisions["content_hash"] = content_hash(decisions["decisions"])
    def envelope(sleeve_id):
        payload = {
            "schema_version": "caerus_sleeve_evaluation_v1",
            "trade_date": "2026-08-19", "run_id": "run:source",
            "sleeve_id": sleeve_id, "strategy_type": "fixture", "role": "fixture",
            "lifecycle": {"status": "shadow", "frozen": False},
            "evaluation": {"status": "OK", "runner": "fixture", "message": "fixture", "evaluated_at": "2026-08-19T11:00:00+00:00"},
            "opportunity": {"available": True, "decision_eligible": True, "candidate_count": 1},
            "eligibility": {"capital_eligible": False},
            "reason_codes": (["EVALUATION_ONLY", "NON_DECISION_GRADE_UNIVERSE"] if sleeve_id == "caerus_lyra" else ["CONTROL"]),
        }
        if sleeve_id == "caerus_lyra":
            payload["universe"] = {"snapshot_hash": FREEZE["source_sha256"]}
            payload["provenance"] = {"source_artifacts": [{"sha256": "b" * 64}]}
        return payload
    evaluations = {
        "schema_version": "caerus_all_sleeve_evaluation_v1",
        "trade_date": "2026-08-19", "run_id": "run:source",
        "generated_at": "2026-08-19T11:00:00+00:00",
        "all_non_frozen_evaluated": True,
        "expected_non_frozen_sleeve_ids": ["caerus_polaris", "caerus_lyra"],
        "envelopes": [envelope("caerus_polaris"), envelope("caerus_lyra")],
    }
    return decisions, evaluations


def _build(**changes):
    decisions, evaluations = _sources(changes.pop("symbol", "AAPL"))
    return build_generic_lyra_v2_decision_batch(
        legacy_decision_batch=decisions, evaluation_batch=evaluations,
        universe_freeze=FREEZE, universe_path=ROOT / "data/universe.csv",
        session_as_of="2026-08-19T07:00:00-04:00",
        generated_at="2026-08-19T11:01:00+00:00", **changes,
    )


def test_prospective_exact_freeze_builds_only_lyra_v2_and_preserves_targets() -> None:
    batch = _build()
    decision = next(row for row in batch["decisions"] if row["sleeve_id"] == "caerus_lyra")
    assert batch["expected_sleeve_ids"] == ["caerus_lyra", "caerus_polaris"]
    assert decision["schema_version"] == "caerus.sleeve_decision.v2"
    assert decision["decision_grade"] == "READY"
    assert decision["target_rows"] == [{"symbol": "AAPL", "target_weight": 1.0}]
    assert "GOVERNED_UNIVERSE_FREEZE_PROSPECTIVE" in decision["reason_codes"]
    assert "NON_DECISION_GRADE_UNIVERSE" in decision["reason_codes"]
    assert decision["liquidity_status"] == "UNKNOWN"
    assert all("lane" not in key for key in decision)


def test_pre_freeze_session_and_file_drift_fail_closed(tmp_path) -> None:
    decisions, evaluations = _sources()
    with pytest.raises(GovernedUniverseFreezeError, match="predates"):
        build_generic_lyra_v2_decision_batch(
            legacy_decision_batch=decisions, evaluation_batch=evaluations,
            universe_freeze=FREEZE, universe_path=ROOT / "data/universe.csv",
            session_as_of="2026-08-18T07:00:00-04:00",
            generated_at="2026-08-19T11:01:00+00:00",
        )
    drift = tmp_path / "universe.csv"
    drift.write_bytes((ROOT / "data/universe.csv").read_bytes() + b"FAKE,Unknown\n")
    with pytest.raises(GovernedUniverseFreezeError, match="bytes differ"):
        build_generic_lyra_v2_decision_batch(
            legacy_decision_batch=decisions, evaluation_batch=evaluations,
            universe_freeze=FREEZE, universe_path=drift,
            session_as_of="2026-08-19T07:00:00-04:00",
            generated_at="2026-08-19T11:01:00+00:00",
        )


def test_out_of_universe_target_and_tampered_source_fail_closed() -> None:
    with pytest.raises(GenericLyraV2ProducerError, match="outside frozen universe"):
        _build(symbol="ZZZZ")
    decisions, evaluations = _sources()
    decisions["decisions"][1]["target_rows"][0]["target_weight"] = 0.5
    with pytest.raises(Exception, match="batch hash mismatch"):
        build_generic_lyra_v2_decision_batch(
            legacy_decision_batch=decisions, evaluation_batch=evaluations,
            universe_freeze=FREEZE, universe_path=ROOT / "data/universe.csv",
            session_as_of="2026-08-19T07:00:00-04:00",
            generated_at="2026-08-19T11:01:00+00:00",
        )
