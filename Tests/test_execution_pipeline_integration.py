from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from core.execution_payload import write_canonical_execution_payload
from core.run_pointer import write_trade_stage_pointer


REPO_ROOT = Path(__file__).resolve().parents[1]
EXECUTE_SCRIPT = REPO_ROOT / "scripts" / "execute_alpaca_orders.py"
CONFIRM_SCRIPT = REPO_ROOT / "scripts" / "send_trading_confirmation_email.py"
CRON_EXECUTE_SCRIPT = REPO_ROOT / "scripts" / "cron_execute.sh"
CRON_PRECOMPUTE_SCRIPT = REPO_ROOT / "scripts" / "cron_precompute.sh"


def _load_module(path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def test_cron_execute_self_heal_invokes_precompute_recovery_mode() -> None:
    text = CRON_EXECUTE_SCRIPT.read_text(encoding="utf-8")

    assert "core.precompute_bundle_validation" in text
    assert 'SELF_HEAL_PRECOMPUTE_ONLY=1 REPORT_DATE="${REPORT_DATE}"' in text
    assert '"${REPO_ROOT}/scripts/cron_precompute.sh"' in text
    assert "execution_self_heal.json" in text
    assert "execution halted to avoid degraded bundle execution" in text
    assert "run_precomputed_alpaca_execution" in text.split("precompute bundle validation failed after self-heal", 1)[-1]


def test_cron_precompute_self_heal_suppresses_noncritical_side_effects() -> None:
    text = CRON_PRECOMPUTE_SCRIPT.read_text(encoding="utf-8")
    self_heal_block = text.split('if [[ "${SELF_HEAL_PRECOMPUTE_ONLY}" =~ ^(1|true|TRUE|yes|YES|y|Y)$ ]]; then', 2)[-1]

    assert "precompute_self_heal.json" in text
    assert "precompute_bundle_validation.json" in text
    assert '"noncritical_side_effects_suppressed": true' in text
    assert '"suppressed_side_effects": ["email", "shadow", "shadow_latest", "shadow_reconciliation"]' in text
    assert "exit ${EXIT_CODE}" in self_heal_block
    assert "scripts.send_precompute_email" not in self_heal_block.split("# --- Send precompute-complete email", 1)[0]
    assert "run_shadow_candidates_daily.sh" not in self_heal_block.split("# --- Send precompute-complete email", 1)[0]


def test_canonical_execution_payload_written_under_run_root(tmp_path: Path) -> None:
    run_root = tmp_path / "outputs" / "runs" / "run-1"
    payload = {
        "run_id": "run-1",
        "trade_date": "2026-03-09",
        "mode": "ALPACA",
        "execution_status": "READY",
        "halt_reason": None,
        "trades": [{"ticker": "SPY", "side": "BUY", "shares": 1}],
        "executable_trades_count": 1,
        "status_label": "TRADES READY",
        "status_reason": None,
    }

    out_path = write_canonical_execution_payload(payload, "2026-03-09", run_root=run_root)
    out = json.loads(Path(out_path).read_text(encoding="utf-8"))

    assert Path(out_path) == run_root / "execution_payload.json"
    assert out["run_id"] == "run-1"
    assert out["execution_status"] == "READY"
    assert out["executable_trades_count"] == 1
    assert "created_at" in out


def test_executor_reads_latest_run_and_loads_payload(tmp_path: Path, monkeypatch) -> None:
    mod = _load_module(EXECUTE_SCRIPT, "execute_alpaca_orders_test_1")
    monkeypatch.chdir(tmp_path)

    run_root = tmp_path / "outputs" / "runs" / "run-abc"
    _write_json(
        tmp_path / "outputs" / "latest_run.json",
        {
            "run_id": "run-abc",
            "trade_date": "2026-03-09",
            "mode": "ALPACA",
            "run_root": str(run_root),
            "workflow_stage": "execution",
        },
    )
    _write_json(
        run_root / "execution_payload.json",
        {
            "run_id": "run-abc",
            "trade_date": "2026-03-09",
            "mode": "ALPACA",
            "execution_status": "HALTED",
            "halt_reason": "TEST",
            "trades": [],
        },
    )

    out = mod.run_execution()
    assert out["run_id"] == "run-abc"
    assert out["status"] == "HALTED"


def test_missing_canonical_payload_writes_halted_results(tmp_path: Path, monkeypatch) -> None:
    mod = _load_module(EXECUTE_SCRIPT, "execute_alpaca_orders_test_2")
    monkeypatch.chdir(tmp_path)

    run_root = tmp_path / "outputs" / "runs" / "run-missing"
    _write_json(
        tmp_path / "outputs" / "latest_run.json",
        {
            "run_id": "run-missing",
            "trade_date": "2026-03-09",
            "mode": "ALPACA",
            "run_root": str(run_root),
            "workflow_stage": "execution",
        },
    )

    out = mod.run_execution()
    results = json.loads((run_root / "execution_results.json").read_text(encoding="utf-8"))

    assert out["status"] == "HALTED"
    assert "ENGINE_RUN_NO_ARTIFACTS" in str(out.get("halt_reason"))
    assert results["submitted_count"] == 0


def test_valid_payload_calls_broker_submission(tmp_path: Path, monkeypatch) -> None:
    mod = _load_module(EXECUTE_SCRIPT, "execute_alpaca_orders_test_3")
    monkeypatch.chdir(tmp_path)

    run_root = tmp_path / "outputs" / "runs" / "run-ready"
    _write_json(
        tmp_path / "outputs" / "latest_run.json",
        {
            "run_id": "run-ready",
            "trade_date": "2026-03-09",
            "mode": "ALPACA",
            "run_root": str(run_root),
            "workflow_stage": "execution",
        },
    )
    _write_json(
        run_root / "execution_payload.json",
        {
            "run_id": "run-ready",
            "trade_date": "2026-03-09",
            "mode": "ALPACA",
            "execution_status": "READY",
            "halt_reason": None,
            "trades": [{"ticker": "SPY", "side": "BUY", "shares": 2, "order_id": "oid-1"}],
        },
    )

    class FakeBroker:
        def __init__(self):
            self.submitted = 0

        def find_order_by_client_id(self, _client_id):
            return None

        def submit_market_order(self, **kwargs):
            self.submitted += 1
            return {"id": "alpaca-1", "status": "accepted", **kwargs}

    fake = FakeBroker()

    class FakeAlpacaBroker:
        @staticmethod
        def from_env():
            return fake

    monkeypatch.setattr(mod, "AlpacaBroker", FakeAlpacaBroker)

    out = mod.run_execution()
    assert out["submitted_count"] == 1
    assert out["accepted_count"] == 1
    assert out["status"] == "EXECUTED"
    assert fake.submitted == 1


def test_duplicate_run_id_does_not_resubmit(tmp_path: Path, monkeypatch) -> None:
    mod = _load_module(EXECUTE_SCRIPT, "execute_alpaca_orders_test_4")
    monkeypatch.chdir(tmp_path)

    run_root = tmp_path / "outputs" / "runs" / "run-dup"
    _write_json(
        tmp_path / "outputs" / "latest_run.json",
        {
            "run_id": "run-dup",
            "trade_date": "2026-03-09",
            "mode": "ALPACA",
            "run_root": str(run_root),
            "workflow_stage": "execution",
        },
    )
    _write_json(
        run_root / "execution_results.json",
        {
            "run_id": "run-dup",
            "trade_date": "2026-03-09",
            "mode": "ALPACA",
            "submitted_count": 2,
            "accepted_count": 2,
            "rejected_count": 0,
            "order_ids": ["a1", "a2"],
            "status": "SUBMITTED",
            "halt_reason": None,
            "broker_responses": [],
        },
    )

    class FakeAlpacaBroker:
        @staticmethod
        def from_env():
            raise AssertionError("from_env should not be called for duplicate run")

    monkeypatch.setattr(mod, "AlpacaBroker", FakeAlpacaBroker)
    out = mod.run_execution()
    assert out["submitted_count"] == 2
    assert out["status"] == "SUBMITTED"


def test_confirmation_email_uses_execution_results(tmp_path: Path, monkeypatch) -> None:
    mod = _load_module(CONFIRM_SCRIPT, "send_trading_confirmation_email_test_1")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("REPORT_DATE", "2026-03-09")

    run_root = tmp_path / "outputs" / "runs" / "run-confirm"
    write_trade_stage_pointer(
        stage="execution",
        run_id="run-confirm",
        trade_date="2026-03-09",
        mode="ALPACA",
        run_root=str(run_root),
        status="success",
        workspace_root=str(tmp_path),
    )
    _write_json(
        run_root / "execution_results.json",
        {
            "run_id": "run-confirm",
            "trade_date": "2026-03-09",
            "mode": "ALPACA",
            "submitted_count": 1,
            "accepted_count": 1,
            "rejected_count": 0,
            "order_ids": ["alpaca-1"],
            "status": "SUBMITTED",
            "halt_reason": None,
            "broker_responses": [],
        },
    )

    sent = {}

    def _fake_send_email(*, subject, body_text, body_html=None):
        sent["subject"] = subject
        sent["body_text"] = body_text
        sent["body_html"] = body_html

    monkeypatch.setattr(mod, "send_email", _fake_send_email)
    monkeypatch.setenv("EMAIL_TRADING_CONFIRMATION", "1")
    monkeypatch.delenv("EMAIL_DRY_RUN", raising=False)

    mod.main()

    assert "Trading Confirmation 2026-03-09" in sent["subject"]
    assert "Submitted: 1" in sent["body_text"]
    assert "Run ID: run-confirm" in sent["body_text"]


def test_executor_blocks_stage_less_latest_run_pointer(tmp_path: Path, monkeypatch) -> None:
    mod = _load_module(EXECUTE_SCRIPT, "execute_alpaca_orders_test_stage_less_latest")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("REPORT_DATE", "2026-03-09")

    run_root = tmp_path / "outputs" / "runs" / "run-stage-less"
    _write_json(
        tmp_path / "outputs" / "latest_run.json",
        {
            "run_id": "run-stage-less",
            "trade_date": "2026-03-09",
            "mode": "ALPACA",
            "run_root": str(run_root),
            "status": "running",
        },
    )
    _write_json(
        run_root / "execution_payload.json",
        {
            "run_id": "run-stage-less",
            "trade_date": "2026-03-09",
            "mode": "ALPACA",
            "execution_status": "READY",
            "halt_reason": None,
            "trades": [{"ticker": "SPY", "side": "BUY", "shares": 1}],
        },
    )

    try:
        mod.run_execution()
        raise AssertionError("expected SystemExit")
    except SystemExit as exc:
        assert int(getattr(exc, "code", 0) or 0) == 1

    results = json.loads(
        (tmp_path / "outputs" / "runs" / "MISSING_POINTER_2026-03-09" / "execution_results.json").read_text(
            encoding="utf-8"
        )
    )
    assert results["status"] == "HALTED"
    assert "LEGACY_POINTER_BLOCKED" in str(results.get("halt_reason"))


def test_confirmation_email_prefers_execution_stage_pointer_over_latest_run(
    tmp_path: Path,
    monkeypatch,
) -> None:
    mod = _load_module(CONFIRM_SCRIPT, "send_trading_confirmation_email_test_2")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("REPORT_DATE", "2026-03-09")
    monkeypatch.setenv("EMAIL_TRADING_CONFIRMATION", "1")
    monkeypatch.delenv("EMAIL_DRY_RUN", raising=False)

    good_run_root = tmp_path / "outputs" / "runs" / "run-confirm-good"
    bad_run_root = tmp_path / "outputs" / "runs" / "run-confirm-bad"

    _write_json(
        tmp_path / "outputs" / "latest_run.json",
        {
            "run_id": "run-confirm-bad",
            "trade_date": "2026-03-09",
            "mode": "ALPACA",
            "run_root": str(bad_run_root),
            "status": "success",
        },
    )
    _write_json(
        bad_run_root / "execution_results.json",
        {
            "run_id": "run-confirm-bad",
            "trade_date": "2026-03-09",
            "mode": "ALPACA",
            "submitted_count": 0,
            "accepted_count": 0,
            "rejected_count": 0,
            "order_ids": [],
            "status": "HALTED",
            "halt_reason": "WRONG_RUN",
            "broker_responses": [],
        },
    )

    write_trade_stage_pointer(
        stage="execution",
        run_id="run-confirm-good",
        trade_date="2026-03-09",
        mode="ALPACA",
        run_root=str(good_run_root),
        status="success",
        workspace_root=str(tmp_path),
    )
    _write_json(
        good_run_root / "execution_results.json",
        {
            "run_id": "run-confirm-good",
            "trade_date": "2026-03-09",
            "mode": "ALPACA",
            "submitted_count": 2,
            "accepted_count": 2,
            "rejected_count": 0,
            "order_ids": ["alpaca-1", "alpaca-2"],
            "status": "EXECUTED",
            "halt_reason": None,
            "broker_responses": [],
        },
    )

    sent = {}

    def _fake_send_email(*, subject, body_text, body_html=None):
        sent["subject"] = subject
        sent["body_text"] = body_text
        sent["body_html"] = body_html

    monkeypatch.setattr(mod, "send_email", _fake_send_email)

    mod.main()

    assert "Trading Confirmation 2026-03-09" in sent["subject"]
    assert "Submitted: 2" in sent["body_text"]
    assert "Run ID: run-confirm-good" in sent["body_text"]


def test_confirmation_email_falls_back_to_broker_snapshot_when_execution_results_missing(
    tmp_path: Path,
    monkeypatch,
) -> None:
    mod = _load_module(CONFIRM_SCRIPT, "send_trading_confirmation_email_test_missing_results")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("REPORT_DATE", "2026-03-09")
    monkeypatch.setenv("EMAIL_TRADING_CONFIRMATION", "1")
    monkeypatch.delenv("EMAIL_DRY_RUN", raising=False)
    monkeypatch.setattr(mod, "_REPO_ROOT", tmp_path)

    run_root = tmp_path / "outputs" / "runs" / "run-missing-results"
    write_trade_stage_pointer(
        stage="execution",
        run_id="run-missing-results",
        trade_date="2026-03-09",
        mode="PAPER",
        run_root=str(run_root),
        status="success",
        workspace_root=str(tmp_path),
    )

    _write_json(
        tmp_path / "outputs" / "broker_snapshot" / "broker_snapshot_2026-03-09.json",
        {
            "counts": {
                "orders_report_date": 2,
                "fills_report_date": 2,
            },
            "orders_report_date": [
                {"id": "alpaca-1", "status": "filled"},
                {"id": "alpaca-2", "status": "filled"},
            ],
            "fills_report_date": [
                {"id": "fill-1", "symbol": "SPY", "side": "buy", "qty": "1"},
                {"id": "fill-2", "symbol": "QQQ", "side": "buy", "qty": "1"},
            ],
        },
    )

    sent = {}

    def _fake_send_email(*, subject, body_text, body_html=None):
        sent["subject"] = subject
        sent["body_text"] = body_text
        sent["body_html"] = body_html

    monkeypatch.setattr(mod, "send_email", _fake_send_email)

    mod.main()

    assert "Trading Confirmation 2026-03-09" in sent["subject"]
    assert "Status: EXECUTED" in sent["body_text"]
    assert "Submitted: 2" in sent["body_text"]
    assert "Run ID: run-missing-results" in sent["body_text"]
    assert "broker_snapshot_2026-03-09.json" in sent["body_text"]


def test_confirmation_email_rejects_running_execution_pointer(tmp_path: Path, monkeypatch) -> None:
    mod = _load_module(CONFIRM_SCRIPT, "send_trading_confirmation_email_test_running_pointer")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("REPORT_DATE", "2026-03-09")

    run_root = tmp_path / "outputs" / "runs" / "run-running"
    write_trade_stage_pointer(
        stage="execution",
        run_id="run-running",
        trade_date="2026-03-09",
        mode="PAPER",
        run_root=str(run_root),
        status="running",
        workspace_root=str(tmp_path),
    )
    _write_json(
        run_root / "execution_results.json",
        {
            "run_id": "run-running",
            "trade_date": "2026-03-09",
            "mode": "PAPER",
            "submitted_count": 1,
            "accepted_count": 1,
            "rejected_count": 0,
            "status": "EXECUTED",
            "halt_reason": None,
            "broker_responses": [],
        },
    )

    try:
        mod.main()
        raise AssertionError("expected RuntimeError")
    except RuntimeError as exc:
        assert "still running" in str(exc)


def test_confirmation_email_falls_back_to_broker_snapshot_on_malformed_execution_pointer(
    tmp_path: Path,
    monkeypatch,
) -> None:
    mod = _load_module(CONFIRM_SCRIPT, "send_trading_confirmation_email_test_malformed_pointer")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("REPORT_DATE", "2026-03-09")
    monkeypatch.setenv("EMAIL_TRADING_CONFIRMATION", "1")
    monkeypatch.delenv("EMAIL_DRY_RUN", raising=False)
    monkeypatch.setattr(mod, "_REPO_ROOT", tmp_path)

    pointer_path = tmp_path / "outputs" / "workflow" / "2026-03-09" / "execution.json"
    pointer_path.parent.mkdir(parents=True, exist_ok=True)
    pointer_path.write_text("{ invalid json }", encoding="utf-8")

    _write_json(
        tmp_path / "outputs" / "broker_snapshot" / "broker_snapshot_2026-03-09.json",
        {
            "counts": {
                "orders_report_date": 1,
                "fills_report_date": 1,
            },
            "orders_report_date": [
                {"id": "alpaca-1", "status": "filled"},
            ],
            "fills_report_date": [
                {"id": "fill-1", "symbol": "SPY", "side": "buy", "qty": "1"},
            ],
        },
    )

    sent = {}

    def _fake_send_email(*, subject, body_text, body_html=None):
        sent["subject"] = subject
        sent["body_text"] = body_text
        sent["body_html"] = body_html

    monkeypatch.setattr(mod, "send_email", _fake_send_email)

    mod.main()

    assert "Trading Confirmation 2026-03-09" in sent["subject"]
    assert "Status: EXECUTED" in sent["body_text"]
    assert "Submitted: 1" in sent["body_text"]


def test_confirmation_email_trade_date_uses_current_et(tmp_path: Path, monkeypatch) -> None:
    mod = _load_module(CONFIRM_SCRIPT, "send_trading_confirmation_email_test_trade_date")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("REPORT_DATE", raising=False)

    class _FakeNow:
        def strftime(self, fmt: str) -> str:
            assert fmt == "%Y-%m-%d"
            return "2026-03-30"

    monkeypatch.setattr(mod, "current_et", lambda: _FakeNow())

    assert mod._resolve_trade_date() == "2026-03-30"
