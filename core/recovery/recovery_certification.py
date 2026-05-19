from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


VOLATILE_KEYS = {"generated_at", "recovery_timestamp", "completed_at"}


def canonicalize_for_certification(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: canonicalize_for_certification(item)
            for key, item in sorted(value.items())
            if key not in VOLATILE_KEYS
        }
    if isinstance(value, list):
        return [canonicalize_for_certification(item) for item in value]
    return value


def stable_hash(payload: Any) -> str:
    canonical = canonicalize_for_certification(payload)
    raw = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def certify_recovery_outputs(
    *,
    output_dir: Path,
    expected_files: set[str] | None = None,
    replay_count: int = 1,
) -> dict[str, Any]:
    expected = expected_files or {
        "recovery_simulation_summary.json",
        "recovery_delta.json",
        "recovery_validation.json",
        "execution_timeline.json",
        "state_transition_trace.json",
        "recovery_risk_report.json",
        "portfolio_drift.json",
        "eventual_settlement.json",
        "recovery_governance_report.json",
        "recovery_lineage.json",
        "lifecycle_graph.json",
    }
    failures: list[str] = []
    hashes: dict[str, str] = {}
    for name in sorted(expected):
        path = output_dir / name
        if not path.exists():
            failures.append(f"missing_artifact:{name}")
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            failures.append(f"artifact_parse_error:{name}:{exc}")
            continue
        hashes[name] = stable_hash(payload)

    summary_path = output_dir / "recovery_simulation_summary.json"
    governance_path = output_dir / "recovery_governance_report.json"
    transition_path = output_dir / "state_transition_trace.json"
    risk_path = output_dir / "recovery_risk_report.json"
    for path in (summary_path, governance_path, transition_path, risk_path):
        if not path.exists():
            continue
    if summary_path.exists():
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        if summary.get("replay_execution") is not False:
            failures.append("summary_allows_replay_execution")
        if summary.get("production_behavior_changed") is not False:
            failures.append("summary_indicates_production_behavior_changed")
    if governance_path.exists():
        governance = json.loads(governance_path.read_text(encoding="utf-8"))
        if governance.get("replay_prohibited") is not True:
            failures.append("governance_does_not_prohibit_replay")
    if transition_path.exists():
        transition = json.loads(transition_path.read_text(encoding="utf-8"))
        if transition.get("ok") is not True:
            failures.append("transition_trace_not_consistent")
    if risk_path.exists():
        risk = json.loads(risk_path.read_text(encoding="utf-8"))
        if risk.get("overall_risk") not in {"LOW", "MODERATE", "HIGH", "CRITICAL"}:
            failures.append("risk_score_invalid")

    return {
        "ok": not failures,
        "certified_at_replay_count": replay_count,
        "failures": failures,
        "artifact_hashes": hashes,
        "deterministic_hash": stable_hash(hashes),
        "certification_scope": "dev_only_recovery_intelligence",
    }

