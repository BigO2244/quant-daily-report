from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import subprocess

from core.orion_precompute_guard import validate_orion_precompute_dependency
from core.paper_target_authority import seal_paper_target_bundle
from core.sleeve_control_plane import validate_orion_decision_lineage
from core.sleeve_control_plane import dispatch_all_sleeves, load_sleeve_control_registry
from scripts.format_precompute_email import _orion_freshness_lines
from Tests.test_live_pilot_build_plan_from_precompute import (
    _bundle,
    _orion_shadow,
    _write_sleeve_evaluations,
)


def _canonical_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _lineaged_source(
    trade_date: str,
    *,
    salt: str,
    weights: dict[str, float] | None = None,
) -> dict:
    from paper.trading_calendar import prev_trading_day

    target_weights = weights if weights is not None else {"AAA": 0.6, "BBB": 0.4}
    market_hash = _canonical_hash(["market", salt])
    panel_hash = _canonical_hash(["panel", salt])
    feature_hash = _canonical_hash(["features", salt])
    history_hash = _canonical_hash(["history", salt])
    rank_hash = _canonical_hash(["ranks", salt])
    prior_date = prev_trading_day(trade_date)
    stage_diagnostics = {
        stage: {
            "stage": stage,
            "source_identity": f"fixture.{stage}",
            "row_count": len(target_weights) if stage == "target_weights" else 2,
            "symbol_count": len(target_weights) if stage == "target_weights" else 2,
            "max_market_timestamp": trade_date,
        }
        for stage in (
            "market_data",
            "normalized_panel",
            "features",
            "full_rank_history",
            "current_rank_table",
            "target_weights",
        )
    }
    return {
        "strategy_slug": "caerus_orion",
        "source_variant": "h2_rank_decay_exit_h6_top5",
        "trade_date": trade_date,
        "effective_trade_date": trade_date,
        "decision_eligible": True,
        "observation_status": "OK",
        "data_status": "OK",
        "coverage_status": "OK",
        "target_weights": target_weights,
        "decision_lineage": {
            "schema_version": "caerus.orion_decision_lineage.v1",
            "trade_date": trade_date,
            "effective_trade_date": trade_date,
            "market_data_asof": f"{trade_date}T20:00:00+00:00",
            "market_data_hash": market_hash,
            "normalized_panel_hash": panel_hash,
            "feature_hash": feature_hash,
            "full_rank_history_hash": history_hash,
            "rank_table_hash": rank_hash,
            "target_weights_hash": _canonical_hash(target_weights),
            "generated_at_utc": f"{trade_date}T22:00:00+00:00",
            "model_version": "h2_rank_decay_exit_h6_top5",
            "source_variant": "h2_rank_decay_exit_h6_top5",
            "parent_artifact_hashes": {
                "normalized_panel": market_hash,
                "features": panel_hash,
                "full_rank_history": feature_hash,
                "current_rank_table": history_hash,
                "target_weights": rank_hash,
            },
            "coverage": {
                "status": "OK",
                "current_session": trade_date,
                "required_anchor_dates": [prior_date],
                "missing_current_session_symbols": [],
                "missing_required_anchor_symbols": {},
                "symbol_count": len(target_weights),
            },
            "selection_trace": [{"symbol": symbol} for symbol in target_weights],
            "stage_diagnostics": stage_diagnostics,
        },
    }


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def test_missing_lineage_and_changed_parent_unchanged_child_fail_closed() -> None:
    missing = _lineaged_source("2026-08-26", salt="current")
    missing.pop("decision_lineage")
    assert validate_orion_decision_lineage(
        missing, effective_trade_date="2026-08-26"
    ) == ["orion_lineage:missing"]

    previous = _lineaged_source("2026-08-25", salt="previous")
    current = _lineaged_source("2026-08-26", salt="current")
    current["decision_lineage"]["feature_hash"] = previous["decision_lineage"][
        "feature_hash"
    ]
    current["decision_lineage"]["parent_artifact_hashes"][
        "full_rank_history"
    ] = current["decision_lineage"]["feature_hash"]
    failures = validate_orion_decision_lineage(
        current,
        effective_trade_date="2026-08-26",
        previous_source_payload=previous,
    )
    assert any("stale_child:feature_hash" in failure for failure in failures)


def test_missing_and_legacy_immediate_prior_lineage_fail_closed() -> None:
    current = _lineaged_source("2026-08-26", salt="current")
    missing = validate_orion_decision_lineage(
        current,
        effective_trade_date="2026-08-26",
        previous_source_payload=None,
    )
    assert "orion_lineage:prior_source_missing" in missing

    legacy_prior = {
        "trade_date": "2026-08-25",
        "effective_trade_date": "2026-08-25",
        "target_weights": current["target_weights"],
    }
    legacy = validate_orion_decision_lineage(
        current,
        effective_trade_date="2026-08-26",
        previous_source_payload=legacy_prior,
    )
    assert "orion_lineage:prior_lineage_missing_or_legacy" in legacy


def test_coverage_stage_diagnostics_and_empty_selection_trace_contract() -> None:
    previous = _lineaged_source("2026-08-25", salt="previous")
    current = _lineaged_source("2026-08-26", salt="current")
    current["decision_lineage"]["selection_trace"] = []
    assert not validate_orion_decision_lineage(
        current,
        effective_trade_date="2026-08-26",
        previous_source_payload=previous,
    )

    empty = _lineaged_source(
        "2026-08-26", salt="empty-current", weights={}
    )
    empty["decision_lineage"]["selection_trace"] = []
    empty["decision_lineage"]["stage_diagnostics"]["target_weights"].update(
        row_count=0,
        symbol_count=0,
    )
    assert not validate_orion_decision_lineage(
        empty,
        effective_trade_date="2026-08-26",
        previous_source_payload=previous,
    )

    bad_coverage = copy.deepcopy(current)
    bad_coverage["decision_lineage"]["coverage"]["current_session"] = "2026-08-25"
    bad_coverage["decision_lineage"]["coverage"][
        "missing_required_anchor_symbols"
    ] = {"2026-08-25": ["AAA"]}
    failures = validate_orion_decision_lineage(
        bad_coverage,
        effective_trade_date="2026-08-26",
        previous_source_payload=previous,
    )
    assert "orion_lineage:coverage:current_session_mismatch" in failures
    assert "orion_lineage:coverage:anchors_incomplete" in failures

    bad_stage = copy.deepcopy(current)
    bad_stage["decision_lineage"]["stage_diagnostics"]["features"][
        "max_market_timestamp"
    ] = "2026-08-25"
    failures = validate_orion_decision_lineage(
        bad_stage,
        effective_trade_date="2026-08-26",
        previous_source_payload=previous,
    )
    assert (
        "orion_lineage:stage_diagnostics:features:max_market_timestamp_mismatch"
        in failures
    )


def test_missing_lineage_is_blocked_from_capital_envelope(tmp_path: Path) -> None:
    registry = load_sleeve_control_registry()
    source = _lineaged_source("2026-08-26", salt="missing")
    source.pop("decision_lineage")
    _write_json(
        tmp_path
        / "outputs"
        / "shadow_candidates"
        / "2026-08-26"
        / "caerus_orion.json",
        source,
    )
    payload = dispatch_all_sleeves(
        trade_date="2026-08-26",
        run_id="missing-lineage",
        daily_snapshot={
            "asof": "2026-08-26",
            "sleeve_allocations": {
                key: 0.0 for key in registry.functional_allocation_keys()
            },
        },
        runtime_root=tmp_path,
        registry=registry,
    )
    orion = next(
        row for row in payload["envelopes"] if row["sleeve_id"] == "caerus_orion"
    )
    assert orion["evaluation"]["status"] == "BLOCKED"
    assert orion["eligibility"]["evaluation_usable_for_capital"] is False
    assert "STALE_DECISION_SUSPECTED" in orion["reason_codes"]


def test_copied_forward_blocks_but_legitimate_unchanged_target_passes() -> None:
    previous = _lineaged_source("2026-08-25", salt="previous")
    copied = copy.deepcopy(previous)
    copied.update(trade_date="2026-08-26", effective_trade_date="2026-08-26")
    copied["decision_lineage"].update(
        trade_date="2026-08-26",
        effective_trade_date="2026-08-26",
        market_data_asof="2026-08-26T20:00:00+00:00",
        generated_at_utc="2026-08-26T22:00:00+00:00",
    )
    failures = validate_orion_decision_lineage(
        copied,
        effective_trade_date="2026-08-26",
        previous_source_payload=previous,
    )
    assert "orion_lineage:copied_forward" in failures

    recomputed = _lineaged_source(
        "2026-08-26", salt="current", weights=previous["target_weights"]
    )
    assert not validate_orion_decision_lineage(
        recomputed,
        effective_trade_date="2026-08-26",
        previous_source_payload=previous,
    )
    assert (
        recomputed["decision_lineage"]["target_weights_hash"]
        == previous["decision_lineage"]["target_weights_hash"]
    )


def test_explicit_prior_only_migration_anchor_cannot_be_current_authority() -> None:
    anchor = _lineaged_source("2026-08-25", salt="migration-anchor")
    anchor["decision_eligible"] = False
    anchor["authority_scope"] = "PRIOR_LINEAGE_TRUST_ANCHOR"
    anchor["valid_as_prior_only_for"] = "2026-08-26"
    current = _lineaged_source("2026-08-26", salt="current")

    assert not validate_orion_decision_lineage(
        current,
        effective_trade_date="2026-08-26",
        previous_source_payload=anchor,
    )
    assert "orion_lineage:source_decision_not_eligible" in validate_orion_decision_lineage(
        anchor,
        effective_trade_date="2026-08-25",
        previous_source_payload=_lineaged_source("2026-08-24", salt="older"),
    )


def test_recomputed_n_seals_for_n_plus_one_and_preserves_lineage(tmp_path: Path) -> None:
    payload_path = _bundle(tmp_path, trade_date="2026-08-17", signals=[])
    source_path = _orion_shadow(tmp_path, trade_date="2026-08-14")
    source_lineage = json.loads(source_path.read_text())["decision_lineage"]
    _write_sleeve_evaluations(payload_path, tmp_path)

    package = seal_paper_target_bundle(
        bundle_dir=payload_path.parent,
        trade_date="2026-08-17",
        repo_root=tmp_path,
        sealed_at="2026-08-17T11:00:00+00:00",
    )

    for filename, field in (
        ("signals.json", "decision_lineage"),
        ("planned_execution_payload.json", "decision_lineage"),
        ("contract.json", "decision_lineage"),
    ):
        assert json.loads((payload_path.parent / filename).read_text())[field] == source_lineage
    assert package["decision_lineage"] == source_lineage
    assert package["decision_freshness_status"] == "VERIFIED"
    assert package["effective_trade_date"] == "2026-08-14"
    assert package["prior_decision_lineage"]["status"] == "BOUND"
    assert package["prior_decision_lineage"]["effective_trade_date"] == "2026-08-13"


def test_guard_requires_latest_completed_session_and_verifies_marker(tmp_path: Path) -> None:
    report_date = "2026-08-27"
    effective_date = "2026-08-26"
    blocked = validate_orion_precompute_dependency(
        repo_root=tmp_path, report_date=report_date
    )
    assert blocked["required_effective_trade_date"] == effective_date
    assert blocked["status"] == "BLOCKED"

    source = _lineaged_source(effective_date, salt="current")
    source_path = (
        tmp_path
        / "outputs"
        / "shadow_candidates"
        / effective_date
        / "caerus_orion.json"
    )
    _write_json(source_path, source)
    previous_source = _lineaged_source("2026-08-25", salt="previous")
    _write_json(
        source_path.parent.parent / "2026-08-25" / source_path.name,
        previous_source,
    )
    hydration = {
        "status": "OK",
        "as_of_date": effective_date,
        "coverage_validation": {"status": "OK"},
        "shadow_refresh": {"status": "OK"},
    }
    hydration_path = (
        tmp_path / "outputs" / "price_hydration" / effective_date / "status.json"
    )
    _write_json(hydration_path, hydration)
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True
    )
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    (tmp_path / ".gitignore").write_text("outputs/\n", encoding="utf-8")
    _write_json(tmp_path / "tracked.json", {"fixture": True})
    subprocess.run(["git", "add", ".gitignore", "tracked.json"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "fixture"], cwd=tmp_path, check=True, capture_output=True)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, check=True, capture_output=True, text=True
    ).stdout.strip()
    marker = {
        "schema_version": "caerus.orion_decision_readiness.v1",
        "status": "READY",
        "trade_date": effective_date,
        "effective_trade_date": effective_date,
        "generated_at_utc": f"{effective_date}T23:00:00+00:00",
        "source_artifact": {
            "path": str(source_path.relative_to(tmp_path)),
            "sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
        },
        "decision_lineage": source["decision_lineage"],
        "decision_lineage_hash": _canonical_hash(source["decision_lineage"]),
        "hydration_status": {
            "path": str(hydration_path.relative_to(tmp_path)),
            "sha256": hashlib.sha256(hydration_path.read_bytes()).hexdigest(),
        },
        "deployed_git_sha": head,
    }
    marker_path = hydration_path.with_name("orion_decision_ready.json")
    _write_json(marker_path, marker)

    ready = validate_orion_precompute_dependency(
        repo_root=tmp_path, report_date=report_date
    )
    assert ready["status"] == "READY"
    assert ready["decision_status"] == "VERIFIED"

    (tmp_path / "tracked.json").write_text(
        json.dumps({"fixture": "dirty after marker"}), encoding="utf-8"
    )
    dirty_runtime = validate_orion_precompute_dependency(
        repo_root=tmp_path, report_date=report_date
    )
    assert dirty_runtime["status"] == "BLOCKED"
    assert "orion_dependency:repo_runtime_not_clean_or_unavailable" in dirty_runtime[
        "failures"
    ]
    assert ready["prior_decision_lineage"]["status"] == "BOUND"

    marker["source_artifact"]["path"] = str(source_path.resolve())
    _write_json(marker_path, marker)
    noncanonical = validate_orion_precompute_dependency(
        repo_root=tmp_path, report_date=report_date
    )
    assert "orion_dependency:source_artifact_path_not_canonical" in noncanonical[
        "failures"
    ]

    marker["source_artifact"]["path"] = str(source_path.relative_to(tmp_path))
    stale = copy.deepcopy(source)
    stale["decision_lineage"]["feature_hash"] = previous_source[
        "decision_lineage"
    ]["feature_hash"]
    stale["decision_lineage"]["parent_artifact_hashes"][
        "full_rank_history"
    ] = stale["decision_lineage"]["feature_hash"]
    _write_json(source_path, stale)
    marker["source_artifact"]["sha256"] = hashlib.sha256(
        source_path.read_bytes()
    ).hexdigest()
    marker["decision_lineage"] = stale["decision_lineage"]
    marker["decision_lineage_hash"] = _canonical_hash(stale["decision_lineage"])
    _write_json(marker_path, marker)
    copied = validate_orion_precompute_dependency(
        repo_root=tmp_path, report_date=report_date
    )
    assert copied["status"] == "BLOCKED"
    assert any("stale_child:feature_hash" in item for item in copied["failures"])


def test_email_evidence_reports_hashes_unchanged_target_and_deployed_sha(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    previous = _lineaged_source("2026-08-25", salt="previous")
    current = _lineaged_source(
        "2026-08-26", salt="current", weights=previous["target_weights"]
    )
    previous_path = (
        tmp_path / "outputs" / "shadow_candidates" / "2026-08-25" / "caerus_orion.json"
    )
    current_path = previous_path.parent.parent / "2026-08-26" / previous_path.name
    _write_json(previous_path, previous)
    _write_json(current_path, current)
    _write_json(
        tmp_path / "outputs" / "precompute" / "2026-08-27" / "paper_target_package.json",
        {
            "effective_trade_date": "2026-08-26",
            "source_strategy_artifact": {"path": str(current_path.relative_to(tmp_path))},
        },
    )
    _write_json(tmp_path / "outputs" / "deploy_state.json", {"deployed_sha": "a" * 40})

    lines = _orion_freshness_lines(
        {
            "decision_lineage": current["decision_lineage"],
            "decision_freshness_status": "VERIFIED",
        },
        "2026-08-27",
    )
    rendered = "\n".join(lines)
    assert "Features recomputed:  YES" in rendered
    assert "Ranks recomputed:     YES" in rendered
    assert "Target changed:       NO" in rendered
    assert f"Deployed Git SHA:     {'a' * 40}" in rendered
