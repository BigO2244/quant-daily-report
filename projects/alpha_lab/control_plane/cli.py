"""CLI for Alpha Lab candidate assessment and owner-review queue generation."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from projects.alpha_lab.factory.canonical import canonical_hash, canonical_json, parse_datetime
from projects.alpha_lab.factory.errors import ContractValidationError, ResearchBoundaryError

from .evaluator import EvaluationPhase, load_spec, run_evaluator
from .lifecycle import assess_candidate, build_cio_queue, render_queue_markdown
from .models import CandidateSnapshot


def _load_json(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ContractValidationError("JSON artifact must contain an object")
    return value


def _seal_candidate(value: Dict[str, Any]) -> Dict[str, Any]:
    if "source_snapshot_hash" in value:
        raise ContractValidationError("draft already contains source_snapshot_hash")
    sealed = dict(value)
    sealed["source_snapshot_hash"] = canonical_hash(value)
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

    seal = subparsers.add_parser("seal-candidate", help="hash and validate a candidate draft")
    seal.add_argument("--draft", type=Path, required=True)
    seal.add_argument("--write", action="store_true")
    seal.add_argument("--repo-root", type=Path, default=Path.cwd())
    seal.add_argument("--at")

    assess = subparsers.add_parser("assess", help="assess one immutable candidate snapshot")
    assess.add_argument("--candidate", type=Path, required=True)
    assess.add_argument("--at")

    queue = subparsers.add_parser("build-queue", help="build the CIO decision queue")
    queue.add_argument("--candidate", action="append", type=Path, default=[])
    queue.add_argument("--candidate-dir", type=Path)
    queue.add_argument("--at")
    queue.add_argument("--write", action="store_true")
    queue.add_argument("--repo-root", type=Path, default=Path.cwd())

    evaluator = subparsers.add_parser("run-evaluator", help="run a frozen research adapter")
    evaluator.add_argument("--spec", type=Path, required=True)
    evaluator.add_argument("--input", type=Path, required=True)
    evaluator.add_argument("--phase", choices=[item.value for item in EvaluationPhase], required=True)
    evaluator.add_argument("--authorize-challenge-access", action="store_true")
    evaluator.add_argument("--write", action="store_true")
    evaluator.add_argument("--repo-root", type=Path, default=Path.cwd())
    evaluator.add_argument("--at")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    now = parse_datetime(args.at) if getattr(args, "at", None) else datetime.now(timezone.utc)

    if args.command == "seal-candidate":
        sealed = _seal_candidate(_load_json(args.draft))
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
        result = assess_candidate(_load_candidate(args.candidate), assessed_at=now)
        print(canonical_json(result.to_dict()))
        return 0
    if args.command == "build-queue":
        paths = _candidate_paths(args.candidate, args.candidate_dir)
        queue = build_cio_queue((_load_candidate(path) for path in paths), generated_at=now)
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
    if args.command == "run-evaluator":
        result = run_evaluator(
            spec=load_spec(args.spec),
            input_packet=_load_json(args.input),
            phase=EvaluationPhase(args.phase),
            challenge_access_authorized=args.authorize_challenge_access,
        )
        response = {"result": result}
        if args.write:
            final_dir = _write_bundle(
                repo_root=args.repo_root.expanduser().resolve(),
                namespace="evaluator_runs/{}".format(result["hypothesis_id"]),
                payloads={"result.json": (canonical_json(result) + "\n").encode("utf-8")},
                generated_at=now,
            )
            response["bundle_dir"] = str(final_dir)
        print(canonical_json(response))
        return 0
    raise AssertionError("unreachable command")


if __name__ == "__main__":
    raise SystemExit(main())
