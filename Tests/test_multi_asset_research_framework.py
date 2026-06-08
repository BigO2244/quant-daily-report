from __future__ import annotations

import csv
import json
from pathlib import Path

from research_registry.research.multi_asset_research_framework import build_multi_asset_research_framework


def _write_price_matrix(path: Path, symbols: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["Date"] + symbols)
        writer.writeheader()
        writer.writerow({"Date": "2026-06-08", **{symbol: "100" for symbol in symbols}})


def _sleeve(payload: dict, sleeve_id: str) -> dict:
    return next(row for row in payload["candidate_sleeves"] if row["sleeve_id"] == sleeve_id)


def test_multi_asset_candidate_sleeves_present(tmp_path: Path) -> None:
    _write_price_matrix(tmp_path / "alpha_stack_cache" / "csv_export" / "prices_matrix.csv", ["SHY", "IEF", "TLT", "GLD", "DBMF"])

    payload = build_multi_asset_research_framework(trade_date="2026-06-08", repo_root=tmp_path)

    sleeve_ids = {row["sleeve_id"] for row in payload["candidate_sleeves"]}
    assert {"treasury_duration", "cash_tbill", "gold", "broad_commodities", "managed_futures_proxy", "defensive_equity_proxy", "options_overlay"}.issubset(sleeve_ids)
    assert _sleeve(payload, "treasury_duration")["available_symbols"] == ["SHY", "IEF", "TLT"]
    assert "GLD" in _sleeve(payload, "gold")["available_symbols"]


def test_multi_asset_missing_data_degrades(tmp_path: Path) -> None:
    payload = build_multi_asset_research_framework(trade_date="2026-06-08", repo_root=tmp_path)

    assert payload["status"] == "PARTIAL"
    assert payload["missing_data"]
    assert "PRICE_SOURCE_MISSING" in payload["reason_codes"]


def test_multi_asset_options_marked_deferred(tmp_path: Path) -> None:
    payload = build_multi_asset_research_framework(trade_date="2026-06-08", repo_root=tmp_path)

    assert payload["options_status"] == "DEFERRED_DESIGN_ONLY"
    assert _sleeve(payload, "options_overlay")["status"] == "DEFERRED_DESIGN_ONLY"


def test_multi_asset_has_no_execution_integration(tmp_path: Path) -> None:
    payload = build_multi_asset_research_framework(trade_date="2026-06-08", repo_root=tmp_path)

    assert payload["execution_impact"] == "NON_EXECUTIONAL"
    assert payload["integration_scope"]["execution_integration"] is False
    assert payload["integration_scope"]["broker_submission"] is False
    assert payload["integration_scope"]["order_generation"] is False


def test_multi_asset_deterministic_output(tmp_path: Path) -> None:
    _write_price_matrix(tmp_path / "alpha_stack_cache" / "csv_export" / "prices_matrix.csv", ["TLT", "SHY", "GLD"])

    first = build_multi_asset_research_framework(trade_date="2026-06-08", repo_root=tmp_path, write=False)
    second = build_multi_asset_research_framework(trade_date="2026-06-08", repo_root=tmp_path, write=False)

    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
