from __future__ import annotations

import json
from pathlib import Path

from research_registry.sleeves import EVIDENCE_SCHEMA_VERSION, validate_sleeve_evidence
from scripts.research import validate_sleeve_evidence as cli


def _valid_payload(**overrides) -> dict:
    payload = {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "sleeve_id": "orion",
        "name": "Caerus Orion",
        "thesis": "Rank-decay momentum challenger under FR-069 evidence review.",
        "status": "current_shadow_challenger",
        "owner": "Caerus Research Program",
        "source": "fixture",
        "hypothesis_class": "core_momentum",
        "data_requirements": ["pit_universe", "pit_prices", "shadow_nav"],
        "artifact_paths": ["outputs/research/example/orion_evidence.json"],
        "benchmark": "SPY",
        "evaluation_window": {"start": "2026-05-12", "end": "2026-06-12"},
        "metrics_required": ["return_correlation", "active_share", "max_drawdown"],
        "known_bias_risks": ["survivorship_bias", "short_observation_window"],
        "promotion_blockers": ["pit_rebaseline_pending"],
        "production_impact": "none",
        "decision_state": "research_ready",
        "evidence_last_updated": "2026-06-16",
        "strategy_id": "caerus_orion",
        "family": "core_momentum",
        "sleeve_type": "security_selection",
        "lifecycle_status": "shadow_observed",
        "universe_family": "caerus_large_cap",
        "universe_method": "pit_universe",
        "universe_snapshot_hash": "sha256:fixture",
        "price_source": "sharadar_sep",
        "holdout_excluded": True,
        "spec_version": "fr069-b2-fixture",
        "metrics": {"return_correlation": 0.75},
        "holdings": [],
        "attribution": [],
        "reason_codes": [],
        "governance_label": "RESEARCH_ONLY",
        "execution_impact": "NON_EXECUTIONAL",
    }
    payload.update(overrides)
    return payload


def _write_json(tmp_path: Path, payload: dict) -> Path:
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def test_valid_pit_evidence_is_decision_grade(tmp_path: Path) -> None:
    path = _write_json(tmp_path, _valid_payload())

    payload = validate_sleeve_evidence(path)

    assert payload["valid"] is True
    assert payload["decision_grade"] is True
    assert payload["classification"] == "research_decision_grade"
    assert payload["error_count"] == 0


def test_missing_critical_field_blocks_contract(tmp_path: Path) -> None:
    evidence = _valid_payload()
    del evidence["sleeve_id"]
    path = _write_json(tmp_path, evidence)

    payload = validate_sleeve_evidence(path)

    assert payload["valid"] is False
    assert payload["decision_grade"] is False
    assert any("missing required field: sleeve_id" in error["message"] for error in payload["errors"])


def test_legacy_current_universe_is_valid_but_non_decision_grade(tmp_path: Path) -> None:
    path = _write_json(
        tmp_path,
        _valid_payload(
            universe_method="legacy_current_universe",
            universe_snapshot_hash="",
            holdout_excluded=True,
        ),
    )

    payload = validate_sleeve_evidence(path)

    assert payload["valid"] is True
    assert payload["decision_grade"] is False
    assert payload["classification"] == "non_decision_grade"
    assert any("legacy_current_universe" in warning for warning in payload["warnings"])


def test_missing_holdout_flag_warns_and_demotes(tmp_path: Path) -> None:
    evidence = _valid_payload()
    del evidence["holdout_excluded"]
    path = _write_json(tmp_path, evidence)

    payload = validate_sleeve_evidence(path)

    assert payload["valid"] is True
    assert payload["decision_grade"] is False
    assert any("holdout_excluded" in warning for warning in payload["warnings"])


def test_execution_impact_other_than_non_executional_fails(tmp_path: Path) -> None:
    path = _write_json(tmp_path, _valid_payload(execution_impact="PAPER_TRADING"))

    payload = validate_sleeve_evidence(path)

    assert payload["valid"] is False
    assert any("execution_impact must be NON_EXECUTIONAL" in error["message"] for error in payload["errors"])


def test_unknown_sleeve_fails_manifest_membership(tmp_path: Path) -> None:
    path = _write_json(tmp_path, _valid_payload(sleeve_id="unknown_sleeve"))

    payload = validate_sleeve_evidence(path)

    assert payload["valid"] is False
    assert any("unknown sleeve_id" in error["message"] for error in payload["errors"])


def test_cli_prints_validation_payload(tmp_path: Path, capsys) -> None:
    path = _write_json(tmp_path, _valid_payload())

    exit_code = cli.main(["--artifact", str(path)])
    out = capsys.readouterr().out
    payload = json.loads(out)

    assert exit_code == 0
    assert payload["valid"] is True
    assert payload["classification"] == "research_decision_grade"
