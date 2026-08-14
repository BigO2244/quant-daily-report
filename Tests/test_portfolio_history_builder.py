from __future__ import annotations

import json
from pathlib import Path

from scripts.build_portfolio_history import build_portfolio_history


def test_portfolio_history_prefers_broker_fills_and_positions(tmp_path: Path) -> None:
    snapshot_dir = tmp_path / "outputs" / "broker_snapshot"
    perf_dir = tmp_path / "outputs" / "perf"
    ledger_dir = tmp_path / "outputs" / "ledger" / "paper"
    snapshot_dir.mkdir(parents=True)
    perf_dir.mkdir(parents=True)
    ledger_dir.mkdir(parents=True)

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
    (ledger_dir / "daily_nav.csv").write_text(
        "date,equity,profit_loss,profit_loss_pct,base_value,source,pulled_at_utc\n"
        "2026-04-07,9900,,,,alpaca_portfolio_history,2026-04-08T23:15:00Z\n"
        "2026-04-08,10000,,,,alpaca_portfolio_history,2026-04-08T23:15:00Z\n",
        encoding="utf-8",
    )
    (ledger_dir / "daily_state_latest.json").write_text(
        json.dumps(
            {
                "days": [
                    {"date": "2026-04-07", "cash": 2600, "positions_market_value": 7300},
                    {"date": "2026-04-08", "cash": 2500, "positions_market_value": 7500},
                ]
            }
        )
        + "\n",
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
    assert json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))["paths"]["nav_source"] == "outputs/ledger/paper/daily_nav.csv"


# --------------------------------------------------------------------------- #
# FR-066 extensions: benchmark columns, append-only guard, checksum manifest
# --------------------------------------------------------------------------- #
def _seed_perf(tmp_path: Path) -> None:
    perf_dir = tmp_path / "outputs" / "perf"
    ledger_dir = tmp_path / "outputs" / "ledger" / "paper"
    perf_dir.mkdir(parents=True, exist_ok=True)
    ledger_dir.mkdir(parents=True, exist_ok=True)
    (ledger_dir / "daily_nav.csv").write_text(
        "date,equity,profit_loss,profit_loss_pct,base_value,source,pulled_at_utc\n"
        "2026-03-03,10000,,,,alpaca_portfolio_history,2026-03-05T23:15:00Z\n"
        "2026-03-04,10100,,,,alpaca_portfolio_history,2026-03-05T23:15:00Z\n"
        "2026-03-05,10050,,,,alpaca_portfolio_history,2026-03-05T23:15:00Z\n",
        encoding="utf-8",
    )
    (perf_dir / "live_overlay_benchmark_close_history.csv").write_text(
        "date,spy_close,spy_return\n"
        "2026-03-03,680,\n"
        "2026-03-04,683.4,0.005\n"
        "2026-03-05,681.0,-0.00351\n",
        encoding="utf-8",
    )


def test_benchmark_columns_and_checksum_manifest(tmp_path: Path) -> None:
    _seed_perf(tmp_path)
    payload = build_portfolio_history(tmp_path, report_date="2026-03-05")

    nav = {row["date"]: row for row in payload["nav"]}
    assert nav["2026-03-03"]["benchmark_nav"] == 10000.0  # indexed to inception equity
    # excess = port_ret - spy_ret on 03-04
    assert round(nav["2026-03-04"]["excess_return_1d"], 6) == round(0.01 - 0.005, 6)
    # rolling beta needs >= 30 obs; only 3 here -> null
    assert nav["2026-03-05"]["rolling_beta_60d"] is None
    assert nav["2026-03-05"]["beta_adjusted_excess_1d"] is None

    summary = payload["summary"]
    assert summary["scoreboard"]["scoreboard_metric"] == "beta_adjusted_excess_information_ratio_vs_spy"
    assert "cumulative_excess_return" in summary["scoreboard"]
    assert summary["paths"]["benchmark_source"] == "outputs/perf/live_overlay_benchmark_close_history.csv"

    manifest_path = tmp_path / "outputs" / "portfolio_history" / "checksum_manifest.json"
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert "nav" in manifest["artifacts"]
    assert manifest["artifacts"]["nav"]["sha256"]


def test_append_only_preserves_immutable_rows_and_flags_restatements(tmp_path: Path) -> None:
    _seed_perf(tmp_path)
    out_dir = tmp_path / "outputs" / "portfolio_history"
    out_dir.mkdir(parents=True, exist_ok=True)
    # Pre-existing canonical series: an older immutable date + a conflicting equity.
    (out_dir / "nav.csv").write_text(
        "date,equity,cash,gross_exposure,net_exposure,return_1d,turnover_dollars,turnover_pct,"
        "cumulative_return,spy_close,spy_return_1d,benchmark_nav,excess_return_1d,rolling_beta_60d,"
        "beta_adjusted_excess_1d,source\n"
        "2026-02-27,9990,,,,,,,,,,,,,,broker_backfill\n"     # immutable older row
        "2026-03-04,9700,,,,,,,,,,,,,,broker_backfill\n",     # conflicts with candidate 10100
        encoding="utf-8",
    )
    payload = build_portfolio_history(tmp_path, report_date="2026-03-05")
    nav = {row["date"]: row for row in payload["nav"]}

    # Older immutable row is preserved (never dropped).
    assert "2026-02-27" in nav
    # Conflicting date keeps the canonical (broker) equity, not the candidate.
    assert float(nav["2026-03-04"]["equity"]) == 9700.0
    # Restatement candidate is recorded, not silently applied.
    rc = payload["summary"]["restatement_candidates"]
    assert any(r["date"] == "2026-03-04" and r["candidate_equity"] == 10100.0 for r in rc)


def test_append_only_can_be_disabled(tmp_path: Path) -> None:
    _seed_perf(tmp_path)
    out_dir = tmp_path / "outputs" / "portfolio_history"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "nav.csv").write_text(
        "date,equity,source\n2026-02-27,9990,broker_backfill\n", encoding="utf-8"
    )
    payload = build_portfolio_history(tmp_path, report_date="2026-03-05", append_only=False)
    nav_dates = {row["date"] for row in payload["nav"]}
    assert "2026-02-27" not in nav_dates  # not merged when append_only is off
