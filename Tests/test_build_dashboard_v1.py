from __future__ import annotations

import json
from pathlib import Path

from scripts.research.build_dashboard_v1 import DashboardV1Builder


def test_build_dashboard_v1_happy_path(tmp_path: Path) -> None:
    broker_dir = tmp_path / "outputs" / "broker"
    snapshot_dir = tmp_path / "outputs" / "broker_snapshot"
    perf_dir = tmp_path / "outputs" / "perf"
    broker_dir.mkdir(parents=True)
    snapshot_dir.mkdir(parents=True)
    perf_dir.mkdir(parents=True)

    report_date = "2026-04-21"
    captured_at = "2026-04-21T14:05:00+00:00"

    (broker_dir / "broker_snapshot_latest.json").write_text(
        json.dumps(
            {
                "trade_date": report_date,
                "captured_at": captured_at,
                "trust_level": "authoritative",
                "cash": "2500.00",
                "equity": "10000.00",
                "buying_power": "20000.00",
                "last_equity": "9900.00",
                "market_value": 7500.00,
                "account": {
                    "cash": "2500.00",
                    "equity": "10000.00",
                    "buying_power": "20000.00",
                    "last_equity": "9900.00",
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (broker_dir / "posttrade_positions.json").write_text(
        json.dumps(
            {
                "trade_date": report_date,
                "captured_at": captured_at,
                "positions_count": 2,
                "positions": [
                    {
                        "symbol": "AAPL",
                        "side": "long",
                        "qty": "10",
                        "market_value": "4000",
                        "current_price": "400",
                        "cost_basis": "3900",
                        "unrealized_pl": "100",
                        "unrealized_plpc": "0.0256",
                    },
                    {
                        "symbol": "MSFT",
                        "side": "long",
                        "qty": "5",
                        "market_value": "3500",
                        "current_price": "700",
                        "cost_basis": "3400",
                        "unrealized_pl": "100",
                        "unrealized_plpc": "0.0294",
                    },
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (snapshot_dir / f"broker_snapshot_{report_date}.json").write_text(
        json.dumps(
            {
                "meta": {
                    "generated_at": captured_at,
                    "report_date": report_date,
                },
                "fills_report_date": [
                    {
                        "id": "fill-1",
                        "symbol": "AAPL",
                        "side": "buy",
                        "qty": "10",
                        "price": "400",
                        "order_id": "order-1",
                        "transaction_time": "2026-04-21T09:35:00-04:00",
                    },
                    {
                        "id": "fill-2",
                        "symbol": "MSFT",
                        "side": "sell",
                        "qty": "2",
                        "price": "700",
                        "order_id": "order-2",
                        "transaction_time": "2026-04-21T09:36:00-04:00",
                    },
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (perf_dir / "live_overlay_nav_series.csv").write_text(
        "date,equity,cash,gross_exposure,net_exposure,return_1d,turnover_dollars,turnover_pct,turnover\n"
        "2026-04-20,9900,2600,0.73,0.73,0.01,0,0,0\n"
        "2026-04-21,10000,2500,0.75,0.75,0.0101010101,0,0,0\n",
        encoding="utf-8",
    )
    (perf_dir / "live_overlay_benchmark_close_history.csv").write_text(
        "date,spy_close,spy_return\n"
        "2026-04-20,500,\n"
        "2026-04-21,505,0.01\n",
        encoding="utf-8",
    )

    payload = DashboardV1Builder(tmp_path, report_date=report_date).build()

    assert payload["status"]["level"] == "ok"
    assert payload["schema_version"] == "dashboard-v2-prototype"
    assert payload["sections"]["nav"]["equity"] == 10000.0
    assert payload["sections"]["positions"]["summary"]["positions_count"] == 2
    assert payload["sections"]["trades_today"]["summary"]["fills_count"] == 2
    assert payload["sections"]["performance_history"]["summary"]["latest_nav"] == 10000.0
    assert payload["terminal"]["headline"]["nav"] == 10000.0
    assert payload["terminal"]["benchmark"]["rolling_5d_return"] is None
    assert payload["terminal"]["leaders"]["winners"][0]["ticker"] == "AAPL"
