from __future__ import annotations

import json
from pathlib import Path

import pytest

from Tests.parity.paper_execution_harness import golden_path, stable_json
from Tests.parity.paper_production_core_harness import (
    capture_paper_production_core,
    production_behavioral_projection,
)
from Tests.parity.scenarios import scenario_by_name, scenarios


@pytest.mark.parametrize("scenario", scenarios(), ids=lambda scenario: scenario.name)
def test_paper_production_core_path_matches_paper_golden(scenario, tmp_path: Path) -> None:
    expected = json.loads(golden_path(scenario.name).read_text(encoding="utf-8"))
    actual = capture_paper_production_core(
        scenario,
        run_output_root=tmp_path / scenario.name,
    )

    assert production_behavioral_projection(actual) == production_behavioral_projection(expected), (
        f"Paper production core path diverged from paper golden for {scenario.name}.\n"
        f"Current production-core capture:\n{stable_json(actual)}"
    )


def test_paper_production_core_real_july_2026_anchor(tmp_path: Path) -> None:
    payload = capture_paper_production_core(
        scenario_by_name("2026_07_07_real"),
        run_output_root=tmp_path / "real",
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


def test_paper_production_core_records_trade_ledger_line(tmp_path: Path) -> None:
    payload = capture_paper_production_core(
        scenario_by_name("2026_07_07_real"),
        run_output_root=tmp_path / "real",
    )
    ledger_path = Path(payload["ledger_path"])
    rows = [json.loads(line) for line in ledger_path.read_text(encoding="utf-8").splitlines()]

    assert rows
    assert rows[0]["event"] == "submitted"
    assert rows[0]["symbol"] == "CVS"
    assert rows[0]["side"] == "SELL"
    assert rows[0]["reason"] == "removed_from_targets"
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
