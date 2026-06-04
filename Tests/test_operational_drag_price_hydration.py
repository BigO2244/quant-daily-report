from __future__ import annotations

import csv
import json
from pathlib import Path

from research.operational_drag import build_operational_drag_analysis
from scripts.hydrate_operational_drag_prices import build_price_hydration

TRADE_DATE = "2026-06-02"


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _fixture_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    _write_json(
        root / "outputs" / "precompute" / "2026-06-01" / "daily_snapshot.json",
        {
            "asof": "2026-06-01 00:00:00",
            "equity": 10000.0,
            "target_cash_weight": 0.10,
            "orders": [
                {"ticker": "AAA", "target_weight": 0.50, "execution_price": 100.0},
                {"ticker": "BBB", "target_weight": 0.40, "execution_price": 50.0},
            ],
        },
    )
    _write_json(
        root / "outputs" / "precompute" / "2026-06-01" / "planned_execution_payload.json",
        {
            "trade_date": "2026-06-01",
            "run_id": "2026-06-01:main:caerus_polaris",
            "equity": 10000.0,
            "trades": [
                {"ticker": "AAA", "side": "BUY", "shares": 50, "entry_price": 100.0, "notional": 5000.0},
                {"ticker": "BBB", "side": "BUY", "shares": 80, "entry_price": 50.0, "notional": 4000.0},
            ],
        },
    )
    _write_csv(
        root / "outputs" / "perf" / "live_overlay_nav_series.csv",
        [
            {
                "date": "2026-06-01",
                "equity": 10000.0,
                "cash": 5000.0,
                "gross_exposure": 0.50,
                "net_exposure": 0.50,
            },
            {
                "date": "2026-06-02",
                "equity": 10050.0,
                "cash": 5000.0,
                "gross_exposure": 0.50,
                "net_exposure": 0.50,
            },
        ],
    )
    _write_json(
        root / "outputs" / "broker_snapshot" / "broker_snapshot_2026-06-02.json",
        {
            "trade_date": "2026-06-02",
            "portfolio_value": "10050.0",
            "cash": "5000.0",
            "positions_current": [
                {"symbol": "AAA", "qty": "20", "current_price": "110", "market_value": "2200"},
                {"symbol": "BBB", "qty": "30", "current_price": "45", "market_value": "1350"},
            ],
        },
    )
    _write_csv(
        root / "outputs" / "perf" / "live_overlay_benchmark_close_history.csv",
        [{"date": "2026-06-01", "spy_close": 100.0, "spy_return": ""}],
    )
    return root


def _fixture_price_csv(path: Path, rows: list[dict]) -> Path:
    _write_csv(path, rows)
    return path


def test_hydration_command_writes_expected_metadata(tmp_path: Path) -> None:
    root = _fixture_repo(tmp_path)
    fixture_prices = _fixture_price_csv(
        tmp_path / "prices.csv",
        [
            {"date": TRADE_DATE, "symbol": "AAA", "close": 110.0},
            {"date": TRADE_DATE, "symbol": "BBB", "close": 45.0},
            {"date": TRADE_DATE, "symbol": "SPY", "close": 101.0},
        ],
    )

    payload = build_price_hydration(trade_date=TRADE_DATE, repo_root=root, fixture_prices=fixture_prices)

    out_dir = root / "outputs" / "operational_drag" / TRADE_DATE
    assert (out_dir / "price_hydration.json").exists()
    assert (out_dir / "price_hydration.md").exists()
    assert payload["available"] is True
    assert payload["symbols_hydrated"] == ["AAA", "BBB", "SPY"]
    assert payload["missing_symbols"] == []
    assert payload["date_range"] == {"start": TRADE_DATE, "end": TRADE_DATE}


def test_operational_drag_uses_date_scoped_hydrated_prices(tmp_path: Path) -> None:
    root = _fixture_repo(tmp_path)
    fixture_prices = _fixture_price_csv(
        tmp_path / "prices.csv",
        [
            {"date": TRADE_DATE, "symbol": "AAA", "close": 110.0},
            {"date": TRADE_DATE, "symbol": "BBB", "close": 45.0},
            {"date": TRADE_DATE, "symbol": "SPY", "close": 101.0},
        ],
    )
    build_price_hydration(trade_date=TRADE_DATE, repo_root=root, fixture_prices=fixture_prices)

    analysis = build_operational_drag_analysis(trade_date=TRADE_DATE, repo_root=root, write=False)

    assert analysis["available"] is True
    assert "missing_spy_price:2026-06-02" not in analysis["reason_codes"]
    assert "missing_price:AAA" not in analysis["reason_codes"]
    assert "missing_price:BBB" not in analysis["reason_codes"]
    assert analysis["operational_drag"]["latest"]["daily_operational_drag"] == 0.005
    assert any(
        source.endswith("outputs/operational_drag/2026-06-02/price_hydration.json")
        for source in analysis["source_diagnostics"]["price"]["selected_paths"]
    )


def test_spy_price_is_included_when_available(tmp_path: Path) -> None:
    root = _fixture_repo(tmp_path)
    fixture_prices = _fixture_price_csv(
        tmp_path / "prices.csv",
        [
            {"date": TRADE_DATE, "symbol": "AAA", "close": 110.0},
            {"date": TRADE_DATE, "symbol": "BBB", "close": 45.0},
            {"date": TRADE_DATE, "symbol": "SPY", "close": 101.0},
        ],
    )

    payload = build_price_hydration(trade_date=TRADE_DATE, repo_root=root, fixture_prices=fixture_prices)

    spy_rows = [row for row in payload["prices"] if row["symbol"] == "SPY"]
    assert spy_rows == [{"date": TRADE_DATE, "symbol": "SPY", "close": 101.0, "source": str(fixture_prices)}]


def test_missing_symbols_remain_explicit_reason_codes(tmp_path: Path) -> None:
    root = _fixture_repo(tmp_path)
    fixture_prices = _fixture_price_csv(
        tmp_path / "prices.csv",
        [
            {"date": TRADE_DATE, "symbol": "AAA", "close": 110.0},
            {"date": TRADE_DATE, "symbol": "SPY", "close": 101.0},
        ],
    )

    payload = build_price_hydration(trade_date=TRADE_DATE, repo_root=root, fixture_prices=fixture_prices)

    assert payload["missing_symbols"] == ["BBB"]
    assert "missing_price:BBB" in payload["reason_codes"]


def test_no_forward_fill_or_fabrication_for_missing_trade_date_prices(tmp_path: Path) -> None:
    root = _fixture_repo(tmp_path)
    fixture_prices = _fixture_price_csv(
        tmp_path / "prices.csv",
        [
            {"date": "2026-06-01", "symbol": "AAA", "close": 100.0},
            {"date": "2026-06-01", "symbol": "BBB", "close": 50.0},
            {"date": "2026-06-01", "symbol": "SPY", "close": 100.0},
        ],
    )

    payload = build_price_hydration(trade_date=TRADE_DATE, repo_root=root, fixture_prices=fixture_prices)

    assert payload["prices"] == []
    assert payload["missing_symbols"] == ["AAA", "BBB", "SPY"]
    assert "price_hydration_empty" in payload["reason_codes"]


def test_stale_csv_fallback_is_lower_priority_than_date_scoped_hydration(tmp_path: Path) -> None:
    root = _fixture_repo(tmp_path)
    _write_csv(
        root / "outputs" / "prices" / "close_history.csv",
        [
            {"date": TRADE_DATE, "symbol": "AAA", "close": 999.0},
            {"date": TRADE_DATE, "symbol": "BBB", "close": 45.0},
            {"date": TRADE_DATE, "symbol": "SPY", "close": 101.0},
        ],
    )
    fixture_prices = _fixture_price_csv(
        tmp_path / "prices.csv",
        [
            {"date": TRADE_DATE, "symbol": "AAA", "close": 110.0},
            {"date": TRADE_DATE, "symbol": "BBB", "close": 45.0},
            {"date": TRADE_DATE, "symbol": "SPY", "close": 101.0},
        ],
    )
    build_price_hydration(trade_date=TRADE_DATE, repo_root=root, fixture_prices=fixture_prices)

    analysis = build_operational_drag_analysis(trade_date=TRADE_DATE, repo_root=root, write=False)

    assert analysis["intended_nav"]["intended_equity_value"] == 10100.0
    assert analysis["intended_nav"]["intended_positions"][0]["price"] == 110.0
