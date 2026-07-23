from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from projects.alpha_lab.experiments import (
    LANES,
    classify_option_trade,
    earnings_revision_score,
    insider_conviction_score,
    options_information_score,
    supply_chain_raw_score,
)
from projects.alpha_lab.experiments.run_data_gate import run_lane
from projects.alpha_lab.experiments.run_data_gate import inspect_asset
from projects.alpha_lab.factory import ContractValidationError, canonical_hash
from projects.alpha_lab.experiments.catalog import DataAsset


REPO_ROOT = Path(__file__).parents[3]


def test_frozen_hypothesis_hashes_match_bodies():
    for lane in LANES:
        text = (REPO_ROOT / lane.spec_path).read_text(encoding="utf-8")
        body, freeze = text.split("## Freeze record\n", 1)
        actual = hashlib.sha256(body.encode("utf-8")).hexdigest()
        assert "sha256:{}".format(actual) in freeze


def test_four_lane_ids_are_append_only_and_unique():
    hypothesis_ids = [lane.hypothesis_id for lane in LANES]
    assert hypothesis_ids[1:5] == [
        "HYP-2026-002",
        "HYP-2026-003",
        "HYP-2026-004",
        "HYP-2026-005",
    ]
    assert hypothesis_ids == [
        "HYP-2026-001",
        "HYP-2026-002",
        "HYP-2026-003",
        "HYP-2026-004",
        "HYP-2026-005",
        "HYP-2026-006",
        "HYP-2026-007",
        "HYP-2026-008",
        "HYP-2026-009",
        "HYP-2026-010",
        "HYP-2026-011",
        "HYP-2026-012",
    ]
    assert len({lane.experiment_id for lane in LANES}) == len(LANES)


def test_catalog_gates_frozen_cross_lane_controls_and_event_inputs():
    fields_by_lane = {
        lane.hypothesis_id: {
            field for asset in lane.assets for field in asset.required_fields
        }
        for lane in LANES
    }
    assert {"book_to_market", "sector_id", "market_cap"} <= fields_by_lane[
        "HYP-2026-003"
    ]
    assert {
        "risk_free_rate",
        "dividend_yield",
        "borrow_assumption",
        "open_interest_available_at",
    } <= fields_by_lane["HYP-2026-004"]
    assert {
        "is_material_8k",
        "commodity_return",
        "sector_return",
        "schedule_available_at",
    } <= fields_by_lane["HYP-2026-005"]


def test_frozen_signal_composites_use_declared_weights():
    assert earnings_revision_score(
        breadth_rank=1.0,
        median_eps_revision_rank=0.5,
        revenue_consensus_change_rank=0.0,
        positive_dispersion_reduction_rank=0.5,
    ) == pytest.approx(0.625)
    assert insider_conviction_score(
        distinct_buyer_count_rank=1.0,
        purchase_value_to_market_cap_rank=0.5,
        average_role_score=0.25,
    ) == pytest.approx(0.70)
    assert options_information_score(
        signed_delta_imbalance_rank=1.0,
        strike_displacement_rank=0.5,
        call_minus_put_iv_change_rank=0.25,
    ) == pytest.approx(0.70)
    assert supply_chain_raw_score([(2.0, 0.75, 0.8), (-1.0, 0.10, 1.0)]) == pytest.approx(
        0.70
    )


def test_signals_fail_closed_on_missing_domain_or_bad_inputs():
    with pytest.raises(ContractValidationError, match=r"\[0, 1\]"):
        earnings_revision_score(
            breadth_rank=1.1,
            median_eps_revision_rank=0.5,
            revenue_consensus_change_rank=0.5,
            positive_dispersion_reduction_rank=0.5,
        )
    with pytest.raises(ContractValidationError, match="at least one positive"):
        supply_chain_raw_score([(-1.0, 0.20, 1.0)])
    with pytest.raises(ContractValidationError, match="offset"):
        supply_chain_raw_score([(1.0, 0.10, 1.0), (-2.0, 0.50, 1.0)])
    with pytest.raises(ContractValidationError, match="at least one"):
        supply_chain_raw_score([])


def test_option_trade_classification_uses_frozen_threshold():
    assert classify_option_trade(price=1.06, bid=1.00, ask=1.10) == "BUYER"
    assert classify_option_trade(price=1.04, bid=1.00, ask=1.10) == "SELLER"
    assert classify_option_trade(price=1.05, bid=1.00, ask=1.10) == "AMBIGUOUS"
    with pytest.raises(ContractValidationError, match="NBBO"):
        classify_option_trade(price=1.0, bid=1.0, ask=1.0)


def test_data_gate_blocks_without_certified_pit_assets(tmp_path):
    # Build the minimum repository-shaped fixture with a verified frozen spec.
    lane = LANES[0]
    source = REPO_ROOT / lane.spec_path
    target = tmp_path / lane.spec_path
    target.parent.mkdir(parents=True)
    target.write_bytes(source.read_bytes())
    factory_source = REPO_ROOT / "projects/alpha_lab/factory"
    experiment_source = REPO_ROOT / "projects/alpha_lab/experiments"
    for source_dir in (factory_source, experiment_source):
        target_dir = tmp_path / source_dir.relative_to(REPO_ROOT)
        target_dir.mkdir(parents=True)
        for path in source_dir.glob("*.py"):
            (target_dir / path.name).write_bytes(path.read_bytes())

    payload = run_lane(
        repo_root=tmp_path,
        hypothesis_id=lane.hypothesis_id,
        run_id="test-data-gate",
        checked_at=datetime(2026, 7, 15, 1, 0, tzinfo=timezone.utc),
    )
    result = payload["result"]
    assert result["outcome"] == "BLOCKED_DATA"
    assert result["classification"] == "UNPROVEN"
    assert result["holdout_accessed"] is False
    assert result["returns_accessed"] is False
    assert result["alpha_claim_permitted"] is False
    assert result["return_variants_attempted"] == 0
    assert result["data_gate_attempts_including_current"] == 1
    assert (Path(payload["run_dir"]) / "events.jsonl").is_file()
    evaluator_input = json.loads(
        (Path(payload["run_dir"]) / "evaluator_input.json").read_text(
            encoding="utf-8"
        )
    )
    assert evaluator_input["repo_root"] == str(tmp_path)


def test_data_gate_rejects_path_traversal_before_writing(tmp_path):
    lane = LANES[0]
    with pytest.raises(ValueError, match="path-safe"):
        run_lane(
            repo_root=tmp_path,
            hypothesis_id=lane.hypothesis_id,
            run_id="../../paper",
            checked_at=datetime(2026, 7, 15, 1, 0, tzinfo=timezone.utc),
        )
    assert not (tmp_path / "paper").exists()


def test_provider_certification_is_bound_to_file_hash_and_physical_schema(tmp_path):
    asset = DataAsset(
        asset_id="sample_v1",
        provider_id="sample.provider",
        dataset_id="sample.dataset",
        patterns=("inputs/sample.csv",),
        required_fields=("security_id", "available_at"),
    )
    data_path = tmp_path / "inputs/sample.csv"
    data_path.parent.mkdir(parents=True)
    data_path.write_text("security_id,available_at\nABC,2026-01-01T00:00:00Z\n", encoding="utf-8")
    file_record = {
        "path": "inputs/sample.csv",
        "bytes": data_path.stat().st_size,
        "sha256": hashlib.sha256(data_path.read_bytes()).hexdigest(),
    }
    certification = {
        "provider_id": asset.provider_id,
        "dataset_id": asset.dataset_id,
        "status": "READY",
        "historical_point_in_time_verified": True,
        "schema_validation_status": "PASS",
        "data_files": [file_record],
        "schema_manifest": [
            {
                "logical_field": "security_id",
                "source_path": "inputs/sample.csv",
                "physical_field": "security_id",
                "data_type": "string",
            },
            {
                "logical_field": "available_at",
                "source_path": "inputs/sample.csv",
                "physical_field": "available_at",
                "data_type": "timestamp",
            },
        ],
        "blockers": [],
    }
    certification["evidence_hash"] = canonical_hash(certification)
    certification_path = tmp_path / asset.certification_path
    certification_path.parent.mkdir(parents=True)
    import json

    certification_path.write_text(json.dumps(certification), encoding="utf-8")
    result = inspect_asset(
        tmp_path,
        asset,
        datetime(2026, 7, 15, 1, 0, tzinfo=timezone.utc),
    )
    assert result["gate"]["ready"] is True

    certification["schema_manifest"][1]["physical_field"] = "invented_timestamp"
    certification["evidence_hash"] = canonical_hash(
        {key: value for key, value in certification.items() if key != "evidence_hash"}
    )
    certification_path.write_text(json.dumps(certification), encoding="utf-8")
    result = inspect_asset(
        tmp_path,
        asset,
        datetime(2026, 7, 15, 1, 0, tzinfo=timezone.utc),
    )
    assert result["gate"]["ready"] is False
    assert any("physical_field_absent" in item for item in result["gate"]["blockers"])


def test_provider_gate_keeps_old_bundles_but_reads_certified_current_file(tmp_path):
    import json

    asset = DataAsset(
        asset_id="immutable_bundle_v1",
        provider_id="sample.provider",
        dataset_id="sample.dataset",
        patterns=("bundles/*/data/events.csv",),
        required_fields=("security_id", "available_at"),
    )
    old = tmp_path / "bundles/old/data/events.csv"
    current = tmp_path / "bundles/current/data/events.csv"
    old.parent.mkdir(parents=True)
    current.parent.mkdir(parents=True)
    old.write_text(
        "security_id,available_at\nOLD,2025-01-01T00:00:00Z\n",
        encoding="utf-8",
    )
    current.write_text(
        "security_id,available_at\nNEW,2026-01-01T00:00:00Z\n",
        encoding="utf-8",
    )
    record = {
        "path": "bundles/current/data/events.csv",
        "bytes": current.stat().st_size,
        "sha256": hashlib.sha256(current.read_bytes()).hexdigest(),
    }
    unsigned = {
        "provider_id": asset.provider_id,
        "dataset_id": asset.dataset_id,
        "status": "READY",
        "historical_point_in_time_verified": True,
        "schema_validation_status": "PASS",
        "data_files": [record],
        "schema_manifest": [
            {
                "logical_field": field,
                "source_path": record["path"],
                "physical_field": field,
                "data_type": "string",
            }
            for field in asset.required_fields
        ],
        "blockers": [],
    }
    certification_path = tmp_path / asset.certification_path
    certification_path.parent.mkdir(parents=True)
    certification_path.write_text(
        json.dumps({**unsigned, "evidence_hash": canonical_hash(unsigned)}),
        encoding="utf-8",
    )
    result = inspect_asset(
        tmp_path,
        asset,
        datetime(2026, 7, 23, 19, 0, tzinfo=timezone.utc),
    )
    assert result["gate"]["ready"] is True
    assert [record["path"] for record in result["files"]] == [
        "bundles/current/data/events.csv"
    ]
