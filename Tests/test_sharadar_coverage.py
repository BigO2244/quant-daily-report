from __future__ import annotations

from scripts.research.verify_sharadar_coverage import (
    SMALL_CAP_SCALES,
    _rows_from_datatable,
    assess_ticker_coverage,
    expected_trading_days,
    resolve_api_key,
    run_verification,
    select_delisted_small_caps,
    summarize,
)


def test_resolve_api_key_prefers_cli(monkeypatch) -> None:
    monkeypatch.delenv("NASDAQ_DATA_LINK_API_KEY", raising=False)
    monkeypatch.delenv("QUANDL_API_KEY", raising=False)
    assert resolve_api_key("abc") == "abc"
    assert resolve_api_key(None) is None
    monkeypatch.setenv("QUANDL_API_KEY", "envkey")
    assert resolve_api_key(None) == "envkey"


def test_datatable_parsing() -> None:
    payload = {"datatable": {"columns": [{"name": "ticker"}, {"name": "date"}],
                             "data": [["AAA", "2015-01-02"], ["AAA", "2015-01-05"]]}}
    rows = _rows_from_datatable(payload)
    assert rows == [{"ticker": "AAA", "date": "2015-01-02"}, {"ticker": "AAA", "date": "2015-01-05"}]


def test_select_delisted_small_caps_filters_and_samples() -> None:
    rows = [
        {"ticker": "DEAD1", "isdelisted": "Y", "lastpricedate": "2015-06-30", "scalemarketcap": "3 - Small"},
        {"ticker": "LIVE", "isdelisted": "N", "lastpricedate": "2024-01-01", "scalemarketcap": "3 - Small"},
        {"ticker": "BIG", "isdelisted": "Y", "lastpricedate": "2016-01-01", "scalemarketcap": "5 - Large"},
        {"ticker": "OLD", "isdelisted": "Y", "lastpricedate": "2008-01-01", "scalemarketcap": "2 - Micro"},
        {"ticker": "DEAD2", "isdelisted": "Y", "lastpricedate": "2020-03-15", "scalemarketcap": "2 - Micro"},
    ]
    picked = select_delisted_small_caps(rows, start_year=2010, end_year=2024, sample_size=10)
    names = {r["ticker"] for r in picked}
    assert names == {"DEAD1", "DEAD2"}  # live, large-cap, and pre-window excluded


def test_select_with_membership_overrides_scale() -> None:
    rows = [
        {"ticker": "MEMB", "isdelisted": "Y", "lastpricedate": "2015-06-30", "scalemarketcap": "5 - Large"},
        {"ticker": "SMALL", "isdelisted": "Y", "lastpricedate": "2015-06-30", "scalemarketcap": "3 - Small"},
    ]
    picked = select_delisted_small_caps(rows, start_year=2010, end_year=2024, sample_size=10,
                                        membership={"MEMB"})
    assert {r["ticker"] for r in picked} == {"MEMB"}  # membership wins over scale proxy


def test_expected_trading_days_and_coverage() -> None:
    # 2015-01-02 (Fri) .. 2015-01-09 (Fri): trading days = 2,5,6,7,8,9 = 6 sessions.
    assert expected_trading_days("2015-01-02", "2015-01-09") == 6
    cov = assess_ticker_coverage(
        ticker="AAA",
        price_dates=["2015-01-02", "2015-01-05", "2015-01-06", "2015-01-07", "2015-01-08", "2015-01-09"],
        first_price_date="2015-01-02", last_price_date="2015-01-09",
    )
    assert cov["coverage_pct"] == 1.0 and cov["reaches_delist_date"] and cov["complete"]


def test_incomplete_coverage_flagged() -> None:
    cov = assess_ticker_coverage(
        ticker="GAP", price_dates=["2015-01-02", "2015-01-05"],  # stops early
        first_price_date="2015-01-02", last_price_date="2015-06-30",
    )
    assert cov["complete"] is False
    assert cov["reaches_delist_date"] is False
    assert "does_not_reach_delist" in cov["reason_codes"]


def _weekday_series(start: str, end: str) -> list[str]:
    import datetime as dt

    d0, d1 = dt.date.fromisoformat(start), dt.date.fromisoformat(end)
    out, cur = [], d0
    while cur <= d1:
        if cur.weekday() < 5:
            out.append(cur.isoformat())
        cur += dt.timedelta(days=1)
    return out


def test_long_delisted_name_scores_without_calendar_dependency() -> None:
    # Regression for the pandas-import bug: a delisted name with a dense price
    # series through its delist date must score complete, not null/false
    # (previously expected_trading_days/coverage_pct were null and
    # reaches_delist_date false whenever paper.trading_calendar was unimportable).
    dates = _weekday_series("2005-01-03", "2010-11-22")
    cov = assess_ticker_coverage(
        ticker="GYMB", price_dates=dates,
        first_price_date="2005-01-03", last_price_date="2010-11-22",
    )
    assert cov["expected_trading_days"] is not None
    assert cov["coverage_pct"] is not None and cov["coverage_pct"] >= 0.95
    assert cov["reaches_delist_date"] is True
    assert cov["complete"] is True
    assert cov["reason_codes"] == ["ok"]


def test_reaches_delist_true_when_observed_last_equals_declared_last() -> None:
    # The exact reported symptom: actual_last == last_price_date must be reaches=True.
    cov = assess_ticker_coverage(
        ticker="X", price_dates=["2010-11-18", "2010-11-19", "2010-11-22"],
        first_price_date="2010-11-18", last_price_date="2010-11-22",
    )
    assert cov["reaches_delist_date"] is True
    assert cov["actual_last_price_date"] == "2010-11-22"


def test_declared_history_short_flag_does_not_block_within_window_coverage() -> None:
    dates = _weekday_series("2008-01-02", "2010-11-22")  # delivered later than declared 1993
    cov = assess_ticker_coverage(
        ticker="GYMB", price_dates=dates,
        first_price_date="1993-04-01", last_price_date="2010-11-22",
    )
    assert cov["reaches_delist_date"] is True
    assert cov["declared_history_complete"] is False
    assert "declared_history_short" in cov["reason_codes"]
    assert cov["coverage_pct"] >= 0.95  # internally complete despite short declared history


def test_expected_trading_days_never_none_for_valid_window_and_none_on_bad_input() -> None:
    assert expected_trading_days("1993-04-01", "2010-11-22") is not None  # ~17yr, was null before
    assert expected_trading_days("bad-date", "2010-01-01") is None
    assert expected_trading_days("2010-01-02", "2010-01-01") is None  # inverted


def test_run_verification_list_only_with_injected_get() -> None:
    tickers_payload = {"datatable": {
        "columns": [{"name": "ticker"}, {"name": "isdelisted"}, {"name": "firstpricedate"},
                    {"name": "lastpricedate"}, {"name": "scalemarketcap"}, {"name": "category"}],
        "data": [["DEAD1", "Y", "2011-01-03", "2015-06-30", "3 - Small", "Domestic"],
                 ["DEAD2", "Y", "2012-01-03", "2020-03-15", "2 - Micro", "Domestic"]],
    }}

    def fake_get(table, params):
        return tickers_payload

    report = run_verification(
        api_key="x", repo_root=__import__("pathlib").Path("."), start_year=2010, end_year=2024,
        sample_size=10, complete_threshold=0.95, membership=None, list_only=True, get_fn=fake_get,
    )
    assert report["mode"] == "list_only"
    assert set(report["sample_tickers"]) == {"DEAD1", "DEAD2"}
    assert "smallcap_scale_proxy" in report["membership_source"]
