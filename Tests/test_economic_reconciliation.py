from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.economic_reconciliation import (
    EconomicTolerance,
    DEFAULT_MARK_TIMING_TOLERANCE_BPS,
    Fill,
    MarkedPosition,
    ReconciliationStatus,
    SleeveAttributionRow,
    reconcile_economic_truth,
    reconcile_sleeve_attribution,
    mark_timing_tolerance,
    verify_canonical_economic_artifact_hash,
    verify_canonical_economics,
    write_canonical_economic_verification,
)


FIXTURE = Path(__file__).parent / "fixtures" / "economic" / "clean_reconciliation.json"


def _fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _economic_result(payload: dict):
    return reconcile_economic_truth(
        trade_date=payload["trade_date"],
        starting_cash=payload["starting_cash"],
        starting_positions=payload["starting_positions"],
        fills=[Fill(**row) for row in payload["fills"]],
        ending_cash=payload["ending_cash"],
        ending_positions=[MarkedPosition(**row) for row in payload["ending_positions"]],
        broker_equity=payload["broker_equity"],
        broker_position_value=payload["broker_position_value"],
    )


def test_positions_cash_fills_marks_reconcile_to_canonical_nav_fixture() -> None:
    result = _economic_result(_fixture())
    assert result.status is ReconciliationStatus.RECONCILED
    assert result.expected_ending_positions == {"AAPL": 8.0, "MSFT": 5.0, "NVDA": 1.0}
    assert result.actual_ending_positions == result.expected_ending_positions
    assert result.expected_ending_cash == pytest.approx(1068.0)
    assert result.actual_ending_cash == pytest.approx(1068.0)
    assert result.fill_notional_buys == pytest.approx(150.0)
    assert result.fill_notional_sells == pytest.approx(220.0)
    assert result.fill_fees == pytest.approx(2.0)
    assert result.marked_position_value == pytest.approx(2051.0)
    assert result.calculated_nav == pytest.approx(3119.0)
    assert result.broker_equity == pytest.approx(3119.0)
    assert result.to_dict()["reason_codes"] == ["ECONOMIC_TRUTH_RECONCILED"]


@pytest.mark.parametrize(
    ("mutation", "reason_code"),
    [
        (lambda payload: payload.update(ending_cash=1067.0), "CASH_FROM_FILLS_MISMATCH"),
        (
            lambda payload: payload["ending_positions"][0].update(quantity=7.0),
            "POSITION_FROM_FILLS_MISMATCH",
        ),
        (
            lambda payload: payload["ending_positions"][0].update(broker_market_value=900.0),
            "POSITION_MARK_MISMATCH",
        ),
        (
            lambda payload: payload.update(broker_position_value=2040.0),
            "BROKER_POSITION_VALUE_MISMATCH",
        ),
        (lambda payload: payload.update(broker_equity=3100.0), "BROKER_EQUITY_MISMATCH"),
    ],
)
def test_each_economic_identity_fails_loudly(mutation, reason_code: str) -> None:
    payload = _fixture()
    mutation(payload)
    result = _economic_result(payload)
    assert result.status is ReconciliationStatus.FAILED_RECONCILIATION
    assert reason_code in result.reason_codes


def test_documented_tolerance_handles_point_in_time_broker_rounding() -> None:
    result = reconcile_economic_truth(
        trade_date="2026-08-12",
        starting_cash=275.96,
        starting_positions={"INTC": 20, "LRCX": 6, "MU": 2, "STX": 3, "WDC": 5},
        fills=[Fill(symbol="STX", side="SELL", quantity=1, price=884.01, sleeve="caerus_orion")],
        ending_cash=1159.97,
        ending_positions=[
            MarkedPosition("INTC", 20, 100.706, 2014.12),
            MarkedPosition("LRCX", 6, 326.985, 1961.91),
            MarkedPosition("MU", 2, 923.0, 1846.0),
            MarkedPosition("STX", 2, 884.625, 1769.25),
            MarkedPosition("WDC", 5, 457.52, 2287.6),
        ],
        broker_position_value=9879.10,
        broker_equity=11039.07,
        tolerance=EconomicTolerance(position_value_abs=0.50, nav_abs=0.50),
    )
    assert result.status is ReconciliationStatus.RECONCILED
    assert result.cash_delta == pytest.approx(0.0)
    assert result.nav_delta == pytest.approx(-0.22)


def test_mark_timing_tolerance_is_bounded_percentage_of_portfolio_nav() -> None:
    assert DEFAULT_MARK_TIMING_TOLERANCE_BPS == 25.0
    assert mark_timing_tolerance(
        starting_equity=11_559.08,
        ending_equity=11_535.92,
    ) == pytest.approx(28.8977)
    assert mark_timing_tolerance(
        starting_equity=10_000.0,
        ending_equity=10_000.0,
        tolerance_bps=0.0,
    ) == pytest.approx(0.01)
    assert mark_timing_tolerance(
        starting_equity=10_000.0,
        ending_equity=10_000.0,
        tolerance_bps=50.0,
    ) == pytest.approx(50.0)
    with pytest.raises(ValueError, match="between 0 and 50 basis points"):
        mark_timing_tolerance(
            starting_equity=11_559.08,
            ending_equity=11_535.92,
            tolerance_bps=50.01,
        )


def test_attribution_mark_timing_percentage_has_a_strict_pass_fail_boundary() -> None:
    tolerance = mark_timing_tolerance(
        starting_equity=10_000.0,
        ending_equity=10_000.0,
        tolerance_bps=25.0,
    )
    assert tolerance == pytest.approx(25.0)
    inside = reconcile_sleeve_attribution(
        trade_date="2026-08-13",
        portfolio_result=0.0,
        rows=[SleeveAttributionRow("2026-08-13", "caerus_orion", 24.99)],
        tolerance=tolerance,
    )
    outside = reconcile_sleeve_attribution(
        trade_date="2026-08-13",
        portfolio_result=0.0,
        rows=[SleeveAttributionRow("2026-08-13", "caerus_orion", 25.01)],
        tolerance=tolerance,
    )
    assert inside.status is ReconciliationStatus.RECONCILED
    assert outside.status is ReconciliationStatus.FAILED_RECONCILIATION
    assert "SLEEVE_SUM_PORTFOLIO_MISMATCH" in outside.reason_codes


@pytest.mark.parametrize(
    ("delta", "expected"),
    [
        (25.0, ReconciliationStatus.RECONCILED),
        (-25.0, ReconciliationStatus.RECONCILED),
        (25.01, ReconciliationStatus.FAILED_RECONCILIATION),
        (-25.01, ReconciliationStatus.FAILED_RECONCILIATION),
    ],
)
def test_attribution_boundary_is_symmetric(delta, expected) -> None:
    result = reconcile_sleeve_attribution(
        trade_date="2026-08-13",
        portfolio_result=0.0,
        rows=[SleeveAttributionRow("2026-08-13", "caerus_orion", delta)],
        tolerance=25.0,
    )
    assert result.status is expected


def test_large_snapshot_residuals_cannot_cancel_across_interval() -> None:
    result = reconcile_sleeve_attribution(
        trade_date="2026-08-13",
        portfolio_result=0.0,
        rows=[SleeveAttributionRow("2026-08-13", "caerus_orion", 0.0)],
        tolerance=25.0,
        timing_evidence={
            "starting_equity": 10_000.0,
            "ending_equity": 10_000.0,
            "starting_cash": 1_000.0,
            "ending_cash": 1_000.0,
            "starting_position_value": 9_020.0,
            "ending_position_value": 9_020.0,
            "starting_snapshot_residual": 20.0,
            "ending_snapshot_residual": 20.0,
            "per_snapshot_tolerance": 12.5,
            "total_tolerance": 25.0,
            "tolerance_bps": 25.0,
        },
    )
    assert result.status is ReconciliationStatus.FAILED_RECONCILIATION
    assert "STARTING_SNAPSHOT_NAV_IDENTITY_MISMATCH" in result.reason_codes
    assert "ENDING_SNAPSHOT_NAV_IDENTITY_MISMATCH" in result.reason_codes


def test_august_13_no_trade_timing_numbers_reconcile_over_one_interval() -> None:
    starting_equity = 11_536.12
    ending_equity = 11_535.92
    starting_cash = ending_cash = 1_646.34
    starting_position_value = 9_889.78
    ending_position_value = 9_889.58
    portfolio_result = ending_equity - starting_equity
    attributed_result = (
        ending_position_value
        - starting_position_value
        + ending_cash
        - starting_cash
    )
    tolerance = mark_timing_tolerance(
        starting_equity=starting_equity,
        ending_equity=ending_equity,
    )
    result = reconcile_sleeve_attribution(
        trade_date="2026-08-13",
        portfolio_result=portfolio_result,
        rows=[
            SleeveAttributionRow(
                "2026-08-13", "caerus_orion", attributed_result
            )
        ],
        tolerance=tolerance,
        timing_evidence={
            "starting_equity": starting_equity,
            "ending_equity": ending_equity,
            "starting_cash": starting_cash,
            "ending_cash": ending_cash,
            "starting_position_value": starting_position_value,
            "ending_position_value": ending_position_value,
            "starting_snapshot_residual": 0.0,
            "ending_snapshot_residual": 0.0,
            "per_snapshot_tolerance": tolerance / 2.0,
            "total_tolerance": tolerance,
            "tolerance_bps": 25.0,
        },
    )
    assert portfolio_result == pytest.approx(-0.20)
    assert attributed_result == pytest.approx(-0.20)
    assert result.attribution_delta == pytest.approx(0.0)
    assert result.status is ReconciliationStatus.RECONCILED


def test_date_and_sleeve_attribution_sums_to_portfolio_fixture() -> None:
    payload = _fixture()
    result = reconcile_sleeve_attribution(
        trade_date=payload["trade_date"],
        portfolio_result=payload["portfolio_result"],
        rows=[SleeveAttributionRow(**row) for row in payload["sleeve_attribution"]],
    )
    assert result.status is ReconciliationStatus.RECONCILED
    assert result.attributed_result == pytest.approx(result.portfolio_result)
    assert result.sleeve_results == {
        "caerus_lyra": 5.0,
        "caerus_orion": 12.0,
        "portfolio_cash_and_fees": 2.0,
    }
    assert len(result.source_artifacts) == 3


def test_combined_canonical_verification_requires_both_contracts() -> None:
    payload = _fixture()
    economic = _economic_result(payload)
    attribution = reconcile_sleeve_attribution(
        trade_date=payload["trade_date"],
        portfolio_result=payload["portfolio_result"],
        rows=[SleeveAttributionRow(**row) for row in payload["sleeve_attribution"]],
    )
    combined = verify_canonical_economics(
        economic_reconciliation=economic,
        sleeve_attribution_reconciliation=attribution,
    )
    assert combined.status is ReconciliationStatus.RECONCILED
    assert combined.to_dict()["reconciled"] is True
    assert verify_canonical_economic_artifact_hash(combined.to_dict())

    failed_attribution = reconcile_sleeve_attribution(
        trade_date=payload["trade_date"],
        portfolio_result=payload["portfolio_result"] + 1.0,
        rows=[SleeveAttributionRow(**row) for row in payload["sleeve_attribution"]],
    )
    failed = verify_canonical_economics(
        economic_reconciliation=economic,
        sleeve_attribution_reconciliation=failed_attribution,
    )
    assert failed.status is ReconciliationStatus.FAILED_RECONCILIATION


def test_combined_verification_artifact_is_hashed_and_immutable(tmp_path) -> None:
    payload = _fixture()
    combined = verify_canonical_economics(
        economic_reconciliation=_economic_result(payload),
        sleeve_attribution_reconciliation=reconcile_sleeve_attribution(
            trade_date=payload["trade_date"],
            portfolio_result=payload["portfolio_result"],
            rows=[SleeveAttributionRow(**row) for row in payload["sleeve_attribution"]],
        ),
    )
    path = write_canonical_economic_verification(
        tmp_path / "canonical_economic_verification.json",
        combined,
    )
    written = json.loads(path.read_text(encoding="utf-8"))
    assert verify_canonical_economic_artifact_hash(written)
    with pytest.raises(FileExistsError, match="immutable"):
        write_canonical_economic_verification(path, combined)


def test_attribution_wrong_date_unknown_sleeve_and_sum_drift_fail() -> None:
    result = reconcile_sleeve_attribution(
        trade_date="2026-08-12",
        portfolio_result=10.0,
        rows=[
            SleeveAttributionRow("2026-08-11", "caerus_orion", 8.0),
            SleeveAttributionRow("2026-08-12", "UNATTRIBUTED", 1.0),
        ],
    )
    assert result.status is ReconciliationStatus.FAILED_RECONCILIATION
    assert set(result.reason_codes) == {
        "ATTRIBUTION_TRADE_DATE_MISMATCH",
        "UNATTRIBUTED_RESULT_PRESENT",
        "SLEEVE_SUM_PORTFOLIO_MISMATCH",
    }


def test_missing_attribution_cannot_reconcile_zero_result_silently() -> None:
    result = reconcile_sleeve_attribution(
        trade_date="2026-08-12",
        portfolio_result=0.0,
        rows=[],
    )
    assert result.status is ReconciliationStatus.FAILED_RECONCILIATION
    assert "ATTRIBUTION_MISSING" in result.reason_codes


def test_economic_truth_rejects_short_position_even_when_fill_math_matches() -> None:
    result = reconcile_economic_truth(
        trade_date="2026-08-12",
        starting_cash=0.0,
        starting_positions={},
        fills=[Fill(symbol="AAPL", side="SELL", quantity=1, price=100.0)],
        ending_cash=100.0,
        ending_positions=[MarkedPosition(symbol="AAPL", quantity=-1, mark=100.0)],
        broker_equity=0.0,
    )
    assert result.status is ReconciliationStatus.FAILED_RECONCILIATION
    assert "UNEXPECTED_SHORT_POSITION" in result.reason_codes
