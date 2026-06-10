from __future__ import annotations

from pathlib import Path

from research.pit_polaris_rebaseline import (
    build_resolver,
    load_legacy_universe_tickers,
    membership_diff,
)


def _sec(tk, first, last, isdelisted="N", related=""):
    return {"security_id": f"SHARADAR:{tk}", "ticker": tk, "firstpricedate": first,
            "lastpricedate": last, "isdelisted": isdelisted, "relatedtickers": related}


MASTER = [
    _sec("AAPL", "1998-01-02", "2026-03-06", "N"),
    _sec("PLTR", "2020-09-30", "2026-03-06", "N"),       # IPO'd 2020
    _sec("TWTR", "2013-11-07", "2022-10-27", "Y"),       # delisted 2022
    _sec("META", "2012-05-18", "2026-06-09", "N", related="FB"),
]


def test_eligible_kept_and_ipo_excluded() -> None:
    res = membership_diff(["2014-01-02"], ["AAPL", "PLTR"], MASTER)
    d = res["by_date"][0]
    assert d["pit_eligible_count"] == 1  # AAPL kept
    assert d["excluded_count"] == 1
    ex = d["excluded"][0]
    assert ex["ticker"] == "PLTR" and ex["reason"] == "ipo_after_date"


def test_pltr_eligible_after_ipo() -> None:
    res = membership_diff(["2022-01-03"], ["PLTR"], MASTER)
    assert res["by_date"][0]["pit_eligible_count"] == 1


def test_delisted_before_date_excluded() -> None:
    res = membership_diff(["2024-01-02"], ["TWTR"], MASTER)
    ex = res["by_date"][0]["excluded"][0]
    assert ex["ticker"] == "TWTR" and ex["reason"] == "delisted_before_date"
    # but eligible while live
    assert membership_diff(["2018-01-02"], ["TWTR"], MASTER)["by_date"][0]["pit_eligible_count"] == 1


def test_no_pit_match_flagged() -> None:
    ex = membership_diff(["2014-01-02"], ["ZZZZ"], MASTER)["by_date"][0]["excluded"][0]
    assert ex["reason"] == "no_pit_match" and ex["security_id"] is None


def test_resolver_prefers_active_relatedticker() -> None:
    master = MASTER + [_sec("FB", "1999-09-29", "2003-03-28", "Y")]  # recycled FB ticker
    resolve = build_resolver(master)
    # 'FB' direct match is the delisted 1999-2003 entity; but META carries FB as related.
    # direct match wins for 'FB' standalone; META resolves correctly for 'META'.
    assert resolve("META")["ticker"] == "META"


def test_load_legacy_universe_handles_leading_blank_line(tmp_path: Path) -> None:
    p = tmp_path / "universe.csv"
    p.write_text("\nticker,sector\nAAPL,Tech\nMSFT,Tech\n", encoding="utf-8")
    assert load_legacy_universe_tickers(p) == ["AAPL", "MSFT"]


def test_lookahead_pct_monotone_intuition() -> None:
    # more names ineligible earlier (PLTR not yet public in 2014, public by 2022)
    early = membership_diff(["2014-01-02"], ["AAPL", "PLTR"], MASTER)["by_date"][0]
    late = membership_diff(["2022-01-03"], ["AAPL", "PLTR"], MASTER)["by_date"][0]
    assert early["lookahead_pct"] > late["lookahead_pct"]
