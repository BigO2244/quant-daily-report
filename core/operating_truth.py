"""Read-only, lane-specific Caerus operating-truth compiler.

Authority, runtime gates, schedules, execution results, broker truth, and
narrative documentation remain separate evidence dimensions.  A PAPER-scoped
flag can never negate an independently governed LIVE lane.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any, Mapping


REGISTRY_SCHEMA = "caerus.operating_lane_registry.v1"
OPERATING_TRUTH_SCHEMA = "caerus.operating_truth.v1"
SAFE_GATE = re.compile(r"^[A-Z][A-Z0-9_]*$")


class OperatingTruthError(RuntimeError):
    pass


def canonical_json(value: Any) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    )


def content_hash(payload: Mapping[str, Any]) -> str:
    body = dict(payload)
    body.pop("content_hash", None)
    return hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def load_lane_registry(path: Path) -> dict[str, Any]:
    payload = _read_json(path)
    if payload is None or payload.get("schema_version") != REGISTRY_SCHEMA:
        raise OperatingTruthError("operating lane registry schema is invalid")
    if payload.get("content_hash") != content_hash(payload):
        raise OperatingTruthError("operating lane registry content hash is invalid")
    lanes = payload.get("lanes")
    if not isinstance(lanes, list) or not lanes:
        raise OperatingTruthError("operating lane registry has no lanes")
    ids = [row.get("lane_id") for row in lanes if isinstance(row, Mapping)]
    if len(ids) != len(lanes) or len(ids) != len(set(ids)):
        raise OperatingTruthError("operating lane ids must be unique")
    return payload


def parse_env_gates(path: Path, allowed: set[str]) -> dict[str, str]:
    """Read only allowlisted non-secret gate values from an env file."""

    result: dict[str, str] = {}
    if not path.is_file() or path.is_symlink():
        return result
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key in allowed and SAFE_GATE.fullmatch(key):
            result[key] = value.strip().strip('"').strip("'")
    return result


def _strategy_rows(repo_root: Path) -> dict[str, dict[str, Any]]:
    payload = _read_json(repo_root / "config/research/strategy_registry.json") or {}
    return {
        str(row.get("strategy_id")): row
        for row in payload.get("strategies") or []
        if isinstance(row, dict) and row.get("strategy_id")
    }


def _authority_status(
    repo_root: Path, lane: Mapping[str, Any], strategies: Mapping[str, Mapping[str, Any]]
) -> tuple[str, list[str]]:
    authority = lane.get("authority") or {}
    kind = authority.get("kind")
    reasons: list[str] = []
    if kind == "strategy_registry_shadow":
        for strategy_id in lane.get("strategy_ids") or []:
            row = strategies.get(str(strategy_id)) or {}
            if (row.get("shadow_tracking") or {}).get("enabled") is not True:
                reasons.append(f"shadow_authority_missing:{strategy_id}")
    elif kind == "strategy_registry_paper":
        strategy_id = str((lane.get("strategy_ids") or [""])[0])
        paper = (strategies.get(strategy_id) or {}).get("paper_execution") or {}
        if (
            paper.get("enabled") is not True
            or paper.get("approval_scope") != authority.get("approval_scope")
            or paper.get("owner_approved_at") != authority.get("owner_approved_at")
        ):
            reasons.append("paper_authority_mismatch")
    elif kind == "owner_decision":
        path = repo_root / str(authority.get("path") or "")
        decision = _read_json(path) or {}
        if (
            decision.get("decision") != "APPROVE"
            or decision.get("decision_id") != authority.get("decision_id")
            or decision.get("content_hash") != authority.get("content_hash")
        ):
            reasons.append("owner_decision_mismatch")
        elif decision.get("content_hash") != content_hash(decision):
            reasons.append("owner_decision_hash_invalid")
    elif kind != "legacy_runtime_gates":
        reasons.append("authority_kind_unknown")
    return ("PROVED" if not reasons else "UNPROVED", reasons)


def _latest_json(paths: list[Path]) -> tuple[Path | None, dict[str, Any] | None]:
    candidates = [path for path in paths if path.is_file()]
    if not candidates:
        return None, None
    path = max(candidates, key=lambda item: (item.parent.name, item.name))
    return path, _read_json(path)


def _result_observation(home: Path, lane: Mapping[str, Any]) -> dict[str, Any]:
    runtime = lane.get("runtime") or {}
    raw_root = runtime.get("state_root")
    if not raw_root:
        return {"status": "NOT_APPLICABLE"}
    root = home / str(raw_root)
    completed_path, completed = _latest_json(list(root.glob("*/result.json")))
    blocked_path, blocked = _latest_json(list(root.glob("*/blocked_attempts/*.json")))
    result: dict[str, Any] = {
        "status": "UNOBSERVED",
        "latest_completed_session": None,
        "latest_blocked_session": None,
    }
    if completed_path and completed:
        result.update(
            {
                "status": str(completed.get("status") or "UNKNOWN"),
                "latest_completed_session": str(
                    completed.get("execution_session") or completed_path.parent.name
                ),
                "completed_path": str(completed_path),
                "completed_sha256": file_hash(completed_path),
                "broker_write_performed": completed.get("broker_write_performed"),
                "reconciliation_status": (
                    (completed.get("posttrade_reconciliation") or {}).get("status")
                ),
            }
        )
    if blocked_path and blocked:
        blocked_session = str(blocked.get("execution_session") or blocked_path.parents[1].name)
        result.update(
            {
                "latest_blocked_session": blocked_session,
                "blocked_path": str(blocked_path),
                "blocked_sha256": file_hash(blocked_path),
                "blocked_reason_code": blocked.get("reason_code"),
                "blocked_broker_write_performed": blocked.get("broker_write_performed"),
                "blocked_broker_write_status": blocked.get("broker_write_status"),
            }
        )
        if not result.get("latest_completed_session") or blocked_session > str(
            result.get("latest_completed_session")
        ):
            result["status"] = "BLOCKED"
    return result


def _broker_observation(repo_root: Path, lane: Mapping[str, Any]) -> dict[str, Any]:
    raw = lane.get("broker_manifest")
    if not raw:
        surface = repo_root / str(lane.get("performance_surface") or "")
        return {
            "status": "NONCAPITAL",
            "path": str(surface.relative_to(repo_root)) if surface.is_file() else None,
            "sha256": file_hash(surface) if surface.is_file() else None,
        }
    path = repo_root / str(raw)
    payload = _read_json(path)
    if payload is None:
        return {"status": "UNOBSERVED", "path": str(raw)}
    reconciliation = payload.get("reconciliation") or {}
    passed = payload.get("pass") is True or reconciliation.get("pass") is True
    return {
        "status": "PASS" if passed else "DEGRADED",
        "path": str(raw),
        "sha256": file_hash(path),
        "as_of": (
            payload.get("last_pull_utc")
            or payload.get("last_pull")
            or payload.get("pulled_at_utc")
        ),
    }


def _paper_execution_observation(repo_root: Path) -> dict[str, Any]:
    """Read the newest dated PAPER workflow, including missing/broken pointers.

    Account reconciliation cannot stand in for execution outcome. Never fall
    back to an older successful pointer when the newest workflow is incomplete.
    This is an observation only; it does not grant retry or trading authority.
    """
    candidates = []
    for path in (repo_root / 'outputs/workflow').glob('*'):
        if not path.is_dir() or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', path.name):
            continue
        try:
            dt.date.fromisoformat(path.name)
        except ValueError:
            continue
        candidates.append(path)
    if not candidates:
        return {'status': 'UNOBSERVED'}
    session = max(candidates, key=lambda p: p.name)
    path = session / 'execution.json'
    result = {'status': 'UNOBSERVED', 'trade_date': session.name,
              'path': str(path.relative_to(repo_root))}
    if not path.is_file():
        return result
    result['sha256'] = file_hash(path)
    payload = _read_json(path)
    if (payload is None or payload.get('trade_date') != session.name
            or str(payload.get('mode', '')).lower() != 'paper'
            or payload.get('stage') != 'execution'):
        result['status'] = 'INVALID'
        return result
    result.update(status=str(payload.get('status') or 'UNKNOWN').upper(),
                  reason_code=payload.get('substatus') or payload.get('status_message'),
                  run_id=payload.get('run_id'), created_at=payload.get('created_at'))
    return result


def scan_narrative_conflicts(repo_root: Path, registry: Mapping[str, Any]) -> list[str]:
    conflicts: list[str] = []
    for surface in registry.get("narrative_surfaces") or []:
        path = repo_root / str(surface.get("path") or "")
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            conflicts.append(f"narrative_surface_missing:{surface.get('path')}")
            continue
        for claim in surface.get("required_claims") or []:
            if str(claim) not in text:
                conflicts.append(f"required_claim_missing:{surface.get('path')}:{claim}")
        for claim in surface.get("forbidden_claims") or []:
            if str(claim) in text:
                conflicts.append(f"forbidden_claim_present:{surface.get('path')}:{claim}")
    return sorted(conflicts)


def installed_crontab() -> str:
    result = subprocess.run(
        ["crontab", "-l"], capture_output=True, text=True, check=False
    )
    if result.returncode == 0:
        return result.stdout
    if result.returncode == 1 and "no crontab" in result.stderr.lower():
        return ""
    raise OperatingTruthError("installed crontab could not be read")


def compile_operating_truth(
    *, repo_root: Path, home: Path, crontab_text: str, observed_at: str
) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    registry_path = root / "config/operations/operating_lane_registry.json"
    registry = load_lane_registry(registry_path)
    strategies = _strategy_rows(root)
    lane_rows: list[dict[str, Any]] = []
    for lane in registry["lanes"]:
        authority_status, reasons = _authority_status(root, lane, strategies)
        marker = str((lane.get("schedule") or {}).get("marker") or "")
        marker_count = crontab_text.count(marker) if marker else 0
        schedule_status = "INSTALLED" if marker_count == 1 else "MISSING_OR_DUPLICATE"
        runtime = lane.get("runtime") or {}
        required = runtime.get("required_gates") or {}
        disabled = runtime.get("disabled_when_any") or {}
        allowed = set(required) | set(disabled)
        gates = parse_env_gates(home / str(runtime.get("env_path") or ""), allowed)
        if required:
            gate_status = "PASS" if all(gates.get(k) == v for k, v in required.items()) else "UNPROVED"
        elif disabled:
            gate_status = "DISABLED" if any(gates.get(k) == v for k, v in disabled.items()) else "UNPROVED"
        else:
            gate_status = "NOT_APPLICABLE"
        execution = (
            _paper_execution_observation(root)
            if lane['lane_id'] == 'orion_paper'
            else _result_observation(home, lane)
        )
        broker = _broker_observation(root, lane)
        declared = str(lane.get("declared_state"))
        if declared == "DISABLED" and gate_status in {"DISABLED", "UNPROVED"}:
            status = "DISABLED"
        elif authority_status != "PROVED":
            status = "UNKNOWN"
        elif schedule_status != "INSTALLED" or gate_status == "UNPROVED":
            status = "DEGRADED"
        elif lane['lane_id'] == 'orion_paper' and (
            execution.get('status') not in {'SUCCESS', 'NO_ACTION'}
            or execution.get('reason_code') == 'dry_run_only'
        ):
            status = "ACTIVE_WITH_EXCEPTION"
        elif execution.get("status") == "BLOCKED":
            status = "ACTIVE_WITH_EXCEPTION"
        elif broker.get("status") in {"DEGRADED", "UNOBSERVED"}:
            status = "ACTIVE_WITH_EXCEPTION"
        else:
            status = "ACTIVE"
        lane_rows.append(
            {
                "lane_id": lane["lane_id"],
                "lane_kind": lane["lane_kind"],
                "strategy_ids": lane["strategy_ids"],
                "declared_state": declared,
                "operating_status": status,
                "authority": {"status": authority_status, "reasons": reasons},
                "runtime_gates": {"status": gate_status, "observed_keys": sorted(gates)},
                "schedule": {"status": schedule_status, "marker_count": marker_count},
                "latest_execution": execution,
                "broker_truth": broker,
            }
        )
    conflicts = scan_narrative_conflicts(root, registry)
    try:
        deployed_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, check=True,
            capture_output=True, text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        deployed_sha = None
    body = {
        "schema_version": OPERATING_TRUTH_SCHEMA,
        "generated_at_utc": observed_at,
        "registry_id": registry["registry_id"],
        "registry_sha256": file_hash(registry_path),
        "canonical_runtime": {"repository_path": str(root), "deployed_sha": deployed_sha},
        "context_integrity": {
            "status": "PASS" if not conflicts else "CONTEXT_CONFLICT",
            "conflicts": conflicts,
        },
        "lanes": lane_rows,
    }
    body["content_hash"] = content_hash(body)
    return body


def render_operating_state(payload: Mapping[str, Any]) -> str:
    lines = [
        "# Current Operating State",
        "",
        "This file is generated from `config/operations/operating_lane_registry.json`.",
        "Runtime observations are written to `outputs/operating_state/current/`.",
        "Do not hand-edit volatile lane claims.",
        "",
        f"Generated: {payload.get('generated_at_utc')}",
        "",
        "| Lane | Strategy | State | Authority |",
        "|---|---|---|---|",
    ]
    for lane in payload.get("lanes") or []:
        strategies = ", ".join(lane.get("strategy_ids") or []) or "—"
        lines.append(
            f"| {lane['lane_kind']} | {strategies.replace('caerus_', '').replace('_', ' ').title()} | "
            f"{lane['operating_status']} | {lane['authority']['status']} |"
        )
    lines.extend(
        [
            "",
            "A strategy may be observed in Shadow while operating in a separate capital lane.",
            "The disabled legacy FR-104 lane does not disable Lyra Live.",
            "Broker evidence governs positions and cash; narrative documents are presentation only.",
            "",
        ]
    )
    return "\n".join(lines)
