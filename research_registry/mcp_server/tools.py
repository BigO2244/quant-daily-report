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
