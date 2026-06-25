#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research_data.data_trust import RUNTIME_IMPACT, SCHEMA_VERSION
from research_data.hydration import read_json


ALLOWED_STATUS = {"PASS", "WARN", "FAIL"}
ALLOWED_RISK = {"INFO", "WARN", "CRITICAL"}
ALLOWED_READINESS = {"OBSERVE_ONLY", "BLOCKED", "MISSING_ARTIFACT"}


def validate_data_trust_summary(path: Path, *, repo_root: Path | None = None) -> list[str]:
    payload = read_json(path)
    root = Path(repo_root or REPO_ROOT)
    errors: list[str] = []
    if payload.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}")
    if payload.get("runtime_impact") != RUNTIME_IMPACT:
        errors.append(f"runtime_impact must be {RUNTIME_IMPACT}")
    if payload.get("readiness_status") not in ALLOWED_STATUS:
        errors.append(f"invalid readiness_status: {payload.get('readiness_status')}")
    for flag in ("broker_submission_invoked", "dashboard_mutation_invoked", "email_send_invoked"):
        if payload.get(flag) is not False:
            errors.append(f"{flag} must be false")

    source_value = payload.get("source_observability_path")
    if not source_value:
        errors.append("source_observability_path missing")
    else:
        source_path = _resolve_path(root, str(source_value))
        if not source_path.exists():
            errors.append(f"source observability path missing: {source_path}")
        else:
            actual_digest = hashlib.sha256(source_path.read_bytes()).hexdigest()
            if payload.get("source_observability_sha256") != actual_digest:
                errors.append("source_observability_sha256 mismatch")

    rows = payload.get("datasets")
    if not isinstance(rows, list):
        return errors + ["datasets must be a list"]
    if payload.get("dataset_count") != len(rows):
        errors.append("dataset_count mismatch")
    if payload.get("observe_only_count") != sum(1 for row in rows if row.get("readiness_status") == "OBSERVE_ONLY"):
        errors.append("observe_only_count mismatch")
    if payload.get("blocked_count") != sum(1 for row in rows if row.get("readiness_status") == "BLOCKED"):
        errors.append("blocked_count mismatch")
    if payload.get("missing_artifact_count") != sum(1 for row in rows if row.get("readiness_status") == "MISSING_ARTIFACT"):
        errors.append("missing_artifact_count mismatch")
    if payload.get("critical_count") != sum(1 for row in rows if row.get("risk_level") == "CRITICAL"):
        errors.append("critical_count mismatch")
    if payload.get("warning_count") != sum(1 for row in rows if row.get("risk_level") == "WARN"):
        errors.append("warning_count mismatch")
    if payload.get("info_count") != sum(1 for row in rows if row.get("risk_level") == "INFO"):
        errors.append("info_count mismatch")

    seen: set[str] = set()
    for idx, row in enumerate(rows):
        dataset_id = str(row.get("dataset_id") or f"<row {idx}>")
        if dataset_id in seen:
            errors.append(f"duplicate dataset_id: {dataset_id}")
        seen.add(dataset_id)
        for field in ("dataset_id", "dataset_name", "tier", "domain", "readiness_status", "risk_level", "reason"):
            if row.get(field) in (None, ""):
                errors.append(f"{dataset_id}: missing {field}")
        if row.get("risk_level") not in ALLOWED_RISK:
            errors.append(f"{dataset_id}: invalid risk_level {row.get('risk_level')}")
        if row.get("readiness_status") not in ALLOWED_READINESS:
            errors.append(f"{dataset_id}: invalid readiness_status {row.get('readiness_status')}")
        if int(row.get("row_count") or 0) < 0:
            errors.append(f"{dataset_id}: row_count cannot be negative")

    errors.extend(_validate_findings(payload))
    return errors


def _validate_findings(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    findings = payload.get("findings")
    if not isinstance(findings, dict):
        return ["findings must be an object"]
    critical = findings.get("critical") or []
    warnings = findings.get("warnings") or []
    info = findings.get("info") or []
    if payload.get("critical_count") != len(critical):
        errors.append("critical findings count mismatch")
    if payload.get("warning_count") != len(warnings):
        errors.append("warning findings count mismatch")
    if payload.get("info_count") != len(info):
        errors.append("info findings count mismatch")
    status = payload.get("readiness_status")
    if status == "FAIL" and not critical:
        errors.append("FAIL summary requires at least one critical finding")
    if status == "WARN" and (critical or not warnings):
        errors.append("WARN summary requires warnings and no critical findings")
    if status == "PASS" and (critical or warnings):
        errors.append("PASS summary cannot include critical or warning findings")
    return errors


def _resolve_path(repo_root: Path, path_value: str) -> Path:
    path = Path(path_value)
    return path if path.is_absolute() else repo_root / path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate read-only FR-DH data trust summary artifacts.")
    parser.add_argument("--path", type=Path, default=REPO_ROOT / "outputs" / "data_trust" / "data_trust_summary.json")
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    errors = validate_data_trust_summary(args.path, repo_root=args.repo_root)
    print(json.dumps({"status": "FAIL" if errors else "OK", "errors": errors}, indent=2, sort_keys=True))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
