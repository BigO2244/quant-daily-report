from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core.recovery.interrupted_state import (
    ExecutionLifecycleState,
    InterruptedRunSnapshot,
    utc_now_iso,
)
from core.recovery.recovery_delta import RecoveryDeltaOrder
from core.recovery.recovery_validator import RecoveryValidationResult


def _json_default(value: Any) -> Any:
    if hasattr(value, "value"):
        return value.value
    if hasattr(value, "__dict__"):
        return value.__dict__
    return str(value)


def write_json(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=_json_default) + "\n",
        encoding="utf-8",
    )
    return path


def build_simulation_artifact(
    *,
    snapshot: InterruptedRunSnapshot,
    lifecycle_state: ExecutionLifecycleState,
    target_positions: dict[str, float],
    recovery_delta: list[RecoveryDeltaOrder],
    validation: RecoveryValidationResult,
    drift_summary: dict[str, object],
    recovery_id: str,
) -> dict[str, Any]:
    simulated_orders = [
        {
            "symbol": order.symbol,
            "side": order.side,
            "qty": order.qty,
            "planned_notional": order.planned_notional,
            "reason": order.reason,
            "simulated_client_order_id": (
                f"{snapshot.trade_date}:{recovery_id}:BUY_ONLY_NORMALIZATION:{order.symbol}:BUY"
                if order.side.upper() == "BUY"
                else None
            ),
        }
        for order in recovery_delta
    ]
    return {
        "source_failed_run_id": snapshot.source_run_id,
        "trade_date": snapshot.trade_date,
        "generated_at": utc_now_iso(),
        "recovery_mode": "BUY_ONLY_NORMALIZATION",
        "dry_run": True,
        "operator_supervised_required": True,
        "replay_execution": False,
        "lifecycle_state": lifecycle_state.value,
        "target_positions": target_positions,
        "current_positions": snapshot.current_broker_state.positions,
        "recovery_delta": simulated_orders,
        "validation": {
            "ok": validation.ok,
            "failures": validation.failures,
            "warnings": validation.warnings,
        },
        "drift_summary": drift_summary,
        "verdict": "SIMULATION_PASS" if validation.ok else "SIMULATION_BLOCKED",
        "production_behavior_changed": False,
    }


def build_lifecycle_markdown(
    *,
    artifact: dict[str, Any],
    risk_report: dict[str, Any] | None = None,
) -> str:
    risk = risk_report or {}
    lines = [
        "# Lifecycle Summary",
        "",
        f"- Source failed run: `{artifact['source_failed_run_id']}`",
        f"- Trade date: `{artifact['trade_date']}`",
        f"- Lifecycle state: `{artifact['lifecycle_state']}`",
        f"- Simulation verdict: `{artifact['verdict']}`",
        f"- Recovery orders modeled: {len(artifact.get('recovery_delta') or [])}",
        f"- Overall recovery risk: `{risk.get('overall_risk', 'UNKNOWN')}`",
        "- Dry run only: `true`",
        "- Operator action required before any future real recovery: `true`",
    ]
    return "\n".join(lines) + "\n"


def build_decision_trace_markdown(
    *,
    artifact: dict[str, Any],
    risk_report: dict[str, Any] | None = None,
) -> str:
    risk = risk_report or {}
    validation = artifact.get("validation") or {}
    lines = [
        "# Recovery Decision Trace",
        "",
        "## Classification",
        f"- Lifecycle state: `{artifact['lifecycle_state']}`",
        f"- Verdict: `{artifact['verdict']}`",
        "",
        "## Validation",
        f"- Validation passed: `{str(validation.get('ok')).lower()}`",
        f"- Failures: `{validation.get('failures') or []}`",
        f"- Warnings: `{validation.get('warnings') or []}`",
        "",
        "## Risk",
        f"- Overall risk: `{risk.get('overall_risk', 'UNKNOWN')}`",
        "",
        "## Recovery Delta",
    ]
    for order in artifact.get("recovery_delta") or []:
        lines.append(
            f"- `{order['side']} {order['symbol']} {order['qty']}` "
            f"client_id=`{order.get('simulated_client_order_id')}`"
        )
    if not artifact.get("recovery_delta"):
        lines.append("- No recovery delta modeled.")
    return "\n".join(lines) + "\n"


def write_simulation_artifacts(
    *,
    output_dir: Path,
    artifact: dict[str, Any],
    execution_timeline: dict[str, Any] | None = None,
    state_transition_trace: dict[str, Any] | None = None,
    recovery_risk_report: dict[str, Any] | None = None,
    portfolio_drift: dict[str, Any] | None = None,
    eventual_settlement: dict[str, Any] | None = None,
    recovery_governance_report: dict[str, Any] | None = None,
    recovery_lineage: dict[str, Any] | None = None,
    lifecycle_graph: dict[str, Any] | None = None,
    transition_matrix: dict[str, Any] | None = None,
    recovery_certification_summary: dict[str, Any] | None = None,
) -> list[Path]:
    paths = [
        write_json(output_dir / "recovery_simulation_summary.json", artifact),
        write_json(
            output_dir / "recovery_delta.json",
            {
                "source_failed_run_id": artifact["source_failed_run_id"],
                "trade_date": artifact["trade_date"],
                "dry_run": True,
                "recovery_delta": artifact["recovery_delta"],
            },
        ),
        write_json(
            output_dir / "recovery_validation.json",
            {
                "source_failed_run_id": artifact["source_failed_run_id"],
                "trade_date": artifact["trade_date"],
                "dry_run": True,
                "validation": artifact["validation"],
                "drift_summary": artifact["drift_summary"],
            },
        ),
    ]
    if execution_timeline is not None:
        paths.append(write_json(output_dir / "execution_timeline.json", execution_timeline))
    if state_transition_trace is not None:
        paths.append(write_json(output_dir / "state_transition_trace.json", state_transition_trace))
    if recovery_risk_report is not None:
        paths.append(write_json(output_dir / "recovery_risk_report.json", recovery_risk_report))
    if portfolio_drift is not None:
        paths.append(write_json(output_dir / "portfolio_drift.json", portfolio_drift))
    if eventual_settlement is not None:
        paths.append(write_json(output_dir / "eventual_settlement.json", eventual_settlement))
    if recovery_governance_report is not None:
        paths.append(write_json(output_dir / "recovery_governance_report.json", recovery_governance_report))
    if recovery_lineage is not None:
        paths.append(write_json(output_dir / "recovery_lineage.json", recovery_lineage))
    if lifecycle_graph is not None:
        paths.append(write_json(output_dir / "lifecycle_graph.json", lifecycle_graph))
    if transition_matrix is not None:
        paths.append(write_json(output_dir / "transition_matrix.json", transition_matrix))
    if recovery_certification_summary is not None:
        paths.append(write_json(output_dir / "recovery_certification_summary.json", recovery_certification_summary))

    lifecycle_summary = output_dir / "lifecycle_summary.md"
    lifecycle_summary.write_text(
        build_lifecycle_markdown(
            artifact=artifact,
            risk_report=recovery_risk_report,
        ),
        encoding="utf-8",
    )
    paths.append(lifecycle_summary)

    decision_trace = output_dir / "recovery_decision_trace.md"
    decision_trace.write_text(
        build_decision_trace_markdown(
            artifact=artifact,
            risk_report=recovery_risk_report,
        ),
        encoding="utf-8",
    )
    paths.append(decision_trace)

    state_diagram = output_dir / "lifecycle_state_diagram.md"
    state_diagram.write_text(
        "\n".join(
            [
                "# Lifecycle State Diagram",
                "",
                "```text",
                "NORMAL_EXECUTION",
                "  -> SELL_PHASE_TIMEOUT",
                "  -> SETTLEMENT_PENDING",
                "  -> RECOVERY_CANDIDATE",
                "  -> RECOVERY_SIMULATED",
                "  -> RECOVERY_APPROVED",
                "  -> RECOVERY_EXECUTED",
                "  -> RECOVERY_RECONCILED",
                "```",
                "",
                "This diagram is informational for dev-only recovery analysis.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    paths.append(state_diagram)

    flow_summary = output_dir / "recovery_flow_summary.md"
    flow_summary.write_text(
        "\n".join(
            [
                "# Recovery Flow Summary",
                "",
                "- Historical artifacts remain immutable.",
                "- Broker-authoritative state dominates recovery analysis.",
                "- Simulation is dry-run only.",
                "- Real recovery would require separate operator approval and a supervised event.",
                "- Automatic replay is prohibited.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    paths.append(flow_summary)

    notes = output_dir / "operator_notes.md"
    notes.write_text(
        "\n".join(
            [
                "# Recovery Simulation",
                "",
                f"- Source failed run: `{artifact['source_failed_run_id']}`",
                f"- Lifecycle state: `{artifact['lifecycle_state']}`",
                f"- Verdict: `{artifact['verdict']}`",
                "- Dry run only: `true`",
                "- Replay execution: `false`",
                "- Production behavior changed: `false`",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    paths.append(notes)
    return paths
