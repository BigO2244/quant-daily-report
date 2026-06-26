from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any, Mapping


def _first_present(*values: Any) -> Any:
    for value in values:
        if value not in (None, ""):
            return value
    return None


def _safe_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _resolve_path(value: Any, repo_root: Path | str | None = None) -> Path | None:
    raw = str(value or "").strip()
    if not raw or raw == "unavailable":
        return None
    path = Path(raw)
    if path.is_absolute():
        return path
    return (Path(repo_root) if repo_root is not None else Path.cwd()) / path


def _artifact_payload(
    context: Mapping[str, Any],
    key: str,
    repo_root: Path | str | None = None,
) -> tuple[dict[str, Any], str | None]:
    raw_path = context.get(key)
    path = _resolve_path(raw_path, repo_root)
    if path is None:
        return {}, None
    return _safe_json(path), str(raw_path)


def _artifact_status(
    context: Mapping[str, Any],
    key: str,
    repo_root: Path | str | None = None,
) -> str | None:
    raw = str(context.get(key) or "").strip()
    if not raw:
        return None
    path = _resolve_path(raw, repo_root)
    if path is None:
        return None
    return "FOUND" if path.exists() else "MISSING"


def _fmt_value(value: Any) -> str:
    if value in (None, ""):
        return "unavailable"
    if isinstance(value, bool):
        return "YES" if value else "NO"
    return str(value)


def _fmt_pct_weight(value: Any) -> str:
    if value in (None, ""):
        return "unavailable"
    try:
        return f"{float(value) * 100.0:.2f}%"
    except Exception:
        return "unavailable"


def _fmt_number(value: Any, digits: int = 2) -> str:
    if value in (None, ""):
        return "unavailable"
    try:
        return f"{float(value):.{digits}f}"
    except Exception:
        return "unavailable"


def _fmt_money(value: Any) -> str:
    if value in (None, ""):
        return "unavailable"
    try:
        return f"${float(value):,.2f}"
    except Exception:
        return "unavailable"


def _join_values(value: Any) -> str:
    if value in (None, ""):
        return "unavailable"
    if isinstance(value, list):
        if not value:
            return "none"
        rendered: list[str] = []
        for item in value:
            if isinstance(item, Mapping):
                ticker = str(item.get("symbol") or item.get("ticker") or "").strip()
                reason = str(item.get("reason") or item.get("reason_code") or "").strip()
                side = str(item.get("side") or "").strip()
                parts = [part for part in (ticker, side, reason) if part]
                rendered.append(":".join(parts) if parts else json.dumps(dict(item), sort_keys=True))
            else:
                rendered.append(str(item))
        return "; ".join(rendered) if rendered else "none"
    return str(value)


def merge_run_reporting_context(
    results: Mapping[str, Any],
    results_path: Path,
    repo_root: Path | str | None = None,
) -> dict[str, Any]:
    context = dict(results)
    run_root = results_path.parent if results_path.name in {"execution_results.json", "execution_payload.json"} else None
    if run_root is not None:
        operator_summary = _safe_json(run_root / "operator_summary.json")
        for key, value in operator_summary.items():
            context.setdefault(key, value)

        trade_date = str(context.get("trade_date") or "").strip()
        if trade_date:
            fallback_paths = {
                "execution_reliability_artifact": run_root / "audit" / f"execution_reliability_report_{trade_date}.json",
                "execution_target_attainment_artifact": run_root / "audit" / f"execution_target_attainment_{trade_date}.json",
                "construction_provenance_artifact": run_root / "audit" / f"construction_provenance_{trade_date}.json",
            }
            research_root = (Path(repo_root) if repo_root is not None else Path.cwd()) / "outputs" / "research" / "fr_105"
            fallback_paths["fr105_phase01_completeness_artifact"] = (
                research_root / trade_date / "phase01_artifact_completeness.json"
            )
            fallback_paths["fr105_shadow_alpha_framework_artifact"] = (
                research_root / "shadow_alpha_chase_framework.json"
            )
            fallback_paths["fr105_phase2_topn_frontier_artifact"] = (
                research_root / trade_date / "phase2_global_topn_frontier.json"
            )
            fallback_paths["fr105_phase3_holding_count_artifact"] = (
                research_root / trade_date / "phase3_optimizer_derived_holding_count.json"
            )
            fallback_paths["fr105_shadow_alpha_chase_comparison_artifact"] = (
                research_root / trade_date / "shadow_alpha_chase_comparison.json"
            )
            for key, path in fallback_paths.items():
                if not context.get(key) and path.exists():
                    context[key] = str(path)

    if repo_root is not None:
        context.setdefault("repo_root", str(repo_root))
    return context


def execution_reliability_rows(
    context: Mapping[str, Any],
    repo_root: Path | str | None = None,
) -> list[list[str]]:
    artifact, artifact_path = _artifact_payload(context, "execution_reliability_artifact", repo_root)
    artifact_status = _artifact_status(context, "execution_reliability_artifact", repo_root)
    trend = artifact.get("trend_metrics") if isinstance(artifact.get("trend_metrics"), Mapping) else {}
    actions = _first_present(
        context.get("execution_reliability_actions"),
        artifact.get("recommended_operator_actions"),
    )
    top_reason = _first_present(
        context.get("execution_reliability_top_reason"),
        artifact.get("top_failure_reason"),
    )
    if top_reason is None and artifact:
        top_reason = "none"
    top_invariant = _first_present(
        context.get("execution_reliability_top_invariant"),
        artifact.get("top_failure_invariant_id"),
    )
    if top_invariant is None and artifact:
        top_invariant = "none"
    rows = [
        [
            "Status",
            _fmt_value(_first_present(context.get("execution_reliability_status"), artifact.get("overall_status"))),
        ],
        [
            "Classification",
            _fmt_value(_first_present(context.get("execution_reliability_classification"), artifact.get("classification"))),
        ],
        [
            "Score",
            _fmt_number(_first_present(context.get("execution_reliability_score"), artifact.get("score")), digits=1),
        ],
        [
            "Top reason",
            _fmt_value(top_reason),
        ],
        [
            "Top invariant",
            _fmt_value(top_invariant),
        ],
        [
            "Clean run streak",
            _fmt_value(_first_present(context.get("execution_reliability_clean_run_streak"), trend.get("clean_run_streak"))),
        ],
        ["Recommended actions", _join_values(actions)],
        ["Artifact status", _fmt_value(artifact_status)],
        ["Artifact", _fmt_value(artifact_path or context.get("execution_reliability_artifact"))],
        ["Readiness artifact", _fmt_value(context.get("execution_reliability_readiness_artifact"))],
        ["History artifact", _fmt_value(context.get("execution_reliability_history_artifact"))],
    ]
    has_signal = bool(
        artifact
        or context.get("execution_reliability_status")
        or context.get("execution_reliability_classification")
        or context.get("execution_reliability_artifact")
    )
    return [row for row in rows if has_signal and row[1] != "unavailable"]


def target_attainment_rows(
    context: Mapping[str, Any],
    repo_root: Path | str | None = None,
) -> list[list[str]]:
    artifact, artifact_path = _artifact_payload(context, "execution_target_attainment_artifact", repo_root)
    artifact_status = _artifact_status(context, "execution_target_attainment_artifact", repo_root)
    warnings = _first_present(artifact.get("warnings"), context.get("execution_target_attainment_warnings"))
    missing_buys = _first_present(artifact.get("missing_intended_buys"), context.get("missing_intended_buys"))
    rows = [
        [
            "Status",
            _fmt_value(_first_present(context.get("execution_target_attainment_status"), artifact.get("status"))),
        ],
        ["Target cash weight", _fmt_pct_weight(_first_present(artifact.get("target_cash_weight"), context.get("target_cash_weight")))],
        ["Achieved cash weight", _fmt_pct_weight(_first_present(artifact.get("achieved_cash_weight"), context.get("achieved_cash_weight")))],
        ["Cash target drift", _fmt_pct_weight(artifact.get("cash_target_drift"))],
        ["Submitted buys", _fmt_value(artifact.get("submitted_buy_count"))],
        ["Filled buys", _fmt_value(artifact.get("filled_buy_count"))],
        ["Pending buys", _fmt_value(artifact.get("pending_buy_count"))],
        ["Missing intended buys", _join_values(missing_buys)],
        ["Warnings", _join_values(warnings)],
        ["Actual posttrade cash", _fmt_money(artifact.get("actual_posttrade_cash"))],
        ["Actual cash source", _fmt_value(artifact.get("actual_posttrade_cash_source"))],
        ["Artifact status", _fmt_value(artifact_status)],
        ["Artifact", _fmt_value(artifact_path or context.get("execution_target_attainment_artifact"))],
    ]
    has_signal = bool(
        artifact
        or context.get("execution_target_attainment_status")
        or context.get("execution_target_attainment_artifact")
        or context.get("target_cash_weight") is not None
        or context.get("achieved_cash_weight") is not None
    )
    return [row for row in rows if has_signal and row[1] != "unavailable"]


def _fmt_counts(counts: Any) -> str:
    if not isinstance(counts, Mapping) or not counts:
        return "unavailable"
    return ", ".join(
        f"{key}={value}"
        for key, value in sorted(counts.items())
    )


def _construction_detail(rows: list[Any], limit: int = 5) -> str:
    rendered: list[str] = []
    safe_rows = [row for row in rows if isinstance(row, Mapping)]
    safe_rows = sorted(
        safe_rows,
        key=lambda row: (
            -float(row.get("final_target_weight") or 0.0)
            if isinstance(row.get("final_target_weight"), (int, float))
            else 0.0,
            str(row.get("ticker") or ""),
        ),
    )
    for row in safe_rows[:limit]:
        ticker = str(row.get("ticker") or "UNKNOWN")
        action = str(row.get("construction_action") or "unavailable")
        sleeve = _join_values(row.get("sleeve_sources"))
        current = _fmt_pct_weight(row.get("current_weight"))
        target = _fmt_pct_weight(row.get("final_target_weight"))
        score = _fmt_value(row.get("raw_score"))
        score_source = _fmt_value(row.get("score_source"))
        reason = _fmt_value(row.get("suppression_block_reason"))
        rendered.append(
            f"{ticker} {action} sleeve={sleeve} current={current} target={target} score={score} source={score_source} reason={reason}"
        )
    return "; ".join(rendered) if rendered else "unavailable"


def construction_provenance_rows(
    context: Mapping[str, Any],
    repo_root: Path | str | None = None,
) -> list[list[str]]:
    artifact, artifact_path = _artifact_payload(context, "construction_provenance_artifact", repo_root)
    artifact_status = _artifact_status(context, "construction_provenance_artifact", repo_root)
    summary = artifact.get("summary") if isinstance(artifact.get("summary"), Mapping) else {}
    source_artifacts = artifact.get("source_artifacts") if isinstance(artifact.get("source_artifacts"), Mapping) else {}
    source_status = {
        key: source.get("status")
        for key, source in source_artifacts.items()
        if isinstance(source, Mapping)
    }
    rows = [
        ["Status", _fmt_value(summary.get("status"))],
        ["Rows", _fmt_value(summary.get("row_count"))],
        ["Actions", _fmt_counts(summary.get("action_counts"))],
        ["Active constraints", _fmt_counts(summary.get("constraint_counts"))],
        ["Score-backed rows", _fmt_value(summary.get("score_backed_count"))],
        ["Unavailable score rows", _fmt_value(summary.get("unavailable_score_count"))],
        ["Top construction rows", _construction_detail(_as_list(artifact.get("rows")))],
        ["Source artifact status", _fmt_counts(source_status)],
        ["Artifact status", _fmt_value(artifact_status)],
        ["Artifact", _fmt_value(artifact_path or context.get("construction_provenance_artifact"))],
    ]
    has_signal = bool(
        artifact
        or context.get("construction_provenance_artifact")
    )
    return [row for row in rows if has_signal and row[1] != "unavailable"]


def _compact_list(values: Any, limit: int = 5) -> str:
    safe_values = [str(value) for value in _as_list(values) if str(value)]
    if not safe_values:
        return "unavailable"
    rendered = safe_values[:limit]
    suffix = f"; +{len(safe_values) - limit} more" if len(safe_values) > limit else ""
    return "; ".join(rendered) + suffix


def fr105_research_status_rows(
    context: Mapping[str, Any],
    repo_root: Path | str | None = None,
) -> list[list[str]]:
    completeness, completeness_path = _artifact_payload(context, "fr105_phase01_completeness_artifact", repo_root)
    completeness_status = _artifact_status(context, "fr105_phase01_completeness_artifact", repo_root)
    framework, framework_path = _artifact_payload(context, "fr105_shadow_alpha_framework_artifact", repo_root)
    framework_status = _artifact_status(context, "fr105_shadow_alpha_framework_artifact", repo_root)
    phase2, phase2_path = _artifact_payload(context, "fr105_phase2_topn_frontier_artifact", repo_root)
    phase2_status = _artifact_status(context, "fr105_phase2_topn_frontier_artifact", repo_root)
    phase3, phase3_path = _artifact_payload(context, "fr105_phase3_holding_count_artifact", repo_root)
    phase3_status = _artifact_status(context, "fr105_phase3_holding_count_artifact", repo_root)
    shadow, shadow_path = _artifact_payload(context, "fr105_shadow_alpha_chase_comparison_artifact", repo_root)
    shadow_status = _artifact_status(context, "fr105_shadow_alpha_chase_comparison_artifact", repo_root)
    summary = completeness.get("summary") if isinstance(completeness.get("summary"), Mapping) else {}
    readiness = completeness.get("readiness") if isinstance(completeness.get("readiness"), Mapping) else {}
    phase_status = completeness.get("phase_status") if isinstance(completeness.get("phase_status"), Mapping) else {}
    framework_metadata = framework.get("metadata") if isinstance(framework.get("metadata"), Mapping) else {}
    framework_eval = framework.get("evaluation_status") if isinstance(framework.get("evaluation_status"), Mapping) else {}
    phase2_readiness = phase2.get("readiness") if isinstance(phase2.get("readiness"), Mapping) else {}
    phase3_readiness = phase3.get("readiness") if isinstance(phase3.get("readiness"), Mapping) else {}
    shadow_readiness = shadow.get("readiness") if isinstance(shadow.get("readiness"), Mapping) else {}
    alpha_enabled = framework_metadata.get("enabled")
    if alpha_enabled is None and framework:
        alpha_enabled = False
    rows = [
        ["Research Status", _fmt_value(summary.get("status"))],
        ["Phase 0", _fmt_value(phase_status.get("phase0"))],
        ["Phase 1", _fmt_value(phase_status.get("phase1"))],
        ["Complete", _fmt_value(summary.get("complete"))],
        ["Missing artifacts", _compact_list(summary.get("missing_fields"))],
        ["Unavailable evidence", _compact_list(summary.get("unavailable_fields"))],
        ["Readiness", _fmt_value(readiness.get("status"))],
        ["Phase 2 readiness", _fmt_value(phase2_readiness.get("status"))],
        ["Phase 2 blockers", _compact_list(phase2_readiness.get("blocking_gaps"))],
        ["Phase 3 readiness", _fmt_value(phase3_readiness.get("status"))],
        ["Phase 3 blockers", _compact_list(phase3_readiness.get("blocking_gaps"))],
        ["Shadow evaluation", _fmt_value(shadow_readiness.get("status"))],
        ["Alpha Chase enabled", _fmt_value(alpha_enabled)],
        ["Alpha Chase status", _fmt_value(framework_eval.get("status"))],
        ["Recommendations", "none; readiness only"],
        ["Completeness artifact status", _fmt_value(completeness_status)],
        ["Completeness artifact", _fmt_value(completeness_path or context.get("fr105_phase01_completeness_artifact"))],
        ["Framework artifact status", _fmt_value(framework_status)],
        ["Framework artifact", _fmt_value(framework_path or context.get("fr105_shadow_alpha_framework_artifact"))],
        ["Phase 2 artifact status", _fmt_value(phase2_status)],
        ["Phase 2 artifact", _fmt_value(phase2_path or context.get("fr105_phase2_topn_frontier_artifact"))],
        ["Phase 3 artifact status", _fmt_value(phase3_status)],
        ["Phase 3 artifact", _fmt_value(phase3_path or context.get("fr105_phase3_holding_count_artifact"))],
        ["Shadow comparison artifact status", _fmt_value(shadow_status)],
        ["Shadow comparison artifact", _fmt_value(shadow_path or context.get("fr105_shadow_alpha_chase_comparison_artifact"))],
    ]
    has_signal = bool(
        completeness
        or framework
        or phase2
        or phase3
        or shadow
        or context.get("fr105_phase01_completeness_artifact")
        or context.get("fr105_shadow_alpha_framework_artifact")
        or context.get("fr105_phase2_topn_frontier_artifact")
        or context.get("fr105_phase3_holding_count_artifact")
        or context.get("fr105_shadow_alpha_chase_comparison_artifact")
    )
    return [row for row in rows if has_signal and row[1] != "unavailable"]


def text_table_section(title: str, rows: list[list[str]]) -> str:
    if not rows:
        return ""
    lines = [
        "",
        title.upper(),
        "Metric | Value",
        "------ | -----",
    ]
    lines.extend(f"{label} | {value}" for label, value in rows)
    return "\n".join(lines)


def simple_html_section(title: str, rows: list[list[str]]) -> str:
    if not rows:
        return ""
    body = "".join(
        "<tr>"
        f"<td style='padding:4px 12px 4px 0;'><b>{html.escape(str(label))}</b></td>"
        f"<td style='padding:4px 0;'>{html.escape(str(value))}</td>"
        "</tr>"
        for label, value in rows
    )
    return (
        f"<h3>{html.escape(title)}</h3>"
        "<table style='border-collapse:collapse; font-family:monospace; font-size:0.95em;'>"
        f"{body}</table>"
    )
