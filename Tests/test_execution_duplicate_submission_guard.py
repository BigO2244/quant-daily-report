from __future__ import annotations

import importlib.util
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
EXECUTE_SCRIPT = REPO_ROOT / "scripts" / "execute_alpaca_orders.py"


def _load_module(path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _write_payload(run_root: Path, run_id: str, trade_date: str) -> None:
    _write_json(
        run_root / "execution_payload.json",
        {
            "run_id": run_id,
            "trade_date": trade_date,
            "mode": "ALPACA",
            "status": "READY",
            "execution_status": "READY",
            "halt_reason": None,
            "executable_trades_count": 1,
            "trades": [{"ticker": "SPY", "side": "BUY", "shares": 2, "order_id": f"{trade_date}:main:v4:SPY:BUY"}],
        },
    )


def _write_latest(tmp_path: Path, run_root: Path, run_id: str, trade_date: str) -> None:
    _write_json(
        tmp_path / "outputs" / "latest_run.json",
        {
            "run_id": run_id,
            "trade_date": trade_date,
            "mode": "ALPACA",
            "run_root": str(run_root),
            "status": "running",
            "workflow_stage": "execution",
        },
    )


def _write_broker_orders(run_root: Path, trade_date: str, rows: list[dict[str, object]]) -> None:
    out_path = run_root / "broker" / f"orders_{trade_date}.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["trade_date,order_id,client_order_id,alpaca_order_id,ticker,side,quantity,status,submitted_at,mode"]
    for row in rows:
        lines.append(
            ",".join(
                [
                    str(row.get("trade_date") or trade_date),
                    str(row.get("order_id") or ""),
                    str(row.get("client_order_id") or ""),
                    str(row.get("alpaca_order_id") or ""),
                    str(row.get("ticker") or ""),
                    str(row.get("side") or ""),
                    str(row.get("quantity") or ""),
                    str(row.get("status") or ""),
                    str(row.get("submitted_at") or ""),
                    str(row.get("mode") or "alpaca"),
                ]
            )
        )
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_sent_ledger(tmp_path: Path, rows: list[dict[str, object]]) -> None:
    out_path = tmp_path / "outputs" / "orders_sent" / "orders_sent.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["date,run_id,order_id,ticker,side,client_order_id,alpaca_order_id,status"]
    for row in rows:
        lines.append(
            ",".join(
                [
                    str(row.get("date") or ""),
                    str(row.get("run_id") or ""),
                    str(row.get("order_id") or ""),
                    str(row.get("ticker") or ""),
                    str(row.get("side") or ""),
                    str(row.get("client_order_id") or ""),
                    str(row.get("alpaca_order_id") or ""),
                    str(row.get("status") or ""),
                ]
            )
        )
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_first_execution_with_no_prior_marker_is_allowed(tmp_path: Path, monkeypatch) -> None:
    mod = _load_module(EXECUTE_SCRIPT, "execute_alpaca_orders_guard_allow")
    monkeypatch.chdir(tmp_path)

    run_root = tmp_path / "outputs" / "runs" / "run-allow"
    _write_latest(tmp_path, run_root, "run-allow", "2026-03-13")
    _write_payload(run_root, "run-allow", "2026-03-13")

    class FakeBroker:
        def __init__(self):
            self.submitted = 0

        def find_order_by_client_id(self, _client_id):
            return None

        def submit_market_order(self, **kwargs):
            self.submitted += 1
            return {"id": "alpaca-1", "status": "accepted", **kwargs}

        def get_account(self):
            return {"cash": "1000.0"}

        def get_positions(self):
            return []

    fake = FakeBroker()

    class FakeAlpacaBroker:
        @staticmethod
        def from_env():
            return fake

    monkeypatch.setattr(mod, "AlpacaBroker", FakeAlpacaBroker)
    out = mod.run_execution()

    assert out["submitted_count"] == 1
    assert out["status"] == "EXECUTED"
    assert fake.submitted == 1


def test_same_run_with_recorded_broker_orders_is_blocked_before_submit(tmp_path: Path, monkeypatch) -> None:
    mod = _load_module(EXECUTE_SCRIPT, "execute_alpaca_orders_guard_block")
    monkeypatch.chdir(tmp_path)

    run_root = tmp_path / "outputs" / "runs" / "run-dup"
    _write_latest(tmp_path, run_root, "run-dup", "2026-03-13")
    _write_payload(run_root, "run-dup", "2026-03-13")
    _write_broker_orders(
        run_root,
        "2026-03-13",
        [
            {
                "order_id": "2026-03-13:main:v4:SPY:BUY",
                "client_order_id": "cid-1",
                "alpaca_order_id": "alpaca-1",
                "ticker": "SPY",
                "side": "BUY",
                "quantity": 2,
                "status": "accepted",
                "submitted_at": "2026-03-13T14:35:00Z",
            }
        ],
    )

    class FakeAlpacaBroker:
        @staticmethod
        def from_env():
            raise AssertionError("from_env should not be called when broker orders were already recorded")

    monkeypatch.setattr(mod, "AlpacaBroker", FakeAlpacaBroker)
    out = mod.run_execution()

    assert out["status"] == "SKIPPED_DUPLICATE"
    assert out["submitted_count"] == 0
    assert out["duplicate_count"] == 1
    assert "EXECUTION_ALREADY_RECORDED" in str(out.get("halt_reason"))
    assert "broker_orders:" in str(out.get("halt_reason"))

    results = json.loads((run_root / "execution_results.json").read_text(encoding="utf-8"))
    assert results["status"] == "SKIPPED_DUPLICATE"
    assert results["duplicate_count"] == 1

    operator_summary = json.loads((run_root / "operator_summary.json").read_text(encoding="utf-8"))
    assert operator_summary["skipped_duplicate"] is True


def test_same_trade_date_sent_ledger_blocks_different_run_id(tmp_path: Path, monkeypatch) -> None:
    mod = _load_module(EXECUTE_SCRIPT, "execute_alpaca_orders_guard_sent_ledger")
    monkeypatch.chdir(tmp_path)

    run_root = tmp_path / "outputs" / "runs" / "run-new"
    _write_latest(tmp_path, run_root, "run-new", "2026-03-13")
    _write_payload(run_root, "run-new", "2026-03-13")
    _write_sent_ledger(
        tmp_path,
        [
            {
                "date": "2026-03-13",
                "run_id": "run-old",
                "order_id": "2026-03-13:main:v4:SPY:BUY",
                "ticker": "SPY",
                "side": "BUY",
                "client_order_id": "cid-old",
                "alpaca_order_id": "alpaca-old",
                "status": "accepted",
            }
        ],
    )

    class FakeAlpacaBroker:
        @staticmethod
        def from_env():
            raise AssertionError("from_env should not be called when same-day sent ledger lock is active")

    monkeypatch.setattr(mod, "AlpacaBroker", FakeAlpacaBroker)
    out = mod.run_execution()

    assert out["status"] == "SKIPPED_DUPLICATE"
    assert out["submitted_count"] == 0
    assert out["duplicate_count"] == 1
    assert "EXECUTION_ALREADY_RECORDED" in str(out.get("halt_reason"))
    assert "sent_ledger:" in str(out.get("halt_reason"))
    assert "prior_runs=run-old" in str(out.get("halt_reason"))


def test_guard_does_not_block_different_trading_day(tmp_path: Path, monkeypatch) -> None:
    mod = _load_module(EXECUTE_SCRIPT, "execute_alpaca_orders_guard_other_day")
    monkeypatch.chdir(tmp_path)

    run_root = tmp_path / "outputs" / "runs" / "run-other-day"
    _write_latest(tmp_path, run_root, "run-other-day", "2026-03-13")
    _write_payload(run_root, "run-other-day", "2026-03-13")
    _write_sent_ledger(
        tmp_path,
        [
            {
                "date": "2026-03-12",
                "run_id": "run-older",
                "order_id": "2026-03-12:main:v4:SPY:BUY",
                "client_order_id": "cid-old",
                "alpaca_order_id": "alpaca-old",
                "ticker": "SPY",
                "side": "BUY",
                "status": "accepted",
            }
        ],
    )

    class FakeBroker:
        def __init__(self):
            self.submitted = 0

        def find_order_by_client_id(self, _client_id):
            return None

        def submit_market_order(self, **kwargs):
            self.submitted += 1
            return {"id": "alpaca-2", "status": "accepted", **kwargs}

        def get_account(self):
            return {"cash": "1000.0"}

        def get_positions(self):
            return []

    fake = FakeBroker()

    class FakeAlpacaBroker:
        @staticmethod
        def from_env():
            return fake

    monkeypatch.setattr(mod, "AlpacaBroker", FakeAlpacaBroker)
    out = mod.run_execution()

    assert out["status"] == "EXECUTED"
    assert out["submitted_count"] == 1
    assert fake.submitted == 1


def test_duplicate_skip_writes_operator_readable_audit_artifacts(tmp_path: Path, monkeypatch) -> None:
    mod = _load_module(EXECUTE_SCRIPT, "execute_alpaca_orders_guard_artifacts")
    monkeypatch.chdir(tmp_path)

    run_root = tmp_path / "outputs" / "runs" / "run-artifacts"
    _write_latest(tmp_path, run_root, "run-artifacts", "2026-03-13")
    _write_payload(run_root, "run-artifacts", "2026-03-13")
    _write_broker_orders(
        run_root,
        "2026-03-13",
        [
            {
                "order_id": "2026-03-13:main:v4:SPY:BUY",
                "client_order_id": "cid-1",
                "alpaca_order_id": "alpaca-1",
                "ticker": "SPY",
                "side": "BUY",
                "quantity": 2,
                "status": "accepted",
                "submitted_at": "2026-03-13T14:35:00Z",
            }
        ],
    )

    class FakeAlpacaBroker:
        @staticmethod
        def from_env():
            raise AssertionError("broker should not be created on duplicate skip")

    monkeypatch.setattr(mod, "AlpacaBroker", FakeAlpacaBroker)
    out = mod.run_execution()

    assert "run_id=run-artifacts" in str(out.get("halt_reason"))
    assert "trade_date=2026-03-13" in str(out.get("halt_reason"))
    assert (run_root / "execution_results.json").exists()
    assert (run_root / "operator_summary.json").exists()
    assert (tmp_path / "outputs" / "execution_audit" / "run-artifacts.json").exists()
