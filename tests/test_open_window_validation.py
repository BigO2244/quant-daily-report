import pandas as pd

from paper.paper_broker import validate_open_window


def _base_weights() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"ticker": "AAA", "sleeve": "core", "target_weight": 0.6},
            {"ticker": "BBB", "sleeve": "core", "target_weight": 0.4},
        ]
    )


def test_open_window_validation_pass_case():
    ok, reasons, details = validate_open_window(
        trade_date="2025-01-06",
        signals_meta={"trade_date": "2025-01-06", "asof_date": "2025-01-03"},
        prices_open=pd.Series({"AAA": 100.0, "BBB": 50.0}),
        prev_closes=pd.Series({"AAA": 99.0, "BBB": 49.5}),
        weights=_base_weights(),
        signals_path="signals/2025-01-06.json",
    )

    assert ok is True
    assert reasons == []
    assert details["result"] == "PASS"


def test_open_window_validation_fail_same_day_leakage():
    ok, reasons, details = validate_open_window(
        trade_date="2025-01-06",
        signals_meta={"trade_date": "2025-01-06", "asof_date": "2025-01-06"},
        prices_open=pd.Series({"AAA": 100.0, "BBB": 50.0}),
        prev_closes=pd.Series({"AAA": 99.0, "BBB": 49.5}),
        weights=_base_weights(),
        signals_path="signals/2025-01-06.json",
    )

    assert ok is False
    assert any("asof_after_cutoff" in reason for reason in reasons)
    assert details["result"] == "FAIL"


def test_open_window_validation_fail_missing_open_price():
    ok, reasons, details = validate_open_window(
        trade_date="2025-01-06",
        signals_meta={"trade_date": "2025-01-06", "asof_date": "2025-01-03"},
        prices_open=pd.Series({"AAA": 100.0}),
        prev_closes=pd.Series({"AAA": 99.0, "BBB": 49.5}),
        weights=_base_weights(),
        signals_path="signals/2025-01-06.json",
    )

    assert ok is True
    assert any("missing_open_prices" in reason for reason in reasons)
    assert details["result"] == "PASS"
    assert details["blocked_tickers"] == {"BBB": ["missing_open_prices"]}
    assert details["ticker_validation"]["AAA"]["pass"] is True
    assert details["ticker_validation"]["BBB"]["pass"] is False
