from __future__ import annotations

import json
from pathlib import Path

from core.precompute_bundle_validation import (
    build_execution_self_heal_status,
    validate_precompute_bundle,
)
from core.precompute_contract import BUNDLE_REQUIRED_FILES
from core.sleeve_control_plane import (
    dispatch_all_sleeves,
    load_sleeve_control_registry,
)
from Tests.test_live_pilot_build_plan_from_precompute import _orion_shadow


REQUIRED_FILES = BUNDLE_REQUIRED_FILES


def _write_bundle_file(bundle_dir: Path, name: str, trade_date: str = "2026-05-15") -> None:
    bundle_dir.mkdir(parents=True, exist_ok=True)
    payload = {"trade_date": trade_date}
    if name == "planned_execution_payload.json":
        payload["trades"] = [
            {
                "ticker": "AAPL",
                "side": "BUY",
                "shares": 1,
                "entry_price": 100.0,
                "notional": 100.0,
            }
        ]
    elif name == "sleeve_evaluations.json":
        registry = load_sleeve_control_registry()
        runtime_root = bundle_dir.parents[2]
        orion = (
            runtime_root
            / "outputs"
            / "shadow_candidates"
            / trade_date
            / "caerus_orion.json"
        )
        _orion_shadow(
            runtime_root,
            trade_date=trade_date,
            weights={"AAPL": 1.0},
        )
        payload = dispatch_all_sleeves(
            trade_date=trade_date,
            run_id="test-precompute-bundle",
            daily_snapshot={
                "asof": trade_date,
                "sleeve_allocations": {
                    key: 0.0 for key in registry.functional_allocation_keys()
                },
            },
            runtime_root=runtime_root,
            registry=registry,
        )
    (bundle_dir / name).write_text(json.dumps(payload) + "\n", encoding="utf-8")


def test_validate_precompute_bundle_requires_all_execution_artifacts(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "outputs" / "precompute" / "2026-05-15"
    _write_bundle_file(bundle_dir, "contract.json")

    result = validate_precompute_bundle(bundle_dir, trade_date="2026-05-15")

    assert result["status"] == "FAILED"
    assert result["present_files"] == ["contract.json"]
    assert set(result["missing_files"]) == {
        "daily_snapshot.json",
        "signals.json",
        "planned_execution_payload.json",
        "sleeve_evaluations.json",
    }
    assert "missing:planned_execution_payload.json" in result["validation_failures"]
    assert "missing:sleeve_evaluations.json" in result["validation_failures"]


def test_validate_precompute_bundle_passes_complete_bundle(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "outputs" / "precompute" / "2026-05-15"
    for name in REQUIRED_FILES:
        _write_bundle_file(bundle_dir, name)

    result = validate_precompute_bundle(bundle_dir, trade_date="2026-05-15")

    assert result["status"] == "OK"
    assert result["missing_files"] == []
    assert result["validation_failures"] == []
    assert result["integrity_summary"]["present_count"] == 5
    sleeve_payload = json.loads(
        (bundle_dir / "sleeve_evaluations.json").read_text(encoding="utf-8")
    )
    assert any(
        envelope["evaluation"]["status"] == "BLOCKED"
        and envelope["eligibility"]["evaluation_only"] is True
        for envelope in sleeve_payload["envelopes"]
    )


def test_validate_precompute_bundle_rejects_corrupt_sleeve_coverage(
    tmp_path: Path,
) -> None:
    bundle_dir = tmp_path / "outputs" / "precompute" / "2026-05-15"
    for name in REQUIRED_FILES:
        _write_bundle_file(bundle_dir, name)
    sidecar_path = bundle_dir / "sleeve_evaluations.json"
    payload = json.loads(sidecar_path.read_text(encoding="utf-8"))
    payload["envelopes"].pop()
    payload["summary"]["envelope_count"] -= 1
    sidecar_path.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    result = validate_precompute_bundle(bundle_dir, trade_date="2026-05-15")

    assert result["status"] == "FAILED"
    assert "sleeve_evaluations:envelope_coverage_mismatch" in result[
        "semantic_failures"
    ]


def test_validate_precompute_bundle_rejects_nonterminal_sleeve_status(
    tmp_path: Path,
) -> None:
    bundle_dir = tmp_path / "outputs" / "precompute" / "2026-05-15"
    for name in REQUIRED_FILES:
        _write_bundle_file(bundle_dir, name)
    sidecar_path = bundle_dir / "sleeve_evaluations.json"
    payload = json.loads(sidecar_path.read_text(encoding="utf-8"))
    payload["envelopes"][0]["evaluation"]["status"] = "PENDING"
    sidecar_path.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    result = validate_precompute_bundle(bundle_dir, trade_date="2026-05-15")

    assert result["status"] == "FAILED"
    assert "sleeve_evaluations:envelope[0]:non_terminal_status" in result[
        "semantic_failures"
    ]


def test_validate_precompute_bundle_rejects_nonfinite_execution_values(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "outputs" / "precompute" / "2026-05-15"
    for name in REQUIRED_FILES:
        _write_bundle_file(bundle_dir, name)
    payload_path = bundle_dir / "planned_execution_payload.json"
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    payload["trades"][0]["entry_price"] = float("nan")
    payload["trades"][0]["notional"] = float("inf")
    payload_path.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    result = validate_precompute_bundle(bundle_dir, trade_date="2026-05-15")

    assert result["status"] == "FAILED"
    assert {
        "$.trades[0].entry_price",
        "$.trades[0].notional",
    }.issubset(set(result["non_finite_values"][0]["paths"]))
    assert result["integrity_summary"]["non_finite_value_count"] == 2


def test_execution_self_heal_status_tracks_attempts_and_suppressed_side_effects(tmp_path: Path) -> None:
    previous = tmp_path / "execution_self_heal.json"
    previous.write_text(
        json.dumps({"trade_date": "2026-05-15", "recovery_attempt_count": 2}) + "\n",
        encoding="utf-8",
    )
    validation = {
        "status": "FAILED",
        "trade_date": "2026-05-15",
        "validated_at": "2026-05-15T12:00:00+00:00",
        "validation_failures": ["missing:signals.json"],
    }

    status = build_execution_self_heal_status(
        validation=validation,
        recovery_attempted=True,
        recovery_result="failed",
        execution_continued=False,
        previous_status_path=previous,
        recovery_started_at="2026-05-15T12:00:00Z",
        recovery_finished_at="2026-05-15T12:00:05Z",
    )

    assert status["recovery_attempt_count"] == 3
    assert status["execution_continued"] is False
    assert status["bundle_validation_result"] == "FAILED"
    assert status["validation_failures"] == ["missing:signals.json"]
    assert status["suppressed_side_effects"] == [
        "email",
        "shadow",
        "shadow_latest",
        "shadow_reconciliation",
    ]
    assert status["stale_degraded_visibility"]["potentially_stale_latest_shadow_artifacts"] is True
