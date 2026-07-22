from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.finalize_deployment import DeploymentAttestationError, finalize_deployment

REPO_ROOT = Path(__file__).resolve().parents[1]


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=str(repo), text=True, stderr=subprocess.DEVNULL
    ).strip()


def _repo(tmp_path: Path, validation_body: str = "exit 0\n") -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t.t")
    _git(repo, "config", "user.name", "t")
    (repo / ".gitignore").write_text("outputs/\n", encoding="utf-8")
    (repo / "a.txt").write_text("hello\n", encoding="utf-8")
    script = repo / "validate.sh"
    script.write_text("#!/usr/bin/env bash\nset -euo pipefail\n" + validation_body, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    head = _git(repo, "rev-parse", "HEAD")
    _git(repo, "update-ref", "refs/remotes/origin/main", head)
    return repo


def test_finalize_records_exact_validated_full_sha(tmp_path: Path) -> None:
    repo = _repo(
        tmp_path,
        '[[ "${CAERUS_DEPLOY_CANDIDATE_SHA:-}" == "$(git rev-parse HEAD)" ]]\n',
    )
    head = _git(repo, "rev-parse", "HEAD")
    path = finalize_deployment(
        repo_root=repo,
        expected_sha=head,
        validation_script=repo / "validate.sh",
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "caerus.deploy_state.v2"
    assert payload["deployed_sha"] == head
    assert payload["validated_sha"] == head
    assert payload["target_sha"] == head
    assert payload["validation_status"] == "PASS"
    assert payload["validated_at"].endswith("Z")
    assert payload["source_ref"] == "origin/main"
    assert len(payload["deployed_sha"]) == 40


def test_failed_validation_does_not_write_marker(tmp_path: Path) -> None:
    repo = _repo(tmp_path, "exit 9\n")
    head = _git(repo, "rev-parse", "HEAD")
    marker = repo / "outputs" / "deploy_state.json"
    with pytest.raises(DeploymentAttestationError, match="validation failed"):
        finalize_deployment(
            repo_root=repo,
            expected_sha=head,
            validation_script=repo / "validate.sh",
        )
    assert not marker.exists()


def test_validation_source_mutation_does_not_write_marker(tmp_path: Path) -> None:
    repo = _repo(tmp_path, 'printf "changed\\n" >> a.txt\n')
    head = _git(repo, "rev-parse", "HEAD")
    marker = repo / "outputs" / "deploy_state.json"
    with pytest.raises(DeploymentAttestationError, match="working tree is not clean"):
        finalize_deployment(
            repo_root=repo,
            expected_sha=head,
            validation_script=repo / "validate.sh",
        )
    assert not marker.exists()


def test_head_must_match_pinned_origin_main(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    origin_head = _git(repo, "rev-parse", "origin/main")
    (repo / "a.txt").write_text("second\n", encoding="utf-8")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-q", "-m", "second")
    current = _git(repo, "rev-parse", "HEAD")
    with pytest.raises(DeploymentAttestationError, match="does not match origin/main"):
        finalize_deployment(
            repo_root=repo,
            expected_sha=current,
            validation_script=repo / "validate.sh",
        )
    assert _git(repo, "rev-parse", "origin/main") == origin_head


def test_external_validation_path_is_recorded_without_crashing(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    external = tmp_path / "external-validator.sh"
    external.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    head = _git(repo, "rev-parse", "HEAD")
    path = finalize_deployment(
        repo_root=repo,
        expected_sha=head,
        validation_script=external,
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["validation_command"] == str(external.resolve())


def test_deploy_script_is_transactional_and_never_autostashes() -> None:
    text = (REPO_ROOT / "scripts" / "deploy.sh").read_text(encoding="utf-8")
    assert "flock -x -w 300 9" in text
    assert "git worktree add --detach" in text
    assert "git merge --ff-only \"${TARGET_SHA}\"" in text
    assert "deploy_state.candidate.json" in text
    assert "scripts/finalize_deployment.py" in text
    assert text.index("git worktree add --detach") < text.index("git merge --ff-only")
    assert text.index("scripts/finalize_deployment.py") < text.index("git merge --ff-only")
    assert "git stash" not in text
    assert "git checkout -B" not in text


def test_canonical_docs_do_not_prescribe_raw_pull_as_deployment() -> None:
    paths = [
        REPO_ROOT / "docs" / "deployment_workflow.md",
        REPO_ROOT / "docs" / "OPERATIONS.md",
        REPO_ROOT / "docs" / "runbook.md",
        REPO_ROOT / "docs" / "mcp_server.md",
    ]
    for path in paths:
        text = path.read_text(encoding="utf-8")
        assert "git pull --ff-only" not in text, path
        assert "scripts/deploy.sh" in text, path


def _deploy_integration_fixture(tmp_path: Path) -> tuple[Path, dict[str, str], str, str]:
    fake_home = tmp_path / "home"
    prod = fake_home / "quant-daily-report"
    prod.mkdir(parents=True)
    _git(prod, "init", "-q", "-b", "main")
    _git(prod, "config", "user.email", "t@t.t")
    _git(prod, "config", "user.name", "t")

    for relative in (
        "scripts/deploy.sh",
        "scripts/finalize_deployment.py",
        "scripts/runtime_env.sh",
        "scripts/ops/run_vm_validation.sh",
        "scripts/live_pilot_sha_guard.py",
        "core/live_pilot_sha_guard.py",
        "core/__init__.py",
    ):
        destination = prod / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / relative, destination)
    (prod / ".gitignore").write_text(
        "outputs/\n__pycache__/\n.pytest_cache/\n", encoding="utf-8"
    )
    (prod / "initial.txt").write_text("initial\n", encoding="utf-8")
    _git(prod, "add", "-A")
    _git(prod, "commit", "-q", "-m", "initial")
    initial = _git(prod, "rev-parse", "HEAD")

    origin = tmp_path / "origin.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(prod), str(origin)], check=True)
    _git(prod, "remote", "add", "origin", str(origin))
    developer = tmp_path / "developer"
    subprocess.run(["git", "clone", "-q", str(origin), str(developer)], check=True)
    _git(developer, "config", "user.email", "t@t.t")
    _git(developer, "config", "user.name", "t")
    (developer / "target.txt").write_text("target\n", encoding="utf-8")
    _git(developer, "add", "target.txt")
    _git(developer, "commit", "-q", "-m", "target")
    _git(developer, "push", "-q", "origin", "main")
    _git(prod, "fetch", "-q", "origin")
    target = _git(prod, "rev-parse", "origin/main")

    bin_dir = fake_home / ".venvs" / "quant-daily-report" / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "activate").write_text("# test venv\n", encoding="utf-8")
    tool = bin_dir / "python3"
    tool.write_text(
        "#!/usr/bin/env bash\n"
        "if [[ \"${1:-}\" == \"scripts/finalize_deployment.py\" ]]; then\n"
        f"  exec {sys.executable!s} \"$@\"\n"
        "fi\n"
        "exit \"${FAKE_TOOL_RC:-0}\"\n",
        encoding="utf-8",
    )
    tool.chmod(0o755)
    shutil.copy2(tool, bin_dir / "python")
    shutil.copy2(tool, bin_dir / "pytest")

    env = dict(os.environ)
    env.update(
        {
            "HOME": str(fake_home),
            "CAERUS_VENV_DIR": str(bin_dir.parent),
            "CAERUS_VM_PYTHON": str(bin_dir / "python3"),
            "CAERUS_VM_PYTEST": str(bin_dir / "pytest"),
            "TMPDIR": str(tmp_path),
        }
    )
    return prod, env, initial, target


def test_deploy_script_publishes_only_validated_target(tmp_path: Path) -> None:
    prod, env, _initial, target = _deploy_integration_fixture(tmp_path)
    proc = subprocess.run(
        ["bash", "scripts/deploy.sh"], cwd=prod, env=env, capture_output=True, text=True
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _git(prod, "rev-parse", "HEAD") == target
    marker = json.loads((prod / "outputs" / "deploy_state.json").read_text(encoding="utf-8"))
    assert marker["schema_version"] == "caerus.deploy_state.v2"
    assert marker["deployed_sha"] == target
    assert marker["validated_sha"] == target
    assert marker["target_sha"] == target


def test_deploy_validation_failure_preserves_head_and_prior_marker(tmp_path: Path) -> None:
    prod, env, initial, _target = _deploy_integration_fixture(tmp_path)
    marker = prod / "outputs" / "deploy_state.json"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("prior-marker\n", encoding="utf-8")
    env["FAKE_TOOL_RC"] = "9"
    proc = subprocess.run(
        ["bash", "scripts/deploy.sh"], cwd=prod, env=env, capture_output=True, text=True
    )
    assert proc.returncode != 0
    assert _git(prod, "rev-parse", "HEAD") == initial
    assert marker.read_text(encoding="utf-8") == "prior-marker\n"


def test_deploy_rejects_untracked_source_without_stashing(tmp_path: Path) -> None:
    prod, env, initial, _target = _deploy_integration_fixture(tmp_path)
    (prod / "untracked_source.py").write_text("VALUE = 1\n", encoding="utf-8")
    proc = subprocess.run(
        ["bash", "scripts/deploy.sh"], cwd=prod, env=env, capture_output=True, text=True
    )
    assert proc.returncode != 0
    assert "production checkout is dirty" in proc.stderr
    assert _git(prod, "rev-parse", "HEAD") == initial
    assert (prod / "untracked_source.py").exists()


def test_candidate_validation_mode_cannot_bypass_attestation_on_main(tmp_path: Path) -> None:
    prod, env, initial, _target = _deploy_integration_fixture(tmp_path)
    _git(prod, "update-ref", "refs/remotes/origin/main", initial)
    env["CAERUS_DEPLOY_CANDIDATE_SHA"] = initial
    env["CAERUS_DEPLOY_INTERNAL"] = "1"
    proc = subprocess.run(
        ["bash", "scripts/ops/run_vm_validation.sh"],
        cwd=prod,
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 4
    assert "restricted to the detached deployment worktree" in proc.stdout
