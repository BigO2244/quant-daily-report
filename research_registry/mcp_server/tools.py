"""Read-only tool functions for the Caerus MCP-compatible server."""

from __future__ import annotations

import dataclasses
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from research_registry.ingestion import ingest_artifact_family
from research_registry.mcp_server.schemas import TOOL_DEFINITIONS
from research_registry.query import RegistryQuery
from research_registry.registry import SQLiteResearchRegistry
from research_registry.research.capabilities import (
    CAPABILITY_REGISTRY,
    available_intents,
    capability_summary,
    check_artifacts,
    classify_question,
)
from research_registry.research.shadow_comparison import (
    DEFAULT_SHADOW_ROOT,
    compare_shadow_strategies,
    parse_strategy_names,
    shadow_comparison_to_dict,
)
from research_registry.research.timing_regime import (
    DEFAULT_REGIME_HISTORY,
    DEFAULT_TIMING_ROOT,
    INSUFFICIENT_SAMPLE_THRESHOLD,
    answer_timing_by_regime_question,
)
from research_registry.research.timing_summary import (
    DEFAULT_TIMING_ROOT as DEFAULT_SUMMARY_TIMING_ROOT,
    parse_offset_highlights,
    summarise_timing,
    timing_summary_to_dict,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = Path("/tmp/caerus-research-registry.db")
DEFAULT_RUNS_ROOT = Path("outputs/runs")
DEFAULT_PACKETS_ROOT = Path("outputs/research_packets")
DEFAULT_DOCS_ROOT = Path("docs/governance")
MCP_PRODUCER = "research_registry.mcp_server"


@dataclass(frozen=True)
class ToolContext:
    db_path: Path = DEFAULT_DB_PATH
    runs_root: Path = DEFAULT_RUNS_ROOT
    packets_root: Path = DEFAULT_PACKETS_ROOT
    docs_root: Path = DEFAULT_DOCS_ROOT
    limit: int = 10


def list_tools() -> list[dict[str, Any]]:
    return [dict(tool) for tool in TOOL_DEFINITIONS]


def call_tool(name: str, arguments: dict[str, Any] | None = None, context: ToolContext | None = None) -> dict[str, Any]:
    arguments = dict(arguments or {})
    context = context or ToolContext()
    dispatch = {
        "build_caerus_registry": build_caerus_registry,
        "latest_runs": latest_runs,
        "run_health": run_health,
        "integrity_findings": integrity_findings,
        "governance_open": governance_open,
        "research_packet_status": research_packet_status,
        "registry_summary": registry_summary,
        "query_registry": query_registry,
        "lineage": lineage,
        "daily_operator_brief": daily_operator_brief,
        "artifact_status": artifact_status,
        "operator_daily_summary": operator_daily_summary,
        "artifact_drilldown": artifact_drilldown,
        "morning_cio_brief": morning_cio_brief,
        "promotion_readiness": promotion_readiness,
        "anomaly_report": anomaly_report,
        "execution_timing_by_vix_regime": execution_timing_by_vix_regime,
        "execution_timing_summary": execution_timing_summary,
        "shadow_comparison": shadow_comparison,
        "answer_research_question": answer_research_question,
    }
    if name not in dispatch:
        return _response("ERROR", _resolve_db_path(arguments, context), warnings=[f"unknown tool: {name}"])
    return dispatch[name](context=context, **arguments)


def build_caerus_registry(
    *,
    context: ToolContext | None = None,
    db_path: str | None = None,
    runs_root: str | None = None,
    packets_root: str | None = None,
    docs_root: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    context = context or ToolContext()
    resolved_db = _resolve_db_path({"db_path": db_path}, context)
    db_warning = _db_boundary_warning(resolved_db)
    if db_warning:
        return _response("ERROR", resolved_db, warnings=[db_warning])

    resolved_runs = Path(runs_root) if runs_root else context.runs_root
    resolved_packets = Path(packets_root) if packets_root else context.packets_root
    resolved_docs = Path(docs_root) if docs_root else context.docs_root
    resolved_limit = _safe_limit(limit if limit is not None else context.limit)

    run_dirs = [path for path in resolved_runs.iterdir() if path.is_dir()] if resolved_runs.exists() else []
    selected_runs = _limit_paths(run_dirs, resolved_limit)
    integrity_paths = [
        path / "audit" / "execution_integrity.json"
        for path in selected_runs
        if (path / "audit" / "execution_integrity.json").exists()
    ]
    packet_dirs = [path for path in resolved_packets.iterdir() if path.is_dir()] if resolved_packets.exists() else []
    selected_packets = _limit_paths(packet_dirs, resolved_limit)
    docs = sorted(resolved_docs.glob("*.md")) if resolved_docs.exists() else []

    registry = SQLiteResearchRegistry(resolved_db)
    try:
        family_results = []
        for family, paths in [
            ("execution_run", selected_runs),
            ("execution_integrity", integrity_paths),
            ("research_packet", selected_packets),
            ("governance_doc", docs),
        ]:
            result = ingest_artifact_family(family=family, artifact_paths=paths, registry=registry)
            family_results.append(
                {
                    "family": family,
                    "path_count": len(paths),
                    "envelope_count": len(result.envelopes),
                    "findings": [dataclasses.asdict(finding) for finding in result.findings],
                }
            )
        query = RegistryQuery(registry)
        return _response(
            "OK",
            resolved_db,
            runs_root=str(resolved_runs),
            packets_root=str(resolved_packets),
            docs_root=str(resolved_docs),
            limit=resolved_limit,
            families=family_results,
            summary=query.registry_summary(),
        )
    finally:
        registry.close()


def latest_runs(*, context: ToolContext | None = None, db_path: str | None = None, limit: int | None = 10) -> dict[str, Any]:
    registry, query, resolved_db = _open_query(db_path, context)
    try:
        integrity_by_run = _integrity_objects_by_run_id(query)
        runs = _sort_recent(_artifact_objects(query, "execution_run"))
        runs = runs[: _safe_limit(limit)] if limit is not None else runs
        return _response(
            "OK",
            resolved_db,
            run_count=len(runs),
            runs=[_run_record(run_obj, integrity_by_run.get(str(run_obj.data.get("run_id")))) for run_obj in runs],
        )
    finally:
        registry.close()


def run_health(*, run_id: str, context: ToolContext | None = None, db_path: str | None = None) -> dict[str, Any]:
    registry, query, resolved_db = _open_query(db_path, context)
    try:
        run_obj = _execution_runs_by_id(query).get(run_id)
        integrity_obj = _integrity_objects_by_run_id(query).get(run_id)
        return _response(
            "FOUND" if run_obj or integrity_obj else "NOT_FOUND",
            resolved_db,
            run_id=run_id,
            execution_run=_run_record(run_obj, integrity_obj) if run_obj else None,
            execution_payload=run_obj.data.get("execution_payload") if run_obj else None,
            operator_summary=run_obj.data.get("operator_summary") if run_obj else None,
            execution_results=run_obj.data.get("execution_results") if run_obj else None,
            execution_integrity=_integrity_record(integrity_obj) if integrity_obj else None,
            source_paths={
                "execution_run": run_obj.provenance.get("source_paths", []) if run_obj else [],
                "execution_integrity": integrity_obj.provenance.get("source_paths", []) if integrity_obj else [],
            },
        )
    finally:
        registry.close()


def integrity_findings(*, context: ToolContext | None = None, db_path: str | None = None, limit: int | None = None) -> dict[str, Any]:
    registry, query, resolved_db = _open_query(db_path, context)
    try:
        audits = [
            obj
            for obj in _sort_recent(_artifact_objects(query, "execution_integrity"))
            if obj.data.get("status") in {"WARN", "FAIL"}
        ]
        if limit is not None:
            audits = audits[: _safe_limit(limit)]
        return _response("OK", resolved_db, finding_object_count=len(audits), integrity_findings=[_integrity_record(obj) for obj in audits])
    finally:
        registry.close()


def governance_open(
    *,
    context: ToolContext | None = None,
    db_path: str | None = None,
    show_duplicates: bool = False,
    include_deferred: bool = False,
) -> dict[str, Any]:
    registry, query, resolved_db = _open_query(db_path, context)
    try:
        if show_duplicates:
            items = [
                item
                for item in [_governance_raw_record(obj) for obj in query.query_by_type("GovernanceFR")]
                if _governance_open_item(item, include_deferred=include_deferred, resolved=False)
            ]
            items = sorted(items, key=lambda item: (str(item["status"]), str(item["fr_id"]), str(item["object_id"])))
            mode = "raw_duplicates"
        else:
            items = [
                item
                for item in _resolve_governance_current_state(query.query_by_type("GovernanceFR"))
                if _governance_open_item(item, include_deferred=include_deferred, resolved=True)
            ]
            mode = "deduped_current_state"
        return _response("OK", resolved_db, mode=mode, open_count=len(items), items=items)
    finally:
        registry.close()


def research_packet_status(*, context: ToolContext | None = None, db_path: str | None = None, limit: int | None = 10) -> dict[str, Any]:
    registry, query, resolved_db = _open_query(db_path, context)
    try:
        packets = _sort_recent(_artifact_objects(query, "research_packet"))
        packets = packets[: _safe_limit(limit)] if limit is not None else packets
        return _response(
            "OK",
            resolved_db,
            packet_count=len(packets),
            packets=[
                {
                    "packet_date": obj.data.get("packet_date") or obj.identity.get("trade_date"),
                    "status": obj.data.get("status"),
                    "source_readiness": obj.data.get("source_readiness"),
                    "confidence": obj.data.get("confidence") or obj.confidence.get("level"),
                    "stale_warnings": obj.data.get("stale_warnings") or [],
                    "missing_warnings": obj.data.get("missing_warnings") or [],
                    "warnings": obj.data.get("warnings") or [],
                    "object_id": obj.object_id,
                }
                for obj in packets
            ],
        )
    finally:
        registry.close()


def registry_summary(*, context: ToolContext | None = None, db_path: str | None = None) -> dict[str, Any]:
    registry, query, resolved_db = _open_query(db_path, context)
    try:
        return _response("OK", resolved_db, summary=query.registry_summary(), statistics=query.registry_statistics())
    finally:
        registry.close()


def query_registry(
    *,
    context: ToolContext | None = None,
    db_path: str | None = None,
    limit: int | None = None,
    artifact_type: str | None = None,
    data_artifact_type: str | None = None,
    surface: str | None = None,
    confidence: str | None = None,
    governance: str | None = None,
) -> dict[str, Any]:
    registry, query, resolved_db = _open_query(db_path, context)
    try:
        objects = query.list_objects()
        if artifact_type:
            objects = [obj for obj in objects if obj.object_type == artifact_type]
        if data_artifact_type:
            objects = [obj for obj in objects if obj.data.get("artifact_type") == data_artifact_type]
        if surface:
            objects = [obj for obj in objects if obj.surface.get("nav_surface_type") == surface]
        if confidence:
            objects = [obj for obj in objects if obj.confidence.get("level") == confidence]
        if governance:
            objects = [obj for obj in objects if obj.governance.get("state") == governance]
        if limit is not None:
            objects = objects[: _safe_limit(limit)]
        return _response("OK", resolved_db, object_count=len(objects), objects=[obj.to_dict() for obj in objects])
    finally:
        registry.close()


def lineage(*, object_id: str, context: ToolContext | None = None, db_path: str | None = None) -> dict[str, Any]:
    registry, query, resolved_db = _open_query(db_path, context)
    try:
        return _response("OK", resolved_db, lineage=query.get_lineage(object_id))
    finally:
        registry.close()


def daily_operator_brief(*, context: ToolContext | None = None, db_path: str | None = None) -> dict[str, Any]:
    registry, query, resolved_db = _open_query(db_path, context)
    try:
        warnings: list[str] = []
        summary = query.registry_summary()

        integrity_by_run = _integrity_objects_by_run_id(query)
        runs = _sort_recent(_artifact_objects(query, "execution_run"))
        latest_run = None
        if runs:
            run_obj = runs[0]
            latest_run = _run_record(run_obj, integrity_by_run.get(str(run_obj.data.get("run_id"))))

        integrity_objects = _sort_recent(_artifact_objects(query, "execution_integrity"))
        warn_fail_integrity = [
            _integrity_record(obj)
            for obj in integrity_objects
            if obj.data.get("status") in {"WARN", "FAIL"}
        ]
        if runs and not integrity_objects:
            warnings.append("missing execution integrity artifacts")
        if warn_fail_integrity:
            warnings.append("WARN/FAIL execution integrity findings present")
        execution_integrity = {
            "status": "WARN_FAIL_PRESENT" if warn_fail_integrity else "OK",
            "latest_warn_fail_findings": warn_fail_integrity,
            "finding_count": len(warn_fail_integrity),
        }

        resolved_governance = _resolve_governance_current_state(query.query_by_type("GovernanceFR"))
        open_items = [
            item
            for item in resolved_governance
            if _governance_open_item(item, include_deferred=False, resolved=True)
        ]
        key_ids = {"FR-031", "HOTFIX-2026-05-27", "FR-021", "FR-028", "FR-029"}
        key_items = [item for item in resolved_governance if item.get("fr_id") in key_ids]
        unresolved_duplicates = [item for item in resolved_governance if item.get("duplicate_count", 0) and not item.get("resolved_from")]
        if unresolved_duplicates:
            warnings.append("governance duplicates unresolved after dedupe")
        governance = {
            "open_count": len(open_items),
            "high_blast_radius_count": len([item for item in open_items if str(item.get("blast_radius")).upper() in {"HIGH", "CRITICAL"}]),
            "deployed_observing_count": len([item for item in resolved_governance if item.get("status") == "DEPLOYED_OBSERVING"]),
            "key_items": key_items,
        }

        packets = _sort_recent(_artifact_objects(query, "research_packet"))
        if packets:
            packet = packets[0]
            research_packet = {
                "packet_date": packet.data.get("packet_date") or packet.identity.get("trade_date"),
                "status": packet.data.get("status"),
                "source_readiness": packet.data.get("source_readiness"),
                "confidence": packet.data.get("confidence") or packet.confidence.get("level"),
                "stale_warnings": packet.data.get("stale_warnings") or [],
                "missing_warnings": packet.data.get("missing_warnings") or [],
                "warnings": packet.data.get("warnings") or [],
                "object_id": packet.object_id,
            }
        else:
            research_packet = None
            warnings.append("missing research packet")

        return _response(
            "OK",
            resolved_db,
            warnings=warnings,
            latest_run=latest_run,
            execution_integrity=execution_integrity,
            governance=governance,
            research_packet=research_packet,
            registry_summary={
                "object_count": summary.get("object_count"),
                "edge_count": summary.get("edge_count"),
                "orphan_count": summary.get("orphan_count"),
                "surface_conflict_count": summary.get("surface_conflict_count"),
            },
        )
    finally:
        registry.close()


def artifact_status(
    *,
    context: ToolContext | None = None,
    outputs_root: str | None = None,
    limit: int | None = 10,
) -> dict[str, Any]:
    context = context or ToolContext()
    root = Path(outputs_root) if outputs_root else Path("outputs")
    resolved_limit = _safe_limit(limit)
    warnings: list[str] = []

    families = _artifact_family_status(root, resolved_limit)
    latest_precompute = _latest_precompute_status(root)
    latest_execution = _latest_execution_status(root)
    latest_broker = _latest_broker_status(root)
    latest_shadow = _latest_shadow_status(root)
    latest_research_packet = _latest_research_packet_status(root)

    for section_name, section in [
        ("precompute", latest_precompute),
        ("execution", latest_execution),
        ("broker_confirmation", latest_broker),
        ("shadow", latest_shadow),
        ("research_packet", latest_research_packet),
    ]:
        if section.get("status") != "OK":
            warnings.append(f"{section_name}: {section.get('status')}")

    return _response(
        "OK",
        context.db_path,
        warnings=warnings,
        outputs_root=str(root),
        artifact_families=families,
        latest_precompute=latest_precompute,
        latest_execution=latest_execution,
        latest_broker_confirmation=latest_broker,
        latest_shadow=latest_shadow,
        latest_research_packet=latest_research_packet,
    )


def operator_daily_summary(
    *,
    context: ToolContext | None = None,
    outputs_root: str | None = None,
    trade_date: str | None = None,
) -> dict[str, Any]:
    context = context or ToolContext()
    target_date = trade_date or datetime.now().date().isoformat()
    status_payload = artifact_status(context=context, outputs_root=outputs_root, limit=5)
    sections = {
        "precompute": status_payload["latest_precompute"],
        "execution": status_payload["latest_execution"],
        "broker_confirmation": status_payload["latest_broker_confirmation"],
        "shadow": status_payload["latest_shadow"],
        "research_packet": status_payload["latest_research_packet"],
    }
    attention = list(status_payload.get("warnings") or [])

    summary = {
        "what_happened_today": {
            "trade_date": target_date,
            "precompute_ran": _section_current(sections["precompute"], target_date),
            "execution_ran": _execution_completed_current(sections["execution"], target_date),
            "broker_recon_present": sections["broker_confirmation"].get("status") == "OK",
            "shadow_ran": _section_current(sections["shadow"], target_date),
            "research_packet_current": _section_current(sections["research_packet"], target_date),
        },
        "sections": sections,
        "operator_attention": [],
    }

    if not summary["what_happened_today"]["precompute_ran"]:
        attention.append(f"precompute is not current for {target_date}")
    if not summary["what_happened_today"]["execution_ran"]:
        attention.append(f"execution is not current for {target_date}")
    if _section_current(sections["execution"], target_date):
        execution_files = sections["execution"].get("files") or {}
        if not (execution_files.get("execution_results") or {}).get("exists"):
            attention.append(f"execution_results artifact is missing for {target_date}")
        if not (execution_files.get("execution_integrity") or {}).get("exists"):
            attention.append(f"execution integrity artifact is missing for {target_date}")
    if not summary["what_happened_today"]["shadow_ran"]:
        attention.append(f"shadow lane is not current for {target_date}")
    if not summary["what_happened_today"]["research_packet_current"]:
        attention.append(f"research packet is not current for {target_date}")
    if not summary["what_happened_today"]["broker_recon_present"]:
        attention.append("broker/reconciliation artifacts are missing")
    integrity_status = sections["execution"].get("integrity_status")
    if integrity_status in {"WARN", "FAIL"}:
        attention.append(f"execution integrity status is {integrity_status}")
    attention = sorted(set(attention))
    summary["operator_attention"] = attention

    return _response(
        "NEEDS_OPERATOR" if attention else "OK",
        context.db_path,
        warnings=attention,
        outputs_root=status_payload["outputs_root"],
        trade_date=target_date,
        summary=summary,
    )


def artifact_drilldown(
    *,
    context: ToolContext | None = None,
    outputs_root: str | None = None,
    family: str | None = "all",
) -> dict[str, Any]:
    context = context or ToolContext()
    root = Path(outputs_root) if outputs_root else Path("outputs")
    family_name = family or "all"
    probes = {
        "precompute": _latest_precompute_status(root),
        "execution": _latest_execution_status(root),
        "broker_confirmation": _latest_broker_status(root),
        "shadow": _latest_shadow_status(root),
        "research_packet": _latest_research_packet_status(root),
    }
    if family_name != "all":
        probes = {family_name: probes.get(family_name, {"status": "NEEDS_OPERATOR", "reason": f"unknown family: {family_name}"})}
    return _response(
        "OK",
        context.db_path,
        outputs_root=str(root),
        family=family_name,
        drilldown=probes,
        note="Compact file probes only; raw artifact payloads are not included.",
    )


def morning_cio_brief(
    *,
    context: ToolContext | None = None,
    outputs_root: str | None = None,
    trade_date: str | None = None,
) -> dict[str, Any]:
    context = context or ToolContext()
    root = Path(outputs_root) if outputs_root else Path("outputs")
    target_date = trade_date or datetime.now().date().isoformat()
    daily = operator_daily_summary(context=context, outputs_root=str(root), trade_date=target_date)
    anomalies = _anomaly_findings(root, target_date=target_date, lookback_days=5)
    needs_attention = sorted(
        set(
            list(daily.get("warnings") or [])
            + [item["message"] for item in anomalies if item["severity"] == "NEEDS_OPERATOR"]
        )
    )
    return _response(
        "NEEDS_OPERATOR" if needs_attention else "OK",
        context.db_path,
        warnings=needs_attention,
        outputs_root=str(root),
        trade_date=target_date,
        operational_status=_operational_status_from_daily(daily),
        strategy_leadership=_strategy_leadership(root),
        portfolio_exposure=_portfolio_exposure(root),
        regime_market_context=_regime_market_context(root),
        operator_attention=needs_attention,
        anomaly_count=len(anomalies),
        anomalies=anomalies[:10],
        longitudinal_memory=_longitudinal_memory(root, lookback_days=5),
    )


def promotion_readiness(
    *,
    context: ToolContext | None = None,
    outputs_root: str | None = None,
    lookback_days: int | None = 5,
) -> dict[str, Any]:
    context = context or ToolContext()
    root = Path(outputs_root) if outputs_root else Path("outputs")
    lookback = max(1, int(lookback_days or 5))
    history = _shadow_history(root, limit=lookback)
    leadership = _strategy_leadership(root)
    metrics = _promotion_metrics(history)
    phase_c = (history[0].get("phase_c_readiness") if history else None) or {}
    phase_c_states = {
        slug: {
            "readiness_state": item.get("readiness_state"),
            "confidence": item.get("confidence"),
            "reason_codes": item.get("reason_codes") or [],
        }
        for slug, item in (phase_c.get("strategies") or {}).items()
    }
    sufficient = metrics["valid_observation_window_count"] >= 20 and metrics["cumulative_excess_vs_spy"] is not None
    if phase_c_states:
        best_state = _strongest_phase_c_state(phase_c_states)
        confidence = best_state.get("confidence") or "LOW"
        recommendation = best_state.get("readiness_state") or "CONTINUE_SHADOW"
    elif sufficient and leadership.get("current_leader") not in {None, "unavailable"}:
        confidence = "MODERATE"
        recommendation = "CANDIDATE_FOR_CAPITAL"
    elif metrics["valid_observation_window_count"] >= 5:
        confidence = "LOW" if metrics["cumulative_excess_vs_spy"] is None else "MODERATE"
        recommendation = "CONTINUE_SHADOW"
    else:
        confidence = "LOW"
        recommendation = "NOT_READY"
    return _response(
        "OK",
        context.db_path,
        outputs_root=str(root),
        lookback_days=lookback,
        current_leader=leadership.get("current_leader"),
        valid_observation_window_count=metrics["valid_observation_window_count"],
        cumulative_excess_vs_spy=metrics["cumulative_excess_vs_spy"],
        drawdown_comparison=metrics["drawdown_comparison"],
        turnover_comparison=metrics["turnover_comparison"],
        concentration_comparison=metrics["concentration_comparison"],
        confidence_level=confidence,
        recommendation=recommendation,
        phase_c_readiness=phase_c_states,
        evidence=history,
        guardrail="capital deployment is never recommended without a sufficient artifact-backed observation window",
    )


def anomaly_report(
    *,
    context: ToolContext | None = None,
    outputs_root: str | None = None,
    trade_date: str | None = None,
    lookback_days: int | None = 5,
) -> dict[str, Any]:
    context = context or ToolContext()
    root = Path(outputs_root) if outputs_root else Path("outputs")
    target_date = trade_date or datetime.now().date().isoformat()
    lookback = max(1, int(lookback_days or 5))
    anomalies = _anomaly_findings(root, target_date=target_date, lookback_days=lookback)
    max_severity = _max_anomaly_severity(anomalies)
    return _response(
        "NEEDS_OPERATOR" if max_severity == "NEEDS_OPERATOR" else "OK",
        context.db_path,
        warnings=[item["message"] for item in anomalies if item["severity"] == "NEEDS_OPERATOR"],
        outputs_root=str(root),
        trade_date=target_date,
        lookback_days=lookback,
        severity=max_severity,
        anomaly_count=len(anomalies),
        anomalies=anomalies,
        longitudinal_memory=_longitudinal_memory(root, lookback_days=lookback),
    )


def _response(status: str, db_path: Path, warnings: list[str] | None = None, **payload: Any) -> dict[str, Any]:
    response = {
        "status": status,
        "db_path": str(db_path),
        "queried_at": _now_utc(),
        "warnings": warnings or [],
        "findings": payload.pop("findings", []),
    }
    response.update(payload)
    return _jsonable(response)


def _jsonable(value: Any) -> Any:
    if hasattr(value, "to_dict"):
        return _jsonable(value.to_dict())
    if dataclasses.is_dataclass(value):
        return _jsonable(dataclasses.asdict(value))
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


def _now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _resolve_db_path(arguments: dict[str, Any], context: ToolContext) -> Path:
    value = arguments.get("db_path")
    return Path(value) if value else context.db_path


def _db_boundary_warning(db_path: Path) -> str | None:
    try:
        resolved = db_path.expanduser().resolve()
        outputs_root = (REPO_ROOT / "outputs").resolve()
        if resolved == outputs_root or outputs_root in resolved.parents:
            return "registry DB path must not be under repo outputs/"
    except OSError:
        return "registry DB path could not be resolved"
    return None


def _open_query(db_path: str | None, context: ToolContext | None) -> tuple[SQLiteResearchRegistry, RegistryQuery, Path]:
    context = context or ToolContext()
    resolved_db = _resolve_db_path({"db_path": db_path}, context)
    registry = SQLiteResearchRegistry(resolved_db)
    return registry, RegistryQuery(registry), resolved_db


def _limit_paths(paths: list[Path], limit: int | None) -> list[Path]:
    paths = sorted(paths, key=lambda item: str(item), reverse=True)
    if limit is None:
        return paths
    return paths[: _safe_limit(limit)]


def _safe_limit(limit: int | None) -> int:
    if limit is None:
        return 10
    return max(0, int(limit))


def _artifact_objects(query: RegistryQuery, artifact_type: str) -> list[Any]:
    return [
        obj
        for obj in query.list_objects()
        if obj.object_type == "ResearchArtifact" and obj.data.get("artifact_type") == artifact_type
    ]


def _sort_recent(objects: list[Any]) -> list[Any]:
    return sorted(
        objects,
        key=lambda obj: (
            str(obj.data.get("trade_date") or obj.data.get("packet_date") or obj.identity.get("trade_date") or ""),
            str(obj.temporal.get("as_of") or ""),
            str(obj.data.get("run_id") or obj.object_id),
        ),
        reverse=True,
    )


def _execution_runs_by_id(query: RegistryQuery) -> dict[str, Any]:
    return {str(obj.data["run_id"]): obj for obj in _artifact_objects(query, "execution_run") if obj.data.get("run_id")}


def _integrity_objects_by_run_id(query: RegistryQuery) -> dict[str, Any]:
    return {str(obj.data["run_id"]): obj for obj in _artifact_objects(query, "execution_integrity") if obj.data.get("run_id")}


def _run_record(run_obj: Any, integrity_obj: Any | None = None) -> dict[str, Any]:
    return {
        "trade_date": run_obj.data.get("trade_date") or run_obj.identity.get("trade_date"),
        "run_id": run_obj.data.get("run_id"),
        "status": run_obj.data.get("status"),
        "operator_execution_status": run_obj.data.get("operator_execution_status"),
        "submitted_count": run_obj.data.get("submitted_count"),
        "accepted_count": run_obj.data.get("accepted_count"),
        "rejected_count": run_obj.data.get("rejected_count"),
        "integrity_status": integrity_obj.data.get("status") if integrity_obj else run_obj.data.get("execution_integrity_status"),
        "object_id": run_obj.object_id,
    }


def _integrity_record(integrity_obj: Any) -> dict[str, Any]:
    findings = integrity_obj.data.get("findings")
    if not isinstance(findings, list):
        findings = []
    return {
        "trade_date": integrity_obj.data.get("trade_date") or integrity_obj.identity.get("trade_date"),
        "run_id": integrity_obj.data.get("run_id"),
        "status": integrity_obj.data.get("status"),
        "finding_count": integrity_obj.data.get("finding_count"),
        "pending_buy_count": integrity_obj.data.get("pending_buy_count"),
        "missing_buy_count": integrity_obj.data.get("missing_buy_count"),
        "findings": findings,
        "object_id": integrity_obj.object_id,
    }


_GOVERNANCE_STATUS_RANK = {
    "REVIEWED_DEFERRED": 5,
    "BACKLOG": 10,
    "READY": 20,
    "READY_VALIDATED": 30,
    "IN_PROGRESS": 40,
    "PROMOTION_READY": 50,
    "DEPLOYED_OBSERVING": 60,
    "DEPLOYED": 70,
}


def _source_path(obj: Any) -> str | None:
    source_paths = obj.provenance.get("source_paths") or []
    if not source_paths:
        return None
    return str(source_paths[0])


def _governance_source_rank(obj: Any) -> int:
    source_path = _source_path(obj) or ""
    if source_path.endswith("docs/governance/fr_active_backlog.md") or source_path.endswith("fr_active_backlog.md"):
        return 30
    if source_path.endswith("docs/governance/fr_registry.md") or source_path.endswith("fr_registry.md"):
        return 20
    return 10


def _governance_raw_record(obj: Any) -> dict[str, Any]:
    return {
        "fr_id": obj.data.get("fr_id"),
        "category": obj.data.get("category"),
        "status": obj.data.get("status"),
        "blast_radius": obj.data.get("blast_radius"),
        "governance_state": obj.governance.get("state"),
        "observation_status": obj.governance.get("observation_status"),
        "source_path": _source_path(obj),
        "object_id": obj.object_id,
    }


def _governance_record_rank(obj: Any) -> tuple[int, int, str]:
    status = str(obj.data.get("status") or "")
    return (_GOVERNANCE_STATUS_RANK.get(status, 0), _governance_source_rank(obj), obj.object_id)


def _resolve_governance_current_state(objects: list[Any]) -> list[dict[str, Any]]:
    grouped: dict[str, list[Any]] = {}
    for obj in objects:
        fr_id = obj.data.get("fr_id")
        if fr_id:
            grouped.setdefault(str(fr_id), []).append(obj)

    resolved: list[dict[str, Any]] = []
    for _, candidates in sorted(grouped.items()):
        winner = max(candidates, key=_governance_record_rank)
        raw_records = [_governance_raw_record(candidate) for candidate in candidates]
        source_paths = sorted({str(record["source_path"]) for record in raw_records if record.get("source_path")})
        suppressed = [
            str(record["status"])
            for record in raw_records
            if record["object_id"] != winner.object_id and record.get("status")
        ]
        record = _governance_raw_record(winner)
        record.update(
            {
                "duplicate_count": max(0, len(candidates) - 1),
                "source_count": len(source_paths),
                "resolved_from": _source_path(winner),
                "suppressed_statuses": sorted(set(suppressed)),
            }
        )
        resolved.append(record)
    return sorted(resolved, key=lambda item: (str(item["status"]), str(item["fr_id"])))


def _governance_open_item(item: dict[str, Any], *, include_deferred: bool = False, resolved: bool = True) -> bool:
    status = str(item.get("status") or "")
    blast_radius = str(item.get("blast_radius") or "").upper()
    if status in {"BACKLOG", "DEPLOYED_OBSERVING"}:
        return True
    if status == "REVIEWED_DEFERRED":
        return include_deferred or resolved
    return blast_radius in {"HIGH", "CRITICAL"}


def _artifact_family_status(root: Path, limit: int) -> list[dict[str, Any]]:
    families = [
        ("precompute", root / "precompute", "dated_directories"),
        ("execution_runs", root / "runs", "run_directories"),
        ("broker", root / "broker", "files"),
        ("workflow", root / "workflow", "dated_directories"),
        ("shadow_candidates", root / "shadow_candidates", "dated_directories"),
        ("research_packets", root / "research_packets", "dated_directories"),
        ("overnight_signals", root / "overnight_signals", "json_files"),
    ]
    payload = []
    for name, path, kind in families:
        children = _family_children(path, kind)
        payload.append(
            {
                "family": name,
                "root": str(path),
                "exists": path.exists(),
                "kind": kind,
                "count": len(children),
                "latest": [str(item) for item in children[:limit]],
            }
        )
    return payload


def _family_children(path: Path, kind: str) -> list[Path]:
    if not path.exists():
        return []
    if kind == "dated_directories":
        children = [
            child
            for child in path.iterdir()
            if child.is_dir() and re.match(r"^\d{4}-\d{2}-\d{2}$", child.name)
        ]
    elif kind == "run_directories":
        children = [
            child
            for child in path.iterdir()
            if child.is_dir() and re.match(r"^\d{4}-\d{2}-\d{2}T", child.name)
        ]
    elif kind == "json_files":
        children = [child for child in path.glob("*.json") if child.is_file()]
    else:
        children = [child for child in path.iterdir() if child.is_file()]
    return sorted(children, key=lambda item: item.name, reverse=True)


def _latest_dated_dir(path: Path) -> Path | None:
    if not path.exists():
        return None
    candidates = [
        child
        for child in path.iterdir()
        if child.is_dir() and re.match(r"^\d{4}-\d{2}-\d{2}$", child.name)
    ]
    return sorted(candidates, key=lambda item: item.name, reverse=True)[0] if candidates else None


def _latest_named_dir(path: Path) -> Path | None:
    if not path.exists():
        return None
    candidates = [
        child
        for child in path.iterdir()
        if child.is_dir() and re.match(r"^\d{4}-\d{2}-\d{2}T", child.name)
    ]
    return sorted(candidates, key=lambda item: item.name, reverse=True)[0] if candidates else None


def _file_probe(path: Path) -> dict[str, Any]:
    return {
        "path": str(path),
        "exists": path.exists(),
        "size_bytes": path.stat().st_size if path.exists() and path.is_file() else None,
    }


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _latest_precompute_status(root: Path) -> dict[str, Any]:
    latest = _latest_dated_dir(root / "precompute")
    if latest is None:
        return {"status": "NEEDS_OPERATOR", "reason": "no precompute bundle found", "path": str(root / "precompute")}
    required = ["contract.json", "daily_snapshot.json", "signals.json", "planned_execution_payload.json"]
    files = {name: _file_probe(latest / name) for name in required}
    missing = [name for name, probe in files.items() if not probe["exists"]]
    contract = _read_json(latest / "contract.json")
    return {
        "status": "OK" if not missing else "NEEDS_OPERATOR",
        "trade_date": latest.name,
        "path": str(latest),
        "missing_required": missing,
        "files": files,
        "contract_status": contract.get("status") or contract.get("phase_status"),
        "run_id": contract.get("run_id"),
    }


def _latest_execution_status(root: Path) -> dict[str, Any]:
    latest = _latest_named_dir(root / "runs")
    if latest is None:
        return {"status": "NEEDS_OPERATOR", "reason": "no execution run directory found", "path": str(root / "runs")}
    payload = _read_json(latest / "execution_payload.json")
    results = _read_json(latest / "execution_results.json")
    summary = _read_json(latest / "operator_summary.json")
    integrity = _read_json(latest / "audit" / "execution_integrity.json")
    files = {
        "execution_payload": _file_probe(latest / "execution_payload.json"),
        "execution_results": _file_probe(latest / "execution_results.json"),
        "operator_summary": _file_probe(latest / "operator_summary.json"),
        "execution_integrity": _file_probe(latest / "audit" / "execution_integrity.json"),
    }
    return {
        "status": "OK" if files["operator_summary"]["exists"] or files["execution_payload"]["exists"] else "NEEDS_OPERATOR",
        "run_id": payload.get("run_id") or results.get("run_id") or summary.get("run_id") or latest.name,
        "trade_date": payload.get("trade_date") or results.get("trade_date") or summary.get("trade_date"),
        "path": str(latest),
        "execution_status": summary.get("terminal_status") or results.get("status") or payload.get("status") or payload.get("execution_status"),
        "operator_execution_status": summary.get("operator_execution_status") or payload.get("operator_execution_status"),
        "integrity_status": integrity.get("status") or summary.get("execution_integrity_status"),
        "files": files,
    }


def _latest_broker_status(root: Path) -> dict[str, Any]:
    broker = root / "broker"
    snapshot = root / "broker_snapshot"
    if not broker.exists() and not snapshot.exists():
        return {"status": "NEEDS_OPERATOR", "reason": "broker artifact roots missing", "paths": [str(broker), str(snapshot)]}
    files = {
        "broker_snapshot_latest": _file_probe(broker / "broker_snapshot_latest.json"),
        "posttrade_positions": _file_probe(broker / "posttrade_positions.json"),
        "posttrade_account": _file_probe(broker / "posttrade_account.json"),
        "latest_snapshot_dir_file_count": len([item for item in snapshot.glob("*.json")]) if snapshot.exists() else 0,
    }
    recon_files = sorted(broker.glob("recon_*.json"), key=lambda item: item.name, reverse=True) if broker.exists() else []
    latest_recon = recon_files[0] if recon_files else None
    return {
        "status": "OK" if any(probe.get("exists") for probe in files.values() if isinstance(probe, dict)) or latest_recon else "NEEDS_OPERATOR",
        "path": str(broker),
        "latest_reconciliation": _file_probe(latest_recon) if latest_recon else None,
        "files": files,
    }


def _latest_shadow_status(root: Path) -> dict[str, Any]:
    latest = _latest_dated_dir(root / "shadow_candidates")
    workflow = _latest_dated_dir(root / "workflow")
    if latest is None:
        return {"status": "NEEDS_OPERATOR", "reason": "no dated shadow candidate directory found", "path": str(root / "shadow_candidates")}
    files = {
        "comparison_json": _file_probe(latest / "comparison.json"),
        "comparison_md": _file_probe(latest / "comparison.md"),
        "shadow_evaluation": _file_probe(latest / "shadow_evaluation.json"),
        "summary": _file_probe(latest / "summary.json"),
    }
    workflow_files = {}
    if workflow is not None:
        workflow_files = {
            "shadow": _file_probe(workflow / "shadow.json"),
            "shadow_generate": _file_probe(workflow / "shadow_generate.json"),
            "shadow_latest": _file_probe(workflow / "shadow_latest.json"),
            "shadow_reconciliation": _file_probe(workflow / "shadow_reconciliation.json"),
        }
    return {
        "status": "OK" if files["comparison_json"]["exists"] or files["comparison_md"]["exists"] else "NEEDS_OPERATOR",
        "trade_date": latest.name,
        "path": str(latest),
        "files": files,
        "workflow_trade_date": workflow.name if workflow else None,
        "workflow_files": workflow_files,
    }


def _latest_research_packet_status(root: Path) -> dict[str, Any]:
    latest = _latest_dated_dir(root / "research_packets")
    if latest is None:
        return {"status": "NEEDS_OPERATOR", "reason": "no research packet directory found", "path": str(root / "research_packets")}
    packet = _read_json(latest / "packet.json")
    summary = _read_json(latest / "summary.json")
    return {
        "status": "OK" if (latest / "packet.json").exists() or (latest / "summary.json").exists() else "NEEDS_OPERATOR",
        "trade_date": latest.name,
        "path": str(latest),
        "packet_status": packet.get("status") or summary.get("status"),
        "confidence": packet.get("confidence") or summary.get("confidence"),
        "source_readiness": packet.get("source_readiness") or summary.get("source_readiness"),
        "files": {
            "packet_json": _file_probe(latest / "packet.json"),
            "summary_json": _file_probe(latest / "summary.json"),
            "packet_md": _file_probe(latest / "packet.md"),
            "packet_html": _file_probe(latest / "packet.html"),
        },
    }


def _section_current(section: dict[str, Any], trade_date: str) -> bool:
    return section.get("status") == "OK" and section.get("trade_date") == trade_date


def _execution_completed_current(section: dict[str, Any], trade_date: str) -> bool:
    if not _section_current(section, trade_date):
        return False
    files = section.get("files") or {}
    return bool((files.get("execution_results") or {}).get("exists"))


def _operational_status_from_daily(daily: dict[str, Any]) -> dict[str, Any]:
    summary = daily.get("summary") or {}
    happened = summary.get("what_happened_today") or {}
    sections = summary.get("sections") or {}
    return {
        "status": daily.get("status"),
        "precompute": _compact_section(sections.get("precompute")),
        "execution": _compact_section(sections.get("execution")),
        "broker_recon": _compact_section(sections.get("broker_confirmation")),
        "shadow_lane": _compact_section(sections.get("shadow")),
        "research_packet": _compact_section(sections.get("research_packet")),
        "precompute_ran": happened.get("precompute_ran"),
        "execution_ran": happened.get("execution_ran"),
        "broker_recon_present": happened.get("broker_recon_present"),
        "shadow_ran": happened.get("shadow_ran"),
        "research_packet_current": happened.get("research_packet_current"),
    }


def _compact_section(section: dict[str, Any] | None) -> dict[str, Any]:
    section = section or {}
    return {
        "status": section.get("status"),
        "trade_date": section.get("trade_date"),
        "path": section.get("path") or section.get("paths"),
        "run_id": section.get("run_id"),
        "reason": section.get("reason"),
    }


def _dated_dirs(path: Path, limit: int | None = None) -> list[Path]:
    dirs = _family_children(path, "dated_directories")
    return dirs[:limit] if limit is not None else dirs


def _run_dirs(path: Path, limit: int | None = None) -> list[Path]:
    dirs = _family_children(path, "run_directories")
    return dirs[:limit] if limit is not None else dirs


def _shadow_history(root: Path, *, limit: int = 5) -> list[dict[str, Any]]:
    history: list[dict[str, Any]] = []
    for directory in _dated_dirs(root / "shadow_candidates", limit=limit):
        readiness = _read_json(directory / "promotion_readiness.json")
        longitudinal = _read_json(directory / "longitudinal_metrics.json")
        payload = _read_first_json(directory, ["comparison.json", "summary.json", "shadow_evaluation.json"])
        readiness_strategies = readiness.get("strategies") or {}
        leader_slug = readiness.get("current_leader")
        history.append(
            {
                "trade_date": directory.name,
                "path": str(directory),
                "status": payload.get("status") or payload.get("overall_status") or ("OK" if payload else "UNAVAILABLE"),
                "leader": leader_slug or _extract_leader(payload),
                "excess_vs_spy": _phase_c_leader_metric(readiness_strategies, leader_slug, "cumulative_excess_vs_spy") or _extract_numeric(payload, ["excess_vs_spy", "excess_return_vs_spy", "cumulative_excess_vs_spy"]),
                "drawdown": _extract_numeric(payload, ["drawdown", "max_drawdown", "current_drawdown"]),
                "turnover": _extract_numeric(payload, ["turnover", "turnover_ratio"]),
                "concentration": _extract_numeric(payload, ["concentration", "top5_weight", "max_position_weight"]),
                "polaris": _extract_strategy_record(payload, "polaris"),
                "lyra": _extract_strategy_record(payload, "lyra"),
                "orion": _extract_strategy_record(payload, "orion"),
                "phase_c_readiness": readiness,
                "phase_c_longitudinal_available": bool(longitudinal),
            }
        )
    return history


def _strategy_leadership(root: Path) -> dict[str, Any]:
    history = _shadow_history(root, limit=5)
    latest = history[0] if history else None
    if not latest:
        return {
            "status": "UNAVAILABLE",
            "current_leader": "unavailable",
            "reason": "no shadow comparison artifacts found",
            "evidence": [],
        }
    leader = latest.get("leader") or _leader_from_strategy_records(latest)
    readiness = latest.get("phase_c_readiness") or {}
    return {
        "status": "OK" if leader else "INSUFFICIENT_EVIDENCE",
        "current_leader": leader or "unavailable",
        "latest_trade_date": latest.get("trade_date"),
        "polaris": latest.get("polaris"),
        "lyra": latest.get("lyra"),
        "orion": latest.get("orion"),
        "latest_excess_vs_spy": latest.get("excess_vs_spy"),
        "readiness_states": {
            slug: {
                "state": item.get("readiness_state"),
                "confidence": item.get("confidence"),
                "reason_codes": item.get("reason_codes") or [],
            }
            for slug, item in (readiness.get("strategies") or {}).items()
        },
        "evidence_count": len(history),
        "evidence": history,
        "reason": None if leader else "latest shadow artifact did not expose comparable leadership metrics",
    }


def _leader_from_strategy_records(record: dict[str, Any]) -> str | None:
    candidates: list[tuple[float, str]] = []
    for name in ["polaris", "lyra", "orion"]:
        data = record.get(name) or {}
        value = _extract_numeric(data, ["excess_vs_spy", "excess_return", "return", "score"])
        if value is not None:
            candidates.append((float(value), name.title()))
    if not candidates:
        return None
    return sorted(candidates, reverse=True)[0][1]


def _portfolio_exposure(root: Path) -> dict[str, Any]:
    precompute = _latest_dated_dir(root / "precompute")
    if precompute is None:
        return {"status": "UNAVAILABLE", "reason": "no precompute bundle found"}
    payload = _read_json(precompute / "planned_execution_payload.json")
    trades = _extract_trades(payload)
    buys = [trade for trade in trades if str(trade.get("side") or "").upper() == "BUY"]
    sells = [trade for trade in trades if str(trade.get("side") or "").upper() == "SELL"]
    return {
        "status": "OK" if trades else "UNAVAILABLE",
        "trade_date": precompute.name,
        "path": str(precompute / "planned_execution_payload.json"),
        "trade_count": len(trades),
        "top_adds": _compact_trades(buys[:5]),
        "top_removes": _compact_trades(sells[:5]),
        "turnover_indicator": payload.get("turnover") or payload.get("estimated_turnover") or payload.get("turnover_ratio"),
        "concentration_indicator": payload.get("max_position_weight") or payload.get("top5_weight"),
        "reason": None if trades else "planned payload did not expose a trade list",
    }


def _regime_market_context(root: Path) -> dict[str, Any]:
    precompute = _latest_dated_dir(root / "precompute")
    if precompute is None:
        return {"status": "UNAVAILABLE", "reason": "no precompute bundle found"}
    snapshot = _read_json(precompute / "daily_snapshot.json")
    contract = _read_json(precompute / "contract.json")
    return {
        "status": "OK" if snapshot or contract else "UNAVAILABLE",
        "trade_date": precompute.name,
        "vix_regime": _nested_get(snapshot, ["regime", "vix_regime"]) or snapshot.get("vix_regime") or snapshot.get("volatility_regime"),
        "portfolio_scaling_state": snapshot.get("portfolio_scaling_state") or _nested_get(snapshot, ["risk", "scaling_state"]),
        "max_position_guidance": snapshot.get("max_position_weight") or contract.get("max_position_weight") or contract.get("max_weight"),
        "regime": snapshot.get("regime") if isinstance(snapshot.get("regime"), str) else None,
        "reason": None if snapshot or contract else "daily snapshot and contract were unreadable",
    }


def _promotion_metrics(history: list[dict[str, Any]]) -> dict[str, Any]:
    latest_readiness = (history[0].get("phase_c_readiness") if history else None) or {}
    if latest_readiness:
        strategies = latest_readiness.get("strategies") or {}
        best = _best_phase_c_strategy(strategies)
        return {
            "valid_observation_window_count": max((int((item or {}).get("valid_observation_windows") or 0) for item in strategies.values()), default=len(history)),
            "cumulative_excess_vs_spy": (best or {}).get("cumulative_excess_vs_spy"),
            "drawdown_comparison": {
                slug: item.get("max_drawdown")
                for slug, item in strategies.items()
            },
            "turnover_comparison": {
                slug: item.get("avg_turnover")
                for slug, item in strategies.items()
            },
            "concentration_comparison": {
                slug: item.get("avg_top_3_concentration")
                for slug, item in strategies.items()
            },
        }
    excess_values = [item.get("excess_vs_spy") for item in history if item.get("excess_vs_spy") is not None]
    return {
        "valid_observation_window_count": len(history),
        "cumulative_excess_vs_spy": sum(excess_values) if excess_values else None,
        "drawdown_comparison": _latest_metric(history, "drawdown"),
        "turnover_comparison": _latest_metric(history, "turnover"),
        "concentration_comparison": _latest_metric(history, "concentration"),
    }


def _phase_c_leader_metric(strategies: dict[str, Any], leader_slug: str | None, field: str) -> float | None:
    if not leader_slug:
        return None
    value = (strategies.get(str(leader_slug)) or {}).get(field)
    return float(value) if isinstance(value, (int, float)) else None


def _best_phase_c_strategy(strategies: dict[str, Any]) -> dict[str, Any] | None:
    candidates = []
    for strategy in strategies.values():
        value = strategy.get("cumulative_excess_vs_spy")
        if isinstance(value, (int, float)):
            candidates.append((float(value), strategy))
    if not candidates:
        return None
    return sorted(candidates, key=lambda item: item[0], reverse=True)[0][1]


def _strongest_phase_c_state(states: dict[str, dict[str, Any]]) -> dict[str, Any]:
    rank = {
        "NOT_READY": 0,
        "OBSERVE": 1,
        "CONTINUE_SHADOW": 2,
        "EMERGING_CANDIDATE": 3,
        "CANDIDATE_FOR_CAPITAL": 4,
    }
    candidates = sorted(
        states.values(),
        key=lambda item: rank.get(str(item.get("readiness_state")), -1),
        reverse=True,
    )
    return candidates[0] if candidates else {"readiness_state": "NOT_READY", "confidence": "LOW"}


def _latest_metric(history: list[dict[str, Any]], key: str) -> dict[str, Any]:
    for item in history:
        if item.get(key) is not None:
            return {"trade_date": item.get("trade_date"), "value": item.get(key)}
    return {"status": "UNAVAILABLE", "reason": f"{key} metric not found in shadow artifacts"}


def _anomaly_findings(root: Path, *, target_date: str, lookback_days: int) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    status = artifact_status(outputs_root=str(root), limit=lookback_days)
    for family in status.get("artifact_families") or []:
        if not family.get("exists"):
            findings.append(_anomaly("NEEDS_OPERATOR", "missing_artifact_family", f"{family['family']} root is missing", family.get("root")))
        elif int(family.get("count") or 0) == 0:
            findings.append(_anomaly("WARNING", "empty_artifact_family", f"{family['family']} root has no recognized artifacts", family.get("root")))
    daily = operator_daily_summary(outputs_root=str(root), trade_date=target_date)
    for warning in daily.get("warnings") or []:
        findings.append(_anomaly("NEEDS_OPERATOR", "daily_summary_warning", str(warning), str(root)))
    execution = status.get("latest_execution") or {}
    if _section_current(execution, target_date):
        files = execution.get("files") or {}
        if not (files.get("execution_integrity") or {}).get("exists"):
            findings.append(_anomaly("NEEDS_OPERATOR", "missing_execution_integrity", "latest execution run is missing execution integrity audit", execution.get("path")))
        if not (files.get("execution_results") or {}).get("exists"):
            findings.append(_anomaly("NEEDS_OPERATOR", "missing_execution_results", "latest execution run is missing execution results", execution.get("path")))
    findings.extend(_date_gap_anomalies(root, lookback_days=lookback_days))
    return _dedupe_anomalies(findings)


def _anomaly(severity: str, code: str, message: str, path: str | None) -> dict[str, Any]:
    return {"severity": severity, "code": code, "message": message, "path": path}


def _date_gap_anomalies(root: Path, *, lookback_days: int) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for family, path in [
        ("precompute", root / "precompute"),
        ("shadow_candidates", root / "shadow_candidates"),
        ("research_packets", root / "research_packets"),
    ]:
        dirs = _dated_dirs(path, limit=lookback_days)
        if not dirs:
            continue
        dates = [directory.name for directory in dirs]
        if len(dates) >= 2 and dates[0] == dates[1]:
            findings.append(_anomaly("WARNING", "duplicate_latest_date", f"{family} has duplicate latest dates", str(path)))
        if len(dates) < min(lookback_days, 3):
            findings.append(_anomaly("INFO", "limited_history", f"{family} has only {len(dates)} recent observations", str(path)))
    for run_dir in _run_dirs(root / "runs", limit=lookback_days):
        if not any(run_dir.iterdir()):
            findings.append(_anomaly("WARNING", "empty_run_directory", "execution run directory is empty", str(run_dir)))
    return findings


def _dedupe_anomalies(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str | None]] = set()
    deduped: list[dict[str, Any]] = []
    for finding in findings:
        key = (str(finding.get("severity")), str(finding.get("message")), finding.get("path"))
        if key not in seen:
            seen.add(key)
            deduped.append(finding)
    rank = {"NEEDS_OPERATOR": 0, "WARNING": 1, "INFO": 2}
    return sorted(deduped, key=lambda item: (rank.get(str(item.get("severity")), 9), str(item.get("code")), str(item.get("path"))))


def _max_anomaly_severity(findings: list[dict[str, Any]]) -> str:
    severities = {item.get("severity") for item in findings}
    if "NEEDS_OPERATOR" in severities:
        return "NEEDS_OPERATOR"
    if "WARNING" in severities:
        return "WARNING"
    if "INFO" in severities:
        return "INFO"
    return "INFO"


def _longitudinal_memory(root: Path, *, lookback_days: int) -> dict[str, Any]:
    return {
        "mode": "computed_read_only",
        "lookback_days": lookback_days,
        "precompute_dates": [path.name for path in _dated_dirs(root / "precompute", limit=lookback_days)],
        "execution_run_ids": [path.name for path in _run_dirs(root / "runs", limit=lookback_days)],
        "shadow_dates": [path.name for path in _dated_dirs(root / "shadow_candidates", limit=lookback_days)],
        "research_packet_dates": [path.name for path in _dated_dirs(root / "research_packets", limit=lookback_days)],
    }


def _read_first_json(directory: Path, names: list[str]) -> dict[str, Any]:
    for name in names:
        payload = _read_json(directory / name)
        if payload:
            return payload
    return {}


def _extract_trades(payload: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ["trades", "orders", "intended_orders", "planned_orders"]:
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _compact_trades(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compact = []
    for trade in trades:
        compact.append(
            {
                "ticker": trade.get("ticker") or trade.get("symbol"),
                "side": trade.get("side"),
                "quantity": trade.get("quantity") or trade.get("qty") or trade.get("shares"),
                "target_weight": trade.get("target_weight") or trade.get("weight"),
            }
        )
    return compact


def _extract_leader(payload: dict[str, Any]) -> str | None:
    for key in ["leader", "current_leader", "winner", "recommended_strategy"]:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _extract_strategy_record(payload: dict[str, Any], strategy: str) -> dict[str, Any] | None:
    candidates = [strategy, strategy.title(), strategy.upper(), f"caerus_{strategy}"]
    for key in candidates:
        value = payload.get(key)
        if isinstance(value, dict):
            return {
                "status": value.get("status"),
                "excess_vs_spy": _extract_numeric(value, ["excess_vs_spy", "excess_return", "relative_return"]),
                "drawdown": _extract_numeric(value, ["drawdown", "max_drawdown"]),
                "turnover": _extract_numeric(value, ["turnover", "turnover_ratio"]),
            }
    strategies = payload.get("strategies") or payload.get("candidates")
    if isinstance(strategies, dict):
        for key in candidates:
            value = strategies.get(key)
            if isinstance(value, dict):
                return {
                    "status": value.get("status"),
                    "excess_vs_spy": _extract_numeric(value, ["excess_vs_spy", "excess_return", "relative_return"]),
                    "drawdown": _extract_numeric(value, ["drawdown", "max_drawdown"]),
                    "turnover": _extract_numeric(value, ["turnover", "turnover_ratio"]),
                }
    return None


def _extract_numeric(payload: dict[str, Any], keys: list[str]) -> float | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value)
            except ValueError:
                continue
    return None


def _nested_get(payload: dict[str, Any], path: list[str]) -> Any:
    current: Any = payload
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


# ---------------------------------------------------------------------------
# Phase-7 research-question tools (timing × VIX regime).
#
# Deliberately read-only and deterministic: no DB writes, no network, no LLM,
# and every "missing data" branch returns a structured status the caller can
# render verbatim (NO_TIMING_DATA / NO_REGIME_DATA / UNSUPPORTED_INTENT).
# ---------------------------------------------------------------------------


def execution_timing_by_vix_regime(
    *,
    context: ToolContext | None = None,
    timing_root: str | None = None,
    regime_history: str | None = None,
    insufficient_sample_threshold: int | None = None,
) -> dict[str, Any]:
    """Stratify timing-replay opportunities by VIX regime.

    Joins the most recent ``outputs/research/execution_timing/<RUN_DATE>``
    replay output to ``outputs/vix_regime/regime_history.csv`` on
    ``execution_date`` and returns per-regime, per-offset opportunity
    statistics (mean/median in USD and bps).

    Fail-closed: ``status == "NO_TIMING_DATA"`` if no replay has been run;
    ``status == "NO_REGIME_DATA"`` if regime history is missing. Buckets
    with fewer than the configured threshold of days are tagged
    ``insufficient_sample`` and excluded from any significance claim.
    """
    timing_path = Path(timing_root) if timing_root else DEFAULT_TIMING_ROOT
    regime_path = Path(regime_history) if regime_history else DEFAULT_REGIME_HISTORY
    threshold = int(insufficient_sample_threshold) if insufficient_sample_threshold is not None else INSUFFICIENT_SAMPLE_THRESHOLD

    answer = answer_timing_by_regime_question(
        timing_root=timing_path,
        regime_history=regime_path,
        threshold=threshold,
    )
    answer["tool"] = "execution_timing_by_vix_regime"
    answer["queried_at"] = _now_utc()
    return _jsonable(answer)


def execution_timing_summary(
    *,
    context: ToolContext | None = None,
    timing_root: str | None = None,
    question: str | None = None,
    highlighted_offsets: list[str] | None = None,
) -> dict[str, Any]:
    """Aggregate (non-regime-stratified) execution-timing summary.

    Reads the latest ``outputs/research/execution_timing/<RUN_DATE>/
    timing_summary.json`` and returns a compact panel of per-offset
    mean/median opportunity USD + bps, plus a conservative recommendation
    (``retain_9_35_baseline`` / ``earlier_timing_appears_better`` /
    ``insufficient_evidence``). If a question is provided, clock-time
    offsets (e.g. ``9:30``, ``9:35``) are extracted and surfaced under
    ``highlighted_offsets`` so the renderer can flag them.
    """
    timing_path = Path(timing_root) if timing_root else DEFAULT_SUMMARY_TIMING_ROOT
    resolved_highlights: list[str] | None = None
    if highlighted_offsets is not None:
        resolved_highlights = list(highlighted_offsets)
    elif question:
        resolved_highlights = list(parse_offset_highlights(question))
    answer = summarise_timing(
        timing_root=timing_path,
        question=question,
        highlighted_offsets=resolved_highlights,
    )
    payload = timing_summary_to_dict(answer)
    payload["tool"] = "execution_timing_summary"
    payload["queried_at"] = _now_utc()
    return _jsonable(payload)


def shadow_comparison(
    *,
    context: ToolContext | None = None,
    outputs_root: str | None = None,
    shadow_root: str | None = None,
    question: str | None = None,
    strategies: list[str] | None = None,
) -> dict[str, Any]:
    """Side-by-side shadow-portfolio comparison.

    Reads the latest ``outputs/shadow_candidates/<DATE>/shadow_evaluation.json``
    + ``comparison.json`` and returns per-strategy NAV / cumulative
    return / excess vs SPY / turnover / drawdown panel plus pairwise
    overlap when two strategies are named. Strategy names parsed from
    the question are restricted to the closed list
    ``polaris|orion|lyra|leda``; unknown names → NEEDS_DATA.
    """
    if shadow_root:
        resolved_root = Path(shadow_root)
    elif outputs_root:
        resolved_root = Path(outputs_root) / "shadow_candidates"
    else:
        resolved_root = DEFAULT_SHADOW_ROOT
    resolved_strategies: list[str] | None = None
    if strategies is not None:
        resolved_strategies = list(strategies)
    elif question:
        names = parse_strategy_names(question)
        resolved_strategies = names or None
    answer = compare_shadow_strategies(
        shadow_root=resolved_root,
        question=question,
        strategies=resolved_strategies,
    )
    payload = shadow_comparison_to_dict(answer)
    payload["tool"] = "shadow_comparison"
    payload["queried_at"] = _now_utc()
    return _jsonable(payload)


def _route_to_tool(
    tool_name: str,
    tool_kwargs: dict[str, Any],
    *,
    context: ToolContext | None,
    pass_question: str | None,
    pass_through: dict[str, Any],
) -> dict[str, Any]:
    """Invoke an existing MCP tool through the standard dispatch path.

    ``pass_through`` carries the optional caller-level overrides
    (``timing_root``, ``regime_history``, etc.) the gateway forwards per
    question. ``pass_question`` is set when the routed tool accepts a
    raw NL ``question`` argument (used by ``execution_timing_summary``
    and ``shadow_comparison`` to extract offsets / strategy names).
    """
    if tool_name == "answer_research_question":
        # Defensive — avoid infinite recursion via the planner's own tool.
        raise RuntimeError("planner attempted to route to itself")
    arguments = dict(tool_kwargs)
    for key, value in pass_through.items():
        if value is None:
            continue
        arguments.setdefault(key, value)
    if pass_question is not None:
        arguments.setdefault("question", pass_question)
    return call_tool(tool_name, arguments, context=context)


def answer_research_question(
    *,
    context: ToolContext | None = None,
    question: str | None = None,
    timing_root: str | None = None,
    regime_history: str | None = None,
    insufficient_sample_threshold: int | None = None,
    outputs_root: str | None = None,
) -> dict[str, Any]:
    """Deterministic capability-based router over the question-answering MCP.

    Pipeline:

    1. :func:`classify_question` matches the question against
       :data:`CAPABILITY_REGISTRY` (regex-only, deterministic). Ties
       broken by registry order.
    2. If no capability matches → ``UNSUPPORTED_INTENT``; the response
       carries up to three ``closest_capabilities`` ranked by token
       overlap plus a generic ``missing_capability_description``.
    3. If the matched capability has no ``tool_name`` →
       ``NEEDS_CAPABILITY``; the response carries the registry entry
       and the ``suggested_next_build`` paragraph describing exactly
       what would have to be built.
    4. If the capability is implemented but its
       ``required_artifact_globs`` are not satisfied on disk →
       ``NEEDS_DATA``; the response names the missing paths.
    5. Otherwise the planner invokes the underlying MCP tool via
       :func:`call_tool` and propagates its inner status (so e.g.
       ``NO_TIMING_DATA`` from the tool still bubbles up cleanly).
    """
    queried_at = _now_utc()
    classification = classify_question(question or "")
    matched = classification.capability

    if matched is None:
        closest = [capability_summary(cap) for cap in classification.closest]
        return _jsonable(
            {
                "status": "UNSUPPORTED_INTENT",
                "tool": "answer_research_question",
                "queried_at": queried_at,
                "question": question,
                "intent": None,
                "routed_to": None,
                "reason": (
                    "No capability matched the question. This MCP is deterministic "
                    "and regex-driven; see `closest_capabilities` for the nearest "
                    "registry entries by token overlap and `available_intents` for "
                    "the full registry. Add a new entry to "
                    "research_registry.research.capabilities.CAPABILITY_REGISTRY "
                    "to extend coverage."
                ),
                "closest_capabilities": closest,
                "missing_capability_description": (
                    "No registry capability matched this question's keywords. "
                    "Either rephrase using one of the example_questions in the "
                    "closest capabilities, or file a new capability entry."
                ),
                "available_intents": available_intents(),
                "warnings": [],
            }
        )

    if not matched.is_implemented():
        return _jsonable(
            {
                "status": "NEEDS_CAPABILITY",
                "tool": "answer_research_question",
                "queried_at": queried_at,
                "question": question,
                "intent": matched.name,
                "routed_to": None,
                "matched_capability": capability_summary(matched),
                "suggested_next_build": matched.suggested_next_build,
                "reason": (
                    f"Capability {matched.name!r} matched the question, but no "
                    "MCP tool is wired to answer it yet. See "
                    "`suggested_next_build` for the concrete next step."
                ),
                "available_intents": available_intents(),
                "warnings": [
                    f"capability {matched.name!r} is recognised but not yet implemented"
                ],
            }
        )

    artifact_status = check_artifacts(matched.required_artifact_globs)
    if not artifact_status.ready:
        return _jsonable(
            {
                "status": "NEEDS_DATA",
                "tool": "answer_research_question",
                "queried_at": queried_at,
                "question": question,
                "intent": matched.name,
                "routed_to": matched.tool_name,
                "matched_capability": capability_summary(matched),
                "missing_artifacts": list(artifact_status.missing),
                "matched_artifacts": list(artifact_status.matched),
                "reason": (
                    f"Capability {matched.name!r} is implemented but at least one "
                    "required artifact is missing on disk. See `missing_artifacts` "
                    "for the exact paths to produce before re-running."
                ),
                "available_intents": available_intents(),
                "warnings": [
                    f"missing artifact: {pattern}" for pattern in artifact_status.missing
                ],
            }
        )

    # Tools that take an NL question for offset / strategy extraction.
    tools_accepting_question = {"execution_timing_summary", "shadow_comparison"}
    underlying = _route_to_tool(
        matched.tool_name,
        matched.tool_kwargs,
        context=context,
        pass_question=question if matched.tool_name in tools_accepting_question else None,
        pass_through={
            "timing_root": timing_root,
            "regime_history": regime_history,
            "insufficient_sample_threshold": insufficient_sample_threshold,
            "outputs_root": outputs_root,
        },
    )

    return _jsonable(
        {
            "status": underlying.get("status", "OK"),
            "tool": "answer_research_question",
            "queried_at": queried_at,
            "question": question,
            "intent": matched.name,
            "routed_to": matched.tool_name,
            "matched_capability": capability_summary(matched),
            "answer": underlying,
            "available_intents": available_intents(),
            "warnings": underlying.get("warnings") or [],
        }
    )
