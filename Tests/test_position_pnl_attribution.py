from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from research.attribution.position_pnl import build_position_attribution


def _write_holdings(root: Path, trade_date: str, strategies: dict) -> None:
    path = root / "outputs" / "portfolio_history" / trade_date / "holdings_snapshot.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": "test",
                "trade_date": trade_date,
                "strategies": strategies,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def _write_prices(root: Path, rows: list[dict[str, object]]) -> Path:
    path = root / "alpha_stack_cache" / "csv_export" / "prices_matrix.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = ["Date"]
    for row in rows:
        for key in row:
            if key != "Date" and key not in columns:
                columns.append(key)
    lines = [",".join(columns)]
    for row in rows:
        lines.append(",".join(str(row.get(col, "")) for col in columns))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _write_price_panel(root: Path, rows: list[dict[str, object]]) -> Path:
    path = root / "outputs" / "research" / "flow_detection_v1" / "price_panel.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(path, index=False)
    return path


def test_normal_attribution_calculation(tmp_path):
    trade_date = "2026-06-01"
    _write_holdings(
        tmp_path,
        trade_date,
        {
            "caerus_polaris": {
                "holdings": [
                    {"ticker": "AAA", "target_weight": 0.6},
                    {"ticker": "BBB", "target_weight": 0.4},
                ]
            }
        },
    )
    _write_prices(
        tmp_path,
        [
            {"Date": "2026-05-29", "AAA": 100, "BBB": 50},
            {"Date": "2026-06-01", "AAA": 110, "BBB": 45},
        ],
    )

    result = build_position_attribution(trade_date=trade_date, repo_root=tmp_path)
    summary = result["summary"]
    records = result["position_attribution"]["positions"]

    assert summary["total_positions_analyzed"] == 2
    assert summary["positions_with_complete_price_data"] == 2
    assert summary["aggregate_confidence"] == "HIGH"
    assert summary["reason_codes"] == ["ok"]
    assert summary["price_source"].endswith("prices_matrix.csv")
    assert summary["price_source_max_date"] == "2026-06-01"
    assert summary["attribution_date"] == trade_date
    assert summary["is_price_source_fresh"] is True
    assert summary["freshness_lag_days"] == 0
    assert summary["freshness_reason_codes"] == ["ok"]
    assert records[0]["symbol"] == "AAA"
    assert records[0]["return_pct"] == 0.1
    assert records[0]["pnl_contribution_pct"] == 0.06
    assert records[0]["rank"] == 1
    assert records[1]["symbol"] == "BBB"
    assert records[1]["pnl_contribution_pct"] == -0.04
    assert summary["top_contributor_per_strategy"]["caerus_polaris"]["symbol"] == "AAA"
    assert summary["top_detractor_per_strategy"]["caerus_polaris"]["symbol"] == "BBB"

    for name in (
        "attribution_summary.json",
        "position_attribution.json",
        "top_contributors.json",
        "top_detractors.json",
    ):
        json.loads((tmp_path / "outputs" / "attribution" / trade_date / name).read_text())


def test_missing_price_data_marks_partial_and_medium_confidence(tmp_path):
    trade_date = "2026-06-01"
    _write_holdings(
        tmp_path,
        trade_date,
        {
            "caerus_orion": {
                "holdings": [
                    {"ticker": "AAA", "target_weight": 1.0},
                    {"ticker": "MISSING", "target_weight": 0.5},
                ]
            }
        },
    )
    _write_prices(
        tmp_path,
        [
            {"Date": "2026-05-29", "AAA": 100},
            {"Date": "2026-06-01", "AAA": 101},
        ],
    )

    result = build_position_attribution(trade_date=trade_date, repo_root=tmp_path)
    summary = result["summary"]
    by_symbol = {
        row["symbol"]: row
        for row in result["position_attribution"]["positions"]
    }

    assert summary["positions_with_complete_price_data"] == 1
    assert summary["positions_missing_price_data"] == 1
    assert summary["aggregate_confidence"] == "MEDIUM"
    assert summary["reason_codes"] == ["missing_end_price", "missing_start_price"]
    assert summary["price_source_max_date"] == "2026-06-01"
    assert summary["is_price_source_fresh"] is True
    assert by_symbol["MISSING"]["data_completeness"] == "PARTIAL"
    assert by_symbol["MISSING"]["confidence"] == "MEDIUM"
    assert by_symbol["MISSING"]["return_pct"] is None
    assert by_symbol["MISSING"]["reason_codes"] == ["missing_end_price", "missing_start_price"]


def test_zero_holdings_writes_low_confidence_artifacts(tmp_path):
    trade_date = "2026-06-01"
    _write_holdings(
        tmp_path,
        trade_date,
        {"caerus_lyra": {"holdings": []}},
    )
    _write_prices(
        tmp_path,
        [
            {"Date": "2026-05-29", "AAA": 100},
            {"Date": "2026-06-01", "AAA": 101},
        ],
    )

    result = build_position_attribution(trade_date=trade_date, repo_root=tmp_path)
    summary = result["summary"]

    assert summary["total_positions_analyzed"] == 0
    assert summary["strategies_covered"] == []
    assert summary["aggregate_confidence"] == "LOW"
    assert "no_holdings" in summary["reason_codes"]
    assert result["position_attribution"]["positions"] == []


def test_deterministic_sorting_for_equal_contributions(tmp_path):
    trade_date = "2026-06-01"
    _write_holdings(
        tmp_path,
        trade_date,
        {
            "caerus_polaris": {
                "holdings": [
                    {"ticker": "CCC", "target_weight": 0.5},
                    {"ticker": "AAA", "target_weight": 0.5},
                    {"ticker": "BBB", "target_weight": 0.5},
                ]
            }
        },
    )
    _write_prices(
        tmp_path,
        [
            {"Date": "2026-05-29", "AAA": 100, "BBB": 100, "CCC": 100},
            {"Date": "2026-06-01", "AAA": 101, "BBB": 101, "CCC": 101},
        ],
    )

    result = build_position_attribution(trade_date=trade_date, repo_root=tmp_path)
    records = result["position_attribution"]["positions"]
    contributors = result["top_contributors"]["positions"]

    assert [row["symbol"] for row in records] == ["AAA", "BBB", "CCC"]
    assert [row["symbol"] for row in contributors] == ["AAA", "BBB", "CCC"]
    assert [row["rank"] for row in records] == [1, 2, 3]


def test_missing_sources_fail_gracefully(tmp_path):
    trade_date = "2026-06-01"
    result = build_position_attribution(trade_date=trade_date, repo_root=tmp_path)
    summary = result["summary"]

    assert summary["total_positions_analyzed"] == 0
    assert summary["aggregate_confidence"] == "LOW"
    assert "holdings_source_missing" in summary["reason_codes"]
    assert "price_source_missing" in summary["reason_codes"]
    assert summary["price_source"] is None
    assert summary["price_source_max_date"] is None
    assert summary["is_price_source_fresh"] is False
    assert summary["freshness_lag_days"] is None
    assert summary["freshness_reason_codes"] == ["price_source_missing"]


def test_prefers_fresh_canonical_price_panel_over_stale_legacy_csv(tmp_path):
    trade_date = "2026-06-01"
    _write_holdings(
        tmp_path,
        trade_date,
        {"caerus_polaris": {"holdings": [{"ticker": "AAA", "target_weight": 1.0}]}},
    )
    _write_prices(
        tmp_path,
        [
            {"Date": "2026-05-29", "AAA": 10},
            {"Date": "2026-05-30", "AAA": 10},
        ],
    )
    panel_path = _write_price_panel(
        tmp_path,
        [
            {"date": "2026-05-29", "ticker": "AAA", "close": 100.0},
            {"date": "2026-06-01", "ticker": "AAA", "close": 105.0},
        ],
    )

    result = build_position_attribution(trade_date=trade_date, repo_root=tmp_path)
    summary = result["summary"]
    record = result["position_attribution"]["positions"][0]

    assert summary["price_source"] == str(panel_path)
    assert summary["price_source_max_date"] == "2026-06-01"
    assert summary["is_price_source_fresh"] is True
    assert summary["freshness_lag_days"] == 0
    assert record["start_price"] == 100.0
    assert record["end_price"] == 105.0
    assert record["return_pct"] == 0.05


def test_stale_price_source_reports_freshness_metadata(tmp_path):
    trade_date = "2026-06-01"
    _write_holdings(
        tmp_path,
        trade_date,
        {"caerus_polaris": {"holdings": [{"ticker": "AAA", "target_weight": 1.0}]}},
    )
    _write_price_panel(
        tmp_path,
        [
            {"date": "2026-05-28", "ticker": "AAA", "close": 90.0},
            {"date": "2026-05-29", "ticker": "AAA", "close": 100.0},
        ],
    )

    result = build_position_attribution(trade_date=trade_date, repo_root=tmp_path)
    summary = result["summary"]
    record = result["position_attribution"]["positions"][0]

    assert summary["price_source_max_date"] == "2026-05-29"
    assert summary["is_price_source_fresh"] is False
    assert summary["freshness_lag_days"] == 3
    assert summary["freshness_reason_codes"] == ["price_source_stale"]
    assert "price_source_stale" in summary["reason_codes"]
    assert "missing_end_price" in summary["reason_codes"]
    assert record["start_price"] == 100.0
    assert record["end_price"] is None
