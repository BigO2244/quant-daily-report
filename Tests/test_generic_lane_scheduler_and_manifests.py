from __future__ import annotations

import ast
import copy
import hashlib
from pathlib import Path

import pytest

from core.generic_live_cutover_manifests import (
    GenericLiveManifestError,
    build_deployment_policy_replacement_template,
    build_generic_live_preflight_manifest,
    build_generic_live_rollback_manifest,
    validate_generic_live_preflight_manifest,
)
from core.lane_environment_adapter import build_generic_live_cutover_preflight
from core.lane_scheduler_dry_run import (
    GenericLaneSchedulerError,
    run_generic_lane_scheduler_dry_run,
    validate_generic_lane_scheduler_result,
)
from Tests.test_lane_environment_adapter import _green_inputs


def _green_cutover(tmp_path):
    inputs = _green_inputs(tmp_path)
    return inputs, build_generic_live_cutover_preflight(**inputs)


def test_scheduler_is_off_by_default_and_never_submits(tmp_path) -> None:
    inputs, _ = _green_cutover(tmp_path)
    result = run_generic_lane_scheduler_dry_run(
        exact_plan=inputs["live_exact_plan"],
        environment_binding=inputs["live_binding"],
        safety_evidence=inputs["safety_evidence"],
    )

    assert result["status"] == "DISABLED_NO_ACTION"
    assert result["scheduler_enabled"] is False
    assert result["broker_call_performed"] is False
    assert result["broker_submission_allowed"] is False
    assert result["legacy_live_executor_reachable"] is False
    assert result["execution_rehearsal_hash"] is None
    assert validate_generic_lane_scheduler_result(result) == result


def test_enabled_live_scheduler_only_runs_generic_no_submit_rehearsal(tmp_path) -> None:
    inputs, cutover = _green_cutover(tmp_path)
    result = run_generic_lane_scheduler_dry_run(
        exact_plan=inputs["live_exact_plan"],
        environment_binding=inputs["live_binding"],
        safety_evidence=inputs["safety_evidence"],
        live_cutover_preflight=cutover,
        scheduler_enabled=True,
    )

    assert result["status"] == "VALIDATED_NO_SUBMIT"
    assert result["execution_rehearsal_hash"]
    assert result["execution_authority"] is False
    assert result["activation_authority"] is False


def test_generic_paper_scheduler_remains_not_cut_over(tmp_path) -> None:
    inputs, _ = _green_cutover(tmp_path)
    result = run_generic_lane_scheduler_dry_run(
        exact_plan=inputs["paper_exact_plan"],
        environment_binding=inputs["paper_binding"],
        safety_evidence=__import__("core.lane_execution_dry_run", fromlist=["build_lane_execution_safety_evidence"]).build_lane_execution_safety_evidence(
            exact_plan=inputs["paper_exact_plan"],
            checked_at="2026-08-18T11:08:30+00:00",
            source_hashes=[hashlib.sha256(b"paper-preflight").hexdigest()],
        ),
        scheduler_enabled=True,
    )
    assert result["status"] == "BLOCKED"
    assert result["reason_codes"] == ["GENERIC_PAPER_NOT_YET_CUT_OVER"]


def test_scheduler_source_cannot_import_or_call_legacy_live() -> None:
    root = Path(__file__).resolve().parents[1]
    for source_path in (
        root / "core/lane_scheduler_dry_run.py",
        root / "scripts/run_generic_lane_scheduler_dry_run.py",
    ):
        tree = ast.parse(source_path.read_text())
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                imports.append(node.module or "")
            elif isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
        assert not any("live_pilot" in name for name in imports)
        assert "scripts.live_pilot_execute" not in source_path.read_text()


def test_deployment_rollback_and_preflight_manifests_are_immutable_advisory(tmp_path) -> None:
    inputs, cutover = _green_cutover(tmp_path)
    template = build_deployment_policy_replacement_template(
        cutover_preflight=cutover,
        environment_binding=inputs["live_binding"],
        generated_at="2026-08-18T11:15:00+00:00",
    )
    rollback = build_generic_live_rollback_manifest(
        deployment_template=template,
        prior_deployment_version="deployment:legacy-live-disabled",
        prior_deployment_hash=hashlib.sha256(b"prior-disabled").hexdigest(),
        candidate_deployment_version="deployment:generic-live-pending",
        candidate_deployment_hash=hashlib.sha256(b"pending").hexdigest(),
        created_at="2026-08-18T11:15:30+00:00",
    )
    manifest = build_generic_live_preflight_manifest(
        cutover_preflight=cutover,
        deployment_template=template,
        rollback_manifest=rollback,
        created_at="2026-08-18T11:16:00+00:00",
    )

    assert template["status"] == "TEMPLATE_ONLY_NOT_ACTIVE"
    assert template["legacy_registry_mutation_allowed"] is False
    assert template["sleeve_selection_allowed"] is False
    assert rollback["kill_switch_must_remain_engaged"] is True
    assert rollback["legacy_executor_must_remain_disabled"] is True
    assert manifest["status"] == "READY_FOR_OWNER_POLICY_COMPILATION"
    assert manifest["active_config_changed"] is False
    assert manifest["schedule_changed"] is False
    assert validate_generic_live_preflight_manifest(manifest) == manifest

    tampered = copy.deepcopy(manifest)
    tampered["active_config_changed"] = True
    with pytest.raises(GenericLiveManifestError, match="cannot mutate"):
        validate_generic_live_preflight_manifest(tampered)


def test_enabled_live_requires_explicit_ready_preflight(tmp_path) -> None:
    inputs, _ = _green_cutover(tmp_path)
    with pytest.raises(GenericLaneSchedulerError, match="requires an explicit"):
        run_generic_lane_scheduler_dry_run(
            exact_plan=inputs["live_exact_plan"],
            environment_binding=inputs["live_binding"],
            safety_evidence=inputs["safety_evidence"],
            scheduler_enabled=True,
        )


def test_static_templates_are_disarmed_redacted_and_non_active() -> None:
    root = Path(__file__).resolve().parents[1]
    env_text = (root / "config/templates/generic_live.env.example").read_text()
    assert "CAERUS_GENERIC_LIVE_KILL_SWITCH=1" in env_text
    assert "CAERUS_GENERIC_LIVE_SUBMIT_APPROVED=0" in env_text
    assert "CAERUS_GENERIC_LIVE_SCHEDULE_ENABLED=0" in env_text
    assert "API_KEY=" not in env_text and "SECRET_KEY=" not in env_text
    policy_text = (root / "config/templates/generic_live_lane_deployment_policy.template.json").read_text()
    assert '"status": "TEMPLATE_ONLY_NOT_ACTIVE"' in policy_text
    assert '"enabled": false' in policy_text
    assert '"activation_authority": false' in policy_text
    runbook = (root / "docs/runbooks/GENERIC_LIVE_CUTOVER.md").read_text()
    assert "Generic PAPER is not cut over" in runbook
    assert "legacy Live remains code-level disabled" in runbook
