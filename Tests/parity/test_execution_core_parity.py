from __future__ import annotations

import json
from pathlib import Path

import pytest

from execution.core import live_pilot_execution_config
from Tests.parity.execution_core_harness import (
    behavioral_projection,
    capture_execution_core,
)
from Tests.parity.paper_execution_harness import golden_path, stable_json
from Tests.parity.scenarios import scenario_by_name, scenarios


@pytest.mark.parametrize("scenario", scenarios(), ids=lambda scenario: scenario.name)
def test_execution_core_matches_paper_golden_behavior(scenario, tmp_path: Path) -> None:
    expected = json.loads(golden_path(scenario.name).read_text(encoding="utf-8"))
    actual = capture_execution_core(
        scenario,
        ledger_output_root=tmp_path / "ledger",
        ledger_enabled=True,
    )

    assert behavioral_projection(actual) == behavioral_projection(expected), (
        f"Execution core diverged from paper golden for {scenario.name}.\n"
        f"Current core capture:\n{stable_json(actual)}"
    )


def test_execution_core_real_july_2026_anchor(tmp_path: Path) -> None:
    payload = capture_execution_core(
        scenario_by_name("2026_07_07_real"),
        ledger_output_root=tmp_path / "ledger",
    )
    comparison = payload["artifact_comparison"]

    assert comparison["actual"]["ending_cash"] == pytest.approx(939.73)
    assert comparison["actual"]["target_cash_weight"] == pytest.approx(0.0875652909)
    assert comparison["actual"]["post_sell_equity"] == pytest.approx(10687.42)
    assert comparison["actual"]["buys"] == ["PNC", "GE"]
    assert comparison["actual"]["skipped"] == [
        "VRTX",
        "WELL",
        "ALL",
        "ABBV",
        "AVGO",
        "ELV",
        "NXPI",
        "F",
        "PANW",
        "GEV",
    ]


def test_execution_core_records_trade_ledger_at_submit_boundary(tmp_path: Path) -> None:
    scenario = scenario_by_name("2026_07_07_real")
    capture_execution_core(scenario, ledger_output_root=tmp_path / "ledger")

    ledger_path = tmp_path / "ledger" / "live_trade_ledger.jsonl"
    rows = [json.loads(line) for line in ledger_path.read_text(encoding="utf-8").splitlines()]

    assert [row["event"] for row in rows] == ["submitted", "submitted", "submitted", "submitted"]
    assert [row["symbol"] for row in rows] == ["CVS", "WBD", "PNC", "GE"]
    assert [row["side"] for row in rows] == ["SELL", "SELL", "BUY", "BUY"]
    assert rows[0]["reason"] == "removed_from_targets"
    assert rows[-1]["reason"] == "post_sell_rebudget_capital_clipped"
    assert set(rows[0]) == {
        "broker_order_id",
        "client_order_id",
        "event",
        "filled_qty",
        "limit_price",
        "notional",
        "qty",
        "reason",
        "run_root",
        "side",
        "status",
        "symbol",
        "ts_utc",
    }


def test_execution_core_config_surface_documents_live_pilot_policy() -> None:
    config = live_pilot_execution_config(approved_cap_usd=500.0)

    assert config.mode == "live_pilot"
    assert config.capital.approved_cap_usd == 500.0
    assert config.capital.over_cap_behavior == "block"
    assert config.capital.reserve_min_cash == 0.0
    assert config.capital.reserve_equity_pct == 0.0
    assert config.constraints.max_one_order is True
    assert config.constraints.max_buy_orders == 1
    assert config.constraints.equity_collar_max_usd == 520.0
    assert config.constraints.malformed_holding_policy == "fail_closed"
