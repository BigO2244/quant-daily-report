from __future__ import annotations

import json
from pathlib import Path

from core.security_master import update_security_master
from research.universe_governance import build_universe_governance


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def _write_aliases(root: Path) -> None:
    _write_json(root / "data" / "security_master" / "manual_aliases.json", {"aliases": {"BK": "BNY"}, "notes": {}})


def _write_plan(root: Path, trade_date: str, symbols: list[str]) -> None:
    _write_json(
        root / "outputs" / "precompute" / trade_date / "planned_execution_payload.json",
        {"trades": [{"ticker": symbol, "side": "BUY", "shares": 1} for symbol in symbols]},
    )


def _write_holdings(root: Path, trade_date: str, symbols: list[str]) -> None:
    _write_json(
        root / "outputs" / "shadow_candidates" / trade_date / "comparison.json",
        {"strategies": {"caerus_lyra": {"holdings": [{"ticker": symbol, "target_weight": 1.0 / len(symbols)} for symbol in symbols]}}},
    )


def _write_master(root: Path, trade_date: str) -> None:
    update_security_master(
        asof_date=trade_date,
        root=root / "data" / "security_master",
        alpaca_assets=[
            {"symbol": "BNY", "name": "Bank of New York Mellon", "status": "active", "tradable": True, "exchange": "NYSE", "asset_class": "us_equity"},
            {"symbol": "AAPL", "name": "Apple", "status": "active", "tradable": True, "exchange": "NASDAQ", "asset_class": "us_equity"},
            {"symbol": "DEAD", "name": "Dead Co", "status": "inactive", "tradable": False, "exchange": "NYSE", "asset_class": "us_equity"},
        ],
        nasdaq_records=[
            {"symbol": "BNY", "security_name": "Bank of New York Mellon", "listing_exchange": "NYSE"},
            {"symbol": "AAPL", "security_name": "Apple", "listing_exchange": "NASDAQ"},
            {"symbol": "DEAD", "security_name": "Dead Co", "listing_exchange": "NYSE"},
        ],
    )


def test_universe_governance_recognizes_bk_alias(tmp_path):
    trade_date = "2026-06-02"
    _write_aliases(tmp_path)
    _write_master(tmp_path, trade_date)
    _write_plan(tmp_path, trade_date, ["BK"])
    _write_holdings(tmp_path, trade_date, ["AAPL"])

    payload = build_universe_governance(trade_date=trade_date, repo_root=tmp_path)

    assert payload["available"] is True
    assert payload["alias_resolutions"] == [
        {
            "original_symbol": "BK",
            "resolved_symbol": "BNY",
            "source": str(tmp_path / "data" / "security_master" / "manual_aliases.json"),
            "reason": "manual_alias:BK->BNY",
        }
    ]


def test_universe_governance_unknown_symbol_blocks(tmp_path):
    trade_date = "2026-06-02"
    _write_master(tmp_path, trade_date)
    _write_plan(tmp_path, trade_date, ["NOPE"])

    payload = build_universe_governance(trade_date=trade_date, repo_root=tmp_path)

    assert payload["available"] is False
    assert "planned:unknown_symbol:NOPE" in payload["blockers"]


def test_universe_governance_inactive_symbol_blocks(tmp_path):
    trade_date = "2026-06-02"
    _write_master(tmp_path, trade_date)
    _write_plan(tmp_path, trade_date, ["DEAD"])

    payload = build_universe_governance(trade_date=trade_date, repo_root=tmp_path)

    assert payload["available"] is False
    assert "planned:inactive_symbol:DEAD" in payload["blockers"]
    assert "planned:non_tradable_symbol:DEAD" in payload["blockers"]


def test_universe_governance_output_is_sorted(tmp_path):
    trade_date = "2026-06-02"
    _write_master(tmp_path, trade_date)
    _write_plan(tmp_path, trade_date, ["AAPL", "BNY"])
    _write_holdings(tmp_path, trade_date, ["BNY", "AAPL"])

    payload = build_universe_governance(trade_date=trade_date, repo_root=tmp_path)
    checks = [(row["context"], row["resolved_symbol"]) for row in payload["symbol_checks"]]

    assert checks == sorted(checks)
