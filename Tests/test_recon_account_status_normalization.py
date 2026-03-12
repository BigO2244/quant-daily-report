from reconciliation import classify_drift


def _base_inputs(account_status):
    return {
        "run_date": "2026-03-12",
        "broker_snapshot": {
            "account_status": account_status,
            "account_error": None,
            "positions_error": None,
            "errors": [],
            "equity": 10000.0,
            "positions": {},
        },
        "model_snapshot": {
            "parse_error": None,
            "equity": 10000.0,
            "positions": {},
            "timestamp_utc": "2026-03-12T12:00:00+00:00",
        },
        "diffs": {
            "missing_in_model": [],
            "missing_in_broker": [],
            "qty_mismatches": [],
        },
        "cash_delta": 0.0,
        "equity_delta": 0.0,
    }


def test_active_status_passes():
    result = classify_drift(**_base_inputs("ACTIVE"))
    assert result["reconciliation_decision"] == "PASS"
    assert result["hard_blocks"] == []


def test_enum_style_active_status_passes():
    result = classify_drift(**_base_inputs("AccountStatus.ACTIVE"))
    assert result["reconciliation_decision"] == "PASS"
    assert result["hard_blocks"] == []


def test_lowercase_active_status_passes():
    result = classify_drift(**_base_inputs("active"))
    assert result["reconciliation_decision"] == "PASS"
    assert result["hard_blocks"] == []


def test_non_active_status_blocks():
    result = classify_drift(**_base_inputs("AccountStatus.BLOCKED"))
    assert result["reconciliation_decision"] == "BLOCK"
    assert result["hard_blocks"] == ["account_status_not_active:BLOCKED"]
