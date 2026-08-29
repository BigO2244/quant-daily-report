"""Execute the frozen experiment data/provenance gate without reading returns."""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import re
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from projects.alpha_lab.experiments.catalog import LANE_BY_HYPOTHESIS, LANES, DataAsset
from projects.alpha_lab.factory import (
    AppendOnlyJSONLEventStore,
    ProviderReadiness,
    ProviderRequirement,
    ProviderStatus,
    RunManifest,
    RunState,
    canonical_hash,
    canonical_json,
    evaluate_provider_readiness,
)
from projects.alpha_lab.factory.canonical import parse_datetime


SCHEMA_VERSION = "caerus_alpha_lab_data_gate_v1"
_DECLARED_HASH = re.compile(r"Spec hash: `sha256:([0-9a-f]{64})`")
_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


@lru_cache(maxsize=None)
def _sha256_file_snapshot(path: Path, size: int, mtime_ns: int) -> str:
    """Hash one stable file snapshot once per multi-lane gate process."""

    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_file(path: Path) -> str:
    stat = path.stat()
    return _sha256_file_snapshot(path, stat.st_size, stat.st_mtime_ns)


def _hash_code(repo_root: Path) -> str:
    records = []
    for directory in (
        repo_root / "projects/alpha_lab/factory",
        repo_root / "projects/alpha_lab/experiments",
    ):
        for path in sorted(directory.glob("*.py")):
            records.append(
                {"path": str(path.relative_to(repo_root)), "sha256": _sha256_file(path)}
            )
    return canonical_hash(records)


def _verify_spec(repo_root: Path, relative_path: str) -> Dict[str, str]:
    path = repo_root / relative_path
    text = path.read_text(encoding="utf-8")
    marker = "## Freeze record\n"
    if marker not in text:
        raise ValueError("frozen hypothesis is missing its Freeze record")
    match = _DECLARED_HASH.search(text)
    if match is None:
        raise ValueError("frozen hypothesis is missing its declared spec hash")
    body = text.split(marker, 1)[0].encode("utf-8")
    actual = hashlib.sha256(body).hexdigest()
    declared = match.group(1)
    if actual != declared:
        raise ValueError("frozen hypothesis hash mismatch")
    return {
        "path": relative_path,
        "declared_sha256": declared,
        "verified_sha256": actual,
    }


def _matching_files(repo_root: Path, patterns: Iterable[str]) -> List[Path]:
    matches = set()
    for pattern in patterns:
        for path in repo_root.glob(pattern):
            if path.is_file():
                matches.add(path.resolve())
    return sorted(matches)


def _read_certification(
    repo_root: Path, asset: DataAsset
) -> Optional[Dict[str, Any]]:
    path = repo_root / asset.certification_path
    if not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("provider certification must be a JSON object")
    return payload


def _flatten_json_fields(value: Any, prefix: str = "") -> set[str]:
    fields = set()
    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key)
            path = "{}.{}".format(prefix, key_text) if prefix else key_text
            fields.add(path)
            fields.update(_flatten_json_fields(item, path))
    elif isinstance(value, list):
        for item in value[:1000]:
            fields.update(_flatten_json_fields(item, prefix))
    return fields


def _physical_fields(path: Path) -> set[str]:
    if path.name.lower().endswith((".jsonl.gz", ".ndjson.gz")):
        fields = set()
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            for line_number, line in enumerate(stream):
                if line_number >= 1000:
                    break
                fields.update(_flatten_json_fields(json.loads(line)))
        return fields
    suffix = path.suffix.lower()
    if suffix == ".csv":
        with path.open("r", encoding="utf-8", newline="") as stream:
            return {item.strip() for item in next(csv.reader(stream), ()) if item.strip()}
    if suffix == ".json":
        return _flatten_json_fields(json.loads(path.read_text(encoding="utf-8")))
    if suffix in {".jsonl", ".ndjson"}:
        fields = set()
        with path.open("r", encoding="utf-8") as stream:
            for line_number, line in enumerate(stream):
                if line_number >= 1000:
                    break
                fields.update(_flatten_json_fields(json.loads(line)))
        return fields
    if suffix in {".parquet", ".pq"}:
        try:
            import pyarrow.parquet as parquet
        except ImportError as exc:
            raise ValueError("parquet schema reader is unavailable") from exc
        return set(parquet.read_schema(path).names)
    raise ValueError("unsupported schema-inspection format: {}".format(suffix or "none"))


def inspect_asset(repo_root: Path, asset: DataAsset, checked_at: datetime) -> Dict[str, Any]:
    matched_files = _matching_files(repo_root, asset.patterns)
    certification = _read_certification(repo_root, asset)
    files = matched_files
    # Immutable bundle patterns intentionally retain prior snapshots.  Once a
    # certification exists, inspect only the exact current files it binds,
    # while still requiring every declared path to be an existing member of
    # the frozen asset pattern.  Otherwise a second valid rebuild would make
    # the latest certification fail merely because the older evidence remains.
    if certification is not None and isinstance(
        certification.get("data_files"), list
    ):
        matched_set = set(matched_files)
        certified_paths = []
        for record in certification["data_files"]:
            if not isinstance(record, dict) or not isinstance(record.get("path"), str):
                certified_paths = []
                break
            candidate = (repo_root / record["path"]).resolve()
            try:
                candidate.relative_to(repo_root.resolve())
            except ValueError:
                certified_paths = []
                break
            if candidate not in matched_set or not candidate.is_file():
                certified_paths = []
                break
            certified_paths.append(candidate)
        if certified_paths and len(certified_paths) == len(
            certification["data_files"]
        ):
            files = sorted(set(certified_paths))
    file_records = [
        {
            "path": str(path.relative_to(repo_root)),
            "bytes": path.stat().st_size,
            "sha256": _sha256_file(path),
        }
        for path in files
    ]
    blockers = []
    fields_available: Sequence[str] = ()
    pit_verified = False
    evidence_hash = None
    status = ProviderStatus.BLOCKED
    if not files:
        blockers.append("required_data_files_absent")
    if certification is None:
        blockers.append("provider_readiness_certification_absent")
    else:
        if certification.get("provider_id") != asset.provider_id:
            blockers.append("certification_provider_id_mismatch")
        if certification.get("dataset_id") != asset.dataset_id:
            blockers.append("certification_dataset_id_mismatch")
        certified_files = certification.get("data_files")
        if certified_files != file_records:
            blockers.append("certification_not_bound_to_current_data_files")

        schema_manifest = certification.get("schema_manifest")
        physical_paths = {record["path"] for record in file_records}
        physical_fields_by_path: Dict[str, set[str]] = {}
        for record in file_records:
            physical_path = repo_root / record["path"]
            try:
                physical_fields_by_path[record["path"]] = _physical_fields(physical_path)
            except (OSError, ValueError, json.JSONDecodeError):
                blockers.append("physical_schema_inspection_failed:{}".format(record["path"]))
        schema_fields = []
        if not isinstance(schema_manifest, list) or not schema_manifest:
            blockers.append("schema_manifest_absent")
        else:
            for item in schema_manifest:
                if not isinstance(item, dict):
                    blockers.append("schema_manifest_entry_invalid")
                    continue
                logical_field = item.get("logical_field")
                source_path = item.get("source_path")
                physical_field = item.get("physical_field")
                data_type = item.get("data_type")
                if not all(
                    isinstance(value, str) and value.strip()
                    for value in (logical_field, source_path, physical_field, data_type)
                ):
                    blockers.append("schema_manifest_entry_incomplete")
                    continue
                if source_path not in physical_paths:
                    blockers.append("schema_manifest_source_not_in_data_files")
                    continue
                if physical_field not in physical_fields_by_path.get(source_path, set()):
                    blockers.append(
                        "physical_field_absent:{}:{}".format(source_path, physical_field)
                    )
                    continue
                schema_fields.append(logical_field)
        fields_available = tuple(sorted(set(schema_fields)))
        if certification.get("schema_validation_status") != "PASS":
            blockers.append("schema_validation_not_passed")

        pit_verified = certification.get("historical_point_in_time_verified") is True
        if not pit_verified:
            blockers.append("certification_pit_validation_not_passed")

        evidence_hash = certification.get("evidence_hash")
        unsigned_certification = dict(certification)
        unsigned_certification.pop("evidence_hash", None)
        expected_evidence_hash = canonical_hash(unsigned_certification)
        if evidence_hash != expected_evidence_hash:
            blockers.append("certification_evidence_hash_mismatch")
            evidence_hash = None
        requested_status = certification.get("status", "UNVERIFIED")
        try:
            status = ProviderStatus(requested_status)
        except ValueError:
            status = ProviderStatus.UNVERIFIED
            blockers.append("provider_readiness_status_invalid")
        declared_blockers = certification.get("blockers", ())
        if not isinstance(declared_blockers, (list, tuple)):
            blockers.append("certification_blockers_invalid")
        else:
            blockers.extend(str(item) for item in declared_blockers)
        if not files and status is ProviderStatus.READY:
            status = ProviderStatus.BLOCKED
        if status is ProviderStatus.READY and blockers:
            status = ProviderStatus.BLOCKED
    if certification is None and files:
        status = ProviderStatus.UNVERIFIED

    if status is ProviderStatus.READY:
        readiness = ProviderReadiness(
            provider_id=asset.provider_id,
            dataset_id=asset.dataset_id,
            status=status,
            checked_at=checked_at,
            fields_available=tuple(fields_available),
            historical_point_in_time_verified=pit_verified,
            evidence_hash=evidence_hash,
        )
    else:
        readiness = ProviderReadiness(
            provider_id=asset.provider_id,
            dataset_id=asset.dataset_id,
            status=status,
            checked_at=checked_at,
            fields_available=tuple(fields_available),
            historical_point_in_time_verified=pit_verified,
            evidence_hash=evidence_hash,
            blockers=tuple(blockers or ("provider_not_ready",)),
        )
    requirement = ProviderRequirement(
        provider_id=asset.provider_id,
        dataset_id=asset.dataset_id,
        required_fields=asset.required_fields,
    )
    gate = evaluate_provider_readiness(requirement, readiness)
    return {
        "asset_id": asset.asset_id,
        "patterns": asset.patterns,
        "certification_path": asset.certification_path,
        "files": file_records,
        "requirement": requirement.to_dict(),
        "readiness": readiness.to_dict(),
        "gate": gate.to_dict(),
        "gate_hash": gate.gate_hash,
    }


def _deferred_asset_result(asset: DataAsset, checked_at: datetime) -> Dict[str, Any]:
    """Fail closed without touching secondary files after a primary source miss."""

    readiness = ProviderReadiness(
        provider_id=asset.provider_id,
        dataset_id=asset.dataset_id,
        status=ProviderStatus.BLOCKED,
        checked_at=checked_at,
        fields_available=(),
        historical_point_in_time_verified=False,
        evidence_hash=None,
        blockers=("not_inspected_primary_event_source_unavailable",),
    )
    requirement = ProviderRequirement(
        provider_id=asset.provider_id,
        dataset_id=asset.dataset_id,
        required_fields=asset.required_fields,
    )
    gate = evaluate_provider_readiness(requirement, readiness)
    return {
        "asset_id": asset.asset_id,
        "patterns": asset.patterns,
        "certification_path": asset.certification_path,
        "files": [],
        "requirement": requirement.to_dict(),
        "readiness": readiness.to_dict(),
        "gate": gate.to_dict(),
        "gate_hash": gate.gate_hash,
    }


def run_lane(
    *,
    repo_root: Path,
    hypothesis_id: str,
    run_id: str,
    checked_at: datetime,
) -> Dict[str, Any]:
    if not _RUN_ID.fullmatch(run_id):
        raise ValueError("run_id must be a path-safe research identifier")
    lane = LANE_BY_HYPOTHESIS[hypothesis_id]
    lane_output_root = (
        repo_root / "outputs/research/alpha_lab" / lane.hypothesis_id
    ).resolve()
    prior_runs = []
    if lane_output_root.is_dir():
        for prior_result_path in sorted(lane_output_root.glob("*/result.json")):
            try:
                prior_result = json.loads(prior_result_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            prior_run_id = prior_result.get("run_id")
            if isinstance(prior_run_id, str) and prior_run_id != run_id:
                prior_runs.append(prior_run_id)
    spec = _verify_spec(repo_root, lane.spec_path)
    asset_results = []
    for index, asset in enumerate(lane.assets):
        asset_result = inspect_asset(repo_root, asset, checked_at)
        asset_results.append(asset_result)
        if asset.short_circuit_on_unready and not asset_result["gate"]["ready"]:
            asset_results.extend(
                _deferred_asset_result(remaining, checked_at)
                for remaining in lane.assets[index + 1 :]
            )
            break
    ready = all(item["gate"]["ready"] for item in asset_results)
    # This command is intentionally only a pre-return data gate. A READY result
    # authorizes a separate frozen evaluator run; it never reads the holdout.
    outcome = "READY_FOR_FROZEN_EVALUATOR" if ready else "BLOCKED_DATA"
    gate_packet = {
        "schema_version": SCHEMA_VERSION,
        "hypothesis_id": lane.hypothesis_id,
        "experiment_id": lane.experiment_id,
        "checked_at": checked_at,
        "repo_root": str(repo_root),
        "local_readiness_audit": lane.local_readiness,
        "ready": ready,
        "assets": asset_results,
    }
    data_snapshot = [
        {"asset_id": item["asset_id"], "files": item["files"]} for item in asset_results
    ]
    manifest = RunManifest(
        run_id=run_id,
        experiment_id=lane.experiment_id,
        hypothesis_id=lane.hypothesis_id,
        state=RunState.REVIEW if ready else RunState.BLOCKED_DATA,
        created_at=checked_at,
        hypothesis_hash=spec["verified_sha256"],
        code_hash=_hash_code(repo_root),
        data_snapshot_hash=canonical_hash(data_snapshot),
        provider_gate_hash=canonical_hash(gate_packet),
    )
    result = {
        "schema_version": SCHEMA_VERSION,
        "outcome": outcome,
        "classification": "UNPROVEN",
        "hypothesis_id": lane.hypothesis_id,
        "experiment_id": lane.experiment_id,
        "run_id": run_id,
        "spec": spec,
        "run_manifest_hash": manifest.manifest_hash,
        "holdout_accessed": False,
        "returns_accessed": False,
        "alpha_claim_permitted": False,
        "return_variants_attempted": 0,
        "prior_data_gate_runs": prior_runs,
        "data_gate_attempts_including_current": len(prior_runs) + 1,
        "missing_or_blocked_assets": [
            item["asset_id"] for item in asset_results if not item["gate"]["ready"]
        ],
        "next_permitted_action": (
            "run the separately reviewed frozen evaluator without changing the spec"
            if ready
            else "satisfy and certify every blocked data contract before return testing"
        ),
        "boundary_attestation": (
            "Research-only data gate; no broker, order, allocation, paper, live, "
            "scheduler, cron, deployment, or promotion surface was read or changed."
        ),
    }
    evaluator_input = {
        "schema_version": "caerus_alpha_lab_evaluator_input_v1",
        "data_gate_status": outcome,
        "hypothesis_id": lane.hypothesis_id,
        "experiment_id": lane.experiment_id,
        "checked_at": checked_at,
        "repo_root": str(repo_root),
        "provider_gate_hash": canonical_hash(gate_packet),
        "assets": {
            item["asset_id"]: {
                "files": item["files"],
                "gate_hash": item["gate_hash"],
            }
            for item in asset_results
        },
        "terminal_return_policy": (
            "REPORT_BOTH_PESSIMISTIC_TOTAL_LOSS_AND_ZERO_INCREMENTAL"
            if any(
                item["asset_id"] == "terminal_return_sensitivity_v1"
                for item in asset_results
            )
            else "FROZEN_DATA_CONTRACT"
        ),
        "challenge_access_authorized": False,
        "trading_behavior_changed": False,
    }

    run_dir = (
        lane_output_root / run_id
    ).resolve()
    research_root = (repo_root / "outputs/research/alpha_lab").resolve()
    try:
        run_dir.relative_to(research_root)
    except ValueError as exc:
        raise ValueError("run directory must remain inside Alpha Lab outputs") from exc
    run_dir.mkdir(parents=True, exist_ok=False)
    (run_dir / "provider_gate.json").write_text(
        canonical_json(gate_packet) + "\n", encoding="utf-8"
    )
    (run_dir / "run_manifest.json").write_text(
        canonical_json(manifest.to_dict()) + "\n", encoding="utf-8"
    )
    (run_dir / "result.json").write_text(
        canonical_json(result) + "\n", encoding="utf-8"
    )
    (run_dir / "evaluator_input.json").write_text(
        canonical_json(evaluator_input) + "\n", encoding="utf-8"
    )
    store = AppendOnlyJSONLEventStore(
        run_dir / "events.jsonl", research_root=research_root
    )
    store.append(
        event_id="{}:started".format(run_id),
        event_type="data_gate_started",
        occurred_at=checked_at,
        recorded_at=checked_at,
        payload={"hypothesis_id": lane.hypothesis_id, "spec_hash": spec["verified_sha256"]},
    )
    store.append(
        event_id="{}:review".format(run_id),
        event_type="data_gate_review",
        occurred_at=checked_at,
        recorded_at=checked_at,
        payload=result,
    )
    return {"run_dir": str(run_dir), "result": result}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--all", action="store_true", help="run all frozen gates")
    group.add_argument(
        "--hypothesis-id",
        choices=tuple(LANE_BY_HYPOTHESIS),
        help="run one frozen hypothesis gate",
    )
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--checked-at",
        help="aware ISO-8601 timestamp; defaults to current UTC",
    )
    parser.add_argument(
        "--run-id-prefix",
        help="deterministic prefix; defaults to checked-at UTC",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    repo_root = args.repo_root.expanduser().resolve()
    checked_at = (
        parse_datetime(args.checked_at)
        if args.checked_at
        else datetime.now(timezone.utc)
    )
    default_prefix = checked_at.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    prefix = args.run_id_prefix or default_prefix
    lanes = LANES if args.all else (LANE_BY_HYPOTHESIS[args.hypothesis_id],)
    summaries = []
    for lane in lanes:
        run_id = "{}-{}-data-gate-v1".format(prefix, lane.hypothesis_id.lower())
        summaries.append(
            run_lane(
                repo_root=repo_root,
                hypothesis_id=lane.hypothesis_id,
                run_id=run_id,
                checked_at=checked_at,
            )
        )
    print(canonical_json(summaries))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
