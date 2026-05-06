import json
import tempfile
import unittest
from pathlib import Path

from scripts.export_alpaca_broker_snapshot import (
    build_snapshot_payload,
    load_env_file,
    write_posttrade_recon_from_snapshot,
    write_supporting_broker_artifacts,
    write_snapshot_json,
)


def test_build_snapshot_payload_shape_and_redaction():
    account = {
        "id": "acct-1",
        "status": "ACTIVE",
        "cash": "1000.00",
        "equity": "1200.00",
        "raw": {"buying_power": "5000.00"},
    }
    positions = [
        {"symbol": "ZZZ", "qty": "1", "side": "long", "raw": {"market_value": "10.00"}},
        {"symbol": "AAA", "qty": "2", "side": "long", "raw": {"market_value": "20.00"}},
    ]
    orders_all = [
        {
            "id": "ord-old",
            "client_order_id": "cid-old",
            "symbol": "OLD",
            "side": "sell",
            "status": "filled",
            "submitted_at": "2026-03-09T14:00:00Z",
            "raw": {"filled_at": "2026-03-09T14:00:10Z", "qty": "1"},
        },
        {
            "id": "ord-today",
            "client_order_id": "cid-today",
            "symbol": "MPC",
            "side": "buy",
            "status": "filled",
            "submitted_at": "2026-03-10T14:00:00Z",
            "raw": {"filled_at": "2026-03-10T14:00:10Z", "qty": "2", "filled_qty": "2"},
        },
    ]
    orders_closed = list(orders_all)
    fills = [
        {
            "id": "fill-1",
            "activity_type": "FILL",
            "symbol": "MPC",
            "qty": "2",
            "price": "101.50",
            "transaction_time": "2026-03-10T14:00:11Z",
            "raw": {"order_id": "ord-today", "side": "buy"},
        }
    ]

    payload = build_snapshot_payload(
        report_date="2026-03-10",
        workflow_run_id="123456",
        git_sha="deadbeef",
        account=account,
        positions=positions,
        orders_all=orders_all,
        orders_closed=orders_closed,
        fills=fills,
    )

    assert set(payload.keys()) == {
        "meta",
        "account",
        "positions_current",
        "orders_report_date",
        "orders_closed_recent",
        "fills_report_date",
        "counts",
    }
    assert payload["meta"]["report_date"] == "2026-03-10"
    assert payload["meta"]["workflow_run_id"] == "123456"
    assert payload["account"]["id"] == "acct-1"
    assert [p["symbol"] for p in payload["positions_current"]] == ["AAA", "ZZZ"]
    assert len(payload["orders_report_date"]) == 1
    assert payload["orders_report_date"][0]["id"] == "ord-today"
    assert payload["counts"]["orders_report_date"] == 1

    text = json.dumps(payload)
    assert '"raw"' not in text
    assert "ALPACA_API_SECRET_KEY" not in text


def test_write_snapshot_json_uses_deterministic_filename(tmp_path):
    payload = {"meta": {"report_date": "2026-03-10"}}
    out_path = write_snapshot_json(payload, tmp_path, "2026-03-10")
    assert out_path == tmp_path / "broker_snapshot_2026-03-10.json"
    assert out_path.exists()


class ExportAlpacaBrokerSnapshotCompatTest(unittest.TestCase):
    def test_load_env_file_accepts_export_syntax(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            env_path = Path(tmp_dir) / "alpaca.env"
            env_path.write_text(
                "export ALPACA_API_KEY_ID='key123'\nALPACA_API_SECRET_KEY=\"secret456\"\n",
                encoding="utf-8",
            )
            load_env_file(env_path)
            self.assertEqual("key123", __import__("os").environ.get("ALPACA_API_KEY_ID"))
            self.assertEqual("secret456", __import__("os").environ.get("ALPACA_API_SECRET_KEY"))

    def test_write_supporting_broker_artifacts_populates_monitor_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            repo_root = Path(tmp_dir)
            payload = build_snapshot_payload(
                report_date="2026-04-08",
                workflow_run_id="run-1",
                git_sha="deadbeef",
                account={"cash": "1500.5", "equity": "9700.0", "buying_power": "11000.0", "last_equity": "9600.0"},
                positions=[{"symbol": "AAPL", "qty": "2", "market_value": "400.0"}],
                orders_all=[],
                orders_closed=[],
                fills=[],
            )
            paths = write_supporting_broker_artifacts(repo_root=repo_root, payload=payload, source_mode="alpaca_rest_api")
            account_snapshot = json.loads(paths["posttrade_account_snapshot"].read_text(encoding="utf-8"))
            positions_snapshot = json.loads(paths["posttrade_positions"].read_text(encoding="utf-8"))
            latest_snapshot = json.loads(paths["broker_snapshot_latest"].read_text(encoding="utf-8"))

            self.assertEqual("2026-04-08", account_snapshot["trade_date"])
            self.assertEqual("9700.0", account_snapshot["equity"])
            self.assertEqual(1, positions_snapshot["positions_count"])
            self.assertEqual("authoritative", latest_snapshot["trust_level"])
            self.assertEqual("alpaca_rest_api", latest_snapshot["source"])

    def test_write_posttrade_recon_from_snapshot_uses_same_day_latest_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            repo_root = Path(tmp_dir)
            run_root = repo_root / "outputs" / "runs" / "2026-04-08T093500-0400_live"
            broker_dir = run_root / "broker"
            broker_dir.mkdir(parents=True, exist_ok=True)
            (repo_root / "outputs").mkdir(parents=True, exist_ok=True)
            (repo_root / "outputs" / "latest_run.json").write_text(
                json.dumps(
                    {
                        "trade_date": "2026-04-08",
                        "run_root": str(run_root),
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (broker_dir / "pretrade_positions.json").write_text(
                json.dumps({"positions": [{"symbol": "AAPL", "qty": "2", "side": "long"}]}) + "\n",
                encoding="utf-8",
            )
            payload = build_snapshot_payload(
                report_date="2026-04-08",
                workflow_run_id="run-1",
                git_sha="deadbeef",
                account={"cash": "1500.5", "equity": "9700.0"},
                positions=[{"symbol": "AAPL", "qty": "3", "side": "long", "market_value": "600.0"}],
                orders_all=[],
                orders_closed=[],
                fills=[{"symbol": "AAPL", "side": "buy", "qty": "1", "price": "200.0"}],
            )
            recon = write_posttrade_recon_from_snapshot(repo_root=repo_root, payload=payload, report_date="2026-04-08")
            self.assertIsNotNone(recon)
            self.assertEqual("OK_RECONCILED", recon["drift_status"])

    def test_write_posttrade_recon_uses_filled_orders_when_fill_activities_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            repo_root = Path(tmp_dir)
            run_root = repo_root / "outputs" / "runs" / "2026-05-06T101413-0400_259de85"
            broker_dir = run_root / "broker"
            broker_dir.mkdir(parents=True, exist_ok=True)
            (repo_root / "outputs").mkdir(parents=True, exist_ok=True)
            (repo_root / "outputs" / "latest_run.json").write_text(
                json.dumps({"trade_date": "2026-05-06", "run_root": str(run_root)}) + "\n",
                encoding="utf-8",
            )
            (broker_dir / "pretrade_positions.json").write_text(
                json.dumps(
                    {
                        "positions": [
                            {"symbol": "GILD", "qty": "4", "side": "long"},
                            {"symbol": "GM", "qty": "15", "side": "long"},
                            {"symbol": "QCOM", "qty": "3", "side": "long"},
                        ]
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            payload = build_snapshot_payload(
                report_date="2026-05-06",
                workflow_run_id="run-1",
                git_sha="deadbeef",
                account={"cash": "2087.83", "equity": "10132.03"},
                positions=[
                    {"symbol": "GM", "qty": "11", "side": "long"},
                    {"symbol": "QCOM", "qty": "2", "side": "long"},
                    {"symbol": "HLT", "qty": "1", "side": "long"},
                    {"symbol": "INTC", "qty": "2", "side": "long"},
                    {"symbol": "UNH", "qty": "1", "side": "long"},
                    {"symbol": "WM", "qty": "2", "side": "long"},
                ],
                orders_all=[
                    {"symbol": "GILD", "side": "sell", "qty": "4", "filled_qty": "4", "status": "filled", "filled_at": "2026-05-06T14:14:23Z"},
                    {"symbol": "GM", "side": "sell", "qty": "4", "filled_qty": "4", "status": "filled", "filled_at": "2026-05-06T14:14:23Z"},
                    {"symbol": "QCOM", "side": "sell", "qty": "1", "filled_qty": "1", "status": "filled", "filled_at": "2026-05-06T14:14:23Z"},
                    {"symbol": "HLT", "side": "buy", "qty": "1", "filled_qty": "1", "status": "filled", "filled_at": "2026-05-06T14:14:26Z"},
                    {"symbol": "INTC", "side": "buy", "qty": "2", "filled_qty": "2", "status": "filled", "filled_at": "2026-05-06T14:14:27Z"},
                    {"symbol": "UNH", "side": "buy", "qty": "1", "filled_qty": "1", "status": "filled", "filled_at": "2026-05-06T14:14:27Z"},
                    {"symbol": "WM", "side": "buy", "qty": "2", "filled_qty": "2", "status": "filled", "filled_at": "2026-05-06T14:14:26Z"},
                ],
                orders_closed=[],
                fills=[],
            )

            recon = write_posttrade_recon_from_snapshot(repo_root=repo_root, payload=payload, report_date="2026-05-06")

            self.assertIsNotNone(recon)
            self.assertEqual("OK_RECONCILED", recon["drift_status"])
            self.assertEqual([], recon["qty_mismatches"])

    def test_write_posttrade_recon_handles_alpaca_enum_serialized_side_and_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            repo_root = Path(tmp_dir)
            run_root = repo_root / "outputs" / "runs" / "2026-05-06T101413-0400_259de85"
            broker_dir = run_root / "broker"
            broker_dir.mkdir(parents=True, exist_ok=True)
            (repo_root / "outputs").mkdir(parents=True, exist_ok=True)
            (repo_root / "outputs" / "latest_run.json").write_text(
                json.dumps({"trade_date": "2026-05-06", "run_root": str(run_root)}) + "\n",
                encoding="utf-8",
            )
            (broker_dir / "pretrade_positions.json").write_text(
                json.dumps(
                    {
                        "positions": [
                            {"symbol": "GILD", "qty": "4", "side": "long"},
                            {"symbol": "GM", "qty": "15", "side": "long"},
                            {"symbol": "QCOM", "qty": "3", "side": "long"},
                        ]
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            payload = build_snapshot_payload(
                report_date="2026-05-06",
                workflow_run_id="run-1",
                git_sha="deadbeef",
                account={"cash": "2087.83", "equity": "10132.03"},
                positions=[
                    {"symbol": "GM", "qty": "11", "side": "long"},
                    {"symbol": "QCOM", "qty": "2", "side": "long"},
                    {"symbol": "HLT", "qty": "1", "side": "long"},
                    {"symbol": "INTC", "qty": "2", "side": "long"},
                    {"symbol": "UNH", "qty": "1", "side": "long"},
                    {"symbol": "WM", "qty": "2", "side": "long"},
                ],
                orders_all=[
                    {"symbol": "GILD", "side": "OrderSide.SELL", "qty": "4", "filled_qty": "4", "status": "OrderStatus.FILLED", "filled_at": "2026-05-06T14:14:23Z"},
                    {"symbol": "GM", "side": "OrderSide.SELL", "qty": "4", "filled_qty": "4", "status": "OrderStatus.FILLED", "filled_at": "2026-05-06T14:14:23Z"},
                    {"symbol": "QCOM", "side": "OrderSide.SELL", "qty": "1", "filled_qty": "1", "status": "OrderStatus.FILLED", "filled_at": "2026-05-06T14:14:23Z"},
                    {"symbol": "HLT", "side": "OrderSide.BUY", "qty": "1", "filled_qty": "1", "status": "OrderStatus.FILLED", "filled_at": "2026-05-06T14:14:26Z"},
                    {"symbol": "INTC", "side": "OrderSide.BUY", "qty": "2", "filled_qty": "2", "status": "OrderStatus.FILLED", "filled_at": "2026-05-06T14:14:27Z"},
                    {"symbol": "UNH", "side": "OrderSide.BUY", "qty": "1", "filled_qty": "1", "status": "OrderStatus.FILLED", "filled_at": "2026-05-06T14:14:27Z"},
                    {"symbol": "WM", "side": "OrderSide.BUY", "qty": "2", "filled_qty": "2", "status": "OrderStatus.FILLED", "filled_at": "2026-05-06T14:14:26Z"},
                ],
                orders_closed=[],
                fills=[],
            )

            recon = write_posttrade_recon_from_snapshot(repo_root=repo_root, payload=payload, report_date="2026-05-06")

            self.assertIsNotNone(recon)
            self.assertEqual("OK_RECONCILED", recon["drift_status"])
            self.assertEqual([], recon["qty_mismatches"])
