from __future__ import annotations

import csv
from pathlib import Path

import pytest

from research.pit_universe import (
    DEFAULT_FAMILY,
    DEFAULT_DATA_DIR,
    PITUniverseUnavailable,
    Universe,
)
from scripts.research.build_pit_universe_from_sharadar import DEMO_FIXTURE, map_ticker_rows


def _seed(
    tmp: Path,
    master_rows: list[dict],
    membership_rows: list[dict] | None = None,
    large_cap_rows: list[dict] | None = None,
) -> Path:
    d = tmp / "data" / "pit_universe"
    d.mkdir(parents=True, exist_ok=True)
    master_fields = ["security_id", "permaticker", "ticker", "name", "exchange", "category",
                     "isdelisted", "firstpricedate", "lastpricedate", "relatedtickers", "confidence", "source"]
    with (d / "security_master.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=master_fields)
        w.writeheader()
        for r in master_rows:
            w.writerow({k: r.get(k) for k in master_fields})
    if membership_rows is not None:
        mfields = ["security_id", "ticker", "membership_family", "membership_start_date",
                   "membership_end_date", "source", "confidence"]
        with (d / "membership_universe.csv").open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=mfields)
            w.writeheader()
            for r in membership_rows:
                w.writerow({k: r.get(k) for k in mfields})
    if large_cap_rows is not None:
        mfields = ["security_id", "ticker", "membership_family", "membership_start_date",
                   "membership_end_date", "scale_source", "source", "confidence"]
        with (d / "membership_universe_large_cap.csv").open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=mfields)
            w.writeheader()
            for r in large_cap_rows:
                w.writerow({k: r.get(k) for k in mfields})
    return d


def _master(sid, ticker, first, last, isdelisted="N", category="Domestic Common Stock", related=""):
    return {"security_id": sid, "permaticker": sid.split(":")[-1], "ticker": ticker,
            "name": ticker, "exchange": "NASDAQ", "category": category, "isdelisted": isdelisted,
            "firstpricedate": first, "lastpricedate": last, "relatedtickers": related,
            "confidence": "DEMO", "source": "fixture"}


# --------------------------------------------------------------------------- #
# Universe(as_of_date) reader (derive-from-master path)
# --------------------------------------------------------------------------- #
def test_active_stock_included_in_valid_range(tmp_path: Path) -> None:
    d = _seed(tmp_path, [_master("SHARADAR:1", "AAPL", "1998-01-02", "2026-03-06", "N")])
    res = Universe("2014-01-02", data_dir=d)
    assert [r["ticker"] for r in res] == ["AAPL"]
    assert res[0]["is_active_on_date"] is True


def test_delisted_included_before_and_excluded_after_lastpricedate(tmp_path: Path) -> None:
    d = _seed(tmp_path, [_master("SHARADAR:2", "TWTR", "2013-11-07", "2022-10-27", "Y")])
    assert [r["ticker"] for r in Universe("2018-06-01", data_dir=d)] == ["TWTR"]
    assert Universe("2025-01-02", data_dir=d) == []  # after delisting


def test_post_ipo_excluded_before_firstpricedate(tmp_path: Path) -> None:
    d = _seed(tmp_path, [_master("SHARADAR:3", "META", "2012-05-18", "2026-03-06", "N")])
    assert Universe("2010-01-04", data_dir=d) == []  # before IPO
    assert [r["ticker"] for r in Universe("2014-01-02", data_dir=d)] == ["META"]


def test_relatedtickers_preserved(tmp_path: Path) -> None:
    d = _seed(tmp_path, [_master("SHARADAR:3", "META", "2012-05-18", "2026-03-06", "N", related="FB")])
    res = Universe("2024-01-02", data_dir=d)
    assert res[0]["relatedtickers"] == "FB"


def test_missing_artifacts_fail_clearly(tmp_path: Path) -> None:
    with pytest.raises(PITUniverseUnavailable):
        Universe("2014-01-02", data_dir=tmp_path / "data" / "pit_universe")


def test_no_fallback_to_universe_csv(tmp_path: Path) -> None:
    # A data/universe.csv exists but PIT artifacts do not -> must NOT fall back.
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "universe.csv").write_text("ticker,sector\nAAPL,Tech\n", encoding="utf-8")
    with pytest.raises(PITUniverseUnavailable):
        Universe("2014-01-02", data_dir=tmp_path / "data" / "pit_universe")


def test_deterministic_ordering(tmp_path: Path) -> None:
    d = _seed(tmp_path, [
        _master("SHARADAR:9", "ZZZ", "2000-01-03", "2026-03-06", "N"),
        _master("SHARADAR:1", "AAA", "2000-01-03", "2026-03-06", "N"),
        _master("SHARADAR:5", "MMM", "2000-01-03", "2026-03-06", "N"),
    ])
    res = Universe("2014-01-02", data_dir=d)
    assert [r["security_id"] for r in res] == ["SHARADAR:1", "SHARADAR:5", "SHARADAR:9"]


def test_common_stock_filter(tmp_path: Path) -> None:
    d = _seed(tmp_path, [
        _master("SHARADAR:1", "AAPL", "1998-01-02", "2026-03-06", "N", category="Domestic Common Stock"),
        _master("SHARADAR:2", "SPY", "1998-01-02", "2026-03-06", "N", category="Exchange Traded Fund"),
    ])
    assert [r["ticker"] for r in Universe("2014-01-02", data_dir=d)] == ["AAPL"]


def test_universe_uses_materialized_membership_when_present(tmp_path: Path) -> None:
    d = _seed(
        tmp_path,
        [_master("SHARADAR:2", "TWTR", "2013-11-07", "2022-10-27", "Y")],
        membership_rows=[{"security_id": "SHARADAR:2", "ticker": "TWTR",
                          "membership_family": DEFAULT_FAMILY, "membership_start_date": "2013-11-07",
                          "membership_end_date": "2022-10-27", "source": "fixture", "confidence": "DEMO"}],
    )
    assert [r["ticker"] for r in Universe("2018-01-02", data_dir=d)] == ["TWTR"]
    assert Universe("2025-01-02", data_dir=d) == []


def test_universe_loads_registered_family_artifact_when_not_in_canonical_membership(tmp_path: Path) -> None:
    d = _seed(
        tmp_path,
        [
            _master("SHARADAR:1", "AAPL", "1998-01-02", "2026-03-06", "N", category="Domestic Common Stock"),
            _master("SHARADAR:2", "TWTR", "2013-11-07", "2022-10-27", "Y", category="Domestic Common Stock"),
            _master("SHARADAR:3", "SPY", "1998-01-02", "2026-03-06", "N", category="Exchange Traded Fund"),
        ],
        membership_rows=[
            {"security_id": "SHARADAR:1", "ticker": "AAPL",
             "membership_family": DEFAULT_FAMILY, "membership_start_date": "1998-01-02",
             "membership_end_date": "", "source": "fixture", "confidence": "DEMO"},
        ],
        large_cap_rows=[
            {"security_id": "SHARADAR:1", "ticker": "AAPL",
             "membership_family": "caerus_large_cap", "membership_start_date": "1998-01-02",
             "membership_end_date": "", "scale_source": "scalemarketcap", "source": "fixture",
             "confidence": "DEMO"},
            {"security_id": "SHARADAR:2", "ticker": "TWTR",
             "membership_family": "caerus_large_cap", "membership_start_date": "2013-11-07",
             "membership_end_date": "2022-10-27", "scale_source": "scalemarketcap",
             "source": "fixture", "confidence": "DEMO"},
            {"security_id": "SHARADAR:3", "ticker": "SPY",
             "membership_family": "caerus_large_cap", "membership_start_date": "1998-01-02",
             "membership_end_date": "", "scale_source": "scalemarketcap", "source": "fixture",
             "confidence": "DEMO"},
        ],
    )
    res = Universe("2018-01-02", "caerus_large_cap", data_dir=d)
    assert [r["ticker"] for r in res] == ["AAPL", "TWTR"]
    assert {r["scale_source"] for r in res} == {"scalemarketcap"}
    assert [r["ticker"] for r in Universe("2025-01-02", "caerus_large_cap", data_dir=d)] == ["AAPL"]


def test_universe_unknown_family_fails_loudly(tmp_path: Path) -> None:
    d = _seed(
        tmp_path,
        [_master("SHARADAR:1", "AAPL", "1998-01-02", "2026-03-06", "N")],
        membership_rows=[{"security_id": "SHARADAR:1", "ticker": "AAPL",
                          "membership_family": DEFAULT_FAMILY, "membership_start_date": "1998-01-02",
                          "membership_end_date": "", "source": "fixture", "confidence": "DEMO"}],
    )
    with pytest.raises(PITUniverseUnavailable, match="no family-specific artifact is registered"):
        Universe("2018-01-02", "unknown_family", data_dir=d)


def test_real_caerus_large_cap_family_certification_counts() -> None:
    expected = {
        "2014-01-02": 1197,
        "2020-01-02": 1243,
        "2026-01-02": 1260,
    }
    for as_of_date, count in expected.items():
        rows = Universe(as_of_date, "caerus_large_cap", data_dir=DEFAULT_DATA_DIR)
        assert len(rows) == count
        assert {row["membership_family"] for row in rows} == {"caerus_large_cap"}
        assert all(row["security_id"].startswith("SHARADAR:") for row in rows)


# --------------------------------------------------------------------------- #
# build mapping (pure, no network)
# --------------------------------------------------------------------------- #
def test_map_ticker_rows_canonical_tables() -> None:
    tables = map_ticker_rows(DEMO_FIXTURE, source="fixture", confidence="DEMO")
    sm = {r["ticker"]: r for r in tables["security_master"]}
    # active vs delisted membership end
    meta_mem = [m for m in tables["membership_universe"] if m["ticker"] == "META"][0]
    twtr_mem = [m for m in tables["membership_universe"] if m["ticker"] == "TWTR"][0]
    assert meta_mem["membership_end_date"] == ""  # active -> open
    assert twtr_mem["membership_end_date"] == "2022-10-27"
    # delisting event only for delisted names
    delisted_events = {e["security_id"] for e in tables["security_events"]}
    assert sm["TWTR"]["security_id"] in delisted_events
    assert sm["AAPL"]["security_id"] not in delisted_events
    # relatedtickers -> symbol_history
    meta_syms = [s["related_ticker"] for s in tables["symbol_history"] if s["ticker"] == "META"]
    assert meta_syms == ["FB"]
    # stable security_id rooted in permaticker
    assert sm["META"]["security_id"] == "SHARADAR:118692"


def test_map_ticker_rows_common_stock_filter() -> None:
    rows = [{"permaticker": "1", "ticker": "AAPL", "category": "Domestic Common Stock",
             "isdelisted": "N", "firstpricedate": "1998-01-02", "lastpricedate": "2026-03-06"},
            {"permaticker": "2", "ticker": "SPY", "category": "Exchange Traded Fund",
             "isdelisted": "N", "firstpricedate": "1998-01-02", "lastpricedate": "2026-03-06"}]
    tables = map_ticker_rows(rows, source="x", confidence="DEMO", common_stock_only=True)
    assert [r["ticker"] for r in tables["security_master"]] == ["AAPL"]
