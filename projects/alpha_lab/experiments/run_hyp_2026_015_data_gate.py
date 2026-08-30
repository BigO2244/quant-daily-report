"""Run HYP-2026-015's frozen source gate without reading market outcomes."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from projects.alpha_lab.factory import (
    AppendOnlyJSONLEventStore,
    canonical_hash,
    canonical_json,
)
from projects.alpha_lab.factory.canonical import parse_datetime


SCHEMA_VERSION = "caerus_alpha_lab_hyp_2026_015_data_gate_v1"
HYPOTHESIS_ID = "HYP-2026-015"
EXPERIMENT_ID = "EXP-2026-0015"
RUNNER_RELATIVE_PATH = (
    "projects/alpha_lab/experiments/run_hyp_2026_015_data_gate.py"
)
SPEC_RELATIVE_PATH = (
    "projects/alpha_lab/hypotheses/"
    "HYP-2026-015_industry_earnings_information_diffusion.md"
)
SPEC_SHA256 = "3ca51f2f477c548d0b9ad266f004b4f61ba532f1d23961847c05db1e5fd033d6"
SOURCE_MANIFEST_RELATIVE_PATH = (
    "outputs/research/alpha_lab/data_spine/sec_original_filings_stream/"
    "20260722T212948Z-8bec6cab476f/manifest.json"
)
SOURCE_MANIFEST_SHA256 = (
    "90bd5b5d43da8d8e02b924308cbc049cee117535db597769a1c43a057036278f"
)
SOURCE_BUNDLE_SHA256 = (
    "25f7cdf591a1f80339309b0ca1a2c5abc18a01529fca9e3d7e3eb004dcfd7ad4"
)
EARNINGS_READINESS_RELATIVE_PATH = (
    "outputs/research/alpha_lab/provider_readiness/pit_earnings_events_v1.json"
)
EARNINGS_READINESS_SHA256 = (
    "44e9d240a34560794f70f29cf73ddee1ad569192529eacfadadf135ee60d89ac"
)
PRICES_READINESS_RELATIVE_PATH = (
    "outputs/research/alpha_lab/provider_readiness/pit_observed_prices_v1.json"
)
PRICES_READINESS_SHA256 = (
    "6a97d2ae3311ae3ad24ee289a37099afde70473b782f26e21c5487d7563af7d0"
)
PRICES_PANEL_SHA256 = (
    "7b6518bc30d84820b5113465fb23d54de36012195ed1672ed19aca9e216c99c0"
)

_SPEC_MARKER = "## Freeze record\n"
_ACCESSION = re.compile(r"(?P<accession>\d{10}-\d{2}-\d{6})")
_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verified_file(repo_root: Path, relative_path: str, expected: str) -> dict[str, Any]:
    path = repo_root / relative_path
    if not path.is_file():
        raise ValueError(f"frozen input is absent: {relative_path}")
    actual = _sha256_file(path)
    if actual != expected:
        raise ValueError(f"frozen input hash mismatch: {relative_path}")
    return {
        "path": relative_path,
        "bytes": path.stat().st_size,
        "sha256": actual,
    }


def _verify_spec(repo_root: Path) -> dict[str, Any]:
    path = repo_root / SPEC_RELATIVE_PATH
    text = path.read_text(encoding="utf-8")
    if _SPEC_MARKER not in text:
        raise ValueError("frozen hypothesis is missing its Freeze record")
    actual = hashlib.sha256(text.split(_SPEC_MARKER, 1)[0].encode("utf-8")).hexdigest()
    if actual != SPEC_SHA256:
        raise ValueError("frozen HYP-2026-015 specification hash mismatch")
    return {"path": SPEC_RELATIVE_PATH, "sha256": actual}


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _status_errors(bundle_root: Path) -> list[dict[str, Any]]:
    status_root = bundle_root / "data/status"
    status_paths = sorted(status_root.glob("part_*_status.json"))
    if not status_paths:
        raise ValueError("source-bundle partition status files are absent")
    errors: list[dict[str, Any]] = []
    for status_path in status_paths:
        payload = _load_json(status_path)
        declared_count = payload.get("error_count")
        records = payload.get("errors", [])
        if not isinstance(declared_count, int) or not isinstance(records, list):
            raise ValueError(f"invalid partition status schema: {status_path}")
        if declared_count != len(records):
            raise ValueError(f"partition error-count mismatch: {status_path}")
        for record in records:
            if not isinstance(record, dict):
                raise ValueError(f"invalid partition error record: {status_path}")
            errors.append(
                {
                    "partition_status_path": str(status_path.relative_to(bundle_root)),
                    "error_type": record.get("error_type"),
                    "source_filename": record.get("source_filename"),
                }
            )
    return errors


def _accession(value: object) -> str | None:
    match = _ACCESSION.search(value) if isinstance(value, str) else None
    return match.group("accession") if match else None


def _scan_item_202_tape(
    path: Path, missing_accessions: Iterable[str]
) -> tuple[int, list[dict[str, Any]]]:
    wanted = set(missing_accessions)
    matches: list[dict[str, Any]] = []
    row_count = 0
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            payload = json.loads(line)
            if not isinstance(payload, dict):
                raise ValueError(f"invalid Item 2.02 row at line {line_number}")
            row_count += 1
            event_id = payload.get("event_id")
            if event_id in wanted:
                matches.append(
                    {
                        "event_id": event_id,
                        "issuer_cik": payload.get("issuer_cik"),
                        "form_type": payload.get("form_type"),
                        "acceptance_datetime_utc": payload.get("acceptance_datetime_utc"),
                        "source_document": payload.get("source_document"),
                        "source_sha256": payload.get("source_sha256"),
                        "event_class": payload.get("event_class"),
                    }
                )
    return row_count, matches


def _deferred_controls() -> list[dict[str, str]]:
    return [
        {"control": name, "status": "NOT_INSPECTED_SOURCE_GATE_FAILED"}
        for name in (
            "exact_acceptance_time_for_every_item_2_02_original",
            "unique_reporter_mapping",
            "unique_eligible_peer_mapping",
            "validation_event_cluster_floor",
            "validation_unique_peer_floor",
            "validation_four_digit_sic_floor",
            "deterministic_overlap_handling",
            "reaction_and_holding_path_inventory",
            "terminal_event_disposition",
        )
    ]


def run_gate(
    *, repo_root: Path, run_id: str, checked_at: datetime
) -> dict[str, Any]:
    if not _RUN_ID.fullmatch(run_id):
        raise ValueError("run_id must be a path-safe research identifier")
    repo_root = repo_root.resolve()
    research_root = (repo_root / "outputs/research/alpha_lab").resolve()
    run_dir = (research_root / HYPOTHESIS_ID / run_id).resolve()
    run_dir.relative_to(research_root)
    if run_dir.exists():
        raise FileExistsError(f"research run already exists: {run_dir}")
    spec = _verify_spec(repo_root)
    code_record = {
        "path": RUNNER_RELATIVE_PATH,
        "sha256": _sha256_file(Path(__file__).resolve()),
    }
    source_record = _verified_file(
        repo_root, SOURCE_MANIFEST_RELATIVE_PATH, SOURCE_MANIFEST_SHA256
    )
    earnings_record = _verified_file(
        repo_root, EARNINGS_READINESS_RELATIVE_PATH, EARNINGS_READINESS_SHA256
    )
    prices_record = _verified_file(
        repo_root, PRICES_READINESS_RELATIVE_PATH, PRICES_READINESS_SHA256
    )

    source_path = repo_root / SOURCE_MANIFEST_RELATIVE_PATH
    source_manifest = _load_json(source_path)
    if source_manifest.get("bundle_hash") != SOURCE_BUNDLE_SHA256:
        raise ValueError("frozen original-filings bundle hash mismatch")
    metadata = source_manifest.get("metadata")
    if not isinstance(metadata, dict):
        raise ValueError("source manifest metadata is absent")
    candidate_count = metadata.get("candidate_count")
    hydrated_count = metadata.get("hydrated_count")
    acceptance_count = metadata.get("acceptance_timestamp_pass_count")
    declared_error_count = metadata.get("error_count")
    if not all(
        isinstance(value, int)
        for value in (candidate_count, hydrated_count, acceptance_count, declared_error_count)
    ):
        raise ValueError("source manifest count fields are invalid")

    bundle_root = source_path.parent
    errors = _status_errors(bundle_root)
    if declared_error_count != len(errors):
        raise ValueError("bundle error count does not match partition statuses")
    missing_accessions = sorted(
        accession
        for accession in (_accession(item.get("source_filename")) for item in errors)
        if accession is not None
    )

    earnings_readiness = _load_json(repo_root / EARNINGS_READINESS_RELATIVE_PATH)
    data_files = earnings_readiness.get("data_files")
    if not isinstance(data_files, list) or len(data_files) != 1:
        raise ValueError("earnings readiness does not bind exactly one event tape")
    tape_relative_path = data_files[0].get("path")
    if not isinstance(tape_relative_path, str):
        raise ValueError("earnings readiness event-tape path is invalid")
    tape_path = repo_root / tape_relative_path
    if not tape_path.is_file():
        raise ValueError("earnings Item 2.02 event tape is absent")
    tape_rows, missing_item_202 = _scan_item_202_tape(tape_path, missing_accessions)

    prices_readiness = _load_json(repo_root / PRICES_READINESS_RELATIVE_PATH)
    panel_hashes = {
        item.get("sha256")
        for item in prices_readiness.get("data_files", [])
        if isinstance(item, dict)
    }
    if PRICES_PANEL_SHA256 not in panel_hashes:
        raise ValueError("observed-price readiness does not bind the frozen panel hash")

    source_complete = (
        declared_error_count == 0
        and candidate_count == hydrated_count == acceptance_count
        and not missing_item_202
    )
    outcome = "SOURCE_READY" if source_complete else "BLOCKED_DATA"
    if source_complete:
        raise ValueError(
            "source gate unexpectedly passed; this runner deliberately stops before "
            "downstream identity, coverage, path, or outcome access"
        )

    coverage = (
        f"{hydrated_count}/{candidate_count}" if candidate_count else "0/0"
    )
    result = {
        "schema_version": SCHEMA_VERSION,
        "hypothesis_id": HYPOTHESIS_ID,
        "experiment_id": EXPERIMENT_ID,
        "run_id": run_id,
        "checked_at": checked_at,
        "outcome": outcome,
        "classification": "UNPROVEN",
        "verdict": "ITERATE — BLOCKED_DATA",
        "spec": spec,
        "frozen_inputs": {
            "runner": code_record,
            "source_manifest": source_record,
            "source_bundle_sha256": SOURCE_BUNDLE_SHA256,
            "earnings_readiness": earnings_record,
            "observed_prices_readiness": prices_record,
            "observed_prices_panel_sha256": PRICES_PANEL_SHA256,
        },
        "source_gate": {
            "status": "FAIL",
            "candidate_original_count": candidate_count,
            "hydrated_original_count": hydrated_count,
            "acceptance_timestamp_pass_count": acceptance_count,
            "error_count": declared_error_count,
            "source_coverage": coverage,
            "item_2_02_discovery_row_count": tape_rows,
            "missing_originals": errors,
            "missing_originals_present_in_item_2_02_tape": missing_item_202,
            "required_coverage": "100%",
            "failure_reason": (
                "At least one missing original filing belongs to the Item 2.02 "
                "candidate tape, so exact original-source and acceptance-time "
                "coverage cannot be certified at 100%."
            ),
        },
        "deferred_controls": _deferred_controls(),
        "return_data_accessed": False,
        "reporter_reaction_accessed": False,
        "forward_return_accessed": False,
        "validation_outcomes_accessed": False,
        "challenge_period_accessed": False,
        "statistical_trial_opened": False,
        "canonical_global_ledger_present": (
            repo_root
            / "outputs/research/alpha_lab/ledger/research_events.v1.jsonl"
        ).is_file(),
        "orders_submitted": False,
        "trading_behavior_changed": False,
        "next_executable_action": (
            "Data Foundry must recover and hash every missing original SEC filing "
            "into a new immutable source bundle, prove exact acceptance time, and "
            "obtain owner approval to bind the replacement data snapshot before "
            "rerunning this no-return gate."
        ),
        "boundary_attestation": (
            "Research-only no-return source gate. No market return, reporter "
            "reaction, validation outcome, challenge input, broker, order, "
            "allocation, scheduler, cron, production, Paper, or Live surface "
            "was accessed or changed."
        ),
    }

    run_dir.mkdir(parents=True, exist_ok=False)
    result_path = run_dir / "result.json"
    result_path.write_text(canonical_json(result) + "\n", encoding="utf-8")
    receipt = {
        "schema_version": "caerus_alpha_lab_hyp_2026_015_data_gate_receipt_v1",
        "run_id": run_id,
        "result_path": str(result_path.relative_to(repo_root)),
        "result_sha256": _sha256_file(result_path),
        "result_canonical_hash": canonical_hash(result),
    }
    receipt_path = run_dir / "receipt.json"
    receipt_path.write_text(canonical_json(receipt) + "\n", encoding="utf-8")
    store = AppendOnlyJSONLEventStore(
        run_dir / "events.jsonl", research_root=research_root
    )
    store.append(
        event_id=f"{run_id}:started",
        event_type="hyp_2026_015_no_return_gate_started",
        occurred_at=checked_at,
        recorded_at=checked_at,
        payload={"spec_sha256": SPEC_SHA256, "outcome_data_accessed": False},
    )
    store.append(
        event_id=f"{run_id}:blocked",
        event_type="hyp_2026_015_no_return_gate_blocked_data",
        occurred_at=checked_at,
        recorded_at=checked_at,
        payload={
            "result_sha256": receipt["result_sha256"],
            "source_coverage": coverage,
            "error_count": declared_error_count,
            "outcome_data_accessed": False,
        },
    )
    return {
        "run_dir": str(run_dir),
        "result": result,
        "receipt": receipt,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--run-id")
    parser.add_argument("--checked-at")
    return parser.parse_args()


def main() -> int:
    arguments = _parse_args()
    checked_at = (
        parse_datetime(arguments.checked_at)
        if arguments.checked_at
        else datetime.now(timezone.utc)
    )
    run_id = arguments.run_id or (
        checked_at.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        + "-hyp-2026-015-data-gate-v1"
    )
    packet = run_gate(
        repo_root=arguments.repo_root.expanduser(),
        run_id=run_id,
        checked_at=checked_at,
    )
    print(canonical_json(packet))
    return 2 if packet["result"]["outcome"] == "BLOCKED_DATA" else 0


if __name__ == "__main__":
    raise SystemExit(main())
