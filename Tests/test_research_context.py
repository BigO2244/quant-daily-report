from __future__ import annotations

import json
from pathlib import Path

from core.research_context import (
    build_research_context,
    register_factor_artifacts,
    write_research_context,
)


def test_build_research_context_includes_signal_targets_and_execution_details(tmp_path: Path):
    signals_path = tmp_path / "signals.json"
    signals_path.write_text(
        json.dumps(
            {
                "cash_target_weight": 0.1,
                "signals": [
                    {"ticker": "AAPL", "target_weight": 0.6, "sleeve": "sleeve_quality"},
                    {"ticker": "MSFT", "target_weight": 0.3, "sleeve": "sleeve_trend"},
                ],
            }
        ),
        encoding="utf-8",
    )

    payload = build_research_context(
        trade_date="2026-03-24",
        run_id="run-123",
        mode="alpaca",
        daily_snapshot={
            "signals_snapshot_path": str(signals_path),
            "target_cash_weight": 0.1,
            "regime_summary": {"composite_regime": "risk_off_defensive"},
            "breaker": {"mode": "partial"},
            "sleeve_allocations": {"sleeve_quality": 0.4},
            "holdings": [{"ticker": "AAPL"}],
            "risk_levels": [{"ticker": "AAPL", "stop_loss": 100.0}],
            "proposed_trades": [{"ticker": "AAPL", "side": "BUY"}],
            "alpha_attribution": {"n_days": 2},
        },
        execution_payload={
            "execution_status": "READY",
            "status_label": "TRADES READY",
            "submitted_count": 1,
            "accepted_count": 1,
            "rejected_count": 0,
            "trades": [{"ticker": "AAPL", "side": "BUY"}],
        },
        paper_summary={
            "market_status": "OPEN",
            "broker_recon_status": "PASS",
            "posttrade_recon_status": "PASS",
            "broker_responses": [{"ticker": "AAPL", "status": "ACCEPTED"}],
        },
    )

    assert payload["artifact_type"] == "caerus_research_context"
    assert payload["schema_version"] == 1
    assert payload["market_context"]["regime_summary"]["composite_regime"] == "risk_off_defensive"
    assert payload["selection_context"]["target_weights"][0]["ticker"] == "AAPL"
    assert payload["execution_context"]["intended_orders"][0]["ticker"] == "AAPL"
    assert payload["execution_context"]["realized_broker_responses"][0]["status"] == "ACCEPTED"
    assert payload["data_completeness"]["has_target_weights"] is True


def test_write_research_context_writes_canonical_and_run_copy(tmp_path: Path):
    output_root = tmp_path / "research_context"
    run_root = tmp_path / "runs" / "run-123"

    result = write_research_context(
        trade_date="2026-03-24",
        run_id="run-123",
        mode="shadow",
        daily_snapshot={},
        execution_payload={},
        paper_summary={},
        run_root=run_root,
        output_root=output_root,
        allow_overwrite=True,
    )

    canonical = Path(result["canonical_path"])
    run_copy = Path(result["run_path"])
    assert canonical.exists()
    assert run_copy.exists()

    canonical_payload = json.loads(canonical.read_text(encoding="utf-8"))
    run_payload = json.loads(run_copy.read_text(encoding="utf-8"))
    assert canonical_payload["trade_date"] == "2026-03-24"
    assert run_payload["run_id"] == "run-123"


def test_register_factor_artifacts_merges_into_existing_research_context(tmp_path: Path):
    output_root = tmp_path / "research_context"
    runs_root = tmp_path / "runs"
    run_root = runs_root / "run-123"

    write_research_context(
        trade_date="2026-03-24",
        run_id="run-123",
        mode="shadow",
        daily_snapshot={},
        execution_payload={},
        paper_summary={},
        run_root=run_root,
        output_root=output_root,
        allow_overwrite=True,
        factor_artifacts={"quality_diagnostics_json": "outputs/model_diagnostics/quality/2026-03-24.json"},
    )

    result = register_factor_artifacts(
        trade_date="2026-03-24",
        factor_artifacts={"quality_factor_ic_json": "outputs/model_diagnostics/quality_factor_ic/2026-03-24.json"},
        output_root=output_root,
        runs_root=runs_root,
        allow_overwrite=True,
    )

    canonical_payload = json.loads(Path(result["canonical_path"]).read_text(encoding="utf-8"))
    run_payload = json.loads(Path(result["run_path"]).read_text(encoding="utf-8"))
    for payload in (canonical_payload, run_payload):
        artifacts = payload["research_inputs"]["factor_artifacts"]
        assert artifacts["quality_diagnostics_json"].endswith("quality/2026-03-24.json")
        assert artifacts["quality_factor_ic_json"].endswith("quality_factor_ic/2026-03-24.json")
        assert payload["data_completeness"]["has_factor_artifacts"] is True
