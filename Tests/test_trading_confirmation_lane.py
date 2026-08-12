"""Hermetic tests for lane/status truthfulness in the confirmation email.

Pins fixes for BLOCKER 5 reporting:
* mode derived from the invoking lane appears in SUBJECT and first body line;
* paper-lane results label PAPER, live-lane label LIVE_PILOT;
* broker-snapshot fallback is marked and never says EXECUTED with zero fills;
* FAILED_RECONCILIATION preserves the [HALTED] subject (existing good behavior);
* Python-exception rejection reasons are labelled internal-adapter vs broker.
"""

from __future__ import annotations

import json
from pathlib import Path

from scripts.live_pilot_execute import _derive_execution_mode
from scripts.send_trading_confirmation_email import (
    _build_confirmation_email,
    _build_results_from_broker_snapshot,
    _classify_reason,
    _record_confirmation_delivery,
)

RESULTS_PATH = Path("/tmp/execution_results.json")


def test_choice2_confirmation_delivery_never_rewrites_canonical_operator_truth(
    tmp_path: Path,
) -> None:
    run_root = tmp_path / "run"
    run_root.mkdir()
    operator = {
        "run_id": "choice2-run",
        "trade_date": "2026-08-12",
        "execution_source": "exact_execution_plan_v3",
        "terminal_status": "SUBMITTED",
        "terminal_outcome": "RECONCILED_SUCCESS",
        "submitted_count": 12,
        "filled_count": 12,
    }
    operator_path = run_root / "operator_summary.json"
    operator_path.write_text(
        json.dumps(operator, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    before = operator_path.read_bytes()

    _record_confirmation_delivery(
        run_root,
        operator_summary_fields={"submitted_count": 0, "terminal_status": "UNKNOWN"},
        confirmation_email_sent=True,
    )

    assert operator_path.read_bytes() == before
    delivery = json.loads(
        (run_root / "trading_confirmation_delivery.json").read_text(encoding="utf-8")
    )
    assert delivery["confirmation_email_sent"] is True
    assert delivery["run_id"] == "choice2-run"


def test_exact_filled_and_authorized_no_trade_have_unambiguous_email_status() -> None:
    filled = {
        "trade_date": "2026-08-12",
        "run_id": "exact-filled",
        "mode": "PAPER",
        "terminal_status": "SUBMITTED",
        "terminal_outcome": "RECONCILED_SUCCESS",
        "orders_submitted_count": 12,
        "orders_filled_count": 12,
    }
    no_trade = {
        "trade_date": "2026-08-12",
        "run_id": "exact-no-trade",
        "mode": "PAPER",
        "terminal_status": "AUTHORIZED_NO_TRADE",
        "terminal_outcome": "AUTHORIZED_NO_TRADE",
        "orders_submitted_count": 0,
    }

    filled_subject, filled_text, _ = _build_confirmation_email(filled, RESULTS_PATH)
    no_trade_subject, no_trade_text, _ = _build_confirmation_email(no_trade, RESULTS_PATH)

    assert "[RECONCILED_SUCCESS]" in filled_subject
    assert "Submitted: 12" in filled_text
    assert "[NO_ACTION]" in no_trade_subject
    assert "STATUS: NO_ACTION" in no_trade_text


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


def test_live_dry_run_is_classified_truthfully() -> None:
    results = {
        "run_id": "r_dry", "trade_date": "2026-07-14", "mode": "LIVE_PILOT",
        "status": "DRY_RUN", "operator_execution_status": "dry_run",
        "submitted_count": 0, "accepted_count": 0, "rejected_count": 0,
    }
    subject, body_text, body_html = _build_confirmation_email(results, RESULTS_PATH)
    assert subject == "[LIVE_PILOT] Trading Confirmation 2026-07-14 [DRY_RUN]"
    assert "STATUS: DRY_RUN" in body_text
    assert "DRY_RUN" in body_html


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
