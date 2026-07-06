from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from scripts.latest_execution_timeline_status import build_latest_execution_timeline_status


REPO_ROOT = Path(__file__).resolve().parents[1]


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_valid_latest_run(root: Path) -> Path:
    run_root = root / "outputs" / "runs" / "run-123"
    _write_json(
        root / "outputs" / "latest_run.json",
        {
            "run_id": "run-123",
            "trade_date": "2026-05-28",
            "mode": "PAPER",
            "run_root": str(run_root),
            "status": "success",
            "workflow_stage": "execution",
        },
    )
    _write_json(
        run_root / "operator_summary.json",
        {
            "terminal_status": "success",
            "operator_execution_status": "executed",
            "execution_integrity_status": "WARN",
            "execution_integrity_findings": ["cash_target_drift"],
        },
    )
    _write_json(
        run_root / "execution_payload.json",
        {
            "run_id": "run-123",
            "trade_date": "2026-05-28",
            "execution_source": "planned_payload_exact",
            "planning_price_basis": "PREV_CLOSE",
            "pricing_asof": "2026-05-27",
            "execution_price_requirement": "PRECOMPUTE_VALIDATED",
            "price_freshness_scope": "precompute_bundle",
            "submitted_count": 13,
            "accepted_count": 13,
            "rejected_count": 0,
        },
    )
    _write_json(
        run_root / "execution_results.json",
        {
            "status": "EXECUTED",
            "submitted_count": 13,
            "accepted_count": 13,
            "rejected_count": 0,
        },
    )
    _write_json(
        run_root / "execution_timeline.json",
        {
            "schema_version": "execution_lifecycle_timeline.v1",
            "run_id": "run-123",
            "trade_date": "2026-05-28",
            "event_count": 15,
            "provenance": {
                "execution_source": "planned_payload_exact",
                "planning_price_basis": "PREV_CLOSE",
                "pricing_asof": "2026-05-27",
                "execution_price_requirement": "PRECOMPUTE_VALIDATED",
                "price_freshness_scope": "precompute_bundle",
            },
            "events": [],
        },
    )
    (run_root / "execution_timeline.md").write_text("# Timeline\n", encoding="utf-8")
    _write_json(
        run_root / "audit" / "execution_integrity.json",
        {
            "status": "WARN",
            "findings": [{"code": "cash_target_drift", "severity": "WARN"}],
        },
    )
    return run_root


def test_latest_run_json_missing_returns_needs_operator(tmp_path: Path) -> None:
    payload = build_latest_execution_timeline_status(tmp_path)

    assert payload["status"] == "NEEDS_OPERATOR"
    assert payload["reason"] == "latest_run_missing"
    assert payload["paths"]["latest_run"]["present"] is False


def test_timeline_missing_returns_needs_operator_with_provenance(tmp_path: Path) -> None:
    run_root = _write_valid_latest_run(tmp_path)
    (run_root / "execution_timeline.json").unlink()

    payload = build_latest_execution_timeline_status(tmp_path)

    assert payload["status"] == "NEEDS_OPERATOR"
    assert payload["reason"] == "execution_timeline_missing_or_unreadable"
    assert payload["execution_source"] == "planned_payload_exact"
    assert payload["paths"]["execution_timeline_json"]["present"] is False


def test_valid_latest_timeline_summary(tmp_path: Path) -> None:
    _write_valid_latest_run(tmp_path)

    payload = build_latest_execution_timeline_status(tmp_path)

    assert payload["status"] == "OK"
    assert payload["run_id"] == "run-123"
    assert payload["trade_date"] == "2026-05-28"
    assert payload["execution_source"] == "planned_payload_exact"
    assert payload["planning_price_basis"] == "PREV_CLOSE"
    assert payload["price_freshness_scope"] == "precompute_bundle"
    assert payload["submitted_count"] == 13
    assert payload["accepted_count"] == 13
    assert payload["rejected_count"] == 0
    assert payload["execution_integrity_status"] == "WARN"
    assert payload["findings"] == ["cash_target_drift"]
    assert payload["equality_gate_observe_status"] == "unavailable"


def test_latest_timeline_surfaces_candidate_trade_lifecycle(tmp_path: Path) -> None:
    run_root = _write_valid_latest_run(tmp_path)
    _write_json(
        run_root / "audit" / "candidate_trade_lifecycle_2026-05-28.json",
        {
            "schema_version": "candidate_trade_lifecycle.v1",
            "trade_date": "2026-05-28",
            "counts": {
                "precompute_candidates": 8,
                "passed_executable_filter": 6,
                "intended_orders": 6,
                "submitted": 2,
                "accepted": 2,
                "filled": 2,
                "suppressed": 6,
                "clipped": 1,
                "suppression_reason_counts": {
                    "buy_blocked_insufficient_buying_power": 4,
                    "min_notional": 2,
                },
                "clipping_reason_counts": {
                    "post_sell_rebudget_capital_clipped": 1,
                },
            },
        },
    )

    payload = build_latest_execution_timeline_status(tmp_path)

    assert payload["status"] == "OK"
    assert payload["planned_payload_trade_count"] == 8
    assert payload["executable_filter_passed_count"] == 6
    assert payload["intended_orders_count"] == 6
    assert payload["filled_count"] == 2
    assert payload["candidate_trade_lifecycle_present"] is True
    assert payload["candidate_trade_lifecycle_summary"]["suppressed"] == 6
    assert payload["candidate_trade_lifecycle_reasons"] == {
        "buy_blocked_insufficient_buying_power": 4,
        "min_notional": 2,
    }
    assert payload["candidate_trade_clipping_reasons"] == {
        "post_sell_rebudget_capital_clipped": 1,
    }
    assert payload["paths"]["candidate_trade_lifecycle"]["present"] is True


def test_latest_timeline_surfaces_equality_gate_as_advisory(tmp_path: Path) -> None:
    run_root = _write_valid_latest_run(tmp_path)
    _write_json(
        run_root / "equality_gate.json",
        {
            "decision": "WOULD_HALT_HASH_MISMATCH",
            "would_block": True,
            "hashes_equal": False,
            "pricing_asof_match": True,
            "execution_source": "planned_payload_exact",
        },
    )

    payload = build_latest_execution_timeline_status(tmp_path)

    assert payload["status"] == "OK"
    assert payload["equality_gate_observe_status"] == "divergence_observed"
    assert payload["equality_gate_decision"] == "WOULD_HALT_HASH_MISMATCH"
    assert payload["equality_gate_would_block"] is True
    assert payload["paths"]["equality_gate"]["present"] is True


def test_direct_script_invocation_from_repo_root(tmp_path: Path) -> None:
    _write_valid_latest_run(tmp_path)

    result = subprocess.run(
        [
            sys.executable,
            "scripts/latest_execution_timeline_status.py",
            "--repo-root",
            str(tmp_path),
            "--json",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["execution_source"] == "planned_payload_exact"


def test_module_invocation_from_repo_root(tmp_path: Path) -> None:
    _write_valid_latest_run(tmp_path)

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.latest_execution_timeline_status",
            "--repo-root",
            str(tmp_path),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "execution_source: planned_payload_exact" in result.stdout
    assert "execution_timeline_json:" in result.stdout
