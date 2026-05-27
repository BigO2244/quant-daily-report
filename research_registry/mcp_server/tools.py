"""Read-only tool functions for the Caerus MCP-compatible server."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from research_registry.ingestion import ingest_artifact_family
from research_registry.mcp_server.schemas import TOOL_DEFINITIONS
from research_registry.query import RegistryQuery
from research_registry.registry import SQLiteResearchRegistry


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
