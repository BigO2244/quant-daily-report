from __future__ import annotations

import copy
import hashlib

import pytest

from core.accounting_journal import canonical_json
from core.lane_execution_dry_run import (
    LaneExecutionDryRunError,
    build_lane_execution_safety_evidence,
    run_lane_execution_dry_run,
    validate_lane_execution_dry_run_result,
)
from core.lane_oms_store import read_lane_oms_store
from Tests.test_lane_reconciliation import _plan


def _source(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


@pytest.mark.parametrize(("lane_kind", "lane_id"), [("PAPER", "paper"), ("LIVE", "live-small")])
def test_green_safety_runs_complete_broker_disabled_lifecycle_without_write(
    tmp_path, lane_kind: str, lane_id: str
) -> None:
    plan, _, _, _ = _plan(lane_kind=lane_kind, lane_id=lane_id)
    evidence = build_lane_execution_safety_evidence(
        exact_plan=plan,
        checked_at="2026-08-18T11:08:30+00:00",
        source_hashes=[_source("preflight")],
    )
    wal = tmp_path / "advisory.jsonl"

    result = run_lane_execution_dry_run(
        exact_plan=plan, safety_evidence=evidence, wal_path=wal
    )

    expected_orders = len(plan["sell_orders"]) + len(plan["buy_orders"])
    assert result["status"] == "VALIDATED_NO_WRITE"
    assert len(result["oms_intent_hashes"]) == expected_orders
    assert result["broker_call_performed"] is False
    assert result["broker_submission_allowed"] is False
    assert not wal.exists()


def test_explicit_advisory_write_is_idempotent_and_never_broker_capable(tmp_path) -> None:
    plan, _, _, _ = _plan()
    evidence = build_lane_execution_safety_evidence(
        exact_plan=plan,
        checked_at="2026-08-18T11:08:30+00:00",
        source_hashes=[_source("preflight")],
    )
    wal = tmp_path / "advisory.jsonl"

    first = run_lane_execution_dry_run(
        exact_plan=plan,
        safety_evidence=evidence,
        wal_path=wal,
        write_enabled=True,
    )
    second = run_lane_execution_dry_run(
        exact_plan=plan,
        safety_evidence=evidence,
        wal_path=wal,
        write_enabled=True,
    )

    expected_records = 3 * (len(plan["sell_orders"]) + len(plan["buy_orders"]))
    assert first["status"] == "ADVISORY_WAL_WRITTEN"
    assert first["written_record_count"] == expected_records
    assert second["status"] == "ADVISORY_WAL_IDEMPOTENT"
    assert second["written_record_count"] == 0
    assert len(read_lane_oms_store(wal, exact_plan=plan, require_complete_lifecycle=True)) == expected_records
    assert all(row["broker_submission_allowed"] is False for row in read_lane_oms_store(wal))


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("kill_switch_state", "DISENGAGED"),
        ("account_pin_status", "MISMATCH"),
        ("deployment_sha_status", "MISMATCH"),
        ("open_order_status", "AMBIGUOUS"),
        ("leverage_status", "ENABLED"),
        ("shorting_status", "ENABLED"),
        ("capital_ceiling_status", "EXCEEDED"),
        ("credential_mode", "WRITE_ENABLED"),
    ],
)
def test_each_safety_break_blocks_before_oms_and_write(tmp_path, field: str, bad_value: str) -> None:
    plan, _, _, _ = _plan()
    kwargs = {field: bad_value}
    evidence = build_lane_execution_safety_evidence(
        exact_plan=plan,
        checked_at="2026-08-18T11:08:30+00:00",
        source_hashes=[_source("preflight")],
        **kwargs,
    )
    wal = tmp_path / "blocked.jsonl"
    blocked = run_lane_execution_dry_run(
        exact_plan=plan, safety_evidence=evidence, wal_path=wal
    )

    assert blocked["status"] == "BLOCKED"
    assert blocked["gate_results"][field] == "BLOCK"
    assert blocked["oms_intent_hashes"] == []
    assert not wal.exists()
    with pytest.raises(LaneExecutionDryRunError, match="cannot persist"):
        run_lane_execution_dry_run(
            exact_plan=plan,
            safety_evidence=evidence,
            wal_path=wal,
            write_enabled=True,
        )


def test_resealed_scope_or_authority_tamper_fails_closed() -> None:
    plan, _, _, _ = _plan()
    evidence = build_lane_execution_safety_evidence(
        exact_plan=plan,
        checked_at="2026-08-18T11:08:30+00:00",
        source_hashes=[_source("preflight")],
    )
    other_plan, _, _, _ = _plan(lane_kind="LIVE", lane_id="live-small")
    with pytest.raises(LaneExecutionDryRunError, match="scope differs"):
        run_lane_execution_dry_run(
            exact_plan=other_plan, safety_evidence=evidence
        )

    result = run_lane_execution_dry_run(exact_plan=plan, safety_evidence=evidence)
    tampered = copy.deepcopy(result)
    tampered["broker_submission_allowed"] = True
    tampered["content_hash"] = hashlib.sha256(
        canonical_json(
            {key: value for key, value in tampered.items() if key != "content_hash"}
        ).encode("utf-8")
    ).hexdigest()
    with pytest.raises(LaneExecutionDryRunError, match="cannot call a broker"):
        validate_lane_execution_dry_run_result(tampered)


def test_safety_evidence_must_be_checked_within_exact_plan_window() -> None:
    plan, _, _, _ = _plan()
    with pytest.raises(LaneExecutionDryRunError, match="validity window"):
        build_lane_execution_safety_evidence(
            exact_plan=plan,
            checked_at="2026-08-18T11:00:30+00:00",
            source_hashes=[_source("stale-preflight")],
        )
