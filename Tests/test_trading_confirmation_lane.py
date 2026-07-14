"""Hermetic tests for lane/status truthfulness in the confirmation email.

Pins fixes for BLOCKER 5 reporting:
* mode derived from the invoking lane appears in SUBJECT and first body line;
* paper-lane results label PAPER, live-lane label LIVE_PILOT;
* broker-snapshot fallback is marked and never says EXECUTED with zero fills;
* FAILED_RECONCILIATION preserves the [HALTED] subject (existing good behavior);
* Python-exception rejection reasons are labelled internal-adapter vs broker.
"""

from __future__ import annotations

from pathlib import Path

from scripts.live_pilot_execute import _derive_execution_mode
from scripts.send_trading_confirmation_email import (
    _build_confirmation_email,
    _build_results_from_broker_snapshot,
    _classify_reason,
)

RESULTS_PATH = Path("/tmp/execution_results.json")


# --- mode derivation from the invoking lane -------------------------------- #

def test_mode_derived_paper_from_output_root() -> None:
    assert _derive_execution_mode(
        Path("outputs/paper_lane/runs/2026-07-13T093604_paper_cron_submit")
    ) == "PAPER"


def test_mode_derived_live_from_output_root() -> None:
    assert _derive_execution_mode(
        Path("outputs/live_pilot/runs/2026-07-10T100930_live_pilot_cron_submit")
    ) == "LIVE_PILOT"


def test_mode_derivation_defaults_to_live_when_ambiguous() -> None:
    # Fail-safe: unknown lane is labelled as the higher-consequence live lane so
    # it can never silently masquerade as paper.
    assert _derive_execution_mode(Path("outputs/somewhere/runs/x")) == "LIVE_PILOT"


# --- subject / body lane labelling ----------------------------------------- #

def test_paper_lane_labeled_paper_in_subject_and_body() -> None:
    results = {
        "run_id": "r_paper", "trade_date": "2026-07-13", "mode": "PAPER",
        "status": "SUBMITTED", "submitted_count": 9, "accepted_count": 9,
        "rejected_count": 0, "orders_filled_count": 9,
    }
    subject, body_text, body_html = _build_confirmation_email(results, RESULTS_PATH)
    assert subject.startswith("[PAPER]")
    assert "EXECUTED" in subject
    assert "LANE: PAPER" in body_text  # first body line
    assert "Mode: PAPER" in body_text
    assert "[PAPER]" in body_html


def test_live_lane_labeled_live_pilot_in_subject_and_body() -> None:
    results = {
        "run_id": "r_live", "trade_date": "2026-07-13", "mode": "LIVE_PILOT",
        "status": "SUBMITTED", "submitted_count": 7, "accepted_count": 7,
        "rejected_count": 0, "orders_filled_count": 7,
    }
    subject, body_text, body_html = _build_confirmation_email(results, RESULTS_PATH)
    assert subject.startswith("[LIVE_PILOT]")
    assert "LANE: LIVE_PILOT" in body_text
    assert "Mode: LIVE_PILOT" in body_text


def test_failed_reconciliation_preserves_halted_subject() -> None:
    results = {
        "run_id": "r_live", "trade_date": "2026-07-10", "mode": "LIVE_PILOT",
        "status": "FAILED_RECONCILIATION", "halt_reason": "FAILED_RECONCILIATION",
        "submitted_count": 3, "accepted_count": 1, "rejected_count": 2,
    }
    subject, _body_text, body_html = _build_confirmation_email(results, RESULTS_PATH)
    assert subject.startswith("[LIVE_PILOT]")
    assert "[HALTED]" in subject
    assert "🛑" in body_html


# --- broker-snapshot fallback truthfulness --------------------------------- #

def test_fallback_open_orders_zero_fills_never_executed() -> None:
    snapshot = {
        "counts": {"orders_report_date": 3, "fills_report_date": 0},
        "orders_report_date": [{"status": "new"}, {"status": "accepted"}, {"status": "new"}],
        "fills_report_date": [],
    }
    results = _build_results_from_broker_snapshot("2026-07-13", snapshot, {"run_id": "snapX"})
    assert results["broker_snapshot_fallback"] is True
    assert results["operator_execution_status"] == "open"
    assert results["status"] != "EXECUTED"  # open/new != accepted-filled

    subject, body_text, _ = _build_confirmation_email(results, RESULTS_PATH)
    assert "EXECUTED" not in subject
    assert "OPEN" in subject
    assert "derived from broker snapshot" in body_text  # fallback marked in body


def test_fallback_with_real_fills_is_executed() -> None:
    snapshot = {
        "counts": {"orders_report_date": 2, "fills_report_date": 2},
        "orders_report_date": [{"status": "filled"}, {"status": "filled"}],
        "fills_report_date": [{}, {}],
    }
    results = _build_results_from_broker_snapshot("2026-07-13", snapshot, {})
    assert results["status"] == "EXECUTED"
    assert results["operator_execution_status"] == "executed"


def test_fallback_all_rejected_is_halted() -> None:
    snapshot = {
        "counts": {"orders_report_date": 2, "fills_report_date": 0},
        "orders_report_date": [{"status": "rejected"}, {"status": "rejected"}],
        "fills_report_date": [],
    }
    results = _build_results_from_broker_snapshot("2026-07-13", snapshot, {})
    assert results["status"] == "HALTED"
    assert results["operator_execution_status"] == "halted"


def test_execution_payload_fallback_marked_in_body() -> None:
    results = {
        "run_id": "r", "trade_date": "2026-07-13", "mode": "PAPER",
        "status": "HALTED", "halt_reason": "some_reason", "submitted_count": 0,
        "execution_payload_fallback": True,
    }
    _subject, body_text, _ = _build_confirmation_email(results, RESULTS_PATH)
    assert "derived from execution payload" in body_text


# --- rejection reason classification --------------------------------------- #

def test_classify_internal_adapter_exception() -> None:
    labelled = _classify_reason("could not convert string to float: 'abc'")
    assert labelled.startswith("internal adapter error")
    assert not labelled.startswith("broker decline")


def test_classify_broker_decline() -> None:
    labelled = _classify_reason("insufficient buying power")
    assert labelled.startswith("broker decline")


def test_classify_empty_reason_passthrough() -> None:
    assert _classify_reason("") == ""
    assert _classify_reason(None) == ""
