from __future__ import annotations

import json
from pathlib import Path

from core.security_master import (
    build_ticker_universe_snapshot,
    parse_nasdaq_symbol_directory,
    resolve_trade_plan_symbols,
    update_security_master,
)


def _write_aliases(root: Path) -> Path:
    path = root / "manual_aliases.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"aliases": {"BK": "BNY", "FB": "META", "SQ": "XYZ"}, "notes": {}}),
        encoding="utf-8",
    )
    return path


def test_bk_resolves_to_bny_and_bk_cannot_appear_in_final_plan(tmp_path):
    alias_path = _write_aliases(tmp_path / "data" / "security_master")
    result = resolve_trade_plan_symbols(
        [{"ticker": "BK", "side": "BUY", "shares": 1, "price": 50.0}],
        root=tmp_path / "data" / "security_master",
        alias_path=alias_path,
        today="2026-06-01",
    )

    assert result.status == "WARN"
    assert result.symbol_aliases_applied == {"BK": "BNY"}
    assert result.alias_resolutions == [
        {
            "original_symbol": "BK",
            "resolved_symbol": "BNY",
            "source": str(alias_path),
            "reason": "manual_alias:BK->BNY",
        }
    ]
    assert result.to_payload()["alias_resolutions"] == result.alias_resolutions
    assert result.trades[0]["ticker"] == "BNY"
    assert "BK" not in {row["ticker"] for row in result.trades}


def test_seeded_historical_aliases_resolve_to_current_symbols(tmp_path):
    alias_path = _write_aliases(tmp_path / "data" / "security_master")
    result = resolve_trade_plan_symbols(
        [
            {"ticker": "FB", "side": "BUY", "shares": 1, "price": 500.0},
            {"ticker": "SQ", "side": "BUY", "shares": 1, "price": 70.0},
        ],
        root=tmp_path / "data" / "security_master",
        alias_path=alias_path,
        today="2026-06-01",
    )

    assert [row["ticker"] for row in result.trades] == ["META", "XYZ"]
    assert result.symbol_aliases_applied == {"FB": "META", "SQ": "XYZ"}
    assert result.alias_resolutions == [
        {
            "original_symbol": "FB",
            "resolved_symbol": "META",
            "source": str(alias_path),
            "reason": "manual_alias:FB->META",
        },
        {
            "original_symbol": "SQ",
            "resolved_symbol": "XYZ",
            "source": str(alias_path),
            "reason": "manual_alias:SQ->XYZ",
        },
    ]


def test_manual_current_alias_wins_when_stale_and_current_symbols_both_exist(tmp_path):
    root = tmp_path / "data" / "security_master"
    alias_path = _write_aliases(root)
    update_security_master(
        asof_date="2026-06-01",
        alpaca_assets=[
            {"symbol": "FB", "name": "Meta Platforms Old", "status": "inactive", "tradable": False, "asset_class": "us_equity"},
            {"symbol": "META", "name": "Meta Platforms Inc.", "status": "active", "tradable": True, "asset_class": "us_equity"},
        ],
        nasdaq_records=[
            {"symbol": "FB", "security_name": "Meta Platforms Old"},
            {"symbol": "META", "security_name": "Meta Platforms Inc."},
        ],
        root=root,
    )

    result = resolve_trade_plan_symbols(
        [
            {"ticker": "FB", "side": "BUY", "shares": 1, "price": 500.0},
            {"ticker": "META", "side": "BUY", "shares": 1, "price": 500.0},
        ],
        root=root,
        alias_path=alias_path,
        today="2026-06-01",
    )

    assert result.status == "PASS"
    assert [row["ticker"] for row in result.trades] == ["META", "META"]
    assert result.inactive_symbols == []
    assert result.non_tradable_symbols == []
    assert result.symbol_aliases_applied == {"FB": "META"}


def test_unknown_symbol_fails_closed_when_security_master_available(tmp_path):
    root = tmp_path / "data" / "security_master"
    update_security_master(
        asof_date="2026-06-01",
        alpaca_assets=[{"symbol": "AAPL", "name": "Apple Inc.", "status": "active", "tradable": True, "asset_class": "us_equity"}],
        nasdaq_records=[{"symbol": "AAPL", "security_name": "Apple Inc."}],
        root=root,
    )

    result = resolve_trade_plan_symbols(
        [{"ticker": "NOPE", "side": "BUY", "shares": 1, "price": 10.0}],
        root=root,
        alias_path=root / "manual_aliases.json",
        today="2026-06-01",
    )

    assert result.status == "FAIL"
    assert result.unknown_symbols == ["NOPE"]
    assert "unknown_symbol:NOPE" in result.reason


def test_stale_universe_warns_or_fails_before_execution(tmp_path):
    root = tmp_path / "data" / "security_master"
    update_security_master(
        asof_date="2026-05-20",
        alpaca_assets=[{"symbol": "AAPL", "name": "Apple Inc.", "status": "active", "tradable": True, "asset_class": "us_equity"}],
        nasdaq_records=[{"symbol": "AAPL", "security_name": "Apple Inc."}],
        root=root,
    )

    warn = resolve_trade_plan_symbols(
        [{"ticker": "AAPL", "side": "BUY", "shares": 1, "price": 10.0}],
        root=root,
        alias_path=root / "manual_aliases.json",
        today="2026-06-01",
    )
    fail = resolve_trade_plan_symbols(
        [{"ticker": "AAPL", "side": "BUY", "shares": 1, "price": 10.0}],
        root=root,
        alias_path=root / "manual_aliases.json",
        today="2026-06-01",
        fail_on_stale=True,
    )

    assert warn.status == "WARN"
    assert warn.stale_universe is True
    assert "stale_security_master:2026-05-20" in warn.reason
    assert fail.status == "FAIL"


def test_daily_update_produces_deterministic_snapshot_and_change_events(tmp_path):
    root = tmp_path / "data" / "security_master"
    update_security_master(
        asof_date="2026-05-31",
        alpaca_assets=[
            {"symbol": "AAPL", "name": "Apple Inc.", "status": "active", "tradable": True, "asset_class": "us_equity"},
            {"symbol": "OLD", "name": "Old Co", "status": "active", "tradable": True, "asset_class": "us_equity"},
        ],
        nasdaq_records=[
            {"symbol": "AAPL", "security_name": "Apple Inc."},
            {"symbol": "OLD", "security_name": "Old Co"},
        ],
        root=root,
    )
    result = update_security_master(
        asof_date="2026-06-01",
        alpaca_assets=[
            {"symbol": "AAPL", "name": "Apple Incorporated", "status": "active", "tradable": True, "asset_class": "us_equity"},
            {"symbol": "BNY", "name": "Bank of New York Mellon Corp", "status": "active", "tradable": True, "asset_class": "us_equity"},
            {"symbol": "HALT", "name": "Halted Corp", "status": "inactive", "tradable": False, "asset_class": "us_equity"},
        ],
        nasdaq_records=[
            {"symbol": "AAPL", "security_name": "Apple Incorporated"},
            {"symbol": "BNY", "security_name": "Bank of New York Mellon Corp"},
            {"symbol": "HALT", "security_name": "Halted Corp"},
        ],
        root=root,
    )

    snapshot = result["snapshot"]
    assert snapshot["schema_version"] == "security-master-v1"
    assert [row["symbol"] for row in snapshot["symbols"]] == ["AAPL", "BNY", "HALT"]
    assert (root / "2026-06-01" / "ticker_universe.json").exists()
    assert (root / "2026-06-01" / "symbol_change_events.json").exists()
    assert (root / "ticker_universe_latest.json").exists()

    events = snapshot["events"]
    assert events["additions"] == [
        {"symbol": "BNY", "name": "Bank of New York Mellon Corp"},
        {"symbol": "HALT", "name": "Halted Corp"},
    ]
    assert events["deletions"] == [{"symbol": "OLD", "name": "Old Co"}]
    assert events["name_changes"] == [
        {"symbol": "AAPL", "previous_name": "Apple Inc.", "current_name": "Apple Incorporated"}
    ]
    assert events["inactive_symbols"] == [
        {"symbol": "HALT", "status": "inactive", "tradable": False, "name": "Halted Corp"}
    ]


def test_nasdaq_symbol_directory_parser_is_deterministic():
    text = "\n".join(
        [
            "Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot Size|ETF|NextShares",
            "AAPL|Apple Inc.|Q|N|N|100|N|N",
            "BNY|Bank of New York Mellon Corp|Q|N|N|100|N|N",
            "File Creation Time:0601202600:00|||||||",
        ]
    )

    rows = parse_nasdaq_symbol_directory(text, source="nasdaqlisted")
    assert [row["symbol"] for row in rows] == ["AAPL", "BNY"]
    assert rows[1]["security_name"] == "Bank of New York Mellon Corp"
