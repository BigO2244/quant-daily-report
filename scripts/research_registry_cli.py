#!/usr/bin/env python3
"""Read-only operator CLI for the Caerus research registry.

The CLI builds or opens a caller-specified disposable SQLite registry. Source
artifacts are only read; they are never modified.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from research_registry.ingestion import ingest_artifact_family
from research_registry.mcp_server.tools import (
    anomaly_report,
    artifact_drilldown,
    artifact_status,
    daily_operator_brief,
    morning_cio_brief,
    operator_daily_summary,
    promotion_readiness,
)
from research_registry.query import RegistryQuery
from research_registry.registry import SQLiteResearchRegistry


def _json_default(value: Any) -> Any:
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if dataclasses.is_dataclass(value):
        return dataclasses.asdict(value)
    if isinstance(value, Path):
        return str(value)
    return str(value)


def _print_json(payload: Any) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True, default=_json_default))


def _load_manifest(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict) and isinstance(payload.get("families"), list):
        entries = payload["families"]
    elif isinstance(payload, dict):
        entries = [
            {"family": family, "paths": paths}
            for family, paths in sorted(payload.items())
        ]
    elif isinstance(payload, list):
        entries = payload
    else:
        raise ValueError("manifest must be a JSON object or list")

    normalized: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("manifest entries must be objects")
        family = str(entry.get("family") or "").strip()
        paths = entry.get("paths") or entry.get("artifact_paths") or []
        if not family:
            raise ValueError("manifest entry missing family")
        if isinstance(paths, str):
            paths = [paths]
        if not isinstance(paths, list):
            raise ValueError(f"manifest entry for {family} has non-list paths")
        normalized.append({"family": family, "paths": [str(path) for path in paths]})
    return normalized


def _open_registry(db_path: Path) -> SQLiteResearchRegistry:
    return SQLiteResearchRegistry(db_path)


def _limit_paths(paths: list[Path], limit: int | None) -> list[Path]:
    paths = sorted(paths, key=lambda item: str(item), reverse=True)
    if limit is None:
        return paths
    return paths[: max(0, int(limit))]


def _ingest_family(
    *,
    db_path: Path,
    family: str,
    paths: list[Path],
) -> dict[str, Any]:
    registry = _open_registry(db_path)
    try:
        result = ingest_artifact_family(
            family=family,
            artifact_paths=paths,
            registry=registry,
        )
        query = RegistryQuery(registry)
        return {
            "family": family,
            "path_count": len(paths),
            "envelope_count": len(result.envelopes),
            "findings": [dataclasses.asdict(finding) for finding in result.findings],
            "summary": query.registry_summary(),
        }
    finally:
        registry.close()


def _registry_query(db_path: Path) -> tuple[SQLiteResearchRegistry, RegistryQuery]:
    registry = _open_registry(db_path)
    return registry, RegistryQuery(registry)


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
    runs: dict[str, Any] = {}
    for obj in _artifact_objects(query, "execution_run"):
        run_id = obj.data.get("run_id")
        if run_id:
            runs[str(run_id)] = obj
    return runs


def _integrity_objects_by_run_id(query: RegistryQuery) -> dict[str, Any]:
    audits: dict[str, Any] = {}
    for obj in _artifact_objects(query, "execution_integrity"):
        run_id = obj.data.get("run_id")
        if run_id:
            audits[str(run_id)] = obj
    return audits


def _run_record(run_obj: Any, integrity_obj: Any | None = None) -> dict[str, Any]:
    return {
        "trade_date": run_obj.data.get("trade_date") or run_obj.identity.get("trade_date"),
        "run_id": run_obj.data.get("run_id"),
        "status": run_obj.data.get("status"),
        "operator_execution_status": run_obj.data.get("operator_execution_status"),
        "submitted_count": run_obj.data.get("submitted_count"),
        "accepted_count": run_obj.data.get("accepted_count"),
        "rejected_count": run_obj.data.get("rejected_count"),
        "integrity_status": (
            integrity_obj.data.get("status")
            if integrity_obj is not None
            else run_obj.data.get("execution_integrity_status")
        ),
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
    return (
        _GOVERNANCE_STATUS_RANK.get(status, 0),
        _governance_source_rank(obj),
        obj.object_id,
    )


def _resolve_governance_current_state(objects: list[Any]) -> list[dict[str, Any]]:
    grouped: dict[str, list[Any]] = {}
    for obj in objects:
        fr_id = obj.data.get("fr_id")
        if fr_id:
            grouped.setdefault(str(fr_id), []).append(obj)

    resolved: list[dict[str, Any]] = []
    for fr_id, candidates in sorted(grouped.items()):
        winner = max(candidates, key=_governance_record_rank)
        raw_records = [_governance_raw_record(candidate) for candidate in candidates]
        source_paths = sorted(
            {
                str(record["source_path"])
                for record in raw_records
                if record.get("source_path")
            }
        )
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


def _governance_open_item(
    item: dict[str, Any],
    *,
    include_deferred: bool = False,
    resolved: bool = True,
) -> bool:
    status = str(item.get("status") or "")
    blast_radius = str(item.get("blast_radius") or "").upper()
    if status in {"BACKLOG", "DEPLOYED_OBSERVING"}:
        return True
    if status == "REVIEWED_DEFERRED":
        return include_deferred or resolved
    return blast_radius in {"HIGH", "CRITICAL"}


def cmd_latest_runs(args: argparse.Namespace) -> int:
    registry, query = _registry_query(Path(args.db))
    try:
        integrity_by_run = _integrity_objects_by_run_id(query)
        runs = _sort_recent(_artifact_objects(query, "execution_run"))
        if args.limit is not None:
            runs = runs[: max(0, int(args.limit))]
        _print_json(
            {
                "db_path": str(Path(args.db)),
                "run_count": len(runs),
                "runs": [
                    _run_record(run_obj, integrity_by_run.get(str(run_obj.data.get("run_id"))))
                    for run_obj in runs
                ],
            }
        )
    finally:
        registry.close()
    return 0


def cmd_run_health(args: argparse.Namespace) -> int:
    registry, query = _registry_query(Path(args.db))
    try:
        runs_by_id = _execution_runs_by_id(query)
        integrity_by_run = _integrity_objects_by_run_id(query)
        run_obj = runs_by_id.get(args.run_id)
        integrity_obj = integrity_by_run.get(args.run_id)
        _print_json(
            {
                "db_path": str(Path(args.db)),
                "run_id": args.run_id,
                "status": "FOUND" if run_obj or integrity_obj else "NOT_FOUND",
                "execution_run": _run_record(run_obj, integrity_obj) if run_obj else None,
                "execution_payload": run_obj.data.get("execution_payload") if run_obj else None,
                "operator_summary": run_obj.data.get("operator_summary") if run_obj else None,
                "execution_results": run_obj.data.get("execution_results") if run_obj else None,
                "execution_integrity": _integrity_record(integrity_obj) if integrity_obj else None,
                "source_paths": {
                    "execution_run": run_obj.provenance.get("source_paths", []) if run_obj else [],
                    "execution_integrity": integrity_obj.provenance.get("source_paths", []) if integrity_obj else [],
                },
            }
        )
    finally:
        registry.close()
    return 0


def cmd_integrity_findings(args: argparse.Namespace) -> int:
    registry, query = _registry_query(Path(args.db))
    try:
        audits = [
            obj
            for obj in _sort_recent(_artifact_objects(query, "execution_integrity"))
            if obj.data.get("status") in {"WARN", "FAIL"}
        ]
        if args.limit is not None:
            audits = audits[: max(0, int(args.limit))]
        _print_json(
            {
                "db_path": str(Path(args.db)),
                "finding_object_count": len(audits),
                "integrity_findings": [_integrity_record(obj) for obj in audits],
            }
        )
    finally:
        registry.close()
    return 0


def cmd_governance_open(args: argparse.Namespace) -> int:
    registry, query = _registry_query(Path(args.db))
    try:
        raw_items = [_governance_raw_record(obj) for obj in query.query_by_type("GovernanceFR")]
        if args.show_duplicates:
            items = [
                item
                for item in raw_items
                if _governance_open_item(item, include_deferred=args.include_deferred, resolved=False)
            ]
            items = sorted(items, key=lambda item: (str(item["status"]), str(item["fr_id"]), str(item["object_id"])))
            _print_json(
                {
                    "db_path": str(Path(args.db)),
                    "mode": "raw_duplicates",
                    "open_count": len(items),
                    "items": items,
                }
            )
        else:
            resolved_items = _resolve_governance_current_state(query.query_by_type("GovernanceFR"))
            items = [
                item
                for item in resolved_items
                if _governance_open_item(item, include_deferred=args.include_deferred, resolved=True)
            ]
            _print_json(
                {
                    "db_path": str(Path(args.db)),
                    "mode": "deduped_current_state",
                    "open_count": len(items),
                    "items": items,
                }
            )
    finally:
        registry.close()
    return 0


def cmd_research_packet_status(args: argparse.Namespace) -> int:
    registry, query = _registry_query(Path(args.db))
    try:
        packets = _sort_recent(_artifact_objects(query, "research_packet"))
        if args.limit is not None:
            packets = packets[: max(0, int(args.limit))]
        _print_json(
            {
                "db_path": str(Path(args.db)),
                "packet_count": len(packets),
                "packets": [
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
            }
        )
    finally:
        registry.close()
    return 0


def cmd_daily_operator_brief(args: argparse.Namespace) -> int:
    _print_json(daily_operator_brief(db_path=args.db))
    return 0


def _format_artifact_status_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# MCP Artifact Status",
        "",
        f"- Status: `{payload.get('status')}`",
        f"- Outputs root: `{payload.get('outputs_root')}`",
        f"- Queried at: `{payload.get('queried_at')}`",
        "",
        "## Latest Artifacts",
    ]
    sections = [
        ("Precompute", payload.get("latest_precompute") or {}),
        ("Execution", payload.get("latest_execution") or {}),
        ("Broker / Confirmation", payload.get("latest_broker_confirmation") or {}),
        ("Shadow", payload.get("latest_shadow") or {}),
        ("Research Packet", payload.get("latest_research_packet") or {}),
    ]
    for label, section in sections:
        lines.extend(
            [
                "",
                f"### {label}",
                f"- Status: `{section.get('status')}`",
                f"- Path: `{section.get('path')}`",
            ]
        )
        if section.get("trade_date"):
            lines.append(f"- Trade date: `{section.get('trade_date')}`")
        if section.get("run_id"):
            lines.append(f"- Run ID: `{section.get('run_id')}`")
        if section.get("reason"):
            lines.append(f"- Reason: {section.get('reason')}")
        if section.get("missing_required"):
            lines.append(f"- Missing required: `{', '.join(section.get('missing_required') or [])}`")
    lines.extend(["", "## Artifact Families", ""])
    for family in payload.get("artifact_families") or []:
        lines.append(
            f"- `{family.get('family')}`: count={family.get('count')} root=`{family.get('root')}`"
        )
    if payload.get("warnings"):
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- {warning}" for warning in payload["warnings"])
    return "\n".join(lines) + "\n"


def _format_daily_summary_markdown(payload: dict[str, Any]) -> str:
    summary = payload.get("summary") or {}
    happened = summary.get("what_happened_today") or {}
    lines = [
        "# MCP Daily Summary",
        "",
        f"- Status: `{payload.get('status')}`",
        f"- Trade date: `{payload.get('trade_date')}`",
        f"- Outputs root: `{payload.get('outputs_root')}`",
        f"- Queried at: `{payload.get('queried_at')}`",
        "",
        "## What Happened Today",
        "",
        f"- Precompute ran: `{happened.get('precompute_ran')}`",
        f"- Execution ran: `{happened.get('execution_ran')}`",
        f"- Broker/recon present: `{happened.get('broker_recon_present')}`",
        f"- Shadow lane ran: `{happened.get('shadow_ran')}`",
        f"- Research packet current: `{happened.get('research_packet_current')}`",
        "",
        "## Latest Paths",
    ]
    for key, section in (summary.get("sections") or {}).items():
        lines.append(f"- `{key}`: status=`{section.get('status')}` path=`{section.get('path') or section.get('paths')}`")
    if payload.get("warnings"):
        lines.extend(["", "## Needs Operator Attention", ""])
        lines.extend(f"- {warning}" for warning in payload["warnings"])
    return "\n".join(lines) + "\n"


def _format_artifact_drilldown_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# MCP Artifact Drilldown",
        "",
        f"- Status: `{payload.get('status')}`",
        f"- Outputs root: `{payload.get('outputs_root')}`",
        f"- Family: `{payload.get('family')}`",
        f"- Queried at: `{payload.get('queried_at')}`",
        "",
        "## File Probes",
    ]
    for family, section in (payload.get("drilldown") or {}).items():
        lines.extend(["", f"### {family}", f"- Status: `{section.get('status')}`", f"- Path: `{section.get('path') or section.get('paths')}`"])
        if section.get("trade_date"):
            lines.append(f"- Trade date: `{section.get('trade_date')}`")
        if section.get("run_id"):
            lines.append(f"- Run ID: `{section.get('run_id')}`")
        if section.get("missing_required"):
            lines.append(f"- Missing required: `{', '.join(section.get('missing_required') or [])}`")
        files = section.get("files") or {}
        for name, probe in files.items():
            if isinstance(probe, dict):
                lines.append(f"- `{name}`: exists=`{probe.get('exists')}` size={probe.get('size_bytes')} path=`{probe.get('path')}`")
        workflow_files = section.get("workflow_files") or {}
        for name, probe in workflow_files.items():
            if isinstance(probe, dict):
                lines.append(f"- `workflow.{name}`: exists=`{probe.get('exists')}` size={probe.get('size_bytes')} path=`{probe.get('path')}`")
        latest_recon = section.get("latest_reconciliation")
        if isinstance(latest_recon, dict):
            lines.append(
                f"- `latest_reconciliation`: exists=`{latest_recon.get('exists')}` size={latest_recon.get('size_bytes')} path=`{latest_recon.get('path')}`"
            )
    return "\n".join(lines) + "\n"


def _format_morning_brief_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# MCP Morning CIO Brief",
        "",
        f"- Status: `{payload.get('status')}`",
        f"- Trade date: `{payload.get('trade_date')}`",
        f"- Outputs root: `{payload.get('outputs_root')}`",
        f"- Queried at: `{payload.get('queried_at')}`",
        "",
        "## Operational Status",
    ]
    operational = payload.get("operational_status") or {}
    for key in ["precompute", "execution", "broker_recon", "shadow_lane", "research_packet"]:
        section = operational.get(key) or {}
        lines.append(f"- `{key}`: status=`{section.get('status')}` date=`{section.get('trade_date')}` path=`{section.get('path')}`")
    leadership = payload.get("strategy_leadership") or {}
    lines.extend(
        [
            "",
            "## Strategy Leadership",
            f"- Status: `{leadership.get('status')}`",
            f"- Current leader: `{leadership.get('current_leader')}`",
            f"- Evidence count: `{leadership.get('evidence_count')}`",
        ]
    )
    exposure = payload.get("portfolio_exposure") or {}
    lines.extend(["", "## Portfolio / Exposure", f"- Status: `{exposure.get('status')}`", f"- Trade count: `{exposure.get('trade_count')}`"])
    for trade in exposure.get("top_adds") or []:
        lines.append(f"- Add: `{trade.get('ticker')}` qty=`{trade.get('quantity')}` target=`{trade.get('target_weight')}`")
    for trade in exposure.get("top_removes") or []:
        lines.append(f"- Remove: `{trade.get('ticker')}` qty=`{trade.get('quantity')}` target=`{trade.get('target_weight')}`")
    regime = payload.get("regime_market_context") or {}
    lines.extend(
        [
            "",
            "## Regime / Market Context",
            f"- VIX regime: `{regime.get('vix_regime')}`",
            f"- Scaling state: `{regime.get('portfolio_scaling_state')}`",
            f"- Max position guidance: `{regime.get('max_position_guidance')}`",
        ]
    )
    if payload.get("operator_attention"):
        lines.extend(["", "## Operator Attention"])
        lines.extend(f"- {warning}" for warning in payload["operator_attention"])
    return "\n".join(lines) + "\n"


def _format_promotion_readiness_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# MCP Promotion Readiness",
        "",
        f"- Status: `{payload.get('status')}`",
        f"- Current leader: `{payload.get('current_leader')}`",
        f"- Observation windows: `{payload.get('valid_observation_window_count')}`",
        f"- Cumulative excess vs SPY: `{payload.get('cumulative_excess_vs_spy')}`",
        f"- Confidence: `{payload.get('confidence_level')}`",
        f"- Recommendation: `{payload.get('recommendation')}`",
        f"- Guardrail: {payload.get('guardrail')}",
        "",
        "## Latest Evidence",
    ]
    for item in payload.get("evidence") or []:
        lines.append(f"- `{item.get('trade_date')}` leader=`{item.get('leader')}` excess=`{item.get('excess_vs_spy')}` path=`{item.get('path')}`")
    return "\n".join(lines) + "\n"


def _format_anomaly_report_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# MCP Anomaly Report",
        "",
        f"- Status: `{payload.get('status')}`",
        f"- Severity: `{payload.get('severity')}`",
        f"- Trade date: `{payload.get('trade_date')}`",
        f"- Anomaly count: `{payload.get('anomaly_count')}`",
        "",
        "## Findings",
    ]
    for item in payload.get("anomalies") or []:
        lines.append(f"- `{item.get('severity')}` `{item.get('code')}`: {item.get('message')} path=`{item.get('path')}`")
    return "\n".join(lines) + "\n"


def cmd_artifact_status(args: argparse.Namespace) -> int:
    payload = artifact_status(outputs_root=args.outputs_root, limit=args.limit)
    if args.markdown:
        print(_format_artifact_status_markdown(payload))
    else:
        _print_json(payload)
    return 0


def cmd_daily_summary(args: argparse.Namespace) -> int:
    payload = operator_daily_summary(outputs_root=args.outputs_root, trade_date=args.trade_date)
    if args.markdown:
        print(_format_daily_summary_markdown(payload))
    else:
        _print_json(payload)
    return 0


def cmd_artifact_drilldown(args: argparse.Namespace) -> int:
    payload = artifact_drilldown(outputs_root=args.outputs_root, family=args.family)
    if args.markdown:
        print(_format_artifact_drilldown_markdown(payload))
    else:
        _print_json(payload)
    return 0


def cmd_morning_brief(args: argparse.Namespace) -> int:
    payload = morning_cio_brief(outputs_root=args.outputs_root, trade_date=args.trade_date)
    if args.markdown:
        print(_format_morning_brief_markdown(payload))
    else:
        _print_json(payload)
    return 0


def cmd_promotion_readiness(args: argparse.Namespace) -> int:
    payload = promotion_readiness(outputs_root=args.outputs_root, lookback_days=args.lookback_days)
    if args.markdown:
        print(_format_promotion_readiness_markdown(payload))
    else:
        _print_json(payload)
    return 0


def cmd_anomaly_report(args: argparse.Namespace) -> int:
    payload = anomaly_report(outputs_root=args.outputs_root, trade_date=args.trade_date, lookback_days=args.lookback_days)
    if args.markdown:
        print(_format_anomaly_report_markdown(payload))
    else:
        _print_json(payload)
    return 0


def cmd_build(args: argparse.Namespace) -> int:
    manifest = _load_manifest(Path(args.manifest))
    registry = _open_registry(Path(args.db))
    try:
        family_results = []
        for entry in manifest:
            result = ingest_artifact_family(
                family=entry["family"],
                artifact_paths=[Path(path) for path in entry["paths"]],
                registry=registry,
            )
            family_results.append(
                {
                    "family": entry["family"],
                    "path_count": len(entry["paths"]),
                    "envelope_count": len(result.envelopes),
                    "findings": [dataclasses.asdict(finding) for finding in result.findings],
                }
            )
        query = RegistryQuery(registry)
        _print_json(
            {
                "status": "BUILT",
                "db_path": str(Path(args.db)),
                "families": family_results,
                "summary": query.registry_summary(),
            }
        )
    finally:
        registry.close()
    return 0


def cmd_summary(args: argparse.Namespace) -> int:
    registry = _open_registry(Path(args.db))
    try:
        query = RegistryQuery(registry)
        _print_json(
            {
                "db_path": str(Path(args.db)),
                "summary": query.registry_summary(),
                "statistics": query.registry_statistics(),
            }
        )
    finally:
        registry.close()
    return 0


def _filtered_objects(args: argparse.Namespace, query: RegistryQuery) -> list[Any]:
    objects = query.list_objects()
    if args.artifact_type:
        objects = [obj for obj in objects if obj.object_type == args.artifact_type]
    if getattr(args, "data_artifact_type", None):
        objects = [
            obj for obj in objects if obj.data.get("artifact_type") == args.data_artifact_type
        ]
    if args.surface:
        objects = [obj for obj in objects if obj.surface.get("nav_surface_type") == args.surface]
    if args.confidence:
        objects = [obj for obj in objects if obj.confidence.get("level") == args.confidence]
    if args.governance:
        objects = [obj for obj in objects if obj.governance.get("state") == args.governance]
    return objects


def cmd_list_or_query(args: argparse.Namespace) -> int:
    registry = _open_registry(Path(args.db))
    try:
        query = RegistryQuery(registry)
        objects = _filtered_objects(args, query)
        if args.limit is not None:
            objects = objects[: max(0, int(args.limit))]
        _print_json(
            {
                "db_path": str(Path(args.db)),
                "object_count": len(objects),
                "objects": [obj.to_dict() for obj in objects],
            }
        )
    finally:
        registry.close()
    return 0


def cmd_lineage(args: argparse.Namespace) -> int:
    registry = _open_registry(Path(args.db))
    try:
        query = RegistryQuery(registry)
        _print_json(query.get_lineage(args.object_id))
    finally:
        registry.close()
    return 0


def cmd_ingest_runs(args: argparse.Namespace) -> int:
    runs_root = Path(args.runs_root)
    run_dirs = [path for path in runs_root.iterdir() if path.is_dir()] if runs_root.exists() else []
    selected_runs = _limit_paths(run_dirs, args.limit)
    integrity_paths = [
        path / "audit" / "execution_integrity.json"
        for path in selected_runs
        if (path / "audit" / "execution_integrity.json").exists()
    ]
    execution_result = _ingest_family(
        db_path=Path(args.db),
        family="execution_run",
        paths=selected_runs,
    )
    integrity_result = _ingest_family(
        db_path=Path(args.db),
        family="execution_integrity",
        paths=integrity_paths,
    )
    _print_json(
        {
            "status": "INGESTED_RUNS",
            "db_path": str(Path(args.db)),
            "runs_root": str(runs_root),
            "selected_run_count": len(selected_runs),
            "execution_runs": execution_result,
            "execution_integrity": integrity_result,
        }
    )
    return 0


def cmd_ingest_research_packets(args: argparse.Namespace) -> int:
    packets_root = Path(args.packets_root)
    packet_dirs = [path for path in packets_root.iterdir() if path.is_dir()] if packets_root.exists() else []
    selected_packets = _limit_paths(packet_dirs, args.limit)
    result = _ingest_family(
        db_path=Path(args.db),
        family="research_packet",
        paths=selected_packets,
    )
    _print_json(
        {
            "status": "INGESTED_RESEARCH_PACKETS",
            "db_path": str(Path(args.db)),
            "packets_root": str(packets_root),
            "selected_packet_count": len(selected_packets),
            "research_packets": result,
        }
    )
    return 0


def cmd_ingest_governance(args: argparse.Namespace) -> int:
    docs_root = Path(args.docs_root)
    docs = sorted(docs_root.glob("*.md")) if docs_root.exists() else []
    result = _ingest_family(
        db_path=Path(args.db),
        family="governance_doc",
        paths=docs,
    )
    _print_json(
        {
            "status": "INGESTED_GOVERNANCE",
            "db_path": str(Path(args.db)),
            "docs_root": str(docs_root),
            "selected_doc_count": len(docs),
            "governance": result,
        }
    )
    return 0


def cmd_build_caerus_registry(args: argparse.Namespace) -> int:
    db_path = Path(args.db)
    runs_root = Path(args.runs_root)
    packets_root = Path(args.packets_root)
    docs_root = Path(args.docs_root)

    run_dirs = [path for path in runs_root.iterdir() if path.is_dir()] if runs_root.exists() else []
    selected_runs = _limit_paths(run_dirs, args.limit)
    integrity_paths = [
        path / "audit" / "execution_integrity.json"
        for path in selected_runs
        if (path / "audit" / "execution_integrity.json").exists()
    ]
    packet_dirs = [path for path in packets_root.iterdir() if path.is_dir()] if packets_root.exists() else []
    selected_packets = _limit_paths(packet_dirs, args.limit)
    docs = sorted(docs_root.glob("*.md")) if docs_root.exists() else []

    registry = _open_registry(db_path)
    try:
        family_results = []
        for family, paths in [
            ("execution_run", selected_runs),
            ("execution_integrity", integrity_paths),
            ("research_packet", selected_packets),
            ("governance_doc", docs),
        ]:
            result = ingest_artifact_family(
                family=family,
                artifact_paths=paths,
                registry=registry,
            )
            family_results.append(
                {
                    "family": family,
                    "path_count": len(paths),
                    "envelope_count": len(result.envelopes),
                    "findings": [dataclasses.asdict(finding) for finding in result.findings],
                }
            )
        query = RegistryQuery(registry)
        _print_json(
            {
                "status": "BUILT_CAERUS_REGISTRY",
                "db_path": str(db_path),
                "runs_root": str(runs_root),
                "packets_root": str(packets_root),
                "docs_root": str(docs_root),
                "limit": args.limit,
                "families": family_results,
                "summary": query.registry_summary(),
            }
        )
    finally:
        registry.close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only Caerus research registry operator CLI."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build", help="Build a disposable registry from a bounded manifest.")
    build.add_argument("--db", required=True, help="SQLite registry path to create or update.")
    build.add_argument("--manifest", required=True, help="JSON manifest of artifact families and paths.")
    build.set_defaults(func=cmd_build)

    summary = subparsers.add_parser("summary", help="Print registry summary and statistics.")
    summary.add_argument("--db", required=True, help="SQLite registry path to open.")
    summary.set_defaults(func=cmd_summary)

    list_cmd = subparsers.add_parser("list", help="List registered objects.")
    list_cmd.add_argument("--db", required=True, help="SQLite registry path to open.")
    list_cmd.add_argument("--limit", type=int, default=None)
    list_cmd.add_argument("--artifact-type")
    list_cmd.add_argument("--data-artifact-type")
    list_cmd.add_argument("--surface")
    list_cmd.add_argument("--confidence")
    list_cmd.add_argument("--governance", help="Governance state/category filter.")
    list_cmd.set_defaults(func=cmd_list_or_query)

    query_cmd = subparsers.add_parser("query", help="Query registered objects with filters.")
    query_cmd.add_argument("--db", required=True, help="SQLite registry path to open.")
    query_cmd.add_argument("--limit", type=int, default=None)
    query_cmd.add_argument("--artifact-type")
    query_cmd.add_argument("--data-artifact-type")
    query_cmd.add_argument("--surface")
    query_cmd.add_argument("--confidence")
    query_cmd.add_argument("--governance", help="Governance state/category filter.")
    query_cmd.set_defaults(func=cmd_list_or_query)

    lineage = subparsers.add_parser("lineage", help="Print lineage for one object.")
    lineage.add_argument("--db", required=True, help="SQLite registry path to open.")
    lineage.add_argument("--object-id", required=True)
    lineage.set_defaults(func=cmd_lineage)

    ingest_runs = subparsers.add_parser("ingest-runs", help="Read execution run artifacts into the registry.")
    ingest_runs.add_argument("--db", required=True, help="SQLite registry path to create or update.")
    ingest_runs.add_argument("--runs-root", default="outputs/runs")
    ingest_runs.add_argument("--limit", type=int, default=None)
    ingest_runs.set_defaults(func=cmd_ingest_runs)

    ingest_packets = subparsers.add_parser(
        "ingest-research-packets",
        help="Read research packet artifacts into the registry.",
    )
    ingest_packets.add_argument("--db", required=True, help="SQLite registry path to create or update.")
    ingest_packets.add_argument("--packets-root", default="outputs/research_packets")
    ingest_packets.add_argument("--limit", type=int, default=None)
    ingest_packets.set_defaults(func=cmd_ingest_research_packets)

    ingest_governance = subparsers.add_parser(
        "ingest-governance",
        help="Read governance markdown documents into the registry.",
    )
    ingest_governance.add_argument("--db", required=True, help="SQLite registry path to create or update.")
    ingest_governance.add_argument("--docs-root", default="docs/governance")
    ingest_governance.set_defaults(func=cmd_ingest_governance)

    latest_runs = subparsers.add_parser("latest-runs", help="Show latest registered execution runs.")
    latest_runs.add_argument("--db", required=True, help="SQLite registry path to open.")
    latest_runs.add_argument("--limit", type=int, default=10)
    latest_runs.set_defaults(func=cmd_latest_runs)

    run_health = subparsers.add_parser("run-health", help="Summarize one registered execution run.")
    run_health.add_argument("--db", required=True, help="SQLite registry path to open.")
    run_health.add_argument("--run-id", required=True)
    run_health.set_defaults(func=cmd_run_health)

    integrity_findings = subparsers.add_parser(
        "integrity-findings",
        help="List WARN/FAIL execution integrity objects.",
    )
    integrity_findings.add_argument("--db", required=True, help="SQLite registry path to open.")
    integrity_findings.add_argument("--limit", type=int, default=None)
    integrity_findings.set_defaults(func=cmd_integrity_findings)

    governance_open = subparsers.add_parser(
        "governance-open",
        help="List active or high-blast-radius governance items.",
    )
    governance_open.add_argument("--db", required=True, help="SQLite registry path to open.")
    governance_open.add_argument(
        "--show-duplicates",
        action="store_true",
        help="Show raw unresolved governance entries for debugging.",
    )
    governance_open.add_argument(
        "--include-deferred",
        action="store_true",
        help="Include REVIEWED_DEFERRED items in governance-open output.",
    )
    governance_open.set_defaults(func=cmd_governance_open)

    packet_status = subparsers.add_parser(
        "research-packet-status",
        help="Show latest registered research packet readiness.",
    )
    packet_status.add_argument("--db", required=True, help="SQLite registry path to open.")
    packet_status.add_argument("--limit", type=int, default=10)
    packet_status.set_defaults(func=cmd_research_packet_status)

    daily_brief = subparsers.add_parser(
        "daily-operator-brief",
        help="Print a compact read-only operator brief from the registry.",
    )
    daily_brief.add_argument("--db", required=True, help="SQLite registry path to open.")
    daily_brief.set_defaults(func=cmd_daily_operator_brief)

    artifact_status_cmd = subparsers.add_parser(
        "artifact-status",
        help="Inspect latest Caerus artifact families without writing or rebuilding.",
    )
    artifact_status_cmd.add_argument("--outputs-root", default="outputs")
    artifact_status_cmd.add_argument("--limit", type=int, default=10)
    artifact_status_cmd.add_argument("--json", action="store_true", help="Emit JSON output (default).")
    artifact_status_cmd.add_argument("--markdown", action="store_true", help="Emit Markdown output.")
    artifact_status_cmd.set_defaults(func=cmd_artifact_status)

    daily_summary = subparsers.add_parser(
        "daily-summary",
        help="Summarize today's read-only operator state from latest artifacts.",
    )
    daily_summary.add_argument("--outputs-root", default="outputs")
    daily_summary.add_argument("--trade-date", default=None)
    daily_summary.add_argument("--json", action="store_true", help="Emit JSON output (default).")
    daily_summary.add_argument("--markdown", action="store_true", help="Emit Markdown output.")
    daily_summary.set_defaults(func=cmd_daily_summary)

    artifact_drilldown_cmd = subparsers.add_parser(
        "artifact-drilldown",
        help="Inspect latest artifact paths and required files without raw payload dumps.",
    )
    artifact_drilldown_cmd.add_argument("--outputs-root", default="outputs")
    artifact_drilldown_cmd.add_argument(
        "--family",
        default="all",
        choices=["all", "precompute", "execution", "broker_confirmation", "shadow", "research_packet"],
    )
    artifact_drilldown_cmd.add_argument("--json", action="store_true", help="Emit JSON output (default).")
    artifact_drilldown_cmd.add_argument("--markdown", action="store_true", help="Emit Markdown output.")
    artifact_drilldown_cmd.set_defaults(func=cmd_artifact_drilldown)

    morning_brief = subparsers.add_parser(
        "morning-brief",
        help="Print a compact artifact-backed CIO/operator brief.",
    )
    morning_brief.add_argument("--outputs-root", default="outputs")
    morning_brief.add_argument("--trade-date", default=None)
    morning_brief.add_argument("--json", action="store_true", help="Emit JSON output (default).")
    morning_brief.add_argument("--markdown", action="store_true", help="Emit Markdown output.")
    morning_brief.set_defaults(func=cmd_morning_brief)

    readiness = subparsers.add_parser(
        "promotion-readiness",
        help="Assess challenger readiness from shadow artifacts only.",
    )
    readiness.add_argument("--outputs-root", default="outputs")
    readiness.add_argument("--lookback-days", type=int, default=5)
    readiness.add_argument("--json", action="store_true", help="Emit JSON output (default).")
    readiness.add_argument("--markdown", action="store_true", help="Emit Markdown output.")
    readiness.set_defaults(func=cmd_promotion_readiness)

    anomaly = subparsers.add_parser(
        "anomaly-report",
        help="Report operational and research anomalies from persisted artifacts.",
    )
    anomaly.add_argument("--outputs-root", default="outputs")
    anomaly.add_argument("--trade-date", default=None)
    anomaly.add_argument("--lookback-days", type=int, default=5)
    anomaly.add_argument("--json", action="store_true", help="Emit JSON output (default).")
    anomaly.add_argument("--markdown", action="store_true", help="Emit Markdown output.")
    anomaly.set_defaults(func=cmd_anomaly_report)

    build_caerus = subparsers.add_parser(
        "build-caerus-registry",
        help="Build a read-only Caerus operator registry from recent artifacts.",
    )
    build_caerus.add_argument("--db", required=True, help="SQLite registry path to create or update.")
    build_caerus.add_argument("--runs-root", default="outputs/runs")
    build_caerus.add_argument("--packets-root", default="outputs/research_packets")
    build_caerus.add_argument("--docs-root", default="docs/governance")
    build_caerus.add_argument("--limit", type=int, default=10)
    build_caerus.set_defaults(func=cmd_build_caerus_registry)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
