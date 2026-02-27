from reconciliation import _normalize_positions, compare_positions, verdict_from_diffs


def test_compare_positions_exact_match_passes():
    diffs = compare_positions(
        broker_positions={"AAPL": 10.0, "MSFT": 5.0},
        model_positions={"AAPL": 10.0, "MSFT": 5.0},
        max_qty_diff=0.0,
    )
    assert verdict_from_diffs(diffs, strict=True) == "PASS"


def test_compare_positions_qty_mismatch_fails():
    diffs = compare_positions(
        broker_positions={"AAPL": 10.0},
        model_positions={"AAPL": 9.0},
        max_qty_diff=0.0,
    )
    assert diffs["qty_mismatches"]
    assert verdict_from_diffs(diffs) == "FAIL"


def test_compare_positions_missing_symbol_fails():
    diffs = compare_positions(
        broker_positions={"AAPL": 10.0},
        model_positions={"AAPL": 10.0, "MSFT": 1.0},
        max_qty_diff=0.0,
    )
    assert "MSFT" in diffs["missing_in_broker"]
    assert verdict_from_diffs(diffs) == "FAIL"


def test_verdict_cash_equity_within_tolerance_passes():
    diffs = compare_positions(
        broker_positions={"AAPL": 10.0},
        model_positions={"AAPL": 10.0},
        max_qty_diff=0.0,
    )
    verdict = verdict_from_diffs(
        diffs,
        cash_delta=2.0,
        equity_delta=3.0,
        equity_base=10000.0,
        cash_tol=5.0,
        equity_tol_abs=10.0,
        equity_tol_pct=0.001,
        strict=False,
    )
    assert verdict == "PASS"


def test_verdict_cash_delta_warn_when_not_strict():
    diffs = compare_positions(
        broker_positions={"AAPL": 10.0},
        model_positions={"AAPL": 10.0},
        max_qty_diff=0.0,
    )
    verdict = verdict_from_diffs(
        diffs,
        cash_delta=50.0,
        equity_delta=0.0,
        equity_base=10000.0,
        cash_tol=5.0,
        equity_tol_abs=10.0,
        equity_tol_pct=0.001,
        strict=False,
    )
    assert verdict == "WARN"


def test_verdict_cash_delta_fail_when_strict():
    diffs = compare_positions(
        broker_positions={"AAPL": 10.0},
        model_positions={"AAPL": 10.0},
        max_qty_diff=0.0,
    )
    verdict = verdict_from_diffs(
        diffs,
        cash_delta=50.0,
        equity_delta=0.0,
        equity_base=10000.0,
        cash_tol=5.0,
        equity_tol_abs=10.0,
        equity_tol_pct=0.001,
        strict=True,
    )
    assert verdict == "FAIL"


def test_normalize_positions_handles_list_and_dict_inputs():
    out_list = _normalize_positions(
        [
            {"symbol": "aapl", "qty": "1"},
            {"ticker": "AAPL", "shares": 2},
            {"symbol": "MSFT", "quantity": "3.5"},
        ]
    )
    out_dict = _normalize_positions({"aapl": "1", "MSFT": {"qty": "3.5"}})

    assert out_list["AAPL"] == 3.0
    assert out_list["MSFT"] == 3.5
    assert out_dict["AAPL"] == 1.0
    assert out_dict["MSFT"] == 3.5
