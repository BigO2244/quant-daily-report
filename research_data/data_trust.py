from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from research_data.hydration import read_json, utc_now_iso, write_json


SCHEMA_VERSION = "research_data_trust_summary_v1"
RUNTIME_IMPACT = "read_only_data_trust_summary_no_trading_path_changes"
DEFAULT_OBSERVABILITY_PATH = Path("data/manifests/research_data_observability.json")
DEFAULT_OUTPUT_DIR = Path("outputs/data_trust")


def build_data_trust_summary(
    *,
    repo_root: Path,
    observability_path: Path | None = None,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    root = Path(repo_root)
    source_path = _resolve(root, observability_path or DEFAULT_OBSERVABILITY_PATH)
    destination = _resolve(root, output_dir or DEFAULT_OUTPUT_DIR)
    observability = read_json(source_path)
    generated_at = utc_now_iso()
    datasets = [_dataset_summary(row) for row in observability.get("datasets") or []]
    findings = _findings(datasets)
    status = _summary_status(findings)
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "as_of_date": observability.get("as_of_date"),
        "runtime_impact": RUNTIME_IMPACT,
        "source_observability_path": _display_path(root, source_path),
        "source_observability_sha256": _sha256(source_path),
        "json_path": _display_path(root, destination / "data_trust_summary.json"),
        "markdown_path": _display_path(root, destination / "data_trust_summary.md"),
        "readiness_status": status,
        "broker_submission_invoked": False,
        "dashboard_mutation_invoked": False,
        "email_send_invoked": False,
        "dataset_count": len(datasets),
        "observe_only_count": sum(1 for row in datasets if row["readiness_status"] == "OBSERVE_ONLY"),
        "blocked_count": sum(1 for row in datasets if row["readiness_status"] == "BLOCKED"),
        "missing_artifact_count": sum(1 for row in datasets if row["readiness_status"] == "MISSING_ARTIFACT"),
        "critical_count": len(findings["critical"]),
        "warning_count": len(findings["warnings"]),
        "info_count": len(findings["info"]),
        "findings": findings,
        "datasets": datasets,
    }
    write_json(destination / "data_trust_summary.json", payload)
    (destination / "data_trust_summary.md").write_text(render_data_trust_markdown(payload), encoding="utf-8")
    return payload


def render_data_trust_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Research Data Trust Summary",
        "",
        f"- Status: {payload.get('readiness_status')}",
        f"- As of date: {payload.get('as_of_date') or 'unknown'}",
        f"- Dataset count: {payload.get('dataset_count')}",
        f"- Observe-only artifacts: {payload.get('observe_only_count')}",
        f"- Blocked datasets: {payload.get('blocked_count')}",
        f"- Missing artifacts: {payload.get('missing_artifact_count')}",
        f"- Broker submission invoked: {str(payload.get('broker_submission_invoked')).lower()}",
        "",
        "## Findings",
        "",
    ]
    for label, key in (("Critical", "critical"), ("Warnings", "warnings"), ("Info", "info")):
        items = payload.get("findings", {}).get(key) or []
        lines.append(f"### {label}")
        lines.append("")
        if not items:
            lines.append("- None")
        else:
            for item in items:
                lines.append(f"- `{item['dataset_id']}`: {item['reason']}")
        lines.append("")
    lines.extend(
        [
            "## Dataset Status",
            "",
            "| dataset_id | tier | readiness | validation | PIT | freshness | lineage | rows | reason |",
            "|---|---:|---|---|---|---|---|---:|---|",
        ]
    )
    for row in payload.get("datasets") or []:
        lines.append(
            "| {dataset_id} | {tier} | {readiness_status} | {validation_status} | "
            "{PIT_safe_status} | {freshness_status} | {lineage_status} | {row_count} | {reason} |".format(
                dataset_id=f"`{row.get('dataset_id')}`",
                tier=_md(row.get("tier")),
                readiness_status=_md(row.get("readiness_status")),
                validation_status=_md(row.get("validation_status")),
                PIT_safe_status=_md(row.get("PIT_safe_status")),
                freshness_status=_md(row.get("freshness_status")),
                lineage_status=_md(row.get("lineage_status")),
                row_count=int(row.get("row_count") or 0),
                reason=_md(row.get("reason")),
            )
        )
    lines.append("")
    lines.append("Runtime impact: read-only summary artifact; no dashboard mutation, email send, broker submission, execution, scheduler, allocation, or sleeve-consumer behavior change.")
    lines.append("")
    return "\n".join(lines)


def _dataset_summary(row: dict[str, Any]) -> dict[str, Any]:
    summary = {
        "dataset_id": row.get("dataset_id"),
        "dataset_name": row.get("dataset_name"),
        "tier": row.get("tier"),
        "domain": row.get("domain"),
        "readiness_status": row.get("readiness_status"),
        "normalization_stage": row.get("normalization_stage"),
        "validation_status": row.get("validation_status"),
        "freshness_status": row.get("freshness_status"),
        "hydration_status": row.get("hydration_status"),
        "PIT_safe_status": row.get("PIT_safe_status"),
        "latest_ingestion_timestamp": row.get("latest_ingestion_timestamp"),
        "lineage_status": row.get("lineage_status"),
        "artifact_exists": bool(row.get("artifact_exists")),
        "artifact_path": row.get("artifact_path"),
        "row_count": int(row.get("row_count") or 0),
        "source_artifact_count": int(row.get("source_artifact_count") or 0),
        "input_artifact_count": int(row.get("input_artifact_count") or 0),
        "reason": row.get("blocker_reason") or _status_reason(row),
    }
    summary["risk_level"] = _risk_level(summary)
    return summary


def _findings(datasets: list[dict[str, Any]]) -> dict[str, list[dict[str, str]]]:
    findings: dict[str, list[dict[str, str]]] = {"critical": [], "warnings": [], "info": []}
    for row in datasets:
        item = {"dataset_id": str(row.get("dataset_id")), "reason": str(row.get("reason") or _status_reason(row))}
        if row["risk_level"] == "CRITICAL":
            findings["critical"].append(item)
        elif row["risk_level"] == "WARN":
            findings["warnings"].append(item)
        else:
            findings["info"].append(item)
    return findings


def _risk_level(row: dict[str, Any]) -> str:
    if _has_pit_violation(row.get("PIT_safe_status")) or _has_validation_failure(row.get("validation_status")):
        return "CRITICAL"
    if row.get("tier") == "Tier 1" and row.get("readiness_status") != "OBSERVE_ONLY":
        return "CRITICAL"
    if row.get("readiness_status") in {"BLOCKED", "MISSING_ARTIFACT"}:
        return "WARN"
    if _has_validation_warning(row.get("validation_status")):
        return "WARN"
    if row.get("lineage_status") == "LINEAGE_MISSING":
        return "WARN"
    freshness_status = str(row.get("freshness_status") or "")
    if freshness_status and freshness_status not in {"OK", "CURRENT", "FRESH"}:
        return "WARN"
    return "INFO"


def _summary_status(findings: dict[str, list[dict[str, str]]]) -> str:
    if findings["critical"]:
        return "FAIL"
    if findings["warnings"]:
        return "WARN"
    return "PASS"


def _status_reason(row: dict[str, Any]) -> str:
    if _has_pit_violation(row.get("PIT_safe_status")):
        return f"PIT safety violation: {row.get('PIT_safe_status')}"
    if _has_validation_failure(row.get("validation_status")):
        return f"Validation failed: {row.get('validation_status')}"
    if row.get("tier") == "Tier 1" and row.get("readiness_status") != "OBSERVE_ONLY":
        return "Tier 1 platform dataset is not backed by an observe-only canonical artifact."
    if row.get("readiness_status") == "BLOCKED":
        return "Dataset is blocked before canonical use."
    if row.get("readiness_status") == "MISSING_ARTIFACT":
        return "No read-only canonical artifact exists yet."
    if _has_validation_warning(row.get("validation_status")):
        return f"Validation warning: {row.get('validation_status')}"
    if row.get("lineage_status") == "LINEAGE_MISSING":
        return "Artifact exists but lineage is missing."
    freshness_status = str(row.get("freshness_status") or "")
    if freshness_status and freshness_status not in {"OK", "CURRENT", "FRESH"}:
        hydration_status = str(row.get("hydration_status") or "unknown")
        return f"Freshness warning: {freshness_status} hydration={hydration_status}"
    return "Dataset has an observe-only artifact and no critical data-trust finding."


def _has_pit_violation(value: Any) -> bool:
    text = str(value or "").upper()
    return "PIT_VIOLATION" in text or "PIT_UNSAFE" in text or "LOOKAHEAD" in text or "LOOK_AHEAD" in text


def _has_validation_failure(value: Any) -> bool:
    text = str(value or "").upper()
    return text.startswith("FAIL") or "FAILED" in text or "SCHEMA_ERROR" in text


def _has_validation_warning(value: Any) -> bool:
    text = str(value or "").upper()
    return text.startswith("WARN") or "WARNING" in text or "UNVERIFIED" in text


def _resolve(repo_root: Path, path: Path) -> Path:
    return path if path.is_absolute() else repo_root / path


def _display_path(repo_root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(repo_root))
    except ValueError:
        return str(path)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _md(value: Any) -> str:
    text = str(value if value not in (None, "") else "n/a")
    return text.replace("|", "\\|").replace("\n", " ")
