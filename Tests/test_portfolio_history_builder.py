from __future__ import annotations

import json
from pathlib import Path

from scripts.build_portfolio_history import build_portfolio_history


def test_portfolio_history_prefers_broker_fills_and_positions(tmp_path: Path) -> None:
    snapshot_dir = tmp_path / "outputs" / "broker_snapshot"
    perf_dir = tmp_path / "outputs" / "perf"
    snapshot_dir.mkdir(parents=True)
    perf_dir.mkdir(parents=True)

    (snapshot_dir / "broker_snapshot_2026-04-08.json").write_text(
        json.dumps(
            {
                "meta": {"report_date": "2026-04-08"},
                "account": {"equity": "10000.00", "cash": "2500.00"},
                "fills_report_date": [
                    {
                        "id": "fill-1",
                        "order_id": "order-1",
                        "symbol": "MSFT",
                        "side": "buy",
                        "qty": "2",
                        "price": "300.50",
                        "transaction_time": "2026-04-08T13:35:00Z",
                    },
                    {
                        "id": "fill-2",
                        "order_id": "order-2",
                        "symbol": "NVDA",
                        "side": "sell",
                        "qty": "1",
                        "price": "900",
                        "transaction_time": "2026-04-08T13:36:00Z",
                    },
                ],
                "positions_current": [
                    {
                        "symbol": "MSFT",
                        "qty": "2",
                        "current_price": "301",
                        "market_value": "602",
                        "cost_basis": "601",
                        "unrealized_pl": "1",
                        "unrealized_plpc": "0.00166",
                    },
                    {
                        "symbol": "AAPL",
                        "qty": "3",
                        "current_price": "200",
                        "market_value": "600",
                        "cost_basis": "590",
                        "unrealized_pl": "10",
                        "unrealized_plpc": "0.01695",
                    },
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (perf_dir / "live_overlay_nav_series.csv").write_text(
        "date,equity,cash,gross_exposure,net_exposure,return_1d,turnover_dollars,turnover_pct,turnover\n"
        "2026-04-07,9900,2600,0.70,0.70,,,,\n"
        "2026-04-08,10000,2500,0.75,0.75,,,,\n",
        encoding="utf-8",
    )

    payload = build_portfolio_history(tmp_path, report_date="2026-04-08")

    assert payload["summary"]["counts"]["transactions"] == 2
    assert payload["summary"]["counts"]["positions"] == 2
    assert payload["summary"]["counts"]["nav_rows"] == 2
    assert payload["transactions"][0]["source"] == "broker_fill"
    assert payload["transactions"][1]["signed_notional"] == -900.0
    assert payload["positions"][0]["ticker"] == "MSFT"
    assert payload["positions"][0]["weight"] == 0.0602
    assert round(payload["nav"][-1]["return_1d"], 6) == round((10000 / 9900) - 1, 6)
    assert payload["attribution"][0]["ticker"] == "MSFT"
    assert payload["attribution"][0]["buy_count"] == 1

    out_dir = tmp_path / "outputs" / "portfolio_history"
    assert (out_dir / "transactions.csv").exists()
    assert (out_dir / "positions.csv").exists()
    assert (out_dir / "nav.csv").exists()
    assert (out_dir / "attribution.csv").exists()
    assert json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))["paths"]["nav_source"] == "outputs/perf/live_overlay_nav_series.csv"

