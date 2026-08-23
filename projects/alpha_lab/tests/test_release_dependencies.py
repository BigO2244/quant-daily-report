from __future__ import annotations

import copy
import hashlib
import json
import shutil
from pathlib import Path

import pytest

from projects.alpha_lab.factory import release_dependencies as release


REPO_ROOT = Path(__file__).resolve().parents[3]


def _manifest() -> dict:
    return json.loads((REPO_ROOT / release.MANIFEST_RELATIVE_PATH).read_text())


def _manifest_override(monkeypatch, mutation) -> None:
    manifest = copy.deepcopy(_manifest())
    mutation(manifest)
    monkeypatch.setattr(release, "_strict_json_object", lambda _path: manifest)


def _copy_contract(tmp_path: Path) -> Path:
    target = tmp_path / "repo"
    (target / "projects").mkdir(parents=True)
    shutil.copytree(REPO_ROOT / "projects/alpha_lab", target / "projects/alpha_lab")
    return target


def test_committed_release_dependency_contract_passes() -> None:
    result = release.validate_release_dependency_contract(REPO_ROOT)
    assert result["status"] == "PASS"
    assert result["requirement_count"] == 25
    assert result["lock_sha256"] == (
        "a7db6d5f8f96879aad5b3a8c38ed7ad6ed094aac0ae66c8a0e69677353745549"
    )


def test_cryptography_is_an_explicit_runtime_requirement() -> None:
    runtime_input = (REPO_ROOT / "projects/alpha_lab/requirements.in").read_text()
    assert "cryptography==49.0.0" in runtime_input.splitlines()
    assert _manifest()["import_contract"]["cryptography"] == {
        "distribution": "cryptography",
        "status": "REQUIRED_RUNTIME",
    }


def test_lock_byte_drift_fails(tmp_path: Path) -> None:
    root = _copy_contract(tmp_path)
    lock_path = root / _manifest()["lock"]["path"]
    lock_path.write_bytes(lock_path.read_bytes() + b"\n")
    with pytest.raises(release.ReleaseDependencyError, match="lock identity drift"):
        release.validate_release_dependency_contract(root)


def test_unhashed_requirement_fails_even_with_refreshed_lock_identity(tmp_path: Path) -> None:
    root = _copy_contract(tmp_path)
    manifest_path = root / release.MANIFEST_RELATIVE_PATH
    manifest = json.loads(manifest_path.read_text())
    lock_path = root / manifest["lock"]["path"]
    changed = lock_path.read_text().replace(
        "certifi==2026.7.22 --hash=sha256:62f22742b58a1a33014a2b6b706588a8d7e2a88ae7bd1a6ebe8c992928483775",
        "certifi==2026.7.22",
    )
    lock_path.write_text(changed)
    raw = lock_path.read_bytes()
    manifest["lock"]["bytes"] = len(raw)
    manifest["lock"]["sha256"] = hashlib.sha256(raw).hexdigest()
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(release.ReleaseDependencyError, match="singly hashed"):
        release.validate_release_dependency_contract(root)


@pytest.mark.parametrize("field", ["version", "sha256"])
def test_manifest_version_or_hash_drift_fails(monkeypatch, field: str) -> None:
    def mutate(manifest: dict) -> None:
        wheel = next(item for item in manifest["wheels"] if item["distribution"] == "cryptography")
        wheel[field] = "0.0.0" if field == "version" else "0" * 64

    _manifest_override(monkeypatch, mutate)
    with pytest.raises(release.ReleaseDependencyError, match="version or hash drift"):
        release.validate_release_dependency_contract(REPO_ROOT)


def test_source_distribution_in_manifest_fails(monkeypatch) -> None:
    def mutate(manifest: dict) -> None:
        wheel = next(item for item in manifest["wheels"] if item["distribution"] == "cryptography")
        wheel["filename"] = "cryptography-49.0.0.tar.gz"

    _manifest_override(monkeypatch, mutate)
    with pytest.raises(release.ReleaseDependencyError, match="source distribution is forbidden"):
        release.validate_release_dependency_contract(REPO_ROOT)


def test_wrong_platform_or_abi_in_manifest_fails(monkeypatch) -> None:
    def mutate(manifest: dict) -> None:
        wheel = next(item for item in manifest["wheels"] if item["distribution"] == "cryptography")
        wheel["filename"] = "cryptography-49.0.0-cp310-cp310-win_amd64.whl"

    _manifest_override(monkeypatch, mutate)
    with pytest.raises(release.ReleaseDependencyError, match="wrong target platform or ABI"):
        release.validate_release_dependency_contract(REPO_ROOT)


def test_undeclared_third_party_import_fails(monkeypatch) -> None:
    imports = set(_manifest()["import_contract"]) | {"unregistered_package"}
    monkeypatch.setattr(release, "_third_party_imports", lambda _root: imports)
    with pytest.raises(release.ReleaseDependencyError, match="undeclared import contract drift"):
        release.validate_release_dependency_contract(REPO_ROOT)


@pytest.mark.parametrize("kind", ["missing", "extra"])
def test_wheelhouse_extra_or_missing_file_fails(tmp_path: Path, kind: str) -> None:
    expected = {
        "demo-1.0-py3-none-any.whl": {
            "bytes": 1,
            "sha256": hashlib.sha256(b"x").hexdigest(),
        }
    }
    if kind == "extra":
        (tmp_path / "demo-1.0-py3-none-any.whl").write_bytes(b"x")
        (tmp_path / "unexpected.whl").write_bytes(b"x")
    with pytest.raises(release.ReleaseDependencyError, match="extra or missing files"):
        release._validate_wheelhouse_files(tmp_path, expected)


def test_wheelhouse_file_hash_drift_fails(tmp_path: Path) -> None:
    filename = "demo-1.0-py3-none-any.whl"
    (tmp_path / filename).write_bytes(b"changed")
    expected = {
        filename: {
            "bytes": 8,
            "sha256": hashlib.sha256(b"expected").hexdigest(),
        }
    }
    with pytest.raises(release.ReleaseDependencyError, match="file hash/size drift"):
        release._validate_wheelhouse_files(tmp_path, expected)


def test_python_310_conditional_dependency_closure_is_preserved() -> None:
    assert release._target_dependencies(
        [
            "typing-extensions>=4.13.2 ; python_full_version < '3.11'",
            "tomli>=1 ; python_version < '3.11'",
            "colorama>=0.4 ; sys_platform == 'win32'",
            "bcrypt>=3.1.5 ; extra == 'ssh'",
        ]
    ) == ["tomli", "typing-extensions"]
