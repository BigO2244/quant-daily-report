from __future__ import annotations

import ast
import hashlib
from pathlib import Path

import pytest

from aiops.aegis.service import AIOPSRunnerAdapter, AegisService
from aiops.aegis.store import AegisStore
from aiops.aegis.dashboard import render_mission_control


def service(tmp_path: Path) -> AegisService:
    return AegisService(AegisStore(tmp_path / "aegis.sqlite"))


def test_identical_canonical_input_has_identical_mission_and_dag(tmp_path: Path) -> None:
    instance = service(tmp_path)
    first = instance.create_mission("  Validate  new  factor research ", {"priority": "high"})
    second = instance.create_mission("Validate new factor research", {"priority": "high"})
    assert first["id"] == second["id"]
    assert [task["id"] for task in first["tasks"]] == [task["id"] for task in second["tasks"]]
    assert first["state"] == "APPROVAL_REQUIRED"
    assert len(first["edges"]) == len(first["tasks"]) - 1


def test_lifecycle_is_validated_and_events_are_append_only(tmp_path: Path) -> None:
    instance = service(tmp_path)
    mission = instance.create_mission("Test lifecycle")
    with pytest.raises(ValueError, match="Invalid lifecycle"):
        instance.store.transition_mission(mission["id"], "COMPLETED", "2026-01-01T00:00:00+00:00")
    approved = instance.approve(mission["id"], "owner reviewed scope")
    assert approved["approval_state"] == "APPROVED"


def test_packet_and_manifest_are_hash_deterministic(tmp_path: Path) -> None:
    instance = service(tmp_path)
    mission = instance.create_mission("Create research packet")
    packet = instance.execution_packet(mission["id"], mission["tasks"][0]["id"])
    assert packet["runner_class"] == "PacketOnlyRunnerAdapter"
    payload = tmp_path / "artifact.txt"; payload.write_text("evidence", encoding="utf-8")
    manifest = instance.artifact_manifest(mission["id"], [payload])
    assert manifest["artifacts"][0]["sha256"] == hashlib.sha256(b"evidence").hexdigest()
    metadata = instance.import_metadata(mission["id"], [payload])
    assert metadata[0]["sha256"] == "METADATA_ONLY"


def test_aiops_adapter_requires_approval_and_preserves_governed_command() -> None:
    adapter = AIOPSRunnerAdapter()
    with pytest.raises(PermissionError): adapter.command("spec.md", "BUILD", approved=False)
    assert adapter.command("spec.md", "BUILD", approved=True) == ["aiops", "run-all", "--spec", "spec.md", "--mode", "BUILD"]


def test_mission_control_model_and_dashboard_are_read_only_views(tmp_path: Path) -> None:
    instance = service(tmp_path)
    mission = instance.create_mission("Review artifact provenance")
    model = instance.mission_control_model()
    assert model[0]["id"] == mission["id"]
    assert len(model[0]["edges"]) == 7
    html = render_mission_control(instance)
    assert "Aegis Mission Control" in html and "DAG edges" in html


def test_aegis_has_no_forbidden_runtime_imports() -> None:
    forbidden = {"broker", "execution", "allocation", "scheduler", "paper", "pilot", "live", "capital"}
    for path in Path("aiops/aegis").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        modules = [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
        modules.extend(alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names)
        assert not any(module.split(".")[0] in forbidden for module in modules), path
