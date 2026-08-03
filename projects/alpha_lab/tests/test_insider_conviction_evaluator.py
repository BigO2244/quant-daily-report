from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from projects.alpha_lab.control_plane.evaluator import EvaluationPhase, load_spec, run_evaluator
from projects.alpha_lab.evaluators.insider_conviction import (
    Event,
    _assert_ready_packet,
    build_events,
    evaluate,
    score_clusters,
)
from projects.alpha_lab.factory.errors import ContractValidationError
from projects.alpha_lab.factory.canonical import canonical_hash


ROOT = Path(__file__).parents[3]
ASSETS = (
    "pit_security_master_v1",
    "pit_membership_v1",
    "pit_prices_liquidity_v1",
    "pit_characteristics_v1",
    "factor_panel_v1",
    "sector_returns_v1",
    "cik_identity_input_v1",
    "form4_event_tape_v1",
)


def _purchase(
    *,
    issuer: str,
    security: str,
    owner: str,
    accepted: str,
    control: str,
    title: str = "Director",
) -> dict:
    available = datetime.fromisoformat(accepted.replace("Z", "+00:00")) + timedelta(hours=14)
    return {
        "security_id": security,
        "issuer_cik": issuer,
        "owner_cik": owner,
        "independent_person_id": "person:{}".format(owner),
        "accession_number": "{}-{}".format(issuer, owner),
        "acceptance_datetime_utc": accepted,
        "available_at": available.isoformat(),
        "transaction_code": "P",
        "acquired_disposed_code": "A",
        "transaction_shares": 100,
        "transaction_price": 10,
        "transaction_value": 1000,
        "is_derivative": False,
        "is_director": title == "Director",
        "is_officer": title != "Director",
        "is_ten_percent_owner": False,
        "officer_title": title,
        "is_natural_person": True,
        "control_group_id": control,
        "is_10b5_1": False,
        "parse_status": "PASS_ORIGINAL_XML",
        "amendment_lineage": "ORIGINAL_CAUSALLY_SUPERSESSION_RESOLVED",
        "frozen_role_classification": (
            "DIRECTOR_OR_10_PERCENT_OWNER" if title == "Director" else "OTHER_OFFICER"
        ),
    }


def _ready_packet(tmp_path: Path) -> dict:
    assets = {}
    for asset_id in ASSETS:
        path = tmp_path / "inputs" / "{}.csv".format(asset_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("placeholder\nvalue\n", encoding="utf-8")
        gate = {
            "ready": True,
            "blockers": [],
            "requirement_hash": "b" * 64,
            "readiness_hash": "c" * 64,
        }
        assets[asset_id] = {
            "files": [
                {
                    "path": str(path.relative_to(tmp_path)),
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }
            ],
            "gate": gate,
            "gate_hash": canonical_hash(gate),
            "prechallenge_extract": True,
            "maximum_observation_date": "2024-12-31",
        }
    assets["form4_event_tape_v1"]["causal_amendment_lineage_certified"] = True
    assets["form4_event_tape_v1"]["beneficial_owner_independence_certified"] = True
    return {
        "repo_root": str(tmp_path),
        "hypothesis_id": "HYP-2026-003",
        "data_gate_status": "READY_FOR_FROZEN_EVALUATOR",
        "assets": assets,
    }


def test_build_events_forms_cluster_only_from_certified_independent_people():
    rows = [
        _purchase(
            issuer="1", security="S1", owner="11", accepted="2020-01-01T20:00:00Z", control="A"
        ),
        _purchase(
            issuer="1", security="S1", owner="12", accepted="2020-01-05T20:00:00Z", control="B"
        ),
        _purchase(
            issuer="2", security="S2", owner="21", accepted="2020-01-01T20:00:00Z", control="C"
        ),
    ]
    clusters, singles = build_events(rows)
    assert len(clusters) == 1
    assert clusters[0].owner_ids == ("person:11", "person:12")
    assert [event.security_id for event in singles] == ["S1", "S1", "S2"]


def test_build_events_ignores_uncertified_legacy_control_group_field():
    rows = [
        _purchase(
            issuer="1", security="S1", owner="11", accepted="2020-01-01T20:00:00Z", control="JOINT"
        ),
        _purchase(
            issuer="1", security="S1", owner="12", accepted="2020-01-02T20:00:00Z", control="JOINT"
        ),
    ]
    clusters, singles = build_events(rows)
    assert len(clusters) == 1
    assert len(singles) == 2


def test_certified_independent_person_id_prevents_controlled_vehicle_cluster():
    first = _purchase(
        issuer="1", security="S1", owner="11", accepted="2020-01-01T20:00:00Z", control="A"
    )
    second = _purchase(
        issuer="1", security="S1", owner="12", accepted="2020-01-02T20:00:00Z", control="B"
    )
    first["independent_person_id"] = "controlled-person"
    second["independent_person_id"] = "controlled-person"
    clusters, singles = build_events([first, second])
    assert clusters == []
    assert len(singles) == 2


def test_build_events_aggregates_same_owner_transactions_without_extra_buyer():
    first = _purchase(
        issuer="1", security="S1", owner="11", accepted="2020-01-01T20:00:00Z", control="A"
    )
    second_lot = dict(first)
    second_lot["transaction_value"] = 2500
    third = _purchase(
        issuer="1", security="S1", owner="12", accepted="2020-01-03T20:00:00Z", control="B"
    )
    clusters, singles = build_events([first, second_lot, third])
    assert len(clusters) == 1
    assert len(clusters[0].owner_ids) == 2
    assert clusters[0].purchase_dollars == 4500
    assert len(singles) == 2


def test_build_events_rejects_noncausal_availability_without_guessing_10b5_1():
    invalid = _purchase(
        issuer="1", security="S1", owner="11", accepted="2020-01-02T20:00:00Z", control="A"
    )
    invalid["available_at"] = "2020-01-01T20:00:00+00:00"
    with pytest.raises(ContractValidationError, match="precedes SEC acceptance"):
        build_events([invalid])
    planned = _purchase(
        issuer="1", security="S1", owner="11", accepted="2020-01-02T20:00:00Z", control="A"
    )
    planned["is_10b5_1"] = True
    clusters, singles = build_events([planned])
    assert clusters == []
    assert len(singles) == 1


def test_expanding_cluster_scores_do_not_use_future_events():
    first = Event(
        "one", "S1", "1", datetime(2020, 1, 1, tzinfo=timezone.utc),
        datetime(2020, 1, 2, tzinfo=timezone.utc), ("1", "2"), 1000.0, 0.25, "CLUSTER"
    )
    future = Event(
        "two", "S2", "2", datetime(2020, 2, 1, tzinfo=timezone.utc),
        datetime(2020, 2, 2, tzinfo=timezone.utc), ("3", "4", "5"), 9000.0, 1.0, "CLUSTER"
    )
    first_alone = score_clusters([first], {"one": 100_000.0})[0].score
    first_with_future = score_clusters(
        [future, first], {"one": 100_000.0, "two": 100_000.0}
    )[0].score
    assert first_alone == first_with_future


def test_evaluator_refuses_unready_or_noncausal_form4_data(tmp_path):
    packet = _ready_packet(tmp_path)
    packet["data_gate_status"] = "BLOCKED_DATA"
    with pytest.raises(ContractValidationError, match="ready certified data gate"):
        evaluate(packet)

    packet = _ready_packet(tmp_path)
    del packet["assets"]["form4_event_tape_v1"]["causal_amendment_lineage_certified"]
    with pytest.raises(ContractValidationError, match="causally certified"):
        _assert_ready_packet(packet)

    packet = _ready_packet(tmp_path)
    del packet["assets"]["form4_event_tape_v1"]["beneficial_owner_independence_certified"]
    with pytest.raises(ContractValidationError, match="beneficial-owner independence"):
        _assert_ready_packet(packet)


def test_evaluator_verifies_file_hash_and_prechallenge_boundary(tmp_path):
    packet = _ready_packet(tmp_path)
    packet["assets"]["pit_membership_v1"]["maximum_observation_date"] = "2025-01-01"
    with pytest.raises(ContractValidationError, match="locked challenge"):
        _assert_ready_packet(packet)

    packet = _ready_packet(tmp_path)
    packet["assets"]["pit_membership_v1"]["files"][0]["sha256"] = "0" * 64
    with pytest.raises(ContractValidationError, match="certified SHA-256"):
        _assert_ready_packet(packet)

    packet = _ready_packet(tmp_path)
    packet["assets"]["pit_membership_v1"]["gate"]["readiness_hash"] = "d" * 64
    with pytest.raises(ContractValidationError, match="gate_hash mismatch"):
        _assert_ready_packet(packet)


def test_evaluator_defaults_to_discovery_and_refuses_challenge(tmp_path, monkeypatch):
    packet = _ready_packet(tmp_path)
    monkeypatch.setattr(
        "projects.alpha_lab.evaluators.insider_conviction._records",
        lambda *_: [],
    )
    first = evaluate(packet)
    second = evaluate(packet, phase="DISCOVERY")
    assert first == second
    assert first["variant_count"] == 1
    assert first["challenge_period_accessed"] is False
    assert first["alpha_claim_permitted"] is False
    assert first["lifecycle_classification"] == "UNPROVEN"
    with pytest.raises(ContractValidationError, match="challenge access"):
        evaluate(packet, phase="CHALLENGE")


def test_frozen_spec_runs_through_generic_boundary_without_orders(tmp_path, monkeypatch):
    packet = _ready_packet(tmp_path)
    monkeypatch.setattr(
        "projects.alpha_lab.evaluators.insider_conviction._records",
        lambda *_: [],
    )
    spec = load_spec(ROOT / "projects/alpha_lab/experiments/evaluator_specs/HYP-2026-003.json")
    result = run_evaluator(
        spec=spec,
        input_packet=packet,
        phase=EvaluationPhase.DISCOVERY,
        challenge_access_authorized=False,
    )
    assert result["result"]["orders_submitted"] is False
    assert result["result"]["variant_count"] == 1
    assert result["promotion_performed"] is False
    assert result["trading_behavior_changed"] is False
