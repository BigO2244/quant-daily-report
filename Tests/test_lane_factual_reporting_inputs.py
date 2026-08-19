from __future__ import annotations

import copy
import hashlib

import pytest

from core.accounting_journal import ACCOUNTING_JOURNAL_ENTRY_SCHEMA, seal_journal_entry
from core.lane_factual_reporting_inputs import (
    LaneFactualReportingInputsError,
    build_lane_factual_reporting_inputs,
    run_lane_factual_reporting_inputs_dry_run,
)
from core.lane_oms import build_lane_oms_intents
from core.lane_reconciliation import build_lane_reconciliation
from core.reconciled_fill_accounting import build_reconciled_fill_journal_entries
from Tests.test_lane_reconciliation import _full_evidence, _plan


def _posting(identity: str, account: str, debit: float, credit: float, sleeve: str) -> dict:
    return {
        "posting_id": identity, "ledger_account": account, "sleeve_id": sleeve,
        "currency": "USD", "debit_amount": debit, "credit_amount": credit,
    }


def _inputs() -> tuple[dict, dict, dict, list[dict]]:
    plan, _, _, _ = _plan()
    orders, fills, ending = _full_evidence(plan)
    recon = build_lane_reconciliation(
        exact_plan=plan,
        wal_intents=build_lane_oms_intents(plan),
        broker_orders=orders,
        broker_fills=fills,
        ending_state=ending,
        reconciled_at="2026-08-18T11:12:00+00:00",
    )
    source = hashlib.sha256(b"opening-state").hexdigest()
    scope = {
        "trade_date": plan["trade_date"], "account_id_hash": plan["account_id_hash"],
        "lane_id": plan["lane_id"], "lane_kind": plan["lane_kind"],
        "deployment_version": plan["deployment_version"], "sleeve_id": "sleeve_beta",
        "counterparty_sleeve_id": None, "attribution_status": "ATTRIBUTED",
        "performance_surface": "FACTUAL_PAPER", "economic_authority": "BROKER_RECONCILED",
    }
    opening_cash = seal_journal_entry(
        {
            "schema_version": ACCOUNTING_JOURNAL_ENTRY_SCHEMA,
            "journal_entry_id": "opening-cash", "event_type": "OPENING_CAPITAL",
            "event_time": "2026-08-18T11:00:00+00:00", **scope, "symbol": None,
            "quantity": 0.0, "price": 0.0, "gross_amount": plan["starting_cash"],
            "fee_amount": 0.0, "net_amount": plan["starting_cash"],
            "session_id": None, "decision_id": None, "allocation_id": None,
            "plan_id": None, "broker_order_id": None, "fill_id": None,
            "source_hash": source,
            "postings": [
                _posting("opening-cash-asset", "ASSET:CASH", plan["starting_cash"], 0.0, "sleeve_beta"),
                _posting("opening-cash-equity", "EQUITY:OPENING_CAPITAL", 0.0, plan["starting_cash"], "sleeve_beta"),
            ],
        }
    )
    starting = plan["starting_positions"][0]
    opening_position = seal_journal_entry(
        {
            "schema_version": ACCOUNTING_JOURNAL_ENTRY_SCHEMA,
            "journal_entry_id": "opening-position", "event_type": "CORPORATE_ACTION",
            "event_time": "2026-08-18T11:00:01+00:00", **scope,
            "symbol": starting["symbol"], "quantity": starting["quantity"], "price": 0.0,
            "gross_amount": 0.0, "fee_amount": 0.0, "net_amount": 0.0,
            "session_id": None, "decision_id": None, "allocation_id": None,
            "plan_id": None, "broker_order_id": None, "fill_id": None,
            "source_hash": source,
            "postings": [
                _posting("opening-position-qty", "MEMO:SECURITY_QUANTITY", 0.0, 0.0, "sleeve_beta"),
                _posting("opening-position-action", "MEMO:CORPORATE_ACTION", 0.0, 0.0, "sleeve_beta"),
            ],
        }
    )
    journal = [
        opening_cash,
        opening_position,
        *build_reconciled_fill_journal_entries(recon, exact_plan=plan),
    ]
    return plan, recon, ending, journal


def test_builds_factual_valuation_performance_and_green_truth_inputs() -> None:
    plan, recon, ending, journal = _inputs()
    built = build_lane_factual_reporting_inputs(
        exact_plan=plan, reconciliation=recon, ending_state=ending,
        journal_entries=journal, valuation_date="2026-08-18",
    )

    assert built["valuation"]["lane_nav"] == pytest.approx(ending["equity"])
    assert built["performance"]["factual"] is True
    assert built["journal_status"]["status"] == "PASS"
    assert built["reconciliation_status"]["status"] == "PASS"
    assert built["write_enabled"] is False
    assert built["broker_call_performed"] is False


def test_default_dry_run_creates_nothing_and_explicit_write_is_idempotent(tmp_path) -> None:
    plan, recon, ending, journal = _inputs()
    kwargs = {
        "exact_plan": plan, "reconciliation": recon, "ending_state": ending,
        "journal_entries": journal, "valuation_date": "2026-08-18",
    }
    result = run_lane_factual_reporting_inputs_dry_run(
        output_root=tmp_path, **kwargs
    )
    assert result["write_enabled"] is False
    assert list(tmp_path.iterdir()) == []

    first = run_lane_factual_reporting_inputs_dry_run(
        output_root=tmp_path, write_enabled=True, **kwargs
    )
    second = run_lane_factual_reporting_inputs_dry_run(
        output_root=tmp_path, write_enabled=True, **kwargs
    )
    assert first == second
    assert first["write_enabled"] is True
    assert len(list(tmp_path.rglob("*.json"))) == 1


def test_partial_or_mismatched_ending_state_cannot_publish_factual_inputs() -> None:
    plan, recon, ending, journal = _inputs()
    bad_recon = copy.deepcopy(recon)
    bad_recon["status"] = "PARTIAL"
    from core.lane_reconciliation import lane_reconciliation_content_hash

    bad_recon["content_hash"] = lane_reconciliation_content_hash(bad_recon)
    with pytest.raises(LaneFactualReportingInputsError, match="reconciliation evidence is invalid|accounting-ready PASS"):
        build_lane_factual_reporting_inputs(
            exact_plan=plan, reconciliation=bad_recon, ending_state=ending,
            journal_entries=journal, valuation_date="2026-08-18",
        )

    bad_state = copy.deepcopy(ending)
    bad_state["cash"] += 1.0
    from core.lane_reconciliation import evidence_content_hash

    bad_state["content_hash"] = evidence_content_hash(bad_state)
    with pytest.raises(LaneFactualReportingInputsError, match="reconciliation evidence is invalid|does not bind"):
        build_lane_factual_reporting_inputs(
            exact_plan=plan, reconciliation=recon, ending_state=bad_state,
            journal_entries=journal, valuation_date="2026-08-18",
        )
