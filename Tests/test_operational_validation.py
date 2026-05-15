from __future__ import annotations

from pathlib import Path

from scripts.operational_validation import build_payload


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _minimal_repo(root: Path) -> None:
    _write(
        root / ".github" / "workflows" / "ci.yml",
        "name: ci\non: workflow_dispatch\npermissions:\n  contents: read\njobs:\n  test:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5\n",
    )
    _write(
        root / ".github" / "dependabot.yml",
        'version: 2\nupdates:\n  - package-ecosystem: "pip"\n    directory: "/"\n    schedule:\n      interval: "weekly"\n  - package-ecosystem: "github-actions"\n    directory: "/"\n    schedule:\n      interval: "weekly"\n',
    )


def test_operational_validation_passes_minimal_wave1_repo(tmp_path: Path) -> None:
    _minimal_repo(tmp_path)

    payload = build_payload(repo_root=tmp_path)

    assert payload["summary"]["fail"] == 0
    assert any(check["name"] == "workflow_action_pinning" and check["status"] == "PASS" for check in payload["checks"])
    assert any(check["name"] == "workflow_permissions" and check["status"] == "PASS" for check in payload["checks"])
    assert any(check["name"] == "dependabot" and check["status"] == "PASS" for check in payload["checks"])


def test_operational_validation_flags_mutable_actions(tmp_path: Path) -> None:
    _minimal_repo(tmp_path)
    _write(
        tmp_path / ".github" / "workflows" / "ci.yml",
        "name: ci\non: workflow_dispatch\njobs:\n  test:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: actions/checkout@v4\n",
    )

    payload = build_payload(repo_root=tmp_path)

    assert payload["status"] == "FAIL"
    assert any(check["name"] == "workflow_action_pinning" and check["status"] == "FAIL" for check in payload["checks"])


def test_operational_validation_flags_workflow_scope_write_permissions(tmp_path: Path) -> None:
    _minimal_repo(tmp_path)
    _write(
        tmp_path / ".github" / "workflows" / "ci.yml",
        "name: ci\non: workflow_dispatch\npermissions:\n  contents: write\njobs:\n  test:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5\n",
    )

    payload = build_payload(repo_root=tmp_path)

    assert payload["status"] == "FAIL"
    assert any(check["name"] == "workflow_permissions" and check["status"] == "FAIL" for check in payload["checks"])


def test_operational_validation_requires_dependabot_ecosystems(tmp_path: Path) -> None:
    _minimal_repo(tmp_path)
    _write(
        tmp_path / ".github" / "dependabot.yml",
        'version: 2\nupdates:\n  - package-ecosystem: "pip"\n    directory: "/"\n    schedule:\n      interval: "weekly"\n',
    )

    payload = build_payload(repo_root=tmp_path)

    assert payload["status"] == "FAIL"
    assert any(check["name"] == "dependabot" and check["status"] == "FAIL" for check in payload["checks"])
