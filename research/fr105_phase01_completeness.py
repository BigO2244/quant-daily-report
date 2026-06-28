"""FR-105 Phase 0/1 artifact completeness reporting.

This module reads FR-105 research artifacts and emits a deterministic
completeness report for future Alpha Chase evaluation. It does not invoke
allocation, optimization, sizing, execution, broker, scheduler, paper, or live
trading code.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from research.fr105_phase1_baseline import find_phase0_contract_path
from research.fr105_replay_contract import DEFAULT_OUTPUT_ROOT, FR_ID


PHASE01_COMPLETENESS_SCHEMA_VERSION = "fr105_phase01_artifact_completeness.v1"
ARTIFACT_NAME = "phase01_artifact_completeness.json"

READY = "READY"
READY_WITH_SCORE_SOURCE_UNAVAILABLE = "READY_WITH_SCORE_SOURCE_UNAVAILABLE"
BLOCKED_ARTIFACT_GAPS = "BLOCKED_ARTIFACT_GAPS"

FOUND = "FOUND"
MISSING = "MISSING"
UNAVAILABLE = "UNAVAILABLE"

VALID_STATUSES = {FOUND, MISSING, UNAVAILABLE}

REQUIRED_EVIDENCE_KEYS = (
    "candidate_universe",
    "candidate_pool",
    "pit_lineage",
    "score_source",
    "sleeve_source",
    "target_portfolio",
    "current_holdings",
    "current_weights",
    "target_weights",
    "suppression_reasons",
    "active_constraints",
    "lifecycle_artifact",
    "execution_residuals",
    "provenance_availability",
)

WEIGHT_DERIVED_SCORE_SOURCES = {
    "allocation",
    "allocation_weight",
    "allocation_weights",
    "construction_output",
    "final_allocation_weight",
    "final_target_weight",
    "notional",
    "portfolio_construction",
    "portfolio_weight",
    "rebalance_delta",
    "target_allocation_weight",
    "target_notional",
    "target_portfolio",
    "target_weight",
    "weight",
    "weights",
}

APPROVED_SCORE_SOURCE_FIELDS = (
    "score_source",
    "conviction_score_source",
    "expected_alpha_source",
)
SCORE_SOURCE_UNAVAILABLE_REASONS = {
    "score_fields_unavailable",
    "score_source_provenance_unavailable",
}


def _read_json(path: Path | None) -> Any:
    if path is None or not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _first_present(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", [], {}):
            return value
    return None


def _resolve_path(value: Any, repo_root: Path) -> Path | None:
    if value in (None, "", "unavailable"):
        return None
    path = Path(str(value))
    return path if path.is_absolute() else repo_root / path


def _relative(path: Path | None, repo_root: Path) -> str | None:
    if path is None:
        return None
    try:
        return path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return str(path)


def _sha256(path: Path | None) -> str | None:
    if path is None or not path.exists() or not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _mtime(path: Path | None) -> str | None:
    if path is None or not path.exists():
        return None
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _artifact_timestamp(path: Path | None) -> str | None:
    payload = _as_dict(_read_json(path))
    metadata = _as_dict(payload.get("metadata"))
    generated_at = metadata.get("generated_at")
    if generated_at not in (None, ""):
        return str(generated_at)
    return _mtime(path)


def _status_for_path(path: Path | None, *, expected: bool = True) -> str:
    if path is None:
        return UNAVAILABLE if not expected else MISSING
    return FOUND if path.exists() else MISSING


def _artifact_entry(path: Path | None, repo_root: Path, *, expected: bool = True) -> dict[str, Any]:
    status = _status_for_path(path, expected=expected)
    return {
        "path": _relative(path, repo_root),
        "status": status,
        "timestamp": _artifact_timestamp(path) if status == FOUND else None,
        "sha256": _sha256(path) if status == FOUND else None,
    }


def _source_path(contract: Mapping[str, Any], key: str, repo_root: Path) -> Path | None:
    return _resolve_path(_as_dict(contract.get("source_artifacts")).get(key), repo_root)


def _phase1_path(
    *,
    repo_root: Path,
    trade_date: str,
    run_id: str | None,
    input_baseline_path: Path | str | None,
    output_root: Path | str,
) -> Path | None:
    if input_baseline_path is not None:
        path = Path(input_baseline_path)
        if not path.is_absolute():
            path = repo_root / path
        return path if path.exists() else path
    root = Path(output_root)
    if not root.is_absolute():
        root = repo_root / root
    if run_id:
        return root / run_id / "phase1_current_policy_baseline.json"
    date_path = root / trade_date / "phase1_current_policy_baseline.json"
    if date_path.exists():
        return date_path
    matches: list[Path] = []
    for path in sorted(root.glob("*/phase1_current_policy_baseline.json")):
        payload = _as_dict(_read_json(path))
        if _as_dict(payload.get("metadata")).get("trade_date") == trade_date:
            matches.append(path)
    return matches[-1] if matches else date_path


def _field(status: str, *, evidence: Any = None, source: Any = None, reason: str | None = None) -> dict[str, Any]:
    if status not in VALID_STATUSES:
        raise ValueError(f"invalid completeness status: {status}")
    return {
        "status": status,
        "evidence": evidence if evidence is not None else "unavailable",
        "source": source if source is not None else "unavailable",
        "reason": reason if reason is not None else "unavailable",
    }


def _has_numeric_or_text(value: Any) -> bool:
    return value not in (None, "", "unavailable")


def _candidate_rows(contract: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [dict(row) for row in _as_list(contract.get("sleeve_candidates")) if isinstance(row, Mapping)]


def _selected_target_rows(contract: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [dict(row) for row in _as_list(contract.get("selected_target_candidates")) if isinstance(row, Mapping)]


def _candidate_pool_artifact_rows(contract: Mapping[str, Any], repo_root: Path) -> list[dict[str, Any]]:
    path = _source_path(contract, "candidate_pool_path", repo_root)
    payload = _as_dict(_read_json(path))
    return [dict(row) for row in _as_list(payload.get("candidates")) if isinstance(row, Mapping)]


def _position_rows(contract: Mapping[str, Any], baseline: Mapping[str, Any]) -> list[dict[str, Any]]:
    baseline_rows = _as_list(_as_dict(baseline.get("baseline_positions")).get("positions"))
    if baseline_rows:
        return [dict(row) for row in baseline_rows if isinstance(row, Mapping)]
    contract_rows = _as_list(_as_dict(contract.get("current_portfolio")).get("positions"))
    return [dict(row) for row in contract_rows if isinstance(row, Mapping)]


def _score_source_status(candidates: list[Mapping[str, Any]]) -> dict[str, Any]:
    if not candidates:
        return _field(UNAVAILABLE, reason="candidate_pool_unavailable")
    scored = 0
    prohibited = 0
    unprovenanced = 0
    sources: set[str] = set()
    for row in candidates:
        has_score = any(
            _has_numeric_or_text(row.get(key))
            for key in ("conviction_score", "score", "expected_alpha")
        )
        if not has_score:
            continue
        source = str(_first_present(*(row.get(key) for key in APPROVED_SCORE_SOURCE_FIELDS)) or "").strip()
        source_key = source.lower()
        if not source or source_key == "unavailable":
            unprovenanced += 1
            continue
        if source_key in WEIGHT_DERIVED_SCORE_SOURCES:
            prohibited += 1
            continue
        scored += 1
        sources.add(source)
    if prohibited:
        return _field(
            MISSING,
            evidence={
                "score_backed_candidates": scored,
                "prohibited_weight_derived_score_candidates": prohibited,
                "unprovenanced_score_candidates": unprovenanced,
            },
            reason="weight_derived_score_source_present",
        )
    if unprovenanced:
        return _field(
            UNAVAILABLE,
            evidence={
                "score_backed_candidates": scored,
                "unprovenanced_score_candidates": unprovenanced,
                "score_sources": sorted(sources),
            },
            reason="score_source_provenance_unavailable",
        )
    if scored:
        return _field(FOUND, evidence={"score_backed_candidates": scored, "score_sources": sorted(sources)})
    return _field(UNAVAILABLE, reason="score_fields_unavailable")


def _required_evidence(
    *,
    contract: Mapping[str, Any],
    baseline: Mapping[str, Any],
    repo_root: Path,
) -> dict[str, dict[str, Any]]:
    source_artifacts = _as_dict(contract.get("source_artifacts"))
    candidates = _candidate_rows(contract)
    pool_artifact_rows = _candidate_pool_artifact_rows(contract, repo_root)
    selected_targets = _selected_target_rows(contract)
    target_source_rows = candidates + selected_targets
    positions = _position_rows(contract, baseline)
    universe = _as_dict(contract.get("universe_snapshot"))
    controls = _as_dict(baseline.get("pit_controls"))
    residuals = _as_dict(contract.get("execution_residuals"))
    constraints = _as_dict(contract.get("constraints_snapshot"))

    target_path = _source_path(contract, "target_portfolio_path", repo_root)
    lifecycle_path = _source_path(contract, "candidate_trade_lifecycle_path", repo_root)
    candidate_universe_path = _source_path(contract, "candidate_universe_path", repo_root)
    candidate_pool_path = _source_path(contract, "candidate_pool_path", repo_root)
    candidate_universe_artifact = _as_dict(_read_json(candidate_universe_path))
    candidate_pool_artifact = _as_dict(_read_json(candidate_pool_path))

    candidate_universe_found = any(
        _has_numeric_or_text(value)
        for value in (universe.get("universe_id"), universe.get("ticker_count"), universe.get("source_artifact_path"))
    ) and candidate_universe_path is not None and candidate_universe_path.exists() and _as_dict(candidate_universe_artifact.get("readiness")).get("status") == FOUND
    candidate_pool_found = bool(
        candidate_pool_path is not None
        and candidate_pool_path.exists()
        and _as_dict(candidate_pool_artifact.get("readiness")).get("status") == FOUND
        and (candidates or pool_artifact_rows)
    )
    pit_values = {
        "data_asof": controls.get("data_asof") or _first_present(*(row.get("data_asof") for row in target_source_rows)),
        "universe_asof": controls.get("universe_asof") or universe.get("asof"),
        "price_asof": controls.get("price_asof") or _as_dict(contract.get("metadata")).get("price_asof"),
    }
    target_weight_count = sum(
        1
        for row in target_source_rows
        if _has_numeric_or_text(_first_present(row.get("target_weight"), row.get("final_target_weight")))
    )
    current_weight_count = sum(
        1
        for row in positions
        if _has_numeric_or_text(row.get("current_weight")) or _has_numeric_or_text(row.get("weight"))
    )
    suppression_reasons = dict(_as_dict(residuals.get("suppression_reason_counts")))
    for row in candidates:
        reason = row.get("reason_excluded") or _as_dict(row.get("lifecycle")).get("decision_reason")
        if reason:
            key = str(reason)
            suppression_reasons[key] = suppression_reasons.get(key, 0) + 1
    residual_count = sum(
        1
        for key in (
            "planned_candidates",
            "executable_candidates",
            "intended_orders",
            "submitted_orders",
            "filled_orders",
            "suppressed_count",
            "clipped_count",
            "estimated_unexecuted_notional_total",
        )
        if _has_numeric_or_text(residuals.get(key))
    )
    active_constraints = {
        key: value
        for key, value in constraints.items()
        if value not in (None, "", "unavailable", [], {})
    }

    return {
        "candidate_universe": _field(
            FOUND if candidate_universe_found else UNAVAILABLE,
            evidence={
                "artifact": _artifact_entry(candidate_universe_path, repo_root, expected=bool(source_artifacts.get("candidate_universe_path"))),
                "universe_snapshot": {
                    key: universe.get(key)
                    for key in ("status", "universe_id", "asof", "ticker_count", "source_artifact_path")
                },
                "candidate_universe_count": candidate_universe_artifact.get("candidate_universe_count"),
            },
            source="source_artifacts.candidate_universe_path",
            reason=None if candidate_universe_found else "candidate_universe_artifact_unavailable",
        ),
        "candidate_pool": _field(
            FOUND if candidate_pool_found else UNAVAILABLE,
            evidence={
                "artifact": _artifact_entry(candidate_pool_path, repo_root, expected=bool(source_artifacts.get("candidate_pool_path"))),
                "candidate_count": len(candidates) or len(pool_artifact_rows),
                "contract_candidate_count": len(candidates),
                "artifact_candidate_count": len(pool_artifact_rows),
            },
            source="source_artifacts.candidate_pool_path",
            reason=None if candidate_pool_found else "candidate_pool_artifact_unavailable",
        ),
        "pit_lineage": _field(
            FOUND if all(_has_numeric_or_text(value) for value in pit_values.values()) else UNAVAILABLE,
            evidence=pit_values,
            source="phase1.pit_controls plus phase0 universe/candidates",
            reason=None if all(_has_numeric_or_text(value) for value in pit_values.values()) else "one_or_more_asof_fields_unavailable",
        ),
        "score_source": _score_source_status(candidates),
        "sleeve_source": _field(
            FOUND if any(row.get("sleeve_id") or row.get("strategy_id") or row.get("source_model") for row in target_source_rows) else UNAVAILABLE,
            evidence={
                "candidate_count_with_sleeve_source": sum(
                    1
                    for row in target_source_rows
                    if row.get("sleeve_id") or row.get("strategy_id") or row.get("source_model")
                ),
                "canonical_candidate_count": len(candidates),
                "selected_target_count": len(selected_targets),
            },
            source="global_optimizer_replay_contract.sleeve_candidates or selected_target_candidates",
            reason=None if target_source_rows else "candidate_and_selected_target_sources_unavailable",
        ),
        "target_portfolio": _field(
            FOUND if target_path is not None and target_path.exists() else MISSING if source_artifacts.get("target_portfolio_path") else UNAVAILABLE,
            evidence=_artifact_entry(target_path, repo_root, expected=bool(source_artifacts.get("target_portfolio_path"))),
            source="source_artifacts.target_portfolio_path",
            reason=None if target_path is not None and target_path.exists() else "target_portfolio_artifact_unavailable",
        ),
        "current_holdings": _field(
            FOUND if positions else UNAVAILABLE,
            evidence={"position_count": len(positions), "positions_count_field": _first_present(_as_dict(baseline.get("baseline_positions")).get("positions_count"), _as_dict(contract.get("current_portfolio")).get("positions_count"))},
            source="phase1.baseline_positions or phase0.current_portfolio",
            reason=None if positions else "current_holdings_unavailable",
        ),
        "current_weights": _field(
            FOUND if current_weight_count else UNAVAILABLE,
            evidence={"current_weight_count": current_weight_count},
            source="phase1.baseline_positions or phase0.current_portfolio",
            reason=None if current_weight_count else "current_weights_unavailable",
        ),
        "target_weights": _field(
            FOUND if target_weight_count else UNAVAILABLE,
            evidence={
                "target_weight_count": target_weight_count,
                "canonical_candidate_count": len(candidates),
                "selected_target_count": len(selected_targets),
            },
            source="global_optimizer_replay_contract.sleeve_candidates or selected_target_candidates",
            reason=None if target_weight_count else "target_weights_unavailable",
        ),
        "suppression_reasons": _field(
            FOUND if suppression_reasons else UNAVAILABLE,
            evidence=suppression_reasons,
            source="phase0.execution_residuals and candidate lifecycle fields",
            reason=None if suppression_reasons else "suppression_reasons_unavailable",
        ),
        "active_constraints": _field(
            FOUND if active_constraints else UNAVAILABLE,
            evidence=active_constraints,
            source="global_optimizer_replay_contract.constraints_snapshot",
            reason=None if active_constraints else "constraints_snapshot_sparse",
        ),
        "lifecycle_artifact": _field(
            FOUND if lifecycle_path is not None and lifecycle_path.exists() else MISSING if source_artifacts.get("candidate_trade_lifecycle_path") else UNAVAILABLE,
            evidence=_artifact_entry(lifecycle_path, repo_root, expected=bool(source_artifacts.get("candidate_trade_lifecycle_path"))),
            source="source_artifacts.candidate_trade_lifecycle_path",
            reason=None if lifecycle_path is not None and lifecycle_path.exists() else "candidate_trade_lifecycle_artifact_unavailable",
        ),
        "execution_residuals": _field(
            FOUND if residual_count else UNAVAILABLE,
            evidence={key: residuals.get(key) for key in sorted(residuals)},
            source="global_optimizer_replay_contract.execution_residuals",
            reason=None if residual_count else "execution_residuals_sparse",
        ),
        "provenance_availability": _field(
            FOUND if contract.get("provenance_schema_version") else UNAVAILABLE,
            evidence={"provenance_schema_version": contract.get("provenance_schema_version"), "phase0_validation": _as_dict(contract.get("validation_status")).get("status")},
            source="global_optimizer_replay_contract",
            reason=None if contract.get("provenance_schema_version") else "provenance_schema_version_unavailable",
        ),
    }


def _summary(required: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    by_status = {
        status: sorted(key for key, value in required.items() if value.get("status") == status)
        for status in (FOUND, MISSING, UNAVAILABLE)
    }
    complete = not by_status[MISSING] and not by_status[UNAVAILABLE]
    return {
        "status": "COMPLETE" if complete else "INCOMPLETE",
        "complete": complete,
        "field_count": len(required),
        "found_count": len(by_status[FOUND]),
        "missing_count": len(by_status[MISSING]),
        "unavailable_count": len(by_status[UNAVAILABLE]),
        "missing_fields": by_status[MISSING],
        "unavailable_fields": by_status[UNAVAILABLE],
        "found_fields": by_status[FOUND],
    }


def _score_source_unavailable_waiver(
    required: Mapping[str, Mapping[str, Any]],
    summary: Mapping[str, Any],
) -> dict[str, Any]:
    unavailable_fields = set(_as_list(summary.get("unavailable_fields")))
    missing_fields = set(_as_list(summary.get("missing_fields")))
    score_source = _as_dict(required.get("score_source"))
    waived = (
        not missing_fields
        and unavailable_fields == {"score_source"}
        and score_source.get("status") == UNAVAILABLE
        and score_source.get("reason") in SCORE_SOURCE_UNAVAILABLE_REASONS
    )
    return {
        "applied": bool(waived),
        "waived_fields": ["score_source"] if waived else [],
        "reason": (
            "historical_non_weight_score_source_not_retained"
            if waived
            else "not_applicable"
        ),
        "score_driven_ranking_replayable": False if waived else None,
        "prohibited_score_sources": sorted(WEIGHT_DERIVED_SCORE_SOURCES),
    }


def _readiness_block(required: Mapping[str, Mapping[str, Any]], summary: Mapping[str, Any]) -> dict[str, Any]:
    waiver = _score_source_unavailable_waiver(required, summary)
    complete = bool(summary["complete"])
    status = (
        READY
        if complete
        else READY_WITH_SCORE_SOURCE_UNAVAILABLE
        if waiver["applied"]
        else BLOCKED_ARTIFACT_GAPS
    )
    blocking_gaps = (
        []
        if status in {READY, READY_WITH_SCORE_SOURCE_UNAVAILABLE}
        else sorted(summary["missing_fields"] + summary["unavailable_fields"])
    )
    score_driven_ranking_replayable = (
        _as_dict(required.get("score_source")).get("status") == FOUND
    )
    return {
        "alpha_chase_evaluation_ready": complete,
        "historical_replay_ready": complete or bool(waiver["applied"]),
        "shadow_comparison_ready": complete,
        "score_driven_ranking_replayable": score_driven_ranking_replayable,
        "status": status,
        "blocking_gaps": blocking_gaps,
        "waived_gaps": waiver["waived_fields"],
        "score_source_unavailable_waiver": waiver,
    }


def build_fr105_phase01_completeness(
    *,
    repo_root: Path | str,
    trade_date: str,
    run_id: str | None = None,
    input_contract_path: Path | str | None = None,
    input_baseline_path: Path | str | None = None,
    output_root: Path | str = DEFAULT_OUTPUT_ROOT,
    generated_at: str = "unavailable",
) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    phase0_path = find_phase0_contract_path(
        repo_root=root,
        trade_date=trade_date,
        run_id=run_id,
        input_contract_path=input_contract_path,
        output_root=output_root,
    )
    phase1_path = _phase1_path(
        repo_root=root,
        trade_date=trade_date,
        run_id=run_id,
        input_baseline_path=input_baseline_path,
        output_root=output_root,
    )
    phase0 = _as_dict(_read_json(phase0_path))
    phase1 = _as_dict(_read_json(phase1_path))
    contract_id = str(
        _first_present(
            _as_dict(phase0.get("metadata")).get("contract_id"),
            _as_dict(phase1.get("metadata")).get("contract_id"),
            run_id,
            trade_date,
        )
    )
    required = _required_evidence(contract=phase0, baseline=phase1, repo_root=root)
    summary = _summary(required)
    readiness = _readiness_block(required, summary)
    phase0_entry = _artifact_entry(phase0_path, root)
    phase1_entry = _artifact_entry(phase1_path, root)
    report = {
        "schema_version": PHASE01_COMPLETENESS_SCHEMA_VERSION,
        "metadata": {
            "trade_date": trade_date,
            "run_id": run_id,
            "contract_id": contract_id,
            "generated_at": generated_at,
            "mode": "research_only",
            "fr_id": FR_ID,
            "alpha_chase_default": "off",
            "trading_behavior_changed": False,
            "optimizer_behavior_changed": False,
            "broker_behavior_changed": False,
            "sizing_behavior_changed": False,
            "paper_behavior_changed": False,
            "live_pilot_behavior_changed": False,
            "production_execution_modules_invoked": [],
        },
        "source_artifacts": {
            "phase0_replay_contract": phase0_entry,
            "phase1_current_policy_baseline": phase1_entry,
        },
        "phase_status": {
            "phase0": "COMPLETE" if phase0_entry["status"] == FOUND and phase0 else "MISSING",
            "phase1": "COMPLETE" if phase1_entry["status"] == FOUND and phase1 else "MISSING",
        },
        "required_evidence": required,
        "summary": summary,
        "readiness": readiness,
        "validation_status": {
            "status": "PASS",
            "findings": [],
            "warnings": (
                []
                if summary["complete"]
                else [
                    "score_source_unavailable_historical_waiver_score_driven_ranking_not_replayable"
                ]
                if readiness["status"] == READY_WITH_SCORE_SOURCE_UNAVAILABLE
                else ["artifact_gaps_block_alpha_chase_evaluation"]
            ),
        },
    }
    return report


def write_fr105_phase01_completeness(
    *,
    repo_root: Path | str,
    trade_date: str,
    run_id: str | None = None,
    input_contract_path: Path | str | None = None,
    input_baseline_path: Path | str | None = None,
    output_root: Path | str = DEFAULT_OUTPUT_ROOT,
    generated_at: str = "unavailable",
) -> tuple[Path, dict[str, Any]]:
    payload = build_fr105_phase01_completeness(
        repo_root=repo_root,
        trade_date=trade_date,
        run_id=run_id,
        input_contract_path=input_contract_path,
        input_baseline_path=input_baseline_path,
        output_root=output_root,
        generated_at=generated_at,
    )
    root = Path(repo_root).resolve()
    out_root = Path(output_root)
    if not out_root.is_absolute():
        out_root = root / out_root
    out_path = out_root / str(payload["metadata"]["contract_id"]) / ARTIFACT_NAME
    _write_json(out_path, payload)
    return out_path, payload
