from __future__ import annotations

import pandas as pd

from research.fr068_marketcap_reconstruction import (
    build_daily_marketcap_membership,
    latest_reported_shares_as_of,
    load_daily_marketcap_cache,
)
from scripts.research.hydrate_sharadar_daily_marketcap import (
    daily_rows_to_series,
    hydrate_daily_marketcap,
    select_tickers_from_master,
)


def test_latest_reported_shares_respects_filed_date(tmp_path):
    path = tmp_path / "AAA.parquet"
    pd.DataFrame([
        {
            "tag": "CommonStockSharesOutstanding",
            "unit": "shares",
            "period_end": "2020-03-31",
            "filed_date": "2020-04-30",
            "form": "10-Q",
            "value": 100.0,
        },
        {
            "tag": "CommonStockSharesOutstanding",
            "unit": "shares",
            "period_end": "2020-06-30",
            "filed_date": "2020-08-01",
            "form": "10-Q",
            "value": 200.0,
        },
    ]).to_parquet(path, index=False)

    assert latest_reported_shares_as_of(path, pd.Timestamp("2020-04-29")) is None
    assert latest_reported_shares_as_of(path, pd.Timestamp("2020-05-01")) == 100.0
    assert latest_reported_shares_as_of(path, pd.Timestamp("2020-08-02")) == 200.0


def test_daily_marketcap_membership_breaks_on_below_threshold_day():
    master = pd.DataFrame([
        {
            "security_id": "SHARADAR:1",
            "ticker": "AAA",
            "exchange": "NYSE",
            "category": "Domestic Common Stock",
            "isdelisted": "N",
            "firstpricedate": "2019-01-01",
            "lastpricedate": "2026-01-01",
        },
        {
            "security_id": "SHARADAR:2",
            "ticker": "ETF",
            "exchange": "NYSE",
            "category": "Exchange Traded Fund",
            "isdelisted": "N",
            "firstpricedate": "2019-01-01",
            "lastpricedate": "2026-01-01",
        },
    ])
    daily = pd.DataFrame([
        {"date": pd.Timestamp("2020-01-02"), "ticker": "AAA", "marketcap": 11_000_000_000},
        {"date": pd.Timestamp("2020-01-03"), "ticker": "AAA", "marketcap": 9_000_000_000},
        {"date": pd.Timestamp("2020-01-06"), "ticker": "AAA", "marketcap": 12_000_000_000},
        {"date": pd.Timestamp("2020-01-02"), "ticker": "ETF", "marketcap": 50_000_000_000},
    ])

    membership = build_daily_marketcap_membership(security_master=master, daily_marketcap=daily)

    assert list(membership["security_id"]) == ["SHARADAR:1", "SHARADAR:1"]
    assert list(membership["membership_start_date"]) == ["2020-01-02", "2020-01-06"]
    assert list(membership["membership_end_date"]) == ["2020-01-02", "2020-01-06"]
    assert set(membership["scale_source"]) == {"marketcap"}


def test_daily_rows_to_series_sorted_dedup():
    rows = [
        {"ticker": "AAA", "date": "2020-01-03", "marketcap": 12},
        {"ticker": "AAA", "date": "2020-01-02", "marketcap": 11},
        {"ticker": "AAA", "date": "2020-01-03", "marketcap": 13},
        {"ticker": "AAA", "date": "", "marketcap": 14},
    ]

    assert daily_rows_to_series(rows) == [
        {"date": "2020-01-02", "marketcap": 11},
        {"date": "2020-01-03", "marketcap": 13},
    ]


def test_hydrate_daily_marketcap_with_injected_fetch(tmp_path):
    payload = {
        "datatable": {
            "columns": [{"name": "ticker"}, {"name": "date"}, {"name": "marketcap"}],
            "data": [["AAA", "2020-01-02", 11_000_000_000], ["AAA", "2020-01-03", 12_000_000_000]],
        }
    }

    def fake_get(table, params):
        assert table == "SHARADAR/DAILY"
        assert params["ticker"] == "AAA"
        assert params["qopts.columns"] == "ticker,date,marketcap"
        return payload

    manifest = hydrate_daily_marketcap(
        [{"ticker": "AAA", "security_id": "SHARADAR:1"}],
        api_key="x",
        cache_dir=tmp_path,
        get_fn=fake_get,
        sleep_s=0,
        retrieved_at="2026-06-23T00:00:00+00:00",
    )

    assert manifest["hydrated"] == 1
    assert manifest["total_rows"] == 2
    assert manifest["per_ticker"]["AAA"]["security_id"] == "SHARADAR:1"
    assert (tmp_path / "AAA.csv").exists()


def test_load_daily_marketcap_cache_maps_to_security_id(tmp_path):
    pd.DataFrame([
        {"date": "2020-01-02", "marketcap": 11_000_000_000},
        {"date": "2020-01-03", "marketcap": 12_000_000_000},
    ]).to_csv(tmp_path / "AAA.csv", index=False)
    master = pd.DataFrame([{"security_id": "SHARADAR:1", "ticker": "AAA"}])

    panel = load_daily_marketcap_cache(tmp_path, master)

    assert list(panel["security_id"].unique()) == ["SHARADAR:1"]
    assert list(panel["marketcap"]) == [11_000_000_000, 12_000_000_000]


def test_select_tickers_from_master_normalizes_class_suffix(tmp_path):
    master = tmp_path / "security_master.csv"
    pd.DataFrame([
        {"security_id": "SHARADAR:1", "ticker": "BRK.B"},
        {"security_id": "SHARADAR:2", "ticker": "AAPL"},
    ]).to_csv(master, index=False)

    rows = select_tickers_from_master(master, ["BRK-B", "AAPL"])

    assert rows == [
        {"ticker": "AAPL", "security_id": "SHARADAR:2"},
        {"ticker": "BRK.B", "security_id": "SHARADAR:1"},
    ]
