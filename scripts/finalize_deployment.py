#!/usr/bin/env python3
"""Validate and atomically attest the exact source revision deployed on the VM.

This is the only supported writer for ``outputs/deploy_state.json`` in the
deployment workflow.  It deliberately records the marker *after* validation,
and it verifies the repository both before and after validation so a moving or
dirty checkout can never be certified accidentally.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.live_pilot_sha_guard import write_deploy_state  # noqa: E402


class DeploymentAttestationError(RuntimeError):
    """The candidate revision cannot be safely recorded as deployed."""


def _git(repo_root: Path, *args: str) -> str:
    try:
        return subprocess.check_output(
            ["git", *args],
            cwd=str(repo_root),
            text=True,
            stderr=subprocess.STDOUT,
        ).strip()
    except subprocess.CalledProcessError as exc:
        raise DeploymentAttestationError(
            f"git {' '.join(args)} failed: {exc.output.strip()}"
        ) from exc


def _resolve_commit(repo_root: Path, revision: str) -> str:
    return _git(repo_root, "rev-parse", "--verify", f"{revision}^{{commit}}")


def _verify_candidate(
    repo_root: Path,
    *,
    expected_sha: str,
    expected_branch: str,
    source_ref: str,
) -> str:
    head = _resolve_commit(repo_root, "HEAD")
    expected = _resolve_commit(repo_root, expected_sha)
    source = _resolve_commit(repo_root, source_ref)
    branch = _git(repo_root, "branch", "--show-current")
    status = _git(repo_root, "status", "--porcelain", "--untracked-files=all")

    if head != expected:
        raise DeploymentAttestationError(
            f"HEAD changed: expected {expected}, found {head}"
        )
    if head != source:
        raise DeploymentAttestationError(
            f"HEAD {head} does not match {source_ref} {source}"
        )
    if expected_branch and branch != expected_branch:
        raise DeploymentAttestationError(
            f"branch mismatch: expected {expected_branch}, found {branch or 'DETACHED'}"
        )
    if status:
        raise DeploymentAttestationError(
            "working tree is not clean; deployment cannot be attested:\n" + status
        )
    return head


def finalize_deployment(
    *,
    repo_root: Path,
    expected_sha: str,
    expected_branch: str = "main",
    source_ref: str = "origin/main",
    deploy_state_path: Path | None = None,
    validation_script: Path | None = None,
) -> Path:
    repo_root = repo_root.resolve()
    deploy_state_path = deploy_state_path or repo_root / "outputs" / "deploy_state.json"
    validation_script = validation_script or repo_root / "scripts" / "ops" / "run_vm_validation.sh"

    candidate = _verify_candidate(
        repo_root,
        expected_sha=expected_sha,
        expected_branch=expected_branch,
        source_ref=source_ref,
    )
    if not validation_script.is_file():
        raise DeploymentAttestationError(
            f"validation script not found: {validation_script}"
        )

    validation_env = dict(os.environ)
    validation_env["CAERUS_DEPLOY_CANDIDATE_SHA"] = candidate
    validation_env["CAERUS_DEPLOY_INTERNAL"] = "1"
    validation = subprocess.run(
        ["bash", str(validation_script)],
        cwd=str(repo_root),
        env=validation_env,
        text=True,
        check=False,
    )
    if validation.returncode != 0:
        raise DeploymentAttestationError(
            f"VM validation failed with exit code {validation.returncode}; deploy marker was not updated"
        )

    # Validation must certify the same clean source tree that was inspected
    # before it ran.  A validation command that changes source fails closed.
    candidate_after = _verify_candidate(
        repo_root,
        expected_sha=candidate,
        expected_branch=expected_branch,
        source_ref=source_ref,
    )
    try:
        validation_command = str(validation_script.relative_to(repo_root))
    except ValueError:
        validation_command = str(validation_script.resolve())
    return write_deploy_state(
        deploy_state_path,
        candidate_after,
        branch=expected_branch or "main",
        metadata={
            "schema_version": "caerus.deploy_state.v2",
            "source_ref": source_ref,
            "validated_sha": candidate_after,
            "target_sha": candidate_after,
            "validation_status": "PASS",
            "validated_at": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
            "validation_command": validation_command,
        },
    )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate and attest a Caerus VM deployment")
    parser.add_argument("--repo-root", default=str(REPO_ROOT))
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument("--expected-branch", default="main")
    parser.add_argument("--source-ref", default="origin/main")
    parser.add_argument("--deploy-state", default="")
    parser.add_argument("--validation-script", default="")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    repo_root = Path(args.repo_root)
    try:
        path = finalize_deployment(
            repo_root=repo_root,
            expected_sha=args.expected_sha,
            expected_branch=args.expected_branch,
            source_ref=args.source_ref,
            deploy_state_path=Path(args.deploy_state) if args.deploy_state else None,
            validation_script=Path(args.validation_script) if args.validation_script else None,
        )
    except DeploymentAttestationError as exc:
        print(f"DEPLOYMENT_ATTESTATION_FAILED: {exc}", file=sys.stderr)
        return 3

    payload = json.loads(path.read_text(encoding="utf-8"))
    print(json.dumps({"deploy_state": str(path), **payload}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
