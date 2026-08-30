"""Seal and execute HYP-2026-015's single preregistered validation trial."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import subprocess
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Sequence

from projects.alpha_lab.evaluators.industry_earnings_diffusion import (
    CHALLENGE_START,
    evaluate_primary_v1,
)
from projects.alpha_lab.factory.canonical import (
    canonical_hash,
    canonical_json,
    format_datetime,
    parse_datetime,
)
from projects.alpha_lab.factory.errors import (
    ContractValidationError,
    EventStoreIntegrityError,
    ResearchBoundaryError,
)


HYPOTHESIS_ID = "HYP-2026-015"
EXPERIMENT_ID = "EXP-2026-0015"
FAMILY_ID = "FAMILY-2026-0015"
WAVE_ID = "WAVE-2026-003-ORTHOGONAL-EVENT-01"
TRIAL_ID = "FAMILY-2026-0015-T001"
VARIANT_ID = "PRIMARY_V1"
CHALLENGE_EPOCH_ID = "CHALLENGE-2025H1-2026H1-01"
CHALLENGE_PERIOD = "2025-01-01/2026-06-30"
CHALLENGE_PANEL_SHA256 = (
    "7b6518bc30d84820b5113465fb23d54de36012195ed1672ed19aca9e216c99c0"
)
ORIGINAL_SPEC_RELATIVE_PATH = (
    "projects/alpha_lab/hypotheses/"
    "HYP-2026-015_industry_earnings_information_diffusion.md"
)
ORIGINAL_SPEC_SHA256 = (
    "3ca51f2f477c548d0b9ad266f004b4f61ba532f1d23961847c05db1e5fd033d6"
)
ADDENDUM_RELATIVE_PATH = (
    "projects/alpha_lab/hypotheses/"
    "HYP-2026-015-ADDENDUM-001_source_materiality_and_evaluator_determinism.md"
)
ADDENDUM_SHA256 = (
    "6a3747d98e89efdb3f73e0f7a3587992b38804789e43534a7ec03842ee5e3c8e"
)
ADDENDUM_FULL_FILE_SHA256 = (
    "8a327c8317a7cb4f78b877863eec805ec86a047900488806a751569269704820"
)
EVALUATOR_SPEC_RELATIVE_PATH = (
    "projects/alpha_lab/experiments/evaluator_specs/HYP-2026-015.json.frozen"
)
CODE_MANIFEST_RELATIVE_PATH = (
    "projects/alpha_lab/experiments/evaluator_specs/HYP-2026-015-code-manifest.json.manifest"
)
PRICE_READINESS_RELATIVE_PATH = (
    "outputs/research/alpha_lab/provider_readiness/pit_observed_prices_v1.json"
)
FACTOR_READINESS_RELATIVE_PATH = (
    "outputs/research/alpha_lab/provider_readiness/factor_panel_v1.json"
)
CANONICAL_GCP_REPO_ROOT = Path("/mnt/disks/alpha-lab/alpha-lab-project")
GENESIS_HASH = "0" * 64
_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,159}$")


def _sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _exclusive_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(canonical_json(payload) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def _load_json(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ContractValidationError("JSON artifact must be an object: {}".format(path))
    return value


def _relative_record(repo_root: Path, path: Path) -> Dict[str, Any]:
    resolved = path.resolve()
    try:
        relative = resolved.relative_to(repo_root)
    except ValueError as exc:
        raise ResearchBoundaryError("input path escapes repository root") from exc
    if not resolved.is_file():
        raise FileNotFoundError(resolved)
    return {
        "path": relative.as_posix(),
        "bytes": resolved.stat().st_size,
        "sha256": _sha256_file(resolved),
    }


def _record_for_final(
    repo_root: Path, physical_path: Path, final_path: Path
) -> Dict[str, Any]:
    """Describe staged bytes by their immutable post-finalize path."""

    relative = final_path.resolve().relative_to(repo_root)
    if not physical_path.is_file():
        raise FileNotFoundError(physical_path)
    return {
        "path": relative.as_posix(),
        "bytes": physical_path.stat().st_size,
        "sha256": _sha256_file(physical_path),
    }


def _trial_reservation_path(repo_root: Path) -> Path:
    return (
        repo_root
        / "outputs/research/alpha_lab"
        / HYPOTHESIS_ID
        / "trial_reservations"
        / "{}.json".format(TRIAL_ID)
    )


def _reserve_trial(
    repo_root: Path, *, run_id: str, registration_hash: str, created_at: datetime
) -> Dict[str, Any]:
    payload = {
        "schema_version": "caerus_alpha_lab_hyp015_trial_reservation_v1",
        "statistical_trial_id": TRIAL_ID,
        "run_id": run_id,
        "registration_hash": registration_hash,
        "reserved_at": format_datetime(created_at),
    }
    path = _trial_reservation_path(repo_root)
    try:
        _exclusive_json(path, payload)
    except FileExistsError:
        existing = _load_json(path)
        if (
            existing.get("statistical_trial_id") != TRIAL_ID
            or existing.get("run_id") != run_id
            or existing.get("registration_hash") != registration_hash
        ):
            raise ResearchBoundaryError(
                "the frozen HYP-2026-015 trial is already reserved by another run"
            )
        return existing
    return payload


def _verify_trial_reservation(
    repo_root: Path, *, run_id: str, registration_hash: str
) -> Dict[str, Any]:
    reservation = _load_json(_trial_reservation_path(repo_root))
    if (
        reservation.get("statistical_trial_id") != TRIAL_ID
        or reservation.get("run_id") != run_id
        or reservation.get("registration_hash") != registration_hash
    ):
        raise ResearchBoundaryError("trial reservation does not bind this run")
    return reservation


def _prefreeze_hash(path: Path, marker: str) -> str:
    raw = path.read_bytes()
    boundary = raw.find(marker.encode("utf-8"))
    if boundary < 0:
        raise ContractValidationError("frozen record marker is missing")
    return hashlib.sha256(raw[:boundary]).hexdigest()


def _verify_fixed_contracts(repo_root: Path) -> Dict[str, Any]:
    original = repo_root / ORIGINAL_SPEC_RELATIVE_PATH
    addendum = repo_root / ADDENDUM_RELATIVE_PATH
    if _prefreeze_hash(original, "## Freeze record") != ORIGINAL_SPEC_SHA256:
        raise ContractValidationError("original hypothesis hash mismatch")
    if _prefreeze_hash(addendum, "## Addendum record") != ADDENDUM_SHA256:
        raise ContractValidationError("Addendum 001 hash mismatch")
    if _sha256_file(addendum) != ADDENDUM_FULL_FILE_SHA256:
        raise ContractValidationError("Addendum 001 full-file hash mismatch")
    code_manifest_path = repo_root / CODE_MANIFEST_RELATIVE_PATH
    code_manifest = _load_json(code_manifest_path)
    if code_manifest.get("variant_id") != VARIANT_ID:
        raise ContractValidationError("evaluator code manifest variant mismatch")
    for record in code_manifest.get("files", []):
        path = (repo_root / str(record["path"])).resolve()
        path.relative_to(repo_root)
        if not path.is_file() or _sha256_file(path) != record.get("sha256"):
            raise ContractValidationError("evaluator code manifest hash mismatch")
    return {
        "original_hypothesis": {
            "path": ORIGINAL_SPEC_RELATIVE_PATH,
            "sha256": ORIGINAL_SPEC_SHA256,
        },
        "addendum": {
            "path": ADDENDUM_RELATIVE_PATH,
            "frozen_body_sha256": ADDENDUM_SHA256,
            "full_file_sha256": _sha256_file(addendum),
        },
        "evaluator_spec": _relative_record(
            repo_root, repo_root / EVALUATOR_SPEC_RELATIVE_PATH
        ),
        "evaluator_code_manifest": _relative_record(
            repo_root, code_manifest_path
        ),
    }


def _verify_gate_bundle(repo_root: Path, gate_dir: Path) -> Dict[str, Any]:
    gate_dir = gate_dir.resolve()
    try:
        gate_dir.relative_to(repo_root / "outputs/research/alpha_lab")
    except ValueError as exc:
        raise ResearchBoundaryError("gate bundle is outside the research root") from exc
    manifest_path = gate_dir / "manifest.json"
    result_path = gate_dir / "result.json"
    manifest = _load_json(manifest_path)
    result = _load_json(result_path)
    if result.get("schema_version") != "caerus_alpha_lab_hyp_2026_015_no_return_gate_v2":
        raise ContractValidationError("outcome evaluator requires no-return gate v2")
    if result.get("outcome") != "READY_FOR_OUTCOME_REGISTRATION":
        raise ContractValidationError("no-return gate v2 did not pass")
    if result.get("spec", {}).get("sha256") != ORIGINAL_SPEC_SHA256:
        raise ContractValidationError("gate v2 hypothesis binding mismatch")
    addendum = result.get("owner_addendum", {})
    if addendum.get("frozen_body_sha256") != ADDENDUM_SHA256:
        raise ContractValidationError("gate v2 addendum frozen-body binding mismatch")
    if addendum.get("full_file_sha256") != ADDENDUM_FULL_FILE_SHA256:
        raise ContractValidationError("gate v2 addendum full-file binding mismatch")
    controls = result.get("controls")
    if not isinstance(controls, list) or not controls or any(
        item.get("status") != "PASS" for item in controls
    ):
        raise ContractValidationError("every no-return gate v2 control must pass")
    for flag in (
        "reporter_reaction_accessed",
        "forward_return_accessed",
        "validation_outcomes_accessed",
        "challenge_period_accessed",
        "statistical_trial_opened",
        "orders_submitted",
        "trading_behavior_changed",
    ):
        if result.get(flag) is not False:
            raise ContractValidationError("gate boundary flag is not false: {}".format(flag))
    file_records = {item["name"]: item for item in manifest.get("files", [])}
    required = {
        "result.json",
        "eligibility_manifest.jsonl.gz",
        "exclusion_manifest.jsonl.gz",
    }
    missing = required - set(file_records)
    if missing:
        raise ContractValidationError(
            "gate v2 bundle is missing {}".format(",".join(sorted(missing)))
        )
    for name in sorted(required):
        path = gate_dir / name
        record = file_records[name]
        if not path.is_file() or path.stat().st_size != int(record["bytes"]):
            raise ContractValidationError("gate file size mismatch: {}".format(name))
        if _sha256_file(path) != record["sha256"]:
            raise ContractValidationError("gate file hash mismatch: {}".format(name))
    return {
        "gate_result": _relative_record(repo_root, result_path),
        "gate_manifest": _relative_record(repo_root, manifest_path),
        "eligibility_manifest": _relative_record(
            repo_root, gate_dir / "eligibility_manifest.jsonl.gz"
        ),
        "exclusion_manifest": _relative_record(
            repo_root, gate_dir / "exclusion_manifest.jsonl.gz"
        ),
        "gate_run_id": result.get("run_id"),
    }


def _readiness_asset(repo_root: Path, relative_path: str, role: str) -> Dict[str, Any]:
    readiness_path = repo_root / relative_path
    readiness = _load_json(readiness_path)
    if readiness.get("status") != "READY" or readiness.get(
        "historical_point_in_time_verified"
    ) is not True:
        raise ContractValidationError("{} readiness is not certified".format(role))
    files = readiness.get("data_files")
    if not isinstance(files, list) or len(files) != 1:
        raise ContractValidationError("{} readiness must bind one file".format(role))
    advertised = files[0]
    data_path = (repo_root / str(advertised["path"])).resolve()
    data_path.relative_to(repo_root)
    if not data_path.is_file():
        raise FileNotFoundError(data_path)
    return {
        "role": role,
        "readiness": _relative_record(repo_root, readiness_path),
        "data": {
            "path": data_path.relative_to(repo_root).as_posix(),
            "bytes": int(advertised["bytes"]),
            "sha256": str(advertised["sha256"]),
        },
    }


def _git_commit(repo_root: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        check=True,
        text=True,
        capture_output=True,
    )
    return result.stdout.strip()


def _append_event(
    path: Path,
    *,
    event_id: str,
    event_type: str,
    occurred_at: datetime,
    payload: Mapping[str, Any],
) -> Dict[str, Any]:
    records = _read_events(path)
    if any(item["event_id"] == event_id for item in records):
        raise EventStoreIntegrityError("duplicate event ID")
    previous = records[-1]["event_hash"] if records else GENESIS_HASH
    recorded_at = datetime.now(timezone.utc)
    unsigned = {
        "schema_version": "caerus_alpha_lab_hyp015_local_event_v1",
        "event_id": event_id,
        "event_type": event_type,
        "occurred_at": format_datetime(occurred_at),
        "recorded_at": format_datetime(recorded_at),
        "payload": dict(payload),
        "payload_hash": canonical_hash(payload),
        "previous_event_hash": previous,
    }
    record = dict(unsigned)
    record["event_hash"] = canonical_hash(unsigned)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(descriptor, "a", encoding="utf-8") as stream:
        stream.write(canonical_json(record) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    _read_events(path)
    return record


def _read_events(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    if text and not text.endswith("\n"):
        raise EventStoreIntegrityError("event chain has a partial final record")
    records: List[Dict[str, Any]] = []
    previous = GENESIS_HASH
    for line_number, line in enumerate(text.splitlines(), start=1):
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise EventStoreIntegrityError(
                "invalid event JSON at line {}".format(line_number)
            ) from exc
        unsigned = {key: value for key, value in record.items() if key != "event_hash"}
        if canonical_hash(unsigned) != record.get("event_hash"):
            raise EventStoreIntegrityError("event hash mismatch")
        if canonical_hash(record.get("payload")) != record.get("payload_hash"):
            raise EventStoreIntegrityError("event payload hash mismatch")
        if record.get("previous_event_hash") != previous:
            raise EventStoreIntegrityError("event chain is broken")
        previous = record["event_hash"]
        records.append(record)
    if len({item["event_id"] for item in records}) != len(records):
        raise EventStoreIntegrityError("event IDs are not unique")
    return records


def seal_preregistration(
    *,
    repo_root: Path,
    gate_dir: Path,
    run_id: str,
    created_at: datetime,
    enforce_canonical_gcp: bool = True,
) -> Dict[str, Any]:
    repo_root = repo_root.resolve()
    if enforce_canonical_gcp and repo_root != CANONICAL_GCP_REPO_ROOT:
        raise ResearchBoundaryError("preregistration must be sealed on canonical GCP")
    if not _RUN_ID.fullmatch(run_id):
        raise ValueError("run_id is not path-safe")
    if created_at.tzinfo is None or created_at.utcoffset() is None:
        raise ValueError("created_at must be timezone-aware")

    hypothesis_root = (
        repo_root / "outputs/research/alpha_lab" / HYPOTHESIS_ID
    )
    run_dir = hypothesis_root / run_id
    staging_dir = hypothesis_root / ".staging" / "{}.preregister".format(run_id)

    if run_dir.exists():
        registration, manifest, events = _verify_preregistration(run_dir, repo_root)
        _verify_trial_reservation(
            repo_root,
            run_id=run_id,
            registration_hash=registration["registration_hash"],
        )
        return {
            "run_dir": str(run_dir),
            "registration": registration,
            "preregistration_manifest": manifest,
            "first_event": events[0],
            "idempotent_recovery": True,
        }
    if staging_dir.exists():
        registration, manifest, events = _verify_preregistration(staging_dir)
        if _trial_reservation_path(repo_root).exists():
            _verify_trial_reservation(
                repo_root,
                run_id=run_id,
                registration_hash=registration["registration_hash"],
            )
        else:
            _reserve_trial(
                repo_root,
                run_id=run_id,
                registration_hash=registration["registration_hash"],
                created_at=created_at,
            )
        run_dir.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staging_dir, run_dir)
        _verify_preregistration(run_dir, repo_root)
        return {
            "run_dir": str(run_dir),
            "registration": registration,
            "preregistration_manifest": manifest,
            "first_event": events[0],
            "idempotent_recovery": True,
        }

    # Every dependency and no-return structural row is checked before the sole
    # trial can be reserved or any final run directory becomes visible.
    contracts = _verify_fixed_contracts(repo_root)
    gate = _verify_gate_bundle(repo_root, gate_dir)
    price = _readiness_asset(
        repo_root, PRICE_READINESS_RELATIVE_PATH, "pit_observed_prices_v1"
    )
    factor = _readiness_asset(repo_root, FACTOR_READINESS_RELATIVE_PATH, "factor_panel_v1")
    structural_rows = list(
        _iter_jsonl_gz(gate_dir.resolve() / "eligibility_manifest.jsonl.gz")
    )
    exclusion_rows = list(
        _iter_jsonl_gz(gate_dir.resolve() / "exclusion_manifest.jsonl.gz")
    )
    _event_rows(structural_rows)
    _validate_exclusion_rows(exclusion_rows)
    price_path = _verify_advertised_data(repo_root, price["data"])
    factor_path = _verify_advertised_data(repo_root, factor["data"])
    _preflight_outcome_dependencies(price_path, factor_path)

    staging_dir.parent.mkdir(parents=True, exist_ok=True)
    staging_dir.mkdir(exist_ok=False)
    inputs = {
        "schema_version": "caerus_alpha_lab_hyp015_input_manifest_v1",
        "created_at": format_datetime(created_at),
        "gate": gate,
        "price": price,
        "factor": factor,
        "outcome_data_accessed": False,
        "challenge_accessed": False,
    }
    inputs["data_snapshot_hash"] = canonical_hash(inputs)
    input_path = staging_dir / "input_manifest.json"
    _exclusive_json(input_path, inputs)
    input_record = _record_for_final(
        repo_root, input_path, run_dir / "input_manifest.json"
    )
    evaluator_spec = _load_json(repo_root / EVALUATOR_SPEC_RELATIVE_PATH)
    variant_hash = canonical_hash(evaluator_spec["variant_definition"])
    search_census_hash = canonical_hash(evaluator_spec["internal_search_census"])
    registration = {
        "schema_version": "caerus_alpha_lab_hyp015_preoutcome_registration_v1",
        "created_at": format_datetime(created_at),
        "repository_commit": _git_commit(repo_root),
        "run_id": run_id,
        "hypothesis_id": HYPOTHESIS_ID,
        "experiment_id": EXPERIMENT_ID,
        "family_id": FAMILY_ID,
        "wave_id": WAVE_ID,
        "statistical_trial_id": TRIAL_ID,
        "ordered_wave_membership": [EXPERIMENT_ID],
        "trial_ordinal": 1,
        "maximum_family_trial_units": 1,
        "selection_trial_units": 0,
        "ordered_variant_census": [VARIANT_ID],
        "original_hypothesis": contracts["original_hypothesis"],
        "addendum": contracts["addendum"],
        "evaluator_spec": contracts["evaluator_spec"],
        "evaluator_code_manifest": contracts["evaluator_code_manifest"],
        "frozen_variant_definition_hash": variant_hash,
        "frozen_internal_search_census_hash": search_census_hash,
        "ready_no_return_data_gate": gate["gate_result"],
        "input_data_manifest": input_record,
        "exclusion_manifest": gate["exclusion_manifest"],
        "data_snapshot_hash": inputs["data_snapshot_hash"],
        "primary_metric": "mean_5_session_base_cost_net_peer_minus_industry_return",
        "expected_direction": "GREATER_THAN",
        "null_value": 0.0,
        "economic_hurdle": 0.005,
        "effective_sample_floors": {
            "independent_validation_units": 150,
            "unique_validation_peers": 100,
            "unique_validation_sic4": 20,
        },
        "holm_one_sided_alpha": 0.10,
        "benjamini_yekutieli_q": 0.10,
        "discovery_period": "2012-01-01/2018-12-31",
        "validation_period": "2019-01-01/2024-12-31",
        "challenge": {
            "epoch_id": CHALLENGE_EPOCH_ID,
            "period": CHALLENGE_PERIOD,
            "panel_sha256": CHALLENGE_PANEL_SHA256,
            "state": "SEALED_UNOPENED",
        },
        "governance_state": "LOCAL_PREREGISTRATION_PENDING_AUTHENTICATED_LEDGER_IMPORT",
        "outcome_data_accessed": False,
        "challenge_accessed": False,
        "orders_submitted": False,
        "trading_behavior_changed": False,
    }
    registration["registration_hash"] = canonical_hash(registration)
    registration_path = staging_dir / "preregistration.json"
    _exclusive_json(registration_path, registration)
    registration_record = _record_for_final(
        repo_root, registration_path, run_dir / "preregistration.json"
    )
    manifest = {
        "schema_version": "caerus_alpha_lab_hyp015_preregistration_manifest_v1",
        "classification": "RESEARCH_ONLY_NONEXECUTIONAL",
        "run_id": run_id,
        "files": [input_record, registration_record],
        "registration_hash": registration["registration_hash"],
        "manifest_written_last": True,
        "outcome_data_accessed": False,
        "challenge_accessed": False,
    }
    manifest["manifest_hash"] = canonical_hash(manifest)
    manifest_path = staging_dir / "preregistration_manifest.json"
    _exclusive_json(manifest_path, manifest)
    first_event = _append_event(
        staging_dir / "events.jsonl",
        event_id="{}-registration".format(run_id),
        event_type="preoutcome_registration_sealed",
        occurred_at=created_at,
        payload={
            "registration_hash": registration["registration_hash"],
            "preregistration_manifest_hash": manifest["manifest_hash"],
            "outcome_data_accessed": False,
            "challenge_accessed": False,
        },
    )
    _verify_preregistration(staging_dir)
    _reserve_trial(
        repo_root,
        run_id=run_id,
        registration_hash=registration["registration_hash"],
        created_at=created_at,
    )
    run_dir.parent.mkdir(parents=True, exist_ok=True)
    os.replace(staging_dir, run_dir)
    _verify_preregistration(run_dir, repo_root)
    return {
        "run_dir": str(run_dir),
        "registration": registration,
        "preregistration_manifest": manifest,
        "first_event": first_event,
        "idempotent_recovery": False,
    }


def _iter_jsonl_gz(path: Path) -> Iterator[Dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ContractValidationError(
                    "JSONL row {} must be an object".format(line_number)
                )
            yield value


def _event_rows(structural_rows: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    for raw in structural_rows:
        if _iso(raw.get("reaction_session")) >= CHALLENGE_START:
            raise ContractValidationError("challenge structural row is forbidden")
        reporters = raw.get("reporters")
        if not isinstance(reporters, list) or not reporters:
            raise ContractValidationError(
                "gate v2 structural row requires reporter objects"
            )
        peers = raw.get("included_peer_security_ids")
        controls = raw.get("industry_control_security_ids")
        if not isinstance(peers, list) or not isinstance(controls, list):
            raise ContractValidationError(
                "gate v2 row requires full pre-signal peer and industry-control pools"
            )
        if "peer_report_during_hold_security_ids" not in raw or not isinstance(
            raw["peer_report_during_hold_security_ids"], list
        ):
            raise ContractValidationError(
                "gate v2 row requires causal peer-report-during-hold diagnostics"
            )
        if raw.get("terminal_outcome_required") is not False:
            raise ContractValidationError(
                "gate v2 eligible rows must certify terminal_outcome_required=false"
            )
        sic4 = str(raw.get("four_digit_sic"))
        for reporter in reporters:
            accessions = reporter.get("accessions")
            if accessions is None and reporter.get("accession"):
                accessions = [reporter["accession"]]
            if not isinstance(accessions, list) or not accessions:
                raise ContractValidationError("reporter requires preserved accessions")
            for accession in accessions:
                events.append(
                    {
                        "reaction_session": raw["reaction_session"],
                        "entry_session": raw["entry_session"],
                        "exit_session": raw["exit_session"],
                        "sic4": sic4,
                        "sic2": sic4[:2],
                        "reporter_security_id": reporter["security_id"],
                        "reporter_cik": reporter["cik"],
                        "accession": accession,
                        "peer_security_ids": list(peers),
                        "industry_control_security_ids": list(controls),
                        "peer_report_during_hold_security_ids": list(
                            raw["peer_report_during_hold_security_ids"]
                        ),
                    }
                )
    return events


def _validate_exclusion_rows(rows: Iterable[Mapping[str, Any]]) -> None:
    for raw in rows:
        reaction_session = raw.get("reaction_session")
        if reaction_session is not None and _iso(reaction_session) >= CHALLENGE_START:
            raise ContractValidationError("challenge exclusion row is forbidden")
        if raw.get("adverse_sensitivity_eligible") not in (True, False):
            raise ContractValidationError(
                "exclusion row requires typed adverse_sensitivity_eligible"
            )
        if raw.get("adverse_sensitivity_eligible") is True and not raw.get(
            "potential_cluster_key"
        ):
            raise ContractValidationError(
                "adverse-eligible exclusion requires potential_cluster_key"
            )


def _iso(value: Any) -> str:
    text = str(value).split(" ", 1)[0]
    date.fromisoformat(text)
    return text


def _verify_advertised_data(repo_root: Path, record: Mapping[str, Any]) -> Path:
    path = (repo_root / str(record["path"])).resolve()
    path.relative_to(repo_root)
    if path.stat().st_size != int(record["bytes"]):
        raise ContractValidationError("outcome input size mismatch")
    if _sha256_file(path) != record["sha256"]:
        raise ContractValidationError("outcome input hash mismatch")
    return path


def _load_prices(
    path: Path, events: Sequence[Mapping[str, Any]]
) -> List[Dict[str, Any]]:
    try:
        import pyarrow.dataset as ds
    except ImportError as exc:
        raise RuntimeError("pyarrow is required for HYP-2026-015 evaluation") from exc
    intervals: Dict[str, List[tuple[date, date]]] = {}
    for event in events:
        security_ids = {
            str(event["reporter_security_id"]),
            *map(str, event["peer_security_ids"]),
            *map(str, event["industry_control_security_ids"]),
        }
        reaction = date.fromisoformat(str(event["reaction_session"]))
        interval_start = reaction.fromordinal(reaction.toordinal() - 50)
        interval_end = date.fromisoformat(str(event["exit_session"]))
        for security_id in security_ids:
            intervals.setdefault(security_id, []).append((interval_start, interval_end))
    dataset = ds.dataset(str(path), format="parquet")
    security_ids = sorted(intervals)
    if not security_ids:
        return []
    minimum_date = min(value[0] for values in intervals.values() for value in values)
    maximum_date = max(value[1] for values in intervals.values() for value in values)
    predicate = (
        ds.field("security_id").isin(security_ids)
        & (ds.field("date") >= minimum_date)
        & (ds.field("date") <= maximum_date)
        & (ds.field("date") < date(2025, 1, 1))
    )
    columns = [
        "security_id",
        "date",
        "open",
        "close",
        "closeadj",
        "volume",
        "dollar_ADV_20",
    ]
    rows: Dict[tuple[str, str], Dict[str, Any]] = {}
    for batch in dataset.scanner(columns=columns, filter=predicate).to_batches():
        values = batch.to_pydict()
        for index in range(batch.num_rows):
            security_id = str(values["security_id"][index])
            observed_date = values["date"][index]
            if not any(start <= observed_date <= end for start, end in intervals[security_id]):
                continue
            row = {column: values[column][index] for column in columns}
            row["date"] = observed_date.isoformat()
            rows[(security_id, row["date"])] = row
    return [rows[key] for key in sorted(rows)]


def _load_factors(path: Path) -> List[Dict[str, Any]]:
    columns = ["date", "MKT_RF", "SMB", "HML", "RMW", "CMA", "UMD"]
    rows: List[Dict[str, Any]] = []
    with path.open("rb") as stream:
        header = stream.readline().decode("utf-8").strip().split(",")
        positions = {name: header.index(name) for name in columns}
        prior_date = ""
        for raw_line in stream:
            date_bytes, separator, _ = raw_line.partition(b",")
            if not separator:
                raise ContractValidationError("factor row is malformed")
            observed_date = date_bytes.decode("ascii")
            if prior_date and observed_date < prior_date:
                raise ContractValidationError("factor input is not date ordered")
            if observed_date >= CHALLENGE_START:
                break
            prior_date = observed_date
            if observed_date < "2011-01-01":
                continue
            values = raw_line.decode("utf-8").rstrip("\r\n").split(",")
            if len(values) != len(header):
                raise ContractValidationError("factor row width mismatch")
            rows.append(
                {
                    column: (
                        observed_date
                        if column == "date"
                        else values[positions[column]]
                    )
                    for column in columns
                }
            )
    return rows


def _preflight_outcome_dependencies(price_path: Path, factor_path: Path) -> None:
    try:
        import pyarrow.parquet as parquet
    except ImportError as exc:
        raise RuntimeError("pyarrow is required before consuming the trial") from exc
    required_price = {
        "security_id",
        "date",
        "open",
        "close",
        "closeadj",
        "volume",
        "dollar_ADV_20",
    }
    schema_names = set(parquet.ParquetFile(price_path).schema_arrow.names)
    if not required_price.issubset(schema_names):
        raise ContractValidationError("price input schema is incomplete")
    with factor_path.open("r", encoding="utf-8") as stream:
        header = set(stream.readline().strip().split(","))
    required_factors = {"date", "MKT_RF", "SMB", "HML", "RMW", "CMA", "UMD"}
    if not required_factors.issubset(header):
        raise ContractValidationError("factor input schema is incomplete")


def _verify_preregistration(
    run_dir: Path, repo_root: Path | None = None
) -> tuple[Dict[str, Any], Dict[str, Any], List[Dict[str, Any]]]:
    registration = _load_json(run_dir / "preregistration.json")
    unsigned = {key: value for key, value in registration.items() if key != "registration_hash"}
    if canonical_hash(unsigned) != registration.get("registration_hash"):
        raise ContractValidationError("registration hash mismatch")
    manifest = _load_json(run_dir / "preregistration_manifest.json")
    unsigned_manifest = {key: value for key, value in manifest.items() if key != "manifest_hash"}
    if canonical_hash(unsigned_manifest) != manifest.get("manifest_hash"):
        raise ContractValidationError("preregistration manifest hash mismatch")
    if manifest.get("registration_hash") != registration["registration_hash"]:
        raise ContractValidationError("registration/manifest binding mismatch")
    for record in manifest.get("files", []):
        path = (
            repo_root / str(record["path"])
            if repo_root is not None
            else run_dir / Path(str(record["path"])).name
        )
        if not path.is_file() or path.stat().st_size != int(record["bytes"]):
            raise ContractValidationError("preregistration file size mismatch")
        if _sha256_file(path) != record["sha256"]:
            raise ContractValidationError("preregistration file hash mismatch")
    events = _read_events(run_dir / "events.jsonl")
    if not events or events[0]["event_type"] != "preoutcome_registration_sealed":
        raise EventStoreIntegrityError("preoutcome registration event is missing")
    if events[0]["payload"].get("registration_hash") != registration["registration_hash"]:
        raise EventStoreIntegrityError("registration event binding mismatch")
    return registration, manifest, events


def _verified_result(path: Path, hash_field: str) -> Dict[str, Any]:
    payload = _load_json(path)
    expected = payload.get(hash_field)
    unsigned = {key: value for key, value in payload.items() if key != hash_field}
    if not expected or canonical_hash(unsigned) != expected:
        raise ContractValidationError("{} hash mismatch".format(path.name))
    return payload


def _trial_outcome(result: Mapping[str, Any]) -> str:
    if result.get("primary_validation_pass") is True:
        return "POSITIVE"
    inference = result.get("primary_inference", {})
    if result.get("primary_metric_value") is None or inference.get("status") != "INFERENCE_ELIGIBLE":
        return "NOT_EVALUABLE"
    incomplete = (
        result.get("breadth", {}).get("pass") is not True
        or result.get("capacity", {}).get(
            "primary_pair_pass_for_every_validation_cluster"
        )
        is not True
        or result.get("concentration", {}).get("pass") is not True
        or result.get("adverse_missingness_sensitivity", {}).get("pass") is not True
        or result.get("factor_industry_momentum_attribution", {}).get("status")
        != "EVALUATED"
        or result.get("raw_momentum_comparison", {})
        .get("candidate_minus_raw_momentum_inference", {})
        .get("mean")
        is None
    )
    return "INCONCLUSIVE" if incomplete else "NEGATIVE"


def _build_trial_result(result: Mapping[str, Any]) -> Dict[str, Any]:
    inference = result["primary_inference"]
    trial_result = {
        "schema_version": "caerus_alpha_lab_hyp015_trial_result_v1",
        "statistical_trial_id": TRIAL_ID,
        "trial_delta": 1,
        "variant_id": VARIANT_ID,
        "outcome": _trial_outcome(result),
        "classification": "NON_DECISION_GRADE",
        "primary_metric": result["primary_metric_name"],
        "primary_metric_value": result["primary_metric_value"],
        "raw_one_sided_p_value": inference.get("raw_one_sided_p_value"),
        "holm_adjusted_p_value": inference.get("holm_adjusted_p_value"),
        "by_adjusted_p_value": inference.get("by_adjusted_p_value"),
        "one_sided_lcb_90": inference.get("one_sided_lcb_90"),
        "effective_sample_size": inference.get("effective_sample_size"),
        "challenge_accessed": False,
        "orders_submitted": False,
        "trading_behavior_changed": False,
        "source_result_hash": result["result_hash"],
    }
    trial_result["trial_result_hash"] = canonical_hash(trial_result)
    return trial_result


def _verify_terminal_bundle(
    repo_root: Path, run_dir: Path
) -> tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    manifest = _load_json(run_dir / "manifest.json")
    unsigned = {key: value for key, value in manifest.items() if key != "manifest_hash"}
    if canonical_hash(unsigned) != manifest.get("manifest_hash"):
        raise ContractValidationError("terminal manifest hash mismatch")
    for record in manifest.get("files", []):
        path = (repo_root / str(record["path"])).resolve()
        path.relative_to(repo_root)
        if not path.is_file() or path.stat().st_size != int(record["bytes"]):
            raise ContractValidationError("terminal file size mismatch")
        if _sha256_file(path) != record["sha256"]:
            raise ContractValidationError("terminal file hash mismatch")
    result = _verified_result(run_dir / "evaluator_result.json", "result_hash")
    trial_result = _verified_result(run_dir / "trial_result.json", "trial_result_hash")
    if manifest.get("result_hash") != result["result_hash"]:
        raise ContractValidationError("terminal result binding mismatch")
    if manifest.get("trial_result_hash") != trial_result["trial_result_hash"]:
        raise ContractValidationError("terminal trial binding mismatch")
    if trial_result.get("source_result_hash") != result["result_hash"]:
        raise ContractValidationError("trial/result binding mismatch")
    events = _read_events(run_dir / "events.jsonl")
    if not events or manifest.get("event_chain_head_hash") != events[-1]["event_hash"]:
        raise EventStoreIntegrityError("terminal event-chain head mismatch")
    return result, trial_result, manifest


def execute_preregistered(
    *, repo_root: Path, run_dir: Path, started_at: datetime
) -> Dict[str, Any]:
    repo_root = repo_root.resolve()
    run_dir = run_dir.resolve()
    run_dir.relative_to(repo_root / "outputs/research/alpha_lab" / HYPOTHESIS_ID)
    terminal_manifest = run_dir / "manifest.json"
    if terminal_manifest.exists():
        result, trial_result, manifest = _verify_terminal_bundle(repo_root, run_dir)
        return {
            "run_dir": str(run_dir),
            "result": result,
            "trial_result": trial_result,
            "manifest": manifest,
            "idempotent_recovery": True,
        }
    registration, _, events = _verify_preregistration(run_dir, repo_root)
    _verify_trial_reservation(
        repo_root,
        run_id=registration["run_id"],
        registration_hash=registration["registration_hash"],
    )
    event_types = [item["event_type"] for item in events]
    allowed_states = [
        ["preoutcome_registration_sealed"],
        ["preoutcome_registration_sealed", "outcome_access_started"],
        [
            "preoutcome_registration_sealed",
            "outcome_access_started",
            "validation_evaluation_completed",
        ],
        [
            "preoutcome_registration_sealed",
            "outcome_access_started",
            "validation_evaluation_completed",
            "statistical_trial_closed",
        ],
    ]
    if event_types not in allowed_states:
        raise EventStoreIntegrityError("run cannot be recovered from its current event state")

    inputs = _load_json(run_dir / "input_manifest.json")
    gate_dir = (repo_root / inputs["gate"]["gate_manifest"]["path"]).parent
    structural_rows = list(_iter_jsonl_gz(gate_dir / "eligibility_manifest.jsonl.gz"))
    exclusions = list(_iter_jsonl_gz(gate_dir / "exclusion_manifest.jsonl.gz"))
    event_rows = _event_rows(structural_rows)
    _validate_exclusion_rows(exclusions)
    price_path = _verify_advertised_data(repo_root, inputs["price"]["data"])
    factor_path = _verify_advertised_data(repo_root, inputs["factor"]["data"])
    _preflight_outcome_dependencies(price_path, factor_path)

    if event_types == ["preoutcome_registration_sealed"]:
        _append_event(
            run_dir / "events.jsonl",
            event_id="{}-outcome-access".format(registration["run_id"]),
            event_type="outcome_access_started",
            occurred_at=started_at,
            payload={
                "statistical_trial_id": TRIAL_ID,
                "registration_hash": registration["registration_hash"],
                "trial_delta": 1,
                "challenge_accessed": False,
            },
        )
        event_types.append("outcome_access_started")

    result_path = run_dir / "evaluator_result.json"
    if result_path.exists():
        result = _verified_result(result_path, "result_hash")
    else:
        if "validation_evaluation_completed" in event_types:
            raise EventStoreIntegrityError("validation event exists without its result")
        prices = _load_prices(price_path, event_rows)
        factors = _load_factors(factor_path)
        result = evaluate_primary_v1(
            event_rows,
            prices,
            factors,
            excluded_potential_clusters=exclusions,
        )
        result.update(
            {
                "classification": "NON_DECISION_GRADE",
                "governance_state": "LOCAL_PREREGISTRATION_PENDING_AUTHENTICATED_LEDGER_IMPORT",
                "registration_hash": registration["registration_hash"],
                "statistical_trial_id": TRIAL_ID,
            }
        )
        result["result_hash"] = canonical_hash(result)
        _exclusive_json(result_path, result)
    if "validation_evaluation_completed" not in event_types:
        _append_event(
            run_dir / "events.jsonl",
            event_id="{}-validation-complete".format(registration["run_id"]),
            event_type="validation_evaluation_completed",
            occurred_at=datetime.now(timezone.utc),
            payload={
                "evaluator_result_hash": result["result_hash"],
                "challenge_accessed": False,
            },
        )
        event_types.append("validation_evaluation_completed")

    trial_path = run_dir / "trial_result.json"
    if trial_path.exists():
        trial_result = _verified_result(trial_path, "trial_result_hash")
        if trial_result.get("source_result_hash") != result["result_hash"]:
            raise ContractValidationError("trial result is bound to another result")
    else:
        if "statistical_trial_closed" in event_types:
            raise EventStoreIntegrityError("trial-close event exists without its result")
        trial_result = _build_trial_result(result)
        _exclusive_json(trial_path, trial_result)
    if "statistical_trial_closed" not in event_types:
        _append_event(
            run_dir / "events.jsonl",
            event_id="{}-trial-closed".format(registration["run_id"]),
            event_type="statistical_trial_closed",
            occurred_at=datetime.now(timezone.utc),
            payload={
                "trial_result_hash": trial_result["trial_result_hash"],
                "trial_delta": 1,
                "raw_one_sided_p_value": trial_result["raw_one_sided_p_value"],
                "holm_adjusted_p_value": trial_result["holm_adjusted_p_value"],
                "by_adjusted_p_value": trial_result["by_adjusted_p_value"],
                "challenge_accessed": False,
            },
        )
    event_path = run_dir / "events.jsonl"
    files = [
        _relative_record(repo_root, path)
        for path in (
            run_dir / "input_manifest.json",
            run_dir / "preregistration.json",
            run_dir / "preregistration_manifest.json",
            event_path,
            result_path,
            trial_path,
        )
    ]
    manifest = {
        "schema_version": "caerus_alpha_lab_hyp015_outcome_bundle_v1",
        "classification": "NON_DECISION_GRADE",
        "run_id": registration["run_id"],
        "files": files,
        "registration_hash": registration["registration_hash"],
        "result_hash": result["result_hash"],
        "trial_result_hash": trial_result["trial_result_hash"],
        "event_chain_head_hash": _read_events(event_path)[-1]["event_hash"],
        "manifest_written_last": True,
        "challenge_accessed": False,
        "orders_submitted": False,
        "trading_behavior_changed": False,
    }
    manifest["manifest_hash"] = canonical_hash(manifest)
    _exclusive_json(terminal_manifest, manifest)
    _verify_terminal_bundle(repo_root, run_dir)
    return {
        "run_dir": str(run_dir),
        "result": result,
        "trial_result": trial_result,
        "manifest": manifest,
        "idempotent_recovery": False,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("preregister", "run"))
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--gate-dir", type=Path)
    parser.add_argument("--run-id")
    parser.add_argument("--run-dir", type=Path)
    parser.add_argument("--timestamp")
    return parser.parse_args()


def main() -> int:
    arguments = _parse_args()
    timestamp = (
        parse_datetime(arguments.timestamp)
        if arguments.timestamp
        else datetime.now(timezone.utc)
    )
    if arguments.command == "preregister":
        if arguments.gate_dir is None or not arguments.run_id:
            raise SystemExit("preregister requires --gate-dir and --run-id")
        payload = seal_preregistration(
            repo_root=arguments.repo_root,
            gate_dir=arguments.gate_dir,
            run_id=arguments.run_id,
            created_at=timestamp,
        )
    else:
        if arguments.run_dir is None:
            raise SystemExit("run requires --run-dir")
        payload = execute_preregistered(
            repo_root=arguments.repo_root,
            run_dir=arguments.run_dir,
            started_at=timestamp,
        )
    print(canonical_json(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
