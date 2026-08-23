"""CLI for Alpha Lab candidate assessment and owner-review queue generation."""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from projects.alpha_lab.factory.canonical import canonical_hash, canonical_json, parse_datetime
from projects.alpha_lab.factory.errors import ContractValidationError, ResearchBoundaryError
from projects.alpha_lab.factory.research_ledger import (
    GlobalResearchLedger,
    HoldoutAccess,
    ResearchRunClass,
)

from .authenticated_ledger import (
    load_event_attestations,
    open_authenticated_global_ledger,
    require_event_attestation,
    strict_load_json_object_bytes,
    strict_load_json_object,
)
from .evaluator import EvaluationPhase, load_spec, run_evaluator
from .evaluator_recovery import reconcile_finalized_evaluator_bundle
from .lifecycle import (
    assess_candidate,
    build_cio_queue,
    project_candidate_research_state,
    render_queue_markdown,
)
from .models import CandidateSnapshot, REQUIRED_RESEARCH_GATES_V2


def _load_json(path: Path) -> Dict[str, Any]:
    return strict_load_json_object(path)


def _seal_candidate(
    value: Dict[str, Any],
    *,
    research_ledger: Optional[GlobalResearchLedger] = None,
) -> Dict[str, Any]:
    if "source_snapshot_hash" in value:
        raise ContractValidationError("draft already contains source_snapshot_hash")
    sealed = dict(value)
    if sealed.get("research_verdict") == "EVIDENCE_READY_FOR_OWNER_REVIEW":
        if research_ledger is None:
            raise ContractValidationError(
                "owner-review candidate sealing requires the canonical research ledger"
            )
        research_projection, challenge_evidence_bindings = project_candidate_research_state(
            research_ledger
        )
        family_id = sealed.get("family_id")
        rows = [
            item
            for item in research_projection.get("families", [])
            if item.get("family_id") == family_id
        ]
        lineage_binding = (
            family_id,
            sealed.get("hypothesis_id"),
            sealed.get("experiment_id"),
        )
        if (
            len(rows) != 1
            or rows[0].get("decision_grade_ready") is not True
            or sealed.get("hypothesis_id")
            not in set(rows[0].get("hypothesis_ids", []))
            or lineage_binding not in challenge_evidence_bindings
        ):
            raise ContractValidationError(
                "candidate lineage is not decision-grade in the canonical ledger"
            )
        sealed["schema_version"] = "caerus_alpha_lab_candidate_snapshot_v2"
        ledger_gates = dict(rows[0].get("research_gates", {}))
        if set(ledger_gates) != set(REQUIRED_RESEARCH_GATES_V2) or not all(
            ledger_gates.values()
        ):
            raise ContractValidationError(
                "canonical ledger projection is missing mandatory decision-grade gates"
            )
        sealed["research_gates"] = ledger_gates
        sealed["ledger_event_chain_head"] = research_projection["event_chain_head"]
        sealed["ledger_projection_hash"] = canonical_hash(research_projection)
    sealed["source_snapshot_hash"] = canonical_hash(value)
    if sealed != value:
        unsigned = dict(sealed)
        unsigned.pop("source_snapshot_hash", None)
        sealed["source_snapshot_hash"] = canonical_hash(unsigned)
    CandidateSnapshot.from_dict(sealed)
    return sealed


def _load_candidate(path: Path) -> CandidateSnapshot:
    return CandidateSnapshot.from_dict(_load_json(path))


def _authoritative_root(repo_root: Path) -> Path:
    policy_path = repo_root / "projects/alpha_lab/gcp_storage_policy.json"
    policy = _load_json(policy_path)
    expected = Path(policy["gcp"]["repository_root"]).resolve()
    actual = repo_root.expanduser().resolve()
    if actual != expected:
        raise ResearchBoundaryError(
            "control-plane writes require the authoritative GCP repository root"
        )
    data_root = Path(policy["gcp"]["authoritative_data_root"]).resolve()
    if not data_root.is_dir():
        raise ResearchBoundaryError("authoritative Alpha Lab data root is unavailable")
    return data_root


def _canonical_ledger(
    repo_root: Path,
    ledger_path: Optional[Path],
    *,
    identity_bundle: Optional[Path],
    identity_registry_pin: Optional[str],
    identity_trust_anchor: Optional[Path],
) -> GlobalResearchLedger:
    if ledger_path is None:
        raise ContractValidationError("--ledger is required for decision-grade lifecycle work")
    data_root = _authoritative_root(repo_root)
    expected = (data_root / "ledger/research_events.v1.jsonl").resolve()
    actual = ledger_path.expanduser().resolve()
    if actual != expected:
        raise ResearchBoundaryError("lifecycle requires the canonical global research ledger")
    return open_authenticated_global_ledger(
        ledger_path=actual,
        research_root=data_root,
        identity_bundle=identity_bundle,
        identity_registry_pin=identity_registry_pin,
        identity_trust_anchor=identity_trust_anchor,
    )


def _verify_registered_evaluator_contract(
    runs: Sequence[Dict[str, Any]], spec: Any
) -> None:
    actual_variants = [
        {
            "variant_id": run.get("variant_id"),
            "variant_definition_hash": run.get("variant_definition_hash"),
        }
        for run in runs
    ]
    if actual_variants != spec.frozen_variant_dicts:
        raise ContractValidationError(
            "registered trials differ from the frozen ordered variant contract"
        )
    selection_units = sum(int(run.get("selection_trial_units", 0)) for run in runs)
    if selection_units != spec.selection_trial_units:
        raise ContractValidationError(
            "registered selection units differ from the frozen search census"
        )


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _write_bundle(
    *, repo_root: Path, namespace: str, payloads: Dict[str, bytes], generated_at: datetime
) -> Path:
    data_root = _authoritative_root(repo_root)
    control_root = data_root / "control_plane"
    staging_root = control_root / ".staging"
    staging_root.mkdir(parents=True, exist_ok=True)
    content_hash = canonical_hash(
        {name: _sha256_bytes(content) for name, content in sorted(payloads.items())}
    )
    timestamp = generated_at.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    bundle_id = "{}-{}".format(timestamp, content_hash[:12])
    final_dir = control_root / namespace / generated_at.date().isoformat() / bundle_id
    if final_dir.exists():
        raise FileExistsError("finalized control-plane bundle already exists")
    stage_dir = staging_root / bundle_id
    if stage_dir.exists():
        raise FileExistsError("control-plane staging bundle already exists")
    stage_dir.mkdir(parents=False)
    try:
        files = []
        for name, content in sorted(payloads.items()):
            path = stage_dir / name
            path.write_bytes(content)
            files.append({"name": name, "bytes": len(content), "sha256": _sha256_bytes(content)})
        manifest = {
            "schema_version": "caerus_alpha_lab_control_plane_bundle_v1",
            "classification": "RESEARCH_ONLY_NON_EXECUTIONAL",
            "bundle_id": bundle_id,
            "retrieved_at": generated_at,
            "source_id": "alpha_lab.control_plane",
            "files": files,
            "credentials_persisted": False,
            "trading_behavior_changed": False,
            "promotion_performed": False,
            "purchase_performed": False,
        }
        (stage_dir / "manifest.json").write_text(canonical_json(manifest) + "\n", encoding="utf-8")
        final_dir.parent.mkdir(parents=True, exist_ok=True)
        os.replace(stage_dir, final_dir)
    except Exception:
        if stage_dir.exists():
            shutil.rmtree(stage_dir)
        raise
    return final_dir


def _candidate_paths(explicit: Iterable[Path], candidate_dir: Optional[Path]) -> List[Path]:
    paths = {path.expanduser().resolve() for path in explicit}
    if candidate_dir is not None:
        paths.update(candidate_dir.expanduser().resolve().glob("**/candidate_snapshot*.json"))
    if not paths:
        raise ContractValidationError("at least one candidate snapshot is required")
    return sorted(paths)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_identity_arguments(command: argparse.ArgumentParser) -> None:
        command.add_argument("--identity-bundle", type=Path)
        command.add_argument("--identity-trust-anchor", type=Path)
        command.add_argument("--identity-registry-pin")

    def add_event_attestation_arguments(command: argparse.ArgumentParser) -> None:
        command.add_argument("--event-attestation", action="append", type=Path, default=[])

    seal = subparsers.add_parser("seal-candidate", help="hash and validate a candidate draft")
    seal.add_argument("--draft", type=Path, required=True)
    seal.add_argument("--write", action="store_true")
    seal.add_argument("--repo-root", type=Path, default=Path.cwd())
    seal.add_argument("--ledger", type=Path)
    seal.add_argument("--at")
    add_identity_arguments(seal)

    assess = subparsers.add_parser("assess", help="assess one immutable candidate snapshot")
    assess.add_argument("--candidate", type=Path, required=True)
    assess.add_argument("--repo-root", type=Path, default=Path.cwd())
    assess.add_argument("--ledger", type=Path)
    assess.add_argument("--at")
    add_identity_arguments(assess)

    queue = subparsers.add_parser("build-queue", help="build the CIO decision queue")
    queue.add_argument("--candidate", action="append", type=Path, default=[])
    queue.add_argument("--candidate-dir", type=Path)
    queue.add_argument("--at")
    queue.add_argument("--write", action="store_true")
    queue.add_argument("--repo-root", type=Path, default=Path.cwd())
    queue.add_argument("--ledger", type=Path)
    add_identity_arguments(queue)

    evaluator = subparsers.add_parser("run-evaluator", help="run a frozen research adapter")
    evaluator.add_argument("--spec", type=Path, required=True)
    evaluator.add_argument("--input", type=Path, required=True)
    evaluator.add_argument("--phase", choices=[item.value for item in EvaluationPhase], required=True)
    evaluator.add_argument("--trial-id", action="append", required=True)
    evaluator.add_argument("--ledger", type=Path, required=True)
    evaluator.add_argument("--challenge-access", type=Path)
    evaluator.add_argument("--write", action="store_true")
    evaluator.add_argument("--repo-root", type=Path, default=Path.cwd())
    evaluator.add_argument("--at")
    add_identity_arguments(evaluator)
    add_event_attestation_arguments(evaluator)

    recovery = subparsers.add_parser(
        "reconcile-evaluator-bundle",
        help="idempotently close trials from a finalized evaluator bundle",
    )
    recovery.add_argument("--bundle", type=Path, required=True)
    recovery.add_argument("--spec", type=Path, required=True)
    recovery.add_argument("--ledger", type=Path, required=True)
    recovery.add_argument("--repo-root", type=Path, default=Path.cwd())
    recovery.add_argument("--at")
    add_identity_arguments(recovery)
    add_event_attestation_arguments(recovery)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    now = parse_datetime(args.at) if getattr(args, "at", None) else datetime.now(timezone.utc)

    if args.command == "seal-candidate":
        draft = _load_json(args.draft)
        research_ledger = (
            _canonical_ledger(
                args.repo_root.expanduser().resolve(),
                args.ledger,
                identity_bundle=args.identity_bundle,
                identity_registry_pin=args.identity_registry_pin,
                identity_trust_anchor=args.identity_trust_anchor,
            )
            if draft.get("research_verdict") == "EVIDENCE_READY_FOR_OWNER_REVIEW"
            else None
        )
        sealed = _seal_candidate(draft, research_ledger=research_ledger)
        response: Dict[str, Any] = {"candidate": sealed}
        if args.write:
            final_dir = _write_bundle(
                repo_root=args.repo_root.expanduser().resolve(),
                namespace="candidate_snapshots/{}".format(sealed["hypothesis_id"]),
                payloads={
                    "candidate_snapshot.json": (canonical_json(sealed) + "\n").encode("utf-8")
                },
                generated_at=now,
            )
            response["bundle_dir"] = str(final_dir)
        print(canonical_json(response))
        return 0
    if args.command == "assess":
        candidate = _load_candidate(args.candidate)
        research_ledger = (
            _canonical_ledger(
                args.repo_root.expanduser().resolve(),
                args.ledger,
                identity_bundle=args.identity_bundle,
                identity_registry_pin=args.identity_registry_pin,
                identity_trust_anchor=args.identity_trust_anchor,
            )
            if candidate.research_verdict.value == "EVIDENCE_READY_FOR_OWNER_REVIEW"
            else None
        )
        result = assess_candidate(
            candidate, assessed_at=now, research_ledger=research_ledger
        )
        print(canonical_json(result.to_dict()))
        return 0
    if args.command == "build-queue":
        paths = _candidate_paths(args.candidate, args.candidate_dir)
        candidates = [_load_candidate(path) for path in paths]
        research_ledger = (
            _canonical_ledger(
                args.repo_root.expanduser().resolve(),
                args.ledger,
                identity_bundle=args.identity_bundle,
                identity_registry_pin=args.identity_registry_pin,
                identity_trust_anchor=args.identity_trust_anchor,
            )
            if any(
                item.research_verdict.value == "EVIDENCE_READY_FOR_OWNER_REVIEW"
                for item in candidates
            )
            else None
        )
        queue = build_cio_queue(
            candidates, generated_at=now, research_ledger=research_ledger
        )
        markdown = render_queue_markdown(queue)
        response: Dict[str, Any] = {"queue": queue, "markdown": markdown}
        if args.write:
            final_dir = _write_bundle(
                repo_root=args.repo_root.expanduser().resolve(),
                namespace="cio_queue",
                payloads={
                    "queue.json": (canonical_json(queue) + "\n").encode("utf-8"),
                    "queue.md": markdown.encode("utf-8"),
                },
                generated_at=now,
            )
            response["bundle_dir"] = str(final_dir)
        print(canonical_json(response))
        return 0
    if args.command == "reconcile-evaluator-bundle":
        repo_root = args.repo_root.expanduser().resolve()
        ledger = _canonical_ledger(
            repo_root,
            args.ledger,
            identity_bundle=args.identity_bundle,
            identity_registry_pin=args.identity_registry_pin,
            identity_trust_anchor=args.identity_trust_anchor,
        )
        event_attestations = load_event_attestations(args.event_attestation)
        result = reconcile_finalized_evaluator_bundle(
            bundle_dir=args.bundle,
            ledger=ledger,
            spec=load_spec(args.spec),
            recorded_at=now,
            event_attestations=event_attestations,
        )
        print(canonical_json(result))
        return 0
    if args.command == "run-evaluator":
        if not args.write:
            raise ContractValidationError(
                "outcome-bearing evaluator runs must persist their result bundle"
            )
        repo_root = args.repo_root.expanduser().resolve()
        ledger = _canonical_ledger(
            repo_root,
            args.ledger,
            identity_bundle=args.identity_bundle,
            identity_registry_pin=args.identity_registry_pin,
            identity_trust_anchor=args.identity_trust_anchor,
        )
        event_attestations = load_event_attestations(args.event_attestation)
        phase = EvaluationPhase(args.phase)
        trial_ids = tuple(args.trial_id)
        spec = load_spec(args.spec)
        for trial_id in trial_ids:
            require_event_attestation(
                event_attestations,
                "result:{}".format(trial_id),
                ledger=ledger,
            )
        def require_frozen_policy(runs: Sequence[Dict[str, Any]]) -> None:
            records = ledger.store.read_all()
            family = next(
                (
                    item.payload
                    for item in records
                    if item.event_type == ledger.FAMILY_EVENT
                    and item.payload["family_id"] == spec.family_id
                ),
                None,
            )
            wave = next(
                (
                    item.payload
                    for item in records
                    if item.event_type == ledger.WAVE_EVENT
                    and item.payload["wave_id"] == spec.exploratory_wave_id
                ),
                None,
            )
            if family is None or wave is None:
                raise ContractValidationError("evaluator family policy is not registered")
            expected_policy = {
                "wave_id": spec.exploratory_wave_id,
                "challenge_epoch_id": spec.challenge_epoch_id,
                "expected_direction": spec.expected_direction,
                "null_value": float(spec.null_value),
                "economic_hurdle": float(spec.economic_hurdle),
                "within_family_method": spec.inference_method,
                "family_alpha": float(spec.inference_alpha_or_q),
            }
            if any(family.get(key) != value for key, value in expected_policy.items()):
                raise ContractValidationError(
                    "evaluator spec differs from the registered family policy"
                )
            if spec.family_id not in wave["family_ids"]:
                raise ContractValidationError("evaluator family is not in the frozen wave")
        receipt = None
        challenge_input_sha256 = None
        if phase is EvaluationPhase.CHALLENGE:
            if args.challenge_access is None:
                raise ContractValidationError(
                    "challenge requires a ledger-backed access artifact"
                )
            registered_runs = ledger.require_registered_trials(
                trial_ids, run_class=ResearchRunClass.CHALLENGE_READ
            )
            require_frozen_policy(registered_runs)
            access_payload = _load_json(args.challenge_access)
            declared_accessed_at = access_payload.get("accessed_at")
            if declared_accessed_at is not None and parse_datetime(
                str(declared_accessed_at)
            ) != now:
                raise ContractValidationError(
                    "challenge accessed_at must equal the actual pre-read time"
                )
            access = HoldoutAccess(
                access_id=access_payload["access_id"],
                challenge_epoch_id=access_payload["challenge_epoch_id"],
                trial_ids=tuple(access_payload["trial_ids"]),
                input_sha256_by_trial=dict(
                    access_payload["input_sha256_by_trial"]
                ),
                accessed_at=now,
                consumer=access_payload["consumer"],
                purpose=access_payload["purpose"],
                schema_version=access_payload.get(
                    "schema_version", "caerus_alpha_lab_challenge_access_v1"
                ),
            )
            if access.challenge_epoch_id != spec.challenge_epoch_id:
                raise ContractValidationError(
                    "challenge access is for a different frozen evaluator epoch"
                )
            for run in registered_runs:
                pre_access_bindings = {
                    "family_id": spec.family_id,
                    "hypothesis_id": spec.hypothesis_id,
                    "experiment_id": spec.experiment_id,
                    "primary_metric": spec.primary_metric,
                    "evaluator_spec_sha256": spec.spec_hash,
                    "code_sha256": spec.evaluator_code_sha256,
                    "effective_sample_floor": spec.effective_sample_floor,
                    "data_snapshot_sha256": access.input_sha256_by_trial.get(
                        run["statistical_trial_id"]
                    ),
                }
                if any(
                    run.get(key) != value
                    for key, value in pre_access_bindings.items()
                ):
                    raise ContractValidationError(
                        "challenge trial is not bound to this frozen evaluator generation"
                    )
            prior_access = next(
                (
                    item
                    for item in ledger.store.read_all()
                    if item.event_type == ledger.HOLDOUT_EVENT
                    and item.payload["challenge_epoch_id"]
                    == access.challenge_epoch_id
                ),
                None,
            )
            if prior_access is not None:
                raise ContractValidationError(
                    "single-use challenge epoch has already been consumed"
                )
            if len(trial_ids) != 1:
                raise ContractValidationError(
                    "each challenge evaluator run must name one frozen champion"
                )
            epoch = next(
                (
                    item.payload
                    for item in ledger.store.read_all()
                    if item.event_type == ledger.CHALLENGE_EPOCH_EVENT
                    and item.payload["challenge_epoch_id"]
                    == access.challenge_epoch_id
                ),
                None,
            )
            if epoch is None or len(epoch["trial_ids"]) != 1:
                raise ContractValidationError(
                    "multi-family challenge epochs require atomic batch execution"
                )
            receipt_event = ledger.record_holdout_access(
                access,
                recorded_at=now,
                event_attestation=require_event_attestation(
                    event_attestations,
                    "challenge-access:{}".format(access.access_id),
                    ledger=ledger,
                ),
            )
            receipt = receipt_event
            input_bytes = args.input.read_bytes()
            challenge_input_sha256 = _sha256_bytes(input_bytes)
            expected_sha = receipt_event.payload["input_sha256_by_trial"].get(
                trial_ids[0]
            )
            if challenge_input_sha256 != expected_sha:
                raise ContractValidationError(
                    "challenge input differs from the consumed epoch binding"
                )
            input_packet = strict_load_json_object_bytes(
                input_bytes, source=str(args.input.expanduser().resolve())
            )
        else:
            if args.challenge_access is not None:
                raise ContractValidationError(
                    "discovery cannot carry a challenge access artifact"
                )
            registered_runs = ledger.require_registered_trials(
                trial_ids, run_class=ResearchRunClass.MODEL_TRIAL
            )
            require_frozen_policy(registered_runs)
            input_packet = _load_json(args.input)
        expected_input_hash = (
            _sha256_bytes(args.input.read_bytes())
            if phase is EvaluationPhase.CHALLENGE
            else canonical_hash(input_packet)
        )
        for run in registered_runs:
            expected_bindings = {
                "family_id": spec.family_id,
                "hypothesis_id": spec.hypothesis_id,
                "experiment_id": spec.experiment_id,
                "primary_metric": spec.primary_metric,
                "evaluator_spec_sha256": spec.spec_hash,
                "code_sha256": spec.evaluator_code_sha256,
                "effective_sample_floor": spec.effective_sample_floor,
                "data_snapshot_sha256": expected_input_hash,
            }
            if any(run.get(key) != value for key, value in expected_bindings.items()):
                raise ContractValidationError(
                    "registered trial is not bound to this frozen evaluator generation"
                )
        _verify_registered_evaluator_contract(registered_runs, spec)
        result = run_evaluator(
            spec=spec,
            input_packet=input_packet,
            phase=phase,
            registered_trial_ids=trial_ids,
            challenge_access_receipt=receipt,
            challenge_ledger=ledger if receipt is not None else None,
            challenge_input_sha256=challenge_input_sha256,
        )
        if [item["variant_id"] for item in registered_runs] != [
            item["variant_id"] for item in result["result"]["variants"]
        ]:
            raise ContractValidationError(
                "evaluator variants differ from registered trial definitions"
            )
        response = {"result": result}
        final_dir = _write_bundle(
            repo_root=repo_root,
            namespace="evaluator_runs/{}".format(result["hypothesis_id"]),
            payloads={"result.json": (canonical_json(result) + "\n").encode("utf-8")},
            generated_at=now,
        )
        response["bundle_dir"] = str(final_dir)
        response["ledger_reconciliation"] = reconcile_finalized_evaluator_bundle(
            bundle_dir=final_dir,
            ledger=ledger,
            spec=spec,
            recorded_at=now,
            event_attestations=event_attestations,
        )
        print(canonical_json(response))
        return 0
    raise AssertionError("unreachable command")


if __name__ == "__main__":
    raise SystemExit(main())
