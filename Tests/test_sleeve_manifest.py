from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

from research_registry.sleeves import DEFAULT_MANIFEST_PATH, load_sleeve_manifest, sleeve_inventory_payload, validate_sleeve_manifest


REQUIRED_SLEEVES = {"polaris", "orion", "lyra", "phoenix", "cygnus", "cassiopeia", "argo"}
FUTURE_PLACEHOLDERS = {"phoenix", "cygnus", "cassiopeia", "argo"}


def _write_manifest(tmp_path: Path, payload: dict) -> Path:
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    return path


def test_default_sleeve_manifest_loads_and_validates() -> None:
    payload = validate_sleeve_manifest()

    assert payload["valid"] is True
    assert payload["error_count"] == 0
    assert payload["manifest_path"] == str(DEFAULT_MANIFEST_PATH)


def test_default_manifest_contains_required_sleeves_and_phase_b_guardrails() -> None:
    manifest = load_sleeve_manifest()
    sleeves = {item["sleeve_id"]: item for item in manifest["sleeves"]}

    assert REQUIRED_SLEEVES.issubset(sleeves)
    assert manifest["research_only"] is True
    assert manifest["behavior_change_allowed"] is False
    assert all(item["behavior_change_allowed"] is False for item in sleeves.values())


def test_future_placeholders_are_not_marked_active_paper_or_promoted() -> None:
    manifest = load_sleeve_manifest()
    sleeves = {item["sleeve_id"]: item for item in manifest["sleeves"]}

    for sleeve_id in FUTURE_PLACEHOLDERS:
        sleeve = sleeves[sleeve_id]
        assert sleeve["status"] == "research_placeholder"
        assert sleeve["lifecycle_stage"] not in {"paper_observed", "shadow_observed"}
        assert sleeve["implementation_status"] == "research_placeholder"


def test_duplicate_sleeve_id_fails_validation(tmp_path: Path) -> None:
    manifest = deepcopy(load_sleeve_manifest())
    manifest["sleeves"].append(deepcopy(manifest["sleeves"][0]))
    path = _write_manifest(tmp_path, manifest)

    payload = validate_sleeve_manifest(path)

    assert payload["valid"] is False
    assert any("duplicate sleeve_id" in error["message"] for error in payload["errors"])


def test_missing_required_field_fails_validation(tmp_path: Path) -> None:
    manifest = deepcopy(load_sleeve_manifest())
    del manifest["sleeves"][0]["artifact_requirements"]
    path = _write_manifest(tmp_path, manifest)

    payload = validate_sleeve_manifest(path)

    assert payload["valid"] is False
    assert any("missing sleeve field: artifact_requirements" in error["message"] for error in payload["errors"])


def test_future_placeholder_marked_paper_fails_validation(tmp_path: Path) -> None:
    manifest = deepcopy(load_sleeve_manifest())
    sleeves = {item["sleeve_id"]: item for item in manifest["sleeves"]}
    sleeves["phoenix"]["status"] = "paper"
    sleeves["phoenix"]["lifecycle_stage"] = "paper_observed"
    path = _write_manifest(tmp_path, manifest)

    payload = validate_sleeve_manifest(path)

    assert payload["valid"] is False
    messages = [error["message"] for error in payload["errors"]]
    assert any("invalid status" in message for message in messages)
    assert any("future sleeves must remain research_placeholder" in message for message in messages)


def test_inventory_payload_returns_expected_counts_without_mutation() -> None:
    before = DEFAULT_MANIFEST_PATH.read_bytes()

    payload = sleeve_inventory_payload()

    assert payload["status"] == "OK"
    assert payload["sleeve_count"] == 7
    assert payload["counts_by_status"]["current_paper_baseline"] == 1
    assert payload["counts_by_status"]["current_shadow_challenger"] == 2
    assert payload["counts_by_status"]["research_placeholder"] == 4
    assert {item["sleeve_id"] for item in payload["current_sleeves"]} == {"polaris", "orion", "lyra"}
    assert {item["sleeve_id"] for item in payload["future_placeholders"]} == FUTURE_PLACEHOLDERS
    assert DEFAULT_MANIFEST_PATH.read_bytes() == before
