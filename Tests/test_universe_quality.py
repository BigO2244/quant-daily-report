from __future__ import annotations

import csv
import json
from pathlib import Path

from research_registry.research.universe_quality import build_universe_quality


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def test_universe_quality_current_universe_parses(tmp_path: Path) -> None:
    _write_csv(
        tmp_path / "data" / "universe.csv",
        [{"ticker": "AAA", "sector": "Tech"}, {"ticker": "BBB", "sector": "Health"}],
    )
    _write_csv(
        tmp_path / "prices.csv",
        [{"Date": "2026-06-02", "AAA": "10", "BBB": "20"}],
    )

    payload = build_universe_quality(trade_date="2026-06-02", repo_root=tmp_path, price_path=tmp_path / "prices.csv")

    assert payload["current_universe_size"] == 2
    assert payload["unique_symbol_count"] == 2
    assert payload["price_coverage"]["coverage_ratio"] == 1.0
    assert (tmp_path / "outputs" / "model_quality" / "2026-06-02" / "universe_quality.json").exists()


def test_universe_quality_missing_file_degrades_visibly(tmp_path: Path) -> None:
    payload = build_universe_quality(trade_date="2026-06-02", repo_root=tmp_path)

    assert payload["status"] == "NO_DATA"
    assert "UNIVERSE_FILE_MISSING" in payload["reason_codes"]
    assert payload["price_coverage"]["reason_codes"] == ["PRICE_SOURCE_MISSING"]


def test_universe_quality_duplicate_symbols_detected(tmp_path: Path) -> None:
    _write_csv(
        tmp_path / "data" / "universe.csv",
        [{"ticker": "BBB", "sector": "Health"}, {"ticker": "AAA", "sector": "Tech"}, {"ticker": "AAA", "sector": "Tech"}],
    )
    _write_csv(tmp_path / "prices.csv", [{"Date": "2026-06-02", "AAA": "10", "BBB": "20"}])

    payload = build_universe_quality(trade_date="2026-06-02", repo_root=tmp_path, price_path=tmp_path / "prices.csv")

    assert payload["duplicate_symbols"] == ["AAA"]
    assert "DUPLICATE_UNIVERSE_SYMBOLS" in payload["reason_codes"]


def test_universe_quality_stale_alias_detection(tmp_path: Path) -> None:
    _write_csv(tmp_path / "data" / "universe.csv", [{"ticker": "BK", "sector": "Financials"}])
    _write_csv(tmp_path / "prices.csv", [{"Date": "2026-06-02", "BK": "10"}])
    _write_json(tmp_path / "data" / "security_master" / "manual_aliases.json", {"aliases": {"BK": "BNY"}})

    payload = build_universe_quality(trade_date="2026-06-02", repo_root=tmp_path, price_path=tmp_path / "prices.csv")

    assert payload["alias_issues"] == [
        {
            "original_symbol": "BK",
            "resolved_symbol": "BNY",
            "reason": "manual_alias_present_in_universe:BK->BNY",
        }
    ]
    assert "STALE_ALIAS_SYMBOLS" in payload["reason_codes"]


def test_universe_quality_price_coverage_respects_ticker_exceptions(tmp_path: Path) -> None:
    _write_csv(
        tmp_path / "data" / "universe.csv",
        [{"ticker": "AAA", "sector": "Tech"}, {"ticker": "MMC", "sector": "Financials"}],
    )
    _write_csv(tmp_path / "prices.csv", [{"Date": "2026-06-02", "AAA": "10"}])
    _write_json(tmp_path / "data" / "ticker_exceptions.json", {"ignore": ["MMC"], "aliases": {"BK": "BNY"}})

    payload = build_universe_quality(trade_date="2026-06-02", repo_root=tmp_path, price_path=tmp_path / "prices.csv")

    assert payload["current_universe_size"] == 2
    assert payload["price_coverage"]["coverage_universe_size"] == 1
    assert payload["price_coverage"]["ignored_symbols"] == ["MMC"]
    assert payload["price_coverage"]["coverage_ratio"] == 1.0
    assert "PRICE_COVERAGE_MISSING_SYMBOLS" not in payload["reason_codes"]


def test_universe_quality_sector_output_is_deterministic(tmp_path: Path) -> None:
    _write_csv(
        tmp_path / "data" / "universe.csv",
        [{"ticker": "CCC", "sector": "Utilities"}, {"ticker": "AAA", "sector": "Tech"}, {"ticker": "BBB", "sector": "Health"}],
    )
    _write_csv(tmp_path / "prices.csv", [{"Date": "2026-06-02", "AAA": "10", "BBB": "20", "CCC": "30"}])

    payload = build_universe_quality(trade_date="2026-06-02", repo_root=tmp_path, price_path=tmp_path / "prices.csv")

    sectors = [row["sector"] for row in payload["sector_coverage"]["sectors"]]
    assert sectors == ["Health", "Tech", "Utilities"]
