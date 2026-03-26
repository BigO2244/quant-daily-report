from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _load_module_with_stubs(tmp_path: Path):
    for name in list(sys.modules):
        if name == "core" or name.startswith("core."):
            sys.modules.pop(name, None)
        if name == "paper" or name.startswith("paper."):
            sys.modules.pop(name, None)

    paper_pkg = ModuleType("paper")
    paper_pkg.__path__ = []  # type: ignore[attr-defined]

    build_mod = SimpleNamespace(
        build_execution_email_text=lambda payload: (
            f"Execution {payload.get('trade_date')} [{payload.get('execution_status')}]",
            f"status={payload.get('execution_status')} reason={payload.get('halt_reason')}",
        ),
        build_execution_email_html=lambda payload: ("subject", "<p>body</p>"),
    )
    broker_mod = SimpleNamespace(
        load_config=lambda _path: SimpleNamespace(trading_mode="alpaca"),
        reset_orders_sent_ledger_for_date=lambda *_args, **_kwargs: None,
    )
    send_mod = SimpleNamespace(send_execution_email=lambda **kwargs: None)

    sys.modules["paper"] = paper_pkg
    sys.modules["paper.build_execution_email"] = build_mod
    sys.modules["paper.paper_broker"] = broker_mod
    sys.modules["paper.send_execution_email"] = send_mod

    module_path = Path(__file__).resolve().parents[1] / "daily_trade_execution_email.py"
    spec = importlib.util.spec_from_file_location("daily_trade_execution_email_test", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_halted_execution_status_sends_operator_email(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("EMAIL_PRETRADE", "1")
    execution_email = _load_module_with_stubs(tmp_path)

    run_root = tmp_path / "outputs" / "runs" / "run-halted"
    _write_json(
        tmp_path / "outputs" / "latest_run.json",
        {
            "run_id": "run-halted",
            "trade_date": "2026-03-26",
            "mode": "ALPACA",
            "run_root": str(run_root),
            "status": "failed_pre_execution",
        },
    )
    _write_json(
        run_root / "execution_payload.json",
        {
            "run_id": "run-halted",
            "trade_date": "2026-03-26",
            "mode": "ALPACA",
            "execution_status": "HALTED",
            "halt_reason": "precompute_reconciliation_self_heal",
            "trades": [],
            "order_ids": [],
        },
    )

    sent: dict[str, object] = {}

    monkeypatch.setattr(
        execution_email,
        "send_execution_email",
        lambda **kwargs: sent.update(kwargs),
    )

    execution_email.main([])

    assert "subject" in sent
    assert "HALTED" in str(sent["subject"])
    assert "precompute_reconciliation_self_heal" in str(sent["body_text"])
