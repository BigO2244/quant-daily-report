from __future__ import annotations

from pathlib import Path
from typing import Any

from research_data.hydration import utc_now_iso, write_json
from research_data.parity import DEFAULT_LEGACY_CANDIDATES_ROOT, DEFAULT_MIGRATION_ROOT, build_sleeve_parity_report


SCHEMA_VERSION = "core_momentum_parity_summary_v1"
RUNTIME_IMPACT = "read_only_core_momentum_parity_monitoring_no_trading_path_changes"
CORE_MOMENTUM_SLEEVES = ("polaris", "lyra", "orion")


def build_core_momentum_parity_summary(
    *,
    repo_root: Path,
    as_of_date: str | None = None,
    sleeves: tuple[str, ...] = CORE_MOMENTUM_SLEEVES,
    migration_readiness_path: Path | None = None,
    legacy_candidates_root: Path | None = None,
    output_root: Path | None = None,
) -> dict[str, Any]:
    root = Path(repo_root)
    generated_at = utc_now_iso()
    parity_reports = [
        build_sleeve_parity_report(
            repo_root=root,
            as_of_date=as_of_date,
            sleeve_id=sleeve_id,
            migration_readiness_path=migration_readiness_path,
            legacy_candidates_root=legacy_candidates_root or DEFAULT_LEGACY_CANDIDATES_ROOT,
            output_root=output_root or DEFAULT_MIGRATION_ROOT,
        )
        for sleeve_id in sleeves
    ]
    effective_as_of = str(as_of_date or (parity_reports[0].get("as_of_date") if parity_reports else ""))
    summary_rows = [_summary_row(report) for report in parity_reports]
    overall_status = _overall_status(summary_rows)
    destination = _resolve(root, output_root or DEFAULT_MIGRATION_ROOT) / effective_as_of
    payload = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "as_of_date": effective_as_of,
        "runtime_impact": RUNTIME_IMPACT,
        "sleeve_count": len(summary_rows),
        "pass_count": sum(1 for row in summary_rows if row["parity_status"] == "PASS"),
        "warn_count": sum(1 for row in summary_rows if row["parity_status"] == "WARN"),
        "blocked_count": sum(1 for row in summary_rows if row["parity_status"] == "BLOCKED"),
        "overall_status": overall_status,
        "broker_submission_invoked": any(bool(row["broker_submission_invoked"]) for row in summary_rows),
        "sleeve_runtime_invoked": any(bool(row["sleeve_runtime_invoked"]) for row in summary_rows),
        "allocation_mutation_invoked": any(bool(row["allocation_mutation_invoked"]) for row in summary_rows),
        "promotion_invoked": any(bool(row["promotion_invoked"]) for row in summary_rows),
        "fail_reasons": sorted({reason for row in summary_rows for reason in row["fail_reasons"]}),
        "warning_reasons": sorted({reason for row in summary_rows for reason in row["warning_reasons"]}),
        "sleeves": summary_rows,
    }
    json_path = destination / "core_momentum_parity_summary.json"
    markdown_path = destination / "core_momentum_parity_summary.md"
    payload["json_path"] = _display_path(root, json_path)
    payload["markdown_path"] = _display_path(root, markdown_path)
    write_json(json_path, payload)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(render_core_momentum_parity_summary_markdown(payload), encoding="utf-8")
    return payload


def render_core_momentum_parity_summary_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Core Momentum FR-DH Parity Summary",
        "",
        f"- As of date: {payload.get('as_of_date')}",
        f"- Overall status: {payload.get('overall_status')}",
        f"- Sleeves: {payload.get('sleeve_count')}",
        f"- Broker submission invoked: {str(payload.get('broker_submission_invoked')).lower()}",
        f"- Sleeve runtime invoked: {str(payload.get('sleeve_runtime_invoked')).lower()}",
        f"- Allocation mutation invoked: {str(payload.get('allocation_mutation_invoked')).lower()}",
        "",
        "| sleeve | parity | input | signal | output | warnings | missing symbols | freshness | PIT security master |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for row in payload.get("sleeves") or []:
        lines.append(
            "| {sleeve_id} | {parity_status} | {input_parity_status} | {signal_parity_status} | "
            "{output_parity_status} | {warnings} | {missing_symbols} | {freshness_status} | {pit_status} |".format(
                sleeve_id=row.get("sleeve_id"),
                parity_status=row.get("parity_status"),
                input_parity_status=row.get("input_parity_status"),
                signal_parity_status=row.get("signal_parity_status"),
                output_parity_status=row.get("output_parity_status"),
                warnings=", ".join(row.get("warning_reasons") or []) or "None",
                missing_symbols=", ".join(row.get("missing_symbols") or []) or "None",
                freshness_status=row.get("freshness_status"),
                pit_status=row.get("pit_security_master_status"),
            )
        )
    lines.extend(
        [
            "",
            "Runtime impact: read-only summary artifact only; no trading, broker, scheduler, allocation, sleeve promotion, or production data-path change.",
            "",
        ]
    )
    return "\n".join(lines)


def _summary_row(report: dict[str, Any]) -> dict[str, Any]:
    selected = report.get("selected_sleeve") or {}
    signal = report.get("signal_parity") or {}
    output = report.get("output_parity") or {}
    security = _security_master_summary(selected)
    freshness = _freshness_summary(selected)
    missing_symbols = sorted(
        {
            str(row.get("ticker"))
            for row in report.get("per_symbol_diagnostics") or []
            if row.get("ticker") and row.get("missing_required_inputs")
        }
    )
    return {
        "sleeve_id": selected.get("sleeve_id"),
        "strategy_id": selected.get("strategy_id"),
        "parity_status": report.get("parity_status"),
        "recommendation": report.get("recommendation"),
        "input_parity_status": report.get("input_parity_status"),
        "signal_parity_status": signal.get("signal_parity_status"),
        "output_parity_status": output.get("output_parity_status"),
        "warning_reasons": list(report.get("warning_reasons") or []),
        "fail_reasons": list(report.get("fail_reasons") or []),
        "missing_symbols": missing_symbols,
        "missing_freshness_dataset_ids": list((report.get("legacy_vs_fr_dh_inputs") or {}).get("missing_freshness_dataset_ids") or []),
        "freshness_status": freshness["status"],
        "freshness_by_dataset": freshness["by_dataset"],
        "pit_security_master_status": security["status"],
        "pit_security_master_pit_safe_status": security["pit_safe_status"],
        "pit_security_master_validation_status": security["validation_status"],
        "pit_security_master_missing_symbols": security["missing_symbols"],
        "broker_submission_invoked": bool(report.get("broker_submission_invoked")),
        "sleeve_runtime_invoked": bool(report.get("sleeve_runtime_invoked")),
        "allocation_mutation_invoked": bool(report.get("allocation_mutation_invoked")),
        "promotion_invoked": bool(report.get("promotion_invoked")),
        "source_parity_json_path": report.get("json_path"),
        "source_parity_markdown_path": report.get("markdown_path"),
    }


def _freshness_summary(selected: dict[str, Any]) -> dict[str, Any]:
    rows = [
        row
        for row in selected.get("dataset_requirements") or []
        if row.get("readiness_status") != "SYMBOL_COVERAGE" and row.get("dataset_id")
    ]
    by_dataset = {
        str(row["dataset_id"]): {
            "freshness_status": row.get("freshness_status"),
            "validation_status": row.get("validation_status"),
            "PIT_safe_status": row.get("PIT_safe_status"),
        }
        for row in rows
    }
    statuses = {str(row.get("freshness_status") or "") for row in rows}
    if not rows:
        status = "MISSING"
    elif any(status not in {"OK", "CURRENT", "FRESH"} for status in statuses):
        status = "WARN"
    else:
        status = "OK"
    return {"status": status, "by_dataset": by_dataset}


def _security_master_summary(selected: dict[str, Any]) -> dict[str, Any]:
    coverage = ((selected.get("symbol_coverage") or {}).get("coverage_by_dataset") or {}).get("security_master_pit") or {}
    dataset_row = next(
        (
            row
            for row in selected.get("dataset_requirements") or []
            if row.get("dataset_id") == "security_master_pit" and row.get("readiness_status") != "SYMBOL_COVERAGE"
        ),
        {},
    )
    return {
        "status": coverage.get("pit_grade_status") or dataset_row.get("PIT_safe_status") or "UNKNOWN",
        "pit_safe_status": dataset_row.get("PIT_safe_status"),
        "validation_status": dataset_row.get("validation_status"),
        "missing_symbols": list(coverage.get("missing_symbols") or []),
    }


def _overall_status(rows: list[dict[str, Any]]) -> str:
    if any(row.get("fail_reasons") or row.get("parity_status") == "BLOCKED" for row in rows):
        return "FAIL"
    if any(row.get("warning_reasons") or row.get("parity_status") == "WARN" for row in rows):
        return "WARN"
    return "PASS"


def _resolve(repo_root: Path, path: Path) -> Path:
    return path if path.is_absolute() else repo_root / path


def _display_path(repo_root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(repo_root))
    except ValueError:
        return str(path)
