from __future__ import annotations

import copy
import hashlib
import json

import pytest

from core.accounting_journal import ACCOUNTING_JOURNAL_ENTRY_SCHEMA, seal_journal_entry
from core.lane_oms import build_lane_oms_intents
from core.lane_execution_dry_run import build_lane_execution_safety_evidence
from core.lane_reconciliation import build_lane_reconciliation
from core.reconciled_fill_accounting import build_reconciled_fill_journal_entries
from core.scheduled_v2_factual_pipeline import (
    ScheduledV2FactualPipelineError,
    build_scheduled_v2_factual_pipeline,
    main,
    run_scheduled_v2_factual_pipeline,
    validate_scheduled_v2_factual_pipeline,
)
from Tests.test_exact_execution_plan_v4 import _plan
from Tests.test_lane_reconciliation import _full_evidence
from Tests.test_sleeve_decision_adapter import (
    EXPECTED,
    _evaluation_batch,
    _profiles,
)


def _posting(identity: str, account: str, debit: float, credit: float, sleeve: str) -> dict:
    return {
        "posting_id": identity,
        "ledger_account": account,
        "sleeve_id": sleeve,
        "currency": "USD",
        "debit_amount": debit,
        "credit_amount": credit,
    }


def _lane_input(lane_kind: str, lane_id: str) -> dict:
    plan, _, _, _ = _plan(lane_kind=lane_kind, lane_id=lane_id)
    orders, fills, ending = _full_evidence(plan)
    reconciliation = build_lane_reconciliation(
        exact_plan=plan,
        wal_intents=build_lane_oms_intents(plan),
        broker_orders=orders,
        broker_fills=fills,
        ending_state=ending,
        reconciled_at="2026-08-18T11:12:00+00:00",
    )
    source_hash = hashlib.sha256(
        f"opening-state:{lane_kind}:{lane_id}".encode("utf-8")
    ).hexdigest()
    sleeve_id = "sleeve_beta"
    scope = {
        "trade_date": plan["trade_date"],
        "account_id_hash": plan["account_id_hash"],
        "lane_id": plan["lane_id"],
        "lane_kind": plan["lane_kind"],
        "deployment_version": plan["deployment_version"],
        "sleeve_id": sleeve_id,
        "counterparty_sleeve_id": None,
        "attribution_status": "ATTRIBUTED",
        "performance_surface": f"FACTUAL_{lane_kind}",
        "economic_authority": "BROKER_RECONCILED",
    }
    opening_cash = seal_journal_entry(
        {
            "schema_version": ACCOUNTING_JOURNAL_ENTRY_SCHEMA,
            "journal_entry_id": f"opening-cash:{lane_id}",
            "event_type": "OPENING_CAPITAL",
            "event_time": "2026-08-18T11:00:00+00:00",
            **scope,
            "symbol": None,
            "quantity": 0.0,
            "price": 0.0,
            "gross_amount": plan["starting_cash"],
            "fee_amount": 0.0,
            "net_amount": plan["starting_cash"],
            "session_id": None,
            "decision_id": None,
            "allocation_id": None,
            "plan_id": None,
            "broker_order_id": None,
            "fill_id": None,
            "source_hash": source_hash,
            "postings": [
                _posting(
                    f"opening-cash-asset:{lane_id}",
                    "ASSET:CASH",
                    plan["starting_cash"],
                    0.0,
                    sleeve_id,
                ),
                _posting(
                    f"opening-cash-equity:{lane_id}",
                    "EQUITY:OPENING_CAPITAL",
                    0.0,
                    plan["starting_cash"],
                    sleeve_id,
                ),
            ],
        }
    )
    starting = plan["starting_positions"][0]
    opening_position = seal_journal_entry(
        {
            "schema_version": ACCOUNTING_JOURNAL_ENTRY_SCHEMA,
            "journal_entry_id": f"opening-position:{lane_id}",
            "event_type": "CORPORATE_ACTION",
            "event_time": "2026-08-18T11:00:01+00:00",
            **scope,
            "symbol": starting["symbol"],
            "quantity": starting["quantity"],
            "price": 0.0,
            "gross_amount": 0.0,
            "fee_amount": 0.0,
            "net_amount": 0.0,
            "session_id": None,
            "decision_id": None,
            "allocation_id": None,
            "plan_id": None,
            "broker_order_id": None,
            "fill_id": None,
            "source_hash": source_hash,
            "postings": [
                _posting(
                    f"opening-position-qty:{lane_id}",
                    "MEMO:SECURITY_QUANTITY",
                    0.0,
                    0.0,
                    sleeve_id,
                ),
                _posting(
                    f"opening-position-action:{lane_id}",
                    "MEMO:CORPORATE_ACTION",
                    0.0,
                    0.0,
                    sleeve_id,
                ),
            ],
        }
    )
    journal = [
        opening_cash,
        opening_position,
        *build_reconciled_fill_journal_entries(reconciliation, exact_plan=plan),
    ]
    return {
        "exact_plan": plan,
        "safety_evidence": build_lane_execution_safety_evidence(
            exact_plan=plan,
            checked_at="2026-08-18T11:08:30+00:00",
            source_hashes=[hashlib.sha256(f"preflight:{lane_id}".encode()).hexdigest()],
        ),
        "reconciliation": reconciliation,
        "ending_state": ending,
        "journal_entries": journal,
        "prior_valuations": (),
        "valuation_date": "2026-08-18",
    }


def _inputs() -> dict:
    lanes = [
        _lane_input("PAPER", "paper"),
        _lane_input("LIVE", "live-small"),
    ]
    plan = lanes[0]["exact_plan"]
    return {
        "evaluation_batch": _evaluation_batch(),
        "expected_sleeve_ids": EXPECTED,
        "session_id": plan["session_id"],
        "session_hash": plan["session_hash"],
        "generated_at": "2026-08-18T20:01:00+00:00",
        "decision_inputs": _profiles(),
        "lane_inputs": lanes,
    }


def test_default_is_repeatable_paper_live_no_write_rehearsal(tmp_path) -> None:
    inputs = _inputs()
    first_receipt = run_scheduled_v2_factual_pipeline(output_root=tmp_path, **inputs)
    second_receipt = run_scheduled_v2_factual_pipeline(output_root=tmp_path, **inputs)
    first = first_receipt["artifact"]
    second = second_receipt["artifact"]

    assert first == second
    assert first["status"] == "DISABLED_NO_WRITE_REHEARSAL"
    assert first["paper_live_rehearsal"] is True
    assert first["write_performed"] is False
    assert first["broker_call_performed"] is False
    assert first["broker_submission_allowed"] is False
    assert first["execution_authority"] is False
    assert first["activation_authority"] is False
    assert list(tmp_path.iterdir()) == []
    assert {row["lane_kind"] for row in first["factual_lanes"]} == {"PAPER", "LIVE"}
    assert all(row["accounting_input"]["accounting_ready"] for row in first["factual_lanes"])
    assert all(row["history_input"]["factual"] for row in first["factual_lanes"])
    assert all(
        row["history_input"]["history_write_authorized"] is False
        for row in first["factual_lanes"]
    )
    assert all(
        row["execution_evidence_classification"] == "STRUCTURAL_REHEARSAL"
        and row["execution_rehearsal"]["status"] == "VALIDATED_NO_WRITE"
        and row["execution_rehearsal"]["broker_submission_allowed"] is False
        and row["execution_rehearsal_hash"]
        == row["execution_rehearsal"]["content_hash"]
        for row in first["factual_lanes"]
    )


def test_validator_rebuilds_exact_inputs_and_rejects_tamper() -> None:
    inputs = _inputs()
    result = build_scheduled_v2_factual_pipeline(**inputs)
    assert validate_scheduled_v2_factual_pipeline(result, **inputs) == result

    tampered = copy.deepcopy(result)
    tampered["factual_lanes"][0]["history_input"]["factual"] = False
    with pytest.raises(ScheduledV2FactualPipelineError, match="differs"):
        validate_scheduled_v2_factual_pipeline(tampered, **inputs)


def test_persistence_requires_two_opt_ins_and_is_content_addressed(tmp_path) -> None:
    inputs = _inputs()
    requested = build_scheduled_v2_factual_pipeline(
        schedule_enabled=True, write_enabled=True, **inputs
    )
    assert requested["status"] == "PERSISTENCE_REQUESTED"
    assert requested["write_performed"] is False
    with pytest.raises(ScheduledV2FactualPipelineError, match="independent"):
        run_scheduled_v2_factual_pipeline(
            output_root=tmp_path, write_enabled=True, **inputs
        )
    assert list(tmp_path.iterdir()) == []

    first_receipt = run_scheduled_v2_factual_pipeline(
        output_root=tmp_path,
        schedule_enabled=True,
        write_enabled=True,
        **inputs,
    )
    second_receipt = run_scheduled_v2_factual_pipeline(
        output_root=tmp_path,
        schedule_enabled=True,
        write_enabled=True,
        **inputs,
    )
    first = first_receipt["artifact"]
    second = second_receipt["artifact"]
    assert first == second
    assert first_receipt["persistence"]["outcome"] == "CREATED"
    assert second_receipt["persistence"]["outcome"] == "ALREADY_EXISTS"
    artifacts = list(tmp_path.rglob("*.json"))
    assert len(artifacts) == 1
    assert artifacts[0].stem == first["content_hash"]
    assert first["status"] == "PERSISTED_VERIFIED"
    assert first["write_performed"] is True
    assert validate_scheduled_v2_factual_pipeline(first, **inputs) == first


def test_lane_coverage_and_literal_switches_fail_closed() -> None:
    inputs = _inputs()
    inputs["lane_inputs"] = inputs["lane_inputs"][:1]
    with pytest.raises(ScheduledV2FactualPipelineError, match="exactly cover"):
        build_scheduled_v2_factual_pipeline(**inputs)

    inputs = _inputs()
    with pytest.raises(ScheduledV2FactualPipelineError, match="literal boolean"):
        build_scheduled_v2_factual_pipeline(schedule_enabled=1, **inputs)


def test_resealed_decision_or_non_pass_reconciliation_cannot_enter_bundle() -> None:
    inputs = _inputs()
    inputs["decision_inputs"]["caerus_orion"]["target_rows"][0][
        "target_weight"
    ] = float("nan")
    with pytest.raises(Exception, match="canonical JSON"):
        build_scheduled_v2_factual_pipeline(**inputs)

    inputs = _inputs()
    reconciliation = inputs["lane_inputs"][0]["reconciliation"]
    reconciliation["status"] = "PARTIAL"
    with pytest.raises(Exception, match="reconciliation"):
        build_scheduled_v2_factual_pipeline(**inputs)


def test_cli_default_prints_validated_rehearsal_and_creates_no_output(tmp_path, capsys) -> None:
    input_path = tmp_path / "explicit-inputs.json"
    input_path.write_text(json.dumps(_inputs(), allow_nan=False), encoding="utf-8")

    assert main(["--input", str(input_path)]) == 0

    receipt = json.loads(capsys.readouterr().out)
    assert receipt["artifact"]["status"] == "DISABLED_NO_WRITE_REHEARSAL"
    assert receipt["persistence"]["outcome"] == "NOT_REQUESTED"
    assert sorted(path.name for path in tmp_path.iterdir()) == ["explicit-inputs.json"]


def test_cli_rejects_duplicate_keys_before_orchestration(tmp_path) -> None:
    input_path = tmp_path / "bad.json"
    input_path.write_text('{"evaluation_batch":{},"evaluation_batch":{}}', encoding="utf-8")
    with pytest.raises(ScheduledV2FactualPipelineError, match="duplicate JSON key"):
        main(["--input", str(input_path)])
