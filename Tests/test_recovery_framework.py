from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from core.recovery.interrupted_state import (
    BrokerState,
    ExecutionLifecycleState,
    IntendedOrder,
    InterruptedRunSnapshot,
    OrderState,
)
from core.recovery.recovery_classifier import classify_interrupted_run
from core.recovery.recovery_delta import compute_recovery_delta, target_positions_from_intent
from core.recovery.recovery_validator import (
    assert_prior_sells_terminal_filled,
    validate_recovery_candidate,
)


def _intended_orders() -> list[IntendedOrder]:
    return [
        IntendedOrder("ELV", "SELL", 1, 393.28, "rebalance_to_target"),
        IntendedOrder("DELL", "SELL", 1, 239.80, "removed_from_targets"),
        IntendedOrder("GILD", "SELL", 6, 778.94, "removed_from_targets"),
        IntendedOrder("PFE", "SELL", 14, 359.59, "removed_from_targets"),
        IntendedOrder("SBUX", "SELL", 6, 639.31, "removed_from_targets"),
        IntendedOrder("MRK", "BUY", 7, 799.29, "rebalance_to_target"),
        IntendedOrder("UNH", "BUY", 1, 395.28, "rebalance_to_target"),
        IntendedOrder("PANW", "BUY", 2, 479.00, "rebalance_to_target"),
        IntendedOrder("CSCO", "BUY", 3, 357.35, "rebalance_to_target"),
        IntendedOrder("MNST", "BUY", 4, 350.51, "rebalance_to_target"),
        IntendedOrder("NEM", "BUY", 2, 225.02, "rebalance_to_target"),
    ]


def _pretrade_positions() -> dict[str, float]:
    return {
        "CVS": 5,
        "DELL": 1,
        "ELV": 3,
        "FTNT": 5,
        "GILD": 6,
        "GM": 11,
        "GOOG": 1,
        "GOOGL": 1,
        "PFE": 14,
        "PM": 2,
        "PWR": 1,
        "QCOM": 3,
        "SBUX": 6,
        "STX": 1,
        "VZ": 10,
    }


def _post_sell_positions() -> dict[str, float]:
    return {
        "CVS": 5,
        "ELV": 2,
        "FTNT": 5,
        "GM": 11,
        "GOOG": 1,
        "GOOGL": 1,
        "PM": 2,
        "PWR": 1,
        "QCOM": 3,
        "STX": 1,
        "VZ": 10,
    }


def _terminal_sell_orders() -> list[OrderState]:
    return [
        OrderState("2026-05-18:main:growth_engine_v4:ELV:SELL", "ELV", "sell", 1, 1, "filled"),
        OrderState("2026-05-18:main:growth_engine_v4:DELL:SELL", "DELL", "sell", 1, 1, "filled"),
        OrderState("2026-05-18:main:growth_engine_v4:GILD:SELL", "GILD", "sell", 6, 6, "filled"),
        OrderState("2026-05-18:main:growth_engine_v4:PFE:SELL", "PFE", "sell", 14, 14, "filled"),
        OrderState("2026-05-18:main:growth_engine_v4:SBUX:SELL", "SBUX", "sell", 6, 6, "filled"),
    ]


def test_recovery_delta_computes_missing_buy_only_normalization() -> None:
    target = target_positions_from_intent(
        pretrade_positions=_pretrade_positions(),
        intended_orders=_intended_orders(),
    )

    delta = compute_recovery_delta(
        current_positions=_post_sell_positions(),
        target_positions=target,
        intended_orders=_intended_orders(),
    )

    assert [(order.symbol, order.side, order.qty) for order in delta] == [
        ("CSCO", "BUY", 3.0),
        ("MNST", "BUY", 4.0),
        ("MRK", "BUY", 7.0),
        ("NEM", "BUY", 2.0),
        ("PANW", "BUY", 2.0),
        ("UNH", "BUY", 1.0),
    ]


def test_classifier_marks_reconciled_partial_failure_as_recovery_candidate() -> None:
    target = target_positions_from_intent(
        pretrade_positions=_pretrade_positions(),
        intended_orders=_intended_orders(),
    )
    delta = compute_recovery_delta(
        current_positions=_post_sell_positions(),
        target_positions=target,
        intended_orders=_intended_orders(),
    )
    snapshot = InterruptedRunSnapshot(
        source_run_id="2026-05-18T093507-0400_71f60c1",
        trade_date="2026-05-18",
        execution_status="HALTED",
        execution_outcome="post_submit_artifact_failure",
        halt_reason="post_submit_artifact_failure:posttrade_state_capture_failed",
        submitted_count=5,
        accepted_count=5,
        intended_orders=_intended_orders(),
        pretrade_positions=_pretrade_positions(),
        current_broker_state=BrokerState(positions=_post_sell_positions()),
        posttrade_reconciliation_status="OK_RECONCILED",
    )

    result = classify_interrupted_run(snapshot, recovery_delta=delta)

    assert result.state == ExecutionLifecycleState.RECOVERY_CANDIDATE
    assert result.recovery_candidate is True


def test_classifier_keeps_unresolved_fill_quantities_settlement_pending() -> None:
    snapshot = InterruptedRunSnapshot(
        source_run_id="run",
        trade_date="2026-05-18",
        execution_status="HALTED",
        execution_outcome="post_submit_artifact_failure",
        halt_reason="post_submit_artifact_failure:posttrade_state_capture_failed",
        submitted_count=5,
        accepted_count=5,
        posttrade_reconciliation_status="UNKNOWN",
    )

    result = classify_interrupted_run(snapshot, recovery_delta=[])

    assert result.state == ExecutionLifecycleState.SETTLEMENT_PENDING


def test_prior_sell_validation_blocks_partial_fills() -> None:
    orders = _terminal_sell_orders()
    orders[0] = OrderState("2026-05-18:main:growth_engine_v4:ELV:SELL", "ELV", "sell", 1, 0.5, "partially_filled")

    result = assert_prior_sells_terminal_filled(
        orders=orders,
        expected_sell_client_ids={order.client_order_id for order in _terminal_sell_orders()},
    )

    assert result.ok is False
    assert result.failures == [
        "prior_sell_not_terminal_filled:2026-05-18:main:growth_engine_v4:ELV:SELL:partially_filled"
    ]


def test_validator_blocks_duplicate_recovery_order_and_open_orders() -> None:
    target = target_positions_from_intent(
        pretrade_positions=_pretrade_positions(),
        intended_orders=_intended_orders(),
    )
    delta = compute_recovery_delta(
        current_positions=_post_sell_positions(),
        target_positions=target,
        intended_orders=_intended_orders(),
    )
    broker_state = BrokerState(
        account_status="ACTIVE",
        cash=4000,
        positions=_post_sell_positions(),
        open_orders_count=1,
        orders=[
            OrderState(
                "2026-05-18:recovery_01:BUY_ONLY_NORMALIZATION:MRK:BUY",
                "MRK",
                "buy",
                7,
                0,
                "new",
            )
        ],
    )

    result = validate_recovery_candidate(
        broker_state=broker_state,
        recovery_delta=delta,
        target_positions=target,
        trade_date="2026-05-18",
    )

    assert result.ok is False
    assert "open_orders_present" in result.failures
    assert "duplicate_recovery_client_order_id:2026-05-18:recovery_01:BUY_ONLY_NORMALIZATION:MRK:BUY" in result.failures


def test_validator_warns_but_does_not_fail_on_stale_execution_lock() -> None:
    target = target_positions_from_intent(
        pretrade_positions=_pretrade_positions(),
        intended_orders=_intended_orders(),
    )
    delta = compute_recovery_delta(
        current_positions=_post_sell_positions(),
        target_positions=target,
        intended_orders=_intended_orders(),
    )

    result = validate_recovery_candidate(
        broker_state=BrokerState(account_status="ACTIVE", cash=4000, positions=_post_sell_positions()),
        recovery_delta=delta,
        target_positions=target,
        trade_date="2026-05-18",
        stale_execution_lock_present=True,
    )

    assert result.ok is True
    assert result.warnings == ["stale_execution_lock_present_do_not_reuse"]


def test_already_normalized_portfolio_has_no_recovery_delta() -> None:
    target = target_positions_from_intent(
        pretrade_positions=_pretrade_positions(),
        intended_orders=_intended_orders(),
    )

    delta = compute_recovery_delta(
        current_positions=target,
        target_positions=target,
        intended_orders=_intended_orders(),
    )

    assert delta == []


def test_simulation_cli_writes_deterministic_dry_run_artifacts(tmp_path: Path) -> None:
    pretrade = tmp_path / "pretrade.json"
    intended = tmp_path / "intended.json"
    broker = tmp_path / "broker.json"
    execution = tmp_path / "execution.json"
    output_dir = tmp_path / "simulation"

    pretrade.write_text(json.dumps({"positions": _pretrade_positions()}), encoding="utf-8")
    intended.write_text(
        json.dumps({"orders_intended": [order.__dict__ for order in _intended_orders()]}),
        encoding="utf-8",
    )
    broker.write_text(
        json.dumps(
            {
                "account": {"status": "ACTIVE", "cash": 4000, "equity": 10360, "trading_blocked": False},
                "positions": _post_sell_positions(),
                "orders_report_date": [order.__dict__ for order in _terminal_sell_orders()],
                "open_orders_count": 0,
            }
        ),
        encoding="utf-8",
    )
    execution.write_text(
        json.dumps(
            {
                "execution_status": "HALTED",
                "execution_outcome": "post_submit_artifact_failure",
                "halt_reason": "post_submit_artifact_failure:posttrade_state_capture_failed",
                "submitted_count": 5,
                "accepted_count": 5,
            }
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            "scripts/simulate_interrupted_recovery.py",
            "--source-run-id",
            "2026-05-18T093507-0400_71f60c1",
            "--trade-date",
            "2026-05-18",
            "--pretrade-positions",
            str(pretrade),
            "--intended-orders",
            str(intended),
            "--current-broker-state",
            str(broker),
            "--execution-payload",
            str(execution),
            "--posttrade-reconciliation-status",
            "OK_RECONCILED",
            "--execution-lock-present",
            "--output-dir",
            str(output_dir),
        ],
        cwd=Path(__file__).resolve().parents[1],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    summary = json.loads((output_dir / "recovery_simulation_summary.json").read_text())
    assert summary["dry_run"] is True
    assert summary["replay_execution"] is False
    assert summary["production_behavior_changed"] is False
    assert summary["verdict"] == "SIMULATION_PASS"
    assert summary["lifecycle_state"] == "RECOVERY_CANDIDATE"
    assert len(summary["recovery_delta"]) == 6
    assert (output_dir / "operator_notes.md").exists()

