"""Deterministic, non-trading CIO daily brief construction.

The builder reads already-persisted assurance, operating, and research
evidence.  It has no broker, execution, allocation, email, scheduler, or
network dependency.  Missing evidence is reported explicitly and never
converted into a zero, a pass, or an inferred lifecycle decision.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import shutil
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "caerus.cio_daily_brief.v1"
MANIFEST_SCHEMA_VERSION = "caerus.cio_daily_brief_manifest.v1"
STATUS_ORDER = {"GREEN": 0, "YELLOW": 1, "RED": 2}
RED_CONTROLS = {
    "compute_recomputed",
    "decision_from_certified_compute",
    "precompute_immutable_hashed",
    "execution_consumed_exact_artifact",
    "broker_reconciliation",
}
CONTROL_NAMES = {
    "data_freshness_pit_validity",
    "compute_recomputed",
    "decision_from_certified_compute",
    "precompute_immutable_hashed",
    "execution_consumed_exact_artifact",
    "broker_reconciliation",
}
ALPHA_UNAVAILABLE_REASON = "canonical_authenticated_research_authority_not_implemented"


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def content_hash(payload: Mapping[str, Any]) -> str:
    body = dict(payload)
    body.pop("content_hash", None)
    return hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def source_artifact(
    *, path: str, payload: Mapping[str, Any] | None, raw_bytes: bytes | None
) -> dict[str, Any]:
    return {
        "path": path,
        "status": "AVAILABLE"
        if payload is not None and raw_bytes is not None
        else "MISSING_OR_INVALID",
        "sha256": sha256_bytes(raw_bytes) if raw_bytes is not None else None,
    }


def _status_max(*values: str) -> str:
    return max(values, key=lambda item: STATUS_ORDER[item])


def _exception(
    *, severity: str, code: str, message: str, source: str | None = None
) -> dict[str, Any]:
    return {
        "severity": severity,
        "code": code,
        "message": message,
        "source": source,
    }


def _exception_key(item: Mapping[str, Any]) -> tuple[int, str, str]:
    return (
        -STATUS_ORDER.get(str(item.get("severity")), 0),
        str(item.get("code") or ""),
        str(item.get("message") or ""),
    )


def _certification_section(
    certification: Mapping[str, Any] | None, source_path: str, report_date: str
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if (
        certification is None
        or certification.get("schema_version")
        != "caerus.trading_integrity_certification.v1"
        or certification.get("through_date") != report_date
    ):
        item = _exception(
            severity="RED",
            code="TRADING_INTEGRITY_CERTIFICATION_UNAVAILABLE",
            message="Current trading-integrity certification is missing or invalid; assurance is unproved.",
            source=source_path,
        )
        return {
            "status": "RED",
            "certified_sessions": None,
            "expected_sessions": 20,
            "rate": None,
            "latest_session": None,
            "exceptions": [item],
        }, [item]

    try:
        certified = int(certification["certified_sessions"])
        expected = int(certification["expected_sessions"])
        sessions = list(certification["sessions"])
    except (KeyError, TypeError, ValueError):
        return _certification_section(None, source_path, report_date)
    if expected != 20 or certified < 0 or certified > expected or not sessions:
        return _certification_section(None, source_path, report_date)

    latest = sessions[-1] if isinstance(sessions[-1], Mapping) else {}
    controls = (
        latest.get("controls") if isinstance(latest.get("controls"), Mapping) else {}
    )
    if set(controls) != CONTROL_NAMES:
        return _certification_section(None, source_path, report_date)
    failed = sorted(
        str(name)
        for name, row in controls.items()
        if not isinstance(row, Mapping) or row.get("pass") is not True
    )
    exceptions: list[dict[str, Any]] = []
    for name in failed:
        row = controls.get(name) if isinstance(controls.get(name), Mapping) else {}
        reasons = sorted(str(value) for value in (row.get("reasons") or []))
        severity = "RED" if name in RED_CONTROLS else "YELLOW"
        exceptions.append(
            _exception(
                severity=severity,
                code=f"CONTROL_FAILED:{name}",
                message=f"{latest.get('trade_date', 'latest session')}: {', '.join(reasons) or 'control evidence missing'}",
                source=source_path,
            )
        )
    if failed and any(name in RED_CONTROLS for name in failed):
        status = "RED"
    elif failed or certified != expected:
        status = "YELLOW"
    else:
        status = "GREEN"
    if certified != expected and not failed:
        exceptions.append(
            _exception(
                severity="YELLOW",
                code="HISTORICAL_CERTIFICATION_GAP",
                message=f"Only {certified}/{expected} sessions are fully certified.",
                source=source_path,
            )
        )
    return {
        "status": status,
        "certified_sessions": certified,
        "expected_sessions": expected,
        "rate": certified / expected,
        "latest_session": {
            "trade_date": latest.get("trade_date"),
            "certified": latest.get("certified") is True,
            "controls_passed": latest.get("controls_passed"),
            "controls_expected": latest.get("controls_expected"),
        },
        "exceptions": sorted(exceptions, key=_exception_key)[:3],
    }, exceptions


def _lane_semantics(lane: Mapping[str, Any]) -> dict[str, Any]:
    authority = (
        lane.get("authority") if isinstance(lane.get("authority"), Mapping) else {}
    )
    runtime = (
        lane.get("runtime_gates")
        if isinstance(lane.get("runtime_gates"), Mapping)
        else {}
    )
    schedule = lane.get("schedule") if isinstance(lane.get("schedule"), Mapping) else {}
    broker = (
        lane.get("broker_truth")
        if isinstance(lane.get("broker_truth"), Mapping)
        else {}
    )
    execution = (
        lane.get("latest_execution")
        if isinstance(lane.get("latest_execution"), Mapping)
        else {}
    )
    return {
        "lane_id": str(lane.get("lane_id") or "unknown"),
        "lane_kind": str(lane.get("lane_kind") or "UNKNOWN"),
        "strategy_ids": sorted(
            str(value) for value in (lane.get("strategy_ids") or [])
        ),
        "declared_state": str(lane.get("declared_state") or "UNKNOWN"),
        "operating_status": str(lane.get("operating_status") or "UNKNOWN"),
        "authority_status": str(authority.get("status") or "UNKNOWN"),
        "runtime_gate_status": str(runtime.get("status") or "UNKNOWN"),
        "schedule_status": str(schedule.get("status") or "UNKNOWN"),
        "broker_status": str(broker.get("status") or "UNKNOWN"),
        "execution_status": str(execution.get("status") or "UNKNOWN"),
        "reconciliation_status": execution.get("reconciliation_status"),
        "latest_blocked_session": execution.get("latest_blocked_session"),
    }


def _lane_label(item: Mapping[str, Any]) -> str:
    strategies = (
        ", ".join(
            str(value).replace("caerus_", "").replace("_", " ").title()
            for value in item.get("strategy_ids") or []
        )
        or "none"
    )
    return (
        f"{item.get('lane_id')}: {item.get('operating_status')} "
        f"({strategies}; broker {item.get('broker_status')})"
    )


def _previous_lanes(
    previous_brief: Mapping[str, Any] | None,
) -> dict[str, Mapping[str, Any]]:
    if not isinstance(previous_brief, Mapping):
        return {}
    capital = previous_brief.get("capital")
    if not isinstance(capital, Mapping):
        return {}
    rows = capital.get("lanes")
    if not isinstance(rows, list):
        return {}
    return {
        str(item.get("lane_id")): item
        for item in rows
        if isinstance(item, Mapping) and item.get("lane_id")
    }


def _capital_section(
    operating_truth: Mapping[str, Any] | None,
    previous_brief: Mapping[str, Any] | None,
    source_path: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    valid_truth = bool(
        operating_truth is not None
        and operating_truth.get("schema_version") == "caerus.operating_truth.v1"
        and operating_truth.get("content_hash") == content_hash(operating_truth)
        and isinstance(operating_truth.get("context_integrity"), Mapping)
        and operating_truth["context_integrity"].get("status") == "PASS"
        and isinstance(operating_truth.get("lanes"), list)
    )
    if not valid_truth:
        item = _exception(
            severity="RED",
            code="OPERATING_TRUTH_UNAVAILABLE",
            message="Canonical Live/Paper/Shadow operating truth is missing or invalid.",
            source=source_path,
        )
        return {
            "status": "UNAVAILABLE",
            "baseline": previous_brief is None,
            "lanes": [],
            "meaningful_changes": {
                "LIVE": ["unavailable"],
                "PAPER": ["unavailable"],
                "SHADOW": ["unavailable"],
            },
        }, [item]

    prior = _previous_lanes(previous_brief)
    baseline = not prior
    lanes = sorted(
        (
            _lane_semantics(row)
            for row in operating_truth["lanes"]
            if isinstance(row, Mapping)
        ),
        key=lambda item: (item["lane_kind"], item["lane_id"]),
    )
    changes: dict[str, list[str]] = {"LIVE": [], "PAPER": [], "SHADOW": []}
    exceptions: list[dict[str, Any]] = []
    for lane in lanes:
        lane["semantic_fingerprint"] = hashlib.sha256(
            canonical_json(lane).encode("utf-8")
        ).hexdigest()
        old = prior.get(lane["lane_id"])
        if baseline:
            changes.setdefault(lane["lane_kind"], []).append(
                f"Baseline — {_lane_label(lane)}"
            )
        elif (
            old is None
            or old.get("semantic_fingerprint") != lane["semantic_fingerprint"]
        ):
            changes.setdefault(lane["lane_kind"], []).append(
                f"Changed — {_lane_label(lane)}"
            )
        if lane["declared_state"] == "ACTIVE" and lane["authority_status"] != "PROVED":
            exceptions.append(
                _exception(
                    severity="RED",
                    code=f"LANE_AUTHORITY_UNPROVED:{lane['lane_id']}",
                    message=_lane_label(lane),
                    source=source_path,
                )
            )
        if lane["declared_state"] == "ACTIVE" and lane["broker_status"] not in {
            "PASS",
            "NONCAPITAL",
        }:
            exceptions.append(
                _exception(
                    severity="RED",
                    code=f"BROKER_TRUTH_NOT_GREEN:{lane['lane_id']}",
                    message=_lane_label(lane),
                    source=source_path,
                )
            )
        if lane["declared_state"] == "ACTIVE" and lane["operating_status"] in {
            "ACTIVE_WITH_EXCEPTION",
            "DEGRADED",
            "UNKNOWN",
        }:
            exceptions.append(
                _exception(
                    severity="YELLOW",
                    code=f"LANE_NOT_FULLY_ACTIVE:{lane['lane_id']}",
                    message=_lane_label(lane),
                    source=source_path,
                )
            )
    removed = sorted(set(prior) - {lane["lane_id"] for lane in lanes})
    for lane_id in removed:
        kind = str(prior[lane_id].get("lane_kind") or "UNKNOWN")
        changes.setdefault(kind, []).append(f"Removed from operating truth — {lane_id}")
    return {
        "status": "AVAILABLE",
        "baseline": baseline,
        "lanes": lanes,
        "meaningful_changes": {
            key: value
            for key, value in changes.items()
            if key in {"LIVE", "PAPER", "SHADOW"}
        },
    }, exceptions


def _alpha_section(
    report_date: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    unavailable = {
        "status": "UNAVAILABLE",
        "reason": ALPHA_UNAVAILABLE_REASON,
    }
    exception = _exception(
        severity="YELLOW",
        code="ALPHA_AUTHORITY_UNAVAILABLE",
        message=(
            "Alpha evidence is unavailable until the canonical authenticated GCP "
            "ledger and family authority are implemented."
        ),
        source=None,
    )
    return {
        "status": "UNAVAILABLE",
        "reason": ALPHA_UNAVAILABLE_REASON,
        "improving": dict(unavailable),
        "deteriorating": dict(unavailable),
        "new_shadow": dict(unavailable),
        "killed": dict(unavailable),
        "research_throughput": {
            **unavailable,
            "month": report_date[:7],
            "count": None,
        },
    }, [exception]


def _attention_items(
    exceptions: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    ordered_exceptions = sorted(exceptions, key=_exception_key)
    red = [item for item in ordered_exceptions if item.get("severity") == "RED"]
    non_red = [item for item in ordered_exceptions if item.get("severity") != "RED"]
    for item in red[:2]:
        result.append(
            {
                "kind": "EXCEPTION",
                "message": str(item.get("message") or item.get("code")),
                "evidence": item.get("source"),
            }
        )
    for item in red[2:] + non_red:
        result.append(
            {
                "kind": "EXCEPTION",
                "message": str(item.get("message") or item.get("code")),
                "evidence": item.get("source"),
            }
        )
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in result:
        key = canonical_json(row)
        if key not in seen:
            seen.add(key)
            deduped.append(row)
    if not deduped:
        deduped.append(
            {
                "kind": "NO_ACTION",
                "message": "No CIO action required from current certified evidence.",
                "evidence": None,
            }
        )
    return deduped[:3]


def build_cio_daily_brief(
    *,
    report_date: str,
    certification: Mapping[str, Any] | None,
    operating_truth: Mapping[str, Any] | None,
    previous_brief: Mapping[str, Any] | None = None,
    sources: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Build the pure semantic brief payload from already-loaded evidence."""

    try:
        parsed_report_date = dt.date.fromisoformat(report_date)
    except ValueError as exc:
        raise ValueError("report_date must be YYYY-MM-DD") from exc
    if parsed_report_date.isoformat() != report_date:
        raise ValueError("report_date must be YYYY-MM-DD")
    source_by_kind = {str(item.get("kind")): str(item.get("path")) for item in sources}
    operations, operation_exceptions = _certification_section(
        certification,
        source_by_kind.get("trading_integrity", "trading integrity certification"),
        report_date,
    )
    capital, capital_exceptions = _capital_section(
        operating_truth,
        previous_brief,
        source_by_kind.get("operating_truth", "operating truth"),
    )
    alpha, alpha_exceptions = _alpha_section(report_date)
    all_exceptions = operation_exceptions + capital_exceptions + alpha_exceptions
    capital_status = (
        "RED"
        if any(item["severity"] == "RED" for item in capital_exceptions)
        else ("YELLOW" if capital_exceptions else "GREEN")
    )
    research_status = "YELLOW" if alpha_exceptions else "GREEN"
    operations["status"] = _status_max(
        operations["status"], capital_status, research_status
    )
    operations["exceptions"] = sorted(all_exceptions, key=_exception_key)[:3]
    body: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "report_date": report_date,
        "operations": operations,
        "capital": capital,
        "alpha": alpha,
        "cio_attention": _attention_items(all_exceptions),
        "sources": sorted(
            (dict(item) for item in sources),
            key=lambda item: (str(item.get("kind")), str(item.get("path"))),
        ),
    }
    body["content_hash"] = content_hash(body)
    return body


def _percent(rate: Any) -> str:
    if rate is None:
        return "UNAVAILABLE"
    return f"{float(rate) * 100:.0f}%"


def render_cio_daily_brief(payload: Mapping[str, Any]) -> str:
    operations = payload["operations"]
    capital = payload["capital"]
    alpha = payload["alpha"]
    certified = operations.get("certified_sessions")
    expected = operations.get("expected_sessions")
    ratio = "UNAVAILABLE" if certified is None else f"{certified}/{expected}"
    lines = [
        f"# CAERUS CIO BRIEF — {payload['report_date']}",
        "",
        f"## OPERATIONS: {operations['status']}",
        "",
        f"Trading Integrity Rate: {ratio} ({_percent(operations.get('rate'))})",
        "Exceptions:",
    ]
    if operations.get("exceptions"):
        for item in operations["exceptions"]:
            lines.append(
                f"- {item['code']}: {item['message']} [{item.get('source') or 'source unavailable'}]"
            )
    else:
        lines.append("- none")
    lines.extend(["", "## CAPITAL", ""])
    for kind in ("LIVE", "PAPER", "SHADOW"):
        changes = capital.get("meaningful_changes", {}).get(kind) or []
        lines.append(
            f"{kind.title()}: {'; '.join(changes) if changes else 'no meaningful change'}"
        )
    throughput = alpha["research_throughput"]
    throughput_text = (
        str(throughput["count"])
        if throughput["status"] == "AVAILABLE"
        else f"UNAVAILABLE ({throughput['reason']})"
    )
    lines.extend(
        [
            "",
            "## ALPHA",
            "",
            "Improving: UNAVAILABLE",
            "Deteriorating: UNAVAILABLE",
            "New Shadow: UNAVAILABLE",
            "Killed: UNAVAILABLE",
            f"Research throughput ({throughput['month']}): {throughput_text}",
            "",
            "## CIO ATTENTION",
            "",
        ]
    )
    for index, item in enumerate(payload["cio_attention"], 1):
        suffix = f" [{item['evidence']}]" if item.get("evidence") else ""
        lines.append(f"{index}. {item['message']}{suffix}")
    return "\n".join(lines) + "\n"


def _json_bytes(payload: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    ).encode("utf-8")


def persist_brief_bundle(
    *, output_root: Path, payload: Mapping[str, Any]
) -> dict[str, Any]:
    """Persist a dated immutable JSON/Markdown/manifest bundle.

    Existing identical bytes are accepted idempotently.  Existing divergent
    bytes fail closed and are never overwritten.
    """

    root = Path(output_root)
    target = root / str(payload["report_date"])
    json_bytes = _json_bytes(payload)
    md_bytes = render_cio_daily_brief(payload).encode("utf-8")
    manifest: dict[str, Any] = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "report_date": payload["report_date"],
        "brief_content_hash": payload["content_hash"],
        "artifacts": [
            {
                "name": "brief.json",
                "sha256": sha256_bytes(json_bytes),
                "size_bytes": len(json_bytes),
            },
            {
                "name": "brief.md",
                "sha256": sha256_bytes(md_bytes),
                "size_bytes": len(md_bytes),
            },
        ],
        "sources": payload.get("sources") or [],
    }
    manifest["content_hash"] = content_hash(manifest)
    manifest_bytes = _json_bytes(manifest)
    expected = {
        "brief.json": json_bytes,
        "brief.md": md_bytes,
        "manifest.json": manifest_bytes,
    }
    if target.exists():
        names = (
            {path.name for path in target.iterdir()}
            if target.is_dir() and not target.is_symlink()
            else set()
        )
        if names != set(expected) or any(
            not (target / name).is_file() or (target / name).read_bytes() != value
            for name, value in expected.items()
        ):
            raise FileExistsError(f"immutable CIO brief bundle differs: {target}")
        return manifest
    root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{payload['report_date']}.", dir=root))
    try:
        for name, value in expected.items():
            (staging / name).write_bytes(value)
        os.replace(staging, target)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return manifest
