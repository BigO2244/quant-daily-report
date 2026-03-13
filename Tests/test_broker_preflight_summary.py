from __future__ import annotations

import json
from pathlib import Path

from brokers.alpaca_snapshot import summarize_pretrade_broker_policy
from core.operator_summary import format_broker_preflight_banner, write_operator_summary
from core.trading_day_summary import build_trading_day_summary


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def test_summarize_pretrade_broker_policy_surfaces_account_fields_and_warnings() -> None:
    summary = summarize_pretrade_broker_policy(
        {
            "account": {
                "status": "ACTIVE",
                "cash": "1000.00",
                "equity": "25000.00",
                "buying_power": "0",
                "raw": {
                    "pattern_day_trader": True,
                    "daytrade_count": 3,
                    "trading_blocked": False,
                },
            }
        }
    )

    assert summary["broker_preflight_status"] == "WARN"
    assert summary["broker_preflight_account_status"] == "ACTIVE"
    assert summary["broker_preflight_cash"] == "1000.00"
    assert summary["broker_preflight_equity"] == "25000.00"
    assert summary["broker_preflight_buying_power"] == "0"
    assert summary["broker_preflight_restriction_flags"]["pattern_day_trader"] is True
    assert "pattern_day_trader:true" in summary["broker_preflight_warning_flags"]
    assert "buying_power_non_positive" in summary["broker_preflight_warning_flags"]


def test_broker_preflight_banner_is_operator_readable() -> None:
    banner = format_broker_preflight_banner(
        {
            "broker_preflight_status": "WARN",
            "broker_preflight_account_status": "ACTIVE",
            "broker_preflight_cash": "1000.00",
            "broker_preflight_equity": "25000.00",
            "broker_preflight_buying_power": "0",
            "broker_preflight_restriction_flags": {"pattern_day_trader": True},
            "broker_preflight_warning_flags": ["pattern_day_trader:true", "buying_power_non_positive"],
        }
    )

    assert "[BROKER_PREFLIGHT]" in banner
    assert "status=WARN" in banner
    assert "account_status=ACTIVE" in banner
    assert "buying_power=0" in banner
    assert "warnings=pattern_day_trader:true,buying_power_non_positive" in banner


def test_operator_summary_and_trading_day_summary_include_preflight_fields(tmp_path: Path) -> None:
    run_root = tmp_path / "outputs" / "runs" / "run-preflight"
    broker_dir = run_root / "broker"
    broker_dir.mkdir(parents=True, exist_ok=True)

    write_operator_summary(
        run_root,
        run_id="run-preflight",
        trade_date="2026-03-13",
        mode="ALPACA",
        broker_preflight_status="WARN",
        broker_preflight_account_status="ACTIVE",
        broker_preflight_cash="1000.00",
        broker_preflight_equity="25000.00",
        broker_preflight_buying_power="0",
        broker_preflight_restriction_flags={"pattern_day_trader": True},
        broker_preflight_warning_flags=["pattern_day_trader:true", "buying_power_non_positive"],
    )

    op_payload = json.loads((run_root / "operator_summary.json").read_text(encoding="utf-8"))
    assert op_payload["broker_preflight_status"] == "WARN"
    assert op_payload["broker_preflight_account_status"] == "ACTIVE"
    assert op_payload["broker_preflight_warning_flags"] == ["pattern_day_trader:true", "buying_power_non_positive"]

    _write_json(
        broker_dir / "pretrade_account_snapshot.json",
        {
            "account": {
                "status": "ACTIVE",
                "cash": "1000.00",
                "equity": "25000.00",
                "buying_power": "0",
                "raw": {"pattern_day_trader": True},
            }
        },
    )
    _write_json(broker_dir / "pretrade_positions.json", {"positions_count": 3})
    _write_json(broker_dir / "posttrade_account_snapshot.json", {"cash": 800.0, "equity": 24980.0})
    _write_json(broker_dir / "posttrade_positions.json", {"positions_count": 3})

    summary = build_trading_day_summary(
        run_root=run_root,
        run_id="run-preflight",
        trade_date="2026-03-13",
        workspace_root=tmp_path,
        audit_dir=tmp_path / "outputs" / "execution_audit",
    )

    broker_context = summary["broker_context"]
    assert broker_context["broker_preflight_status"] == "WARN"
    assert broker_context["broker_preflight_account_status"] == "ACTIVE"
    assert broker_context["broker_preflight_buying_power"] == "0"
    assert broker_context["broker_preflight_restriction_flags"] == {"pattern_day_trader": True}
    assert broker_context["broker_preflight_warning_flags"] == ["pattern_day_trader:true", "buying_power_non_positive"]
