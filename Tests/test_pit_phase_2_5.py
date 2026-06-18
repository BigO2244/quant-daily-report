from __future__ import annotations

from datetime import date
from pathlib import Path

from research.pit_large_cap_family import (
    build_large_cap_membership,
    classify_large_cap,
    normalize_ticker,
)
from scripts.research.hydrate_sharadar_sep import (
    coverage_through,
    hydrate,
    sep_rows_to_series,
)


def _sec(sid, tk, first, last, isdelisted="N", category="Domestic Common Stock", exch="NASDAQ"):
    return {"security_id": sid, "ticker": tk, "firstpricedate": first, "lastpricedate": last,
            "isdelisted": isdelisted, "category": category, "exchange": exch,
            "source": "fixture", "confidence": "HIGH"}


# --------------------------------------------------------------------------- #
# ticker normalization (BRK-B / BRK.B edge case)
# --------------------------------------------------------------------------- #
def test_normalize_ticker_class_suffix() -> None:
    assert normalize_ticker("BRK-B") == "BRK.B"
    assert normalize_ticker("BF-B") == "BF.B"
    assert normalize_ticker("AAPL") == "AAPL"
    assert normalize_ticker("BRK.B") == "BRK.B"
    # don't mangle hyphenated multi-char tails that aren't class suffixes
    assert normalize_ticker("FOO-BARR") == "FOO-BARR"


# --------------------------------------------------------------------------- #
# large-cap classification (reason codes)
# --------------------------------------------------------------------------- #
def test_classify_included_with_scale() -> None:
    ok, reasons = classify_large_cap(_sec("S:1", "AAPL", "1998-01-02", "2026-03-06"),
                                     as_of=date(2014, 1, 2), scalemarketcap="6 - Mega")
    assert ok and reasons == ["ok"]


def test_classify_market_cap_unavailable() -> None:
    ok, reasons = classify_large_cap(_sec("S:1", "AAPL", "1998-01-02", "2026-03-06"),
                                     as_of=date(2014, 1, 2))  # no scale provided
    assert not ok and "market_cap_unavailable" in reasons


def test_classify_reason_codes() -> None:
    # not common stock
    _, r = classify_large_cap(_sec("S:2", "SPY", "1998-01-02", "2026-03-06", category="Exchange Traded Fund"),
                              as_of=date(2014, 1, 2), scalemarketcap="6 - Mega")
    assert "not_common_stock" in r
    # non-US exchange
    _, r = classify_large_cap(_sec("S:3", "XYZ", "1998-01-02", "2026-03-06", exch="TSX"),
                              as_of=date(2014, 1, 2), scalemarketcap="6 - Mega")
    assert "non_us_exchange" in r
    # before IPO
    _, r = classify_large_cap(_sec("S:4", "PLTR", "2020-09-30", "2026-03-06"),
                              as_of=date(2014, 1, 2), scalemarketcap="5 - Large")
    assert "no_price_history_on_date" in r
    # delisted before date
    _, r = classify_large_cap(_sec("S:5", "TWTR", "2013-11-07", "2022-10-27", isdelisted="Y"),
                              as_of=date(2024, 1, 2), scalemarketcap="5 - Large")
    assert "delisted_before_date" in r
    # below large-cap scale
    _, r = classify_large_cap(_sec("S:6", "SMOL", "1998-01-02", "2026-03-06"),
                              as_of=date(2014, 1, 2), scalemarketcap="3 - Small")
    assert "below_large_cap_scale" in r


def test_build_family_blocked_without_scale_source() -> None:
    master = [_sec("S:1", "AAPL", "1998-01-02", "2026-03-06")]
    res = build_large_cap_membership(master, ["2014-01-02"])
    assert res["blocked"] is True
    assert res["membership"] == []
    assert "market_cap_unavailable" in res["block_reason"]


def test_build_family_with_scale_source() -> None:
    master = [
        _sec("S:1", "AAPL", "1998-01-02", "2026-03-06"),
        _sec("S:2", "PLTR", "2020-09-30", "2026-03-06"),
        _sec("S:3", "SMOL", "1998-01-02", "2026-03-06"),
    ]
    scale = {"S:1": "6 - Mega", "S:2": "5 - Large", "S:3": "3 - Small"}
    res = build_large_cap_membership(master, ["2014-01-02", "2022-01-03"],
                                     scalemarketcap_by_id=scale)
    assert res["blocked"] is False
    members = {m["ticker"] for m in res["membership"]}
    assert members == {"AAPL", "PLTR"}          # SMOL excluded (small); PLTR membership starts 2020
    d2014 = next(d for d in res["by_date"] if d["date"] == "2014-01-02")
    assert d2014["included"] == 1               # only AAPL tradable+large in 2014 (PLTR not yet public)


# --------------------------------------------------------------------------- #
# SEP hydration (injected fetch; no network)
# --------------------------------------------------------------------------- #
def test_sep_rows_to_series_sorted_dedup() -> None:
    rows = [{"date": "2015-01-05", "closeadj": 11}, {"date": "2015-01-02", "closeadj": 10},
            {"date": "2015-01-05", "closeadj": 11.5}]
    s = sep_rows_to_series(rows)
    assert [r["date"] for r in s] == ["2015-01-02", "2015-01-05"]
    assert s[-1]["closeadj"] == 11.5  # last write wins on dup date


def test_sep_rows_to_series_ohlcv_columns() -> None:
    rows = [{"ticker": "AAPL", "date": "2024-01-02", "open": 1, "high": 2, "low": 0.5,
             "close": 1.5, "closeadj": 1.4, "volume": 100}]
    s = sep_rows_to_series(rows, "ticker,date,open,high,low,close,closeadj,volume")
    assert s == [{"date": "2024-01-02", "open": 1, "high": 2, "low": 0.5,
                  "close": 1.5, "closeadj": 1.4, "volume": 100}]


def test_coverage_through_delist_date() -> None:
    series = [{"date": "2022-10-25"}, {"date": "2022-10-27"}]
    assert coverage_through(series, "2022-10-27") is True
    assert coverage_through([{"date": "2020-01-02"}], "2022-10-27") is False
    assert coverage_through([], "2022-10-27") is False


def test_hydrate_with_injected_fetch_and_resume(tmp_path: Path) -> None:
    payloads = {
        "TWTR": {"datatable": {"columns": [{"name": "ticker"}, {"name": "date"}, {"name": "closeadj"}, {"name": "close"}],
                               "data": [["TWTR", "2022-10-26", 53.6, 53.6], ["TWTR", "2022-10-27", 53.7, 53.7]]}},
        "DEAD": {"datatable": {"columns": [{"name": "ticker"}, {"name": "date"}, {"name": "closeadj"}, {"name": "close"}],
                               "data": []}},  # empty -> reason-coded
    }

    def fake_get(table, params):
        return payloads[params["ticker"]]

    cache = tmp_path / "sep"
    m = hydrate(["TWTR", "DEAD"], api_key="x", cache_dir=cache, get_fn=fake_get, sleep_s=0,
                retrieved_at="2026-06-10T00:00:00+00:00")
    assert m["hydrated"] == 1 and m["empty"] == 1
    assert m["per_ticker"]["DEAD"]["reason"] == "no_sep_rows_returned"
    assert (cache / "TWTR.csv").exists()
    assert m["per_ticker"]["TWTR"]["last"] == "2022-10-27"

    # resume: re-run skips the already-hydrated TWTR
    m2 = hydrate(["TWTR", "DEAD"], api_key="x", cache_dir=cache, get_fn=fake_get, sleep_s=0,
                 retrieved_at="2026-06-10T00:00:00+00:00")
    assert m2["skipped_existing"] == 1
    assert m2["per_ticker"]["TWTR"]["status"] == "skipped_existing"


def test_hydrate_ohlcv_manifest_contract(tmp_path: Path) -> None:
    payload = {"datatable": {"columns": [
        {"name": "ticker"}, {"name": "date"}, {"name": "open"}, {"name": "high"},
        {"name": "low"}, {"name": "close"}, {"name": "closeadj"}, {"name": "volume"},
    ], "data": [["AAPL", "2024-01-02", 180, 181, 179, 180.5, 178.5, 1000]]}}

    def fake_get(table, params):
        assert params["qopts.columns"] == "ticker,date,open,high,low,close,closeadj,volume"
        return payload

    cache = tmp_path / "sep_ohlcv"
    m = hydrate(["AAPL"], api_key="x", cache_dir=cache, get_fn=fake_get, sleep_s=0,
                columns="ticker,date,open,high,low,close,closeadj,volume",
                retrieved_at="2026-06-18T00:00:00+00:00")
    assert m["requested_columns"] == ["ticker", "date", "open", "high", "low", "close", "closeadj", "volume"]
    assert m["ticker_count"] == 1
    assert m["total_rows"] == 1
    assert m["null_counts"]["volume"] == 0
    assert m["per_ticker"]["AAPL"]["sha256"]


def test_hydrate_no_fallback_to_universe_csv(tmp_path: Path) -> None:
    # hydrate only touches the cache dir + injected fetch; it never reads data/universe.csv
    def fake_get(table, params):
        return {"datatable": {"columns": [{"name": "date"}, {"name": "closeadj"}],
                              "data": [["2015-01-02", 10.0]]}}
    m = hydrate(["AAA"], api_key="x", cache_dir=tmp_path / "sep", get_fn=fake_get, sleep_s=0)
    assert m["hydrated"] == 1
