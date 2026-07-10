"""Comprehensive tests for the precompute artifact fetch and bundle discovery pipeline.

These tests cover every failure mode observed in production and every extraction
layout that GitHub Actions / gh CLI can produce.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from core.precompute_contract import (
    BUNDLE_REQUIRED_FILES,
    check_bundle_completeness,
    discover_bundle_root,
    normalize_bundle_to_canonical,
)
from scripts.fetch_precompute_artifact import (
    artifact_name_for_report_date,
    fetch_precompute_artifact,
)

REPORT_DATE = "2026-03-18"
ARTIFACT_NAME = f"alpaca-precompute-{REPORT_DATE}"
BUNDLE_FILES = {"contract.json", "daily_snapshot.json", "signals.json", "planned_execution_payload.json"}


class _Result:
    def __init__(self, stdout: str = "") -> None:
        self.stdout = stdout


def _write_full_bundle(bundle_dir: Path) -> None:
    """Write a complete bundle (all 4 required files) into *bundle_dir*."""
    bundle_dir.mkdir(parents=True, exist_ok=True)
    for name in BUNDLE_FILES:
        (bundle_dir / name).write_text("{}", encoding="utf-8")


def _one_successful_run_json(run_id: int = 12345) -> str:
    return json.dumps([
        {
            "databaseId": run_id,
            "status": "completed",
            "conclusion": "success",
            "headBranch": "main",
            "createdAt": f"{REPORT_DATE}T11:31:00Z",
        }
    ])


# ---------------------------------------------------------------------------
# A. Artifact naming
# ---------------------------------------------------------------------------

def test_artifact_name_is_report_date_specific() -> None:
    assert artifact_name_for_report_date(REPORT_DATE) == ARTIFACT_NAME


# ---------------------------------------------------------------------------
# B. discover_bundle_root — layout-agnostic discovery (Workstream D)
# ---------------------------------------------------------------------------

class TestDiscoverBundleRoot:
    """Verify every extraction layout that has been observed or is plausible."""

    def test_layout_outputs_precompute(self, tmp_path: Path) -> None:
        """Full prefix: outputs/precompute/<date>/contract.json (upload-artifact preserves prefix)."""
        _write_full_bundle(tmp_path / "outputs" / "precompute" / REPORT_DATE)
        root = discover_bundle_root(tmp_path, REPORT_DATE)
        assert root == tmp_path / "outputs" / "precompute" / REPORT_DATE

    def test_layout_precompute(self, tmp_path: Path) -> None:
        """Stripped prefix: precompute/<date>/contract.json (outputs/ stripped by upload-artifact)."""
        _write_full_bundle(tmp_path / "precompute" / REPORT_DATE)
        root = discover_bundle_root(tmp_path, REPORT_DATE)
        assert root == tmp_path / "precompute" / REPORT_DATE

    def test_layout_flat(self, tmp_path: Path) -> None:
        """Flat: <date>/contract.json (direct extraction)."""
        _write_full_bundle(tmp_path / REPORT_DATE)
        root = discover_bundle_root(tmp_path, REPORT_DATE)
        assert root == tmp_path / REPORT_DATE

    def test_layout_artifact_name_wrapped(self, tmp_path: Path) -> None:
        """Wrapped in artifact-name dir: <artifact-name>/outputs/precompute/<date>/contract.json."""
        _write_full_bundle(tmp_path / ARTIFACT_NAME / "outputs" / "precompute" / REPORT_DATE)
        root = discover_bundle_root(tmp_path, REPORT_DATE)
        assert root == tmp_path / ARTIFACT_NAME / "outputs" / "precompute" / REPORT_DATE

    def test_layout_artifact_name_stripped_prefix(self, tmp_path: Path) -> None:
        """Wrapped but stripped: <artifact-name>/precompute/<date>/contract.json."""
        _write_full_bundle(tmp_path / ARTIFACT_NAME / "precompute" / REPORT_DATE)
        root = discover_bundle_root(tmp_path, REPORT_DATE)
        assert root == tmp_path / ARTIFACT_NAME / "precompute" / REPORT_DATE

    def test_returns_none_when_no_contract(self, tmp_path: Path) -> None:
        """No contract.json anywhere."""
        (tmp_path / "precompute" / REPORT_DATE).mkdir(parents=True)
        assert discover_bundle_root(tmp_path, REPORT_DATE) is None

    def test_returns_none_for_wrong_date(self, tmp_path: Path) -> None:
        """Contract exists but for a different date."""
        _write_full_bundle(tmp_path / "precompute" / "2026-03-17")
        assert discover_bundle_root(tmp_path, REPORT_DATE) is None


# ---------------------------------------------------------------------------
# C. check_bundle_completeness
# ---------------------------------------------------------------------------

class TestCheckBundleCompleteness:
    def test_complete_bundle(self, tmp_path: Path) -> None:
        _write_full_bundle(tmp_path)
        complete, present, missing = check_bundle_completeness(tmp_path)
        assert complete is True
        assert set(present) == BUNDLE_FILES
        assert missing == []

    def test_missing_signals(self, tmp_path: Path) -> None:
        _write_full_bundle(tmp_path)
        (tmp_path / "signals.json").unlink()
        complete, present, missing = check_bundle_completeness(tmp_path)
        assert complete is False
        assert "signals.json" in missing
        assert "signals.json" not in present

    def test_empty_dir(self, tmp_path: Path) -> None:
        complete, present, missing = check_bundle_completeness(tmp_path)
        assert complete is False
        assert set(missing) == BUNDLE_FILES


# ---------------------------------------------------------------------------
# D. normalize_bundle_to_canonical
# ---------------------------------------------------------------------------

class TestNormalizeBundleToCanonical:
    def test_copies_to_canonical_location(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        _write_full_bundle(tmp_path / "downloaded" / "precompute" / REPORT_DATE)
        canonical = normalize_bundle_to_canonical(
            tmp_path / "downloaded" / "precompute" / REPORT_DATE,
            REPORT_DATE,
        )
        assert canonical == Path("outputs/precompute") / REPORT_DATE
        for name in BUNDLE_FILES:
            assert (canonical / name).is_file()

    def test_noop_when_already_canonical(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        canonical_dir = Path("outputs/precompute") / REPORT_DATE
        _write_full_bundle(tmp_path / str(canonical_dir))
        result = normalize_bundle_to_canonical(tmp_path / str(canonical_dir), REPORT_DATE)
        assert result.resolve() == (tmp_path / str(canonical_dir)).resolve()


# ---------------------------------------------------------------------------
# E. End-to-end fetch_precompute_artifact tests
# ---------------------------------------------------------------------------

class TestFetchPrecomputeArtifact:
    """Integration tests for the full fetch pipeline."""

    def test_no_successful_runs_returns_missing(self, monkeypatch, tmp_path: Path) -> None:
        monkeypatch.chdir(tmp_path)

        def _fake_run(args: list[str]) -> _Result:
            assert args[:3] == ["gh", "run", "list"]
            return _Result(stdout="[]")

        monkeypatch.setattr("scripts.fetch_precompute_artifact._run_command", _fake_run)
        payload = fetch_precompute_artifact(
            report_date=REPORT_DATE,
            destination=tmp_path / "downloaded",
            repo="owner/repo",
            branch="main",
        )
        assert payload["precompute_bundle_found"] is False
        assert payload["bundle_source"] == "none"
        assert payload["bundle_status"] == "MISSING_PRECOMPUTE_BUNDLE"

    def test_valid_artifact_layout_outputs_precompute(self, monkeypatch, tmp_path: Path) -> None:
        """Layout 1: outputs/precompute/<date>/... — full prefix preserved."""
        monkeypatch.chdir(tmp_path)
        _write_full_bundle(tmp_path / "downloaded" / "outputs" / "precompute" / REPORT_DATE)

        calls: list[list[str]] = []

        def _fake_run(args: list[str]) -> _Result:
            calls.append(args)
            if args[:3] == ["gh", "run", "list"]:
                return _Result(stdout=_one_successful_run_json())
            return _Result(stdout="")

        monkeypatch.setattr("scripts.fetch_precompute_artifact._run_command", _fake_run)
        payload = fetch_precompute_artifact(
            report_date=REPORT_DATE,
            destination=tmp_path / "downloaded",
            repo="owner/repo",
            branch="main",
        )
        assert payload["precompute_bundle_found"] is True
        assert payload["bundle_source"] == "artifact"
        assert payload["bundle_status"] == "bundle_downloaded"
        assert payload["artifact_run_id"] == "12345"
        # Canonical location must be populated.
        for name in BUNDLE_FILES:
            assert (tmp_path / "outputs" / "precompute" / REPORT_DATE / name).is_file()

    def test_valid_artifact_layout_precompute_stripped(self, monkeypatch, tmp_path: Path) -> None:
        """Layout 2: precompute/<date>/... — outputs/ stripped by upload-artifact.

        THIS IS THE EXACT LAYOUT THAT CAUSED THE ORIGINAL PRODUCTION FAILURE.
        """
        monkeypatch.chdir(tmp_path)
        _write_full_bundle(tmp_path / "downloaded" / "precompute" / REPORT_DATE)

        def _fake_run(args: list[str]) -> _Result:
            if args[:3] == ["gh", "run", "list"]:
                return _Result(stdout=_one_successful_run_json())
            return _Result(stdout="")

        monkeypatch.setattr("scripts.fetch_precompute_artifact._run_command", _fake_run)
        payload = fetch_precompute_artifact(
            report_date=REPORT_DATE,
            destination=tmp_path / "downloaded",
            repo="owner/repo",
            branch="main",
        )
        assert payload["precompute_bundle_found"] is True
        assert payload["bundle_source"] == "artifact"
        assert payload["bundle_status"] == "bundle_downloaded"
        # Canonical location must be populated.
        for name in BUNDLE_FILES:
            assert (tmp_path / "outputs" / "precompute" / REPORT_DATE / name).is_file()

    def test_valid_artifact_layout_artifact_name_wrapped(self, monkeypatch, tmp_path: Path) -> None:
        """Layout 3: <artifact-name>/outputs/precompute/<date>/... — legacy wrapped."""
        monkeypatch.chdir(tmp_path)
        _write_full_bundle(
            tmp_path / "downloaded" / ARTIFACT_NAME / "outputs" / "precompute" / REPORT_DATE
        )
        wf = tmp_path / "downloaded" / ARTIFACT_NAME / "outputs" / "workflow_status" / REPORT_DATE
        wf.mkdir(parents=True)
        (wf / "precompute_workflow_summary.json").write_text("{}", encoding="utf-8")

        def _fake_run(args: list[str]) -> _Result:
            if args[:3] == ["gh", "run", "list"]:
                return _Result(stdout=_one_successful_run_json())
            return _Result(stdout="")

        monkeypatch.setattr("scripts.fetch_precompute_artifact._run_command", _fake_run)
        payload = fetch_precompute_artifact(
            report_date=REPORT_DATE,
            destination=tmp_path / "downloaded",
            repo="owner/repo",
            branch="main",
        )
        assert payload["precompute_bundle_found"] is True
        assert payload["bundle_status"] == "bundle_downloaded"

    def test_stale_skipped_artifact_stops_search(self, monkeypatch, tmp_path: Path) -> None:
        """Artifact uploaded by stale-blocked precompute — no contract.json, has summary."""
        monkeypatch.chdir(tmp_path)
        wf = tmp_path / "downloaded" / "workflow_status" / REPORT_DATE
        wf.mkdir(parents=True, exist_ok=True)
        (wf / "precompute_workflow_summary.json").write_text(
            json.dumps({"bundle_status": "SKIPPED_AS_STALE", "workflow_kind": "precompute"}),
            encoding="utf-8",
        )

        def _fake_run(args: list[str]) -> _Result:
            if args[:3] == ["gh", "run", "list"]:
                return _Result(
                    stdout=json.dumps([
                        {"databaseId": 77777, "status": "completed", "conclusion": "success",
                         "headBranch": "main", "createdAt": f"{REPORT_DATE}T14:00:00Z"},
                        {"databaseId": 66666, "status": "completed", "conclusion": "success",
                         "headBranch": "main", "createdAt": f"{REPORT_DATE}T07:30:00Z"},
                    ])
                )
            return _Result(stdout="")

        calls: list[list[str]] = []
        original_fake = _fake_run

        def _tracking_fake(args: list[str]) -> _Result:
            calls.append(args)
            return original_fake(args)

        monkeypatch.setattr("scripts.fetch_precompute_artifact._run_command", _tracking_fake)
        payload = fetch_precompute_artifact(
            report_date=REPORT_DATE,
            destination=tmp_path / "downloaded",
            repo="owner/repo",
            branch="main",
        )
        assert payload["precompute_bundle_found"] is False
        assert payload["bundle_status"] == "bundle_skipped_as_stale"
        # Must NOT have tried downloading from the second (older) run.
        download_calls = [c for c in calls if c[:3] == ["gh", "run", "download"]]
        assert len(download_calls) == 1
        assert download_calls[0][3] == "77777"

    def test_empty_artifact_no_summary_is_invalid(self, monkeypatch, tmp_path: Path) -> None:
        """Artifact downloaded but empty — no contract.json and no summary."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "downloaded").mkdir(parents=True)

        def _fake_run(args: list[str]) -> _Result:
            if args[:3] == ["gh", "run", "list"]:
                return _Result(stdout=_one_successful_run_json(88888))
            return _Result(stdout="")

        monkeypatch.setattr("scripts.fetch_precompute_artifact._run_command", _fake_run)
        payload = fetch_precompute_artifact(
            report_date=REPORT_DATE,
            destination=tmp_path / "downloaded",
            repo="owner/repo",
            branch="main",
        )
        assert payload["precompute_bundle_found"] is False
        assert payload["bundle_status"] == "bundle_invalid"

    def test_incomplete_bundle_missing_signals(self, monkeypatch, tmp_path: Path) -> None:
        """Artifact has contract.json but missing companion file."""
        monkeypatch.chdir(tmp_path)
        bundle_dir = tmp_path / "downloaded" / "precompute" / REPORT_DATE
        _write_full_bundle(bundle_dir)
        (bundle_dir / "signals.json").unlink()

        def _fake_run(args: list[str]) -> _Result:
            if args[:3] == ["gh", "run", "list"]:
                return _Result(stdout=_one_successful_run_json())
            return _Result(stdout="")

        monkeypatch.setattr("scripts.fetch_precompute_artifact._run_command", _fake_run)
        payload = fetch_precompute_artifact(
            report_date=REPORT_DATE,
            destination=tmp_path / "downloaded",
            repo="owner/repo",
            branch="main",
        )
        assert payload["precompute_bundle_found"] is False
        assert payload["bundle_status"] == "bundle_invalid"
        assert "signals.json" in payload["bundle_required_files_missing"]

    def test_all_downloads_fail_returns_missing(self, monkeypatch, tmp_path: Path) -> None:
        """Every gh run download throws — artifact name not on any run."""
        monkeypatch.chdir(tmp_path)

        def _fake_run(args: list[str]) -> _Result:
            if args[:3] == ["gh", "run", "list"]:
                return _Result(stdout=_one_successful_run_json())
            raise subprocess.CalledProcessError(1, args, stderr="no artifact found")

        monkeypatch.setattr("scripts.fetch_precompute_artifact._run_command", _fake_run)
        payload = fetch_precompute_artifact(
            report_date=REPORT_DATE,
            destination=tmp_path / "downloaded",
            repo="owner/repo",
            branch="main",
        )
        assert payload["precompute_bundle_found"] is False
        assert payload["bundle_status"] == "MISSING_PRECOMPUTE_BUNDLE"

    def test_operator_fields_correct_on_success(self, monkeypatch, tmp_path: Path) -> None:
        """Verify all operator-visible fields are set correctly on success."""
        monkeypatch.chdir(tmp_path)
        _write_full_bundle(tmp_path / "downloaded" / "precompute" / REPORT_DATE)

        def _fake_run(args: list[str]) -> _Result:
            if args[:3] == ["gh", "run", "list"]:
                return _Result(stdout=_one_successful_run_json(99999))
            return _Result(stdout="")

        monkeypatch.setattr("scripts.fetch_precompute_artifact._run_command", _fake_run)
        payload = fetch_precompute_artifact(
            report_date=REPORT_DATE,
            destination=tmp_path / "downloaded",
            repo="owner/repo",
            branch="main",
        )
        assert payload["precompute_bundle_required"] is True
        assert payload["precompute_bundle_found"] is True
        assert payload["bundle_source"] == "artifact"
        assert payload["bundle_report_date"] == REPORT_DATE
        assert payload["bundle_status"] == "bundle_downloaded"
        assert payload["artifact_run_id"] == "99999"
        assert payload["normalized_bundle_root"] != ""
        assert payload["bundle_required_files_missing"] == []
        assert set(payload["bundle_required_files_present"]) == BUNDLE_FILES

    def test_operator_fields_correct_on_failure(self, monkeypatch, tmp_path: Path) -> None:
        """Verify operator-visible fields are set correctly on failure."""
        monkeypatch.chdir(tmp_path)

        def _fake_run(args: list[str]) -> _Result:
            if args[:3] == ["gh", "run", "list"]:
                return _Result(stdout="[]")
            return _Result(stdout="")

        monkeypatch.setattr("scripts.fetch_precompute_artifact._run_command", _fake_run)
        payload = fetch_precompute_artifact(
            report_date=REPORT_DATE,
            destination=tmp_path / "downloaded",
            repo="owner/repo",
            branch="main",
        )
        assert payload["precompute_bundle_required"] is True
        assert payload["precompute_bundle_found"] is False
        assert payload["bundle_source"] == "none"
        assert set(payload["bundle_required_files_missing"]) == BUNDLE_FILES

    def test_branch_filter_excludes_other_branches(self, monkeypatch, tmp_path: Path) -> None:
        monkeypatch.chdir(tmp_path)

        def _fake_run(args: list[str]) -> _Result:
            if args[:3] == ["gh", "run", "list"]:
                return _Result(stdout=json.dumps([
                    {"databaseId": 111, "status": "completed", "conclusion": "success",
                     "headBranch": "feature-branch", "createdAt": f"{REPORT_DATE}T11:31:00Z"},
                ]))
            return _Result(stdout="")

        monkeypatch.setattr("scripts.fetch_precompute_artifact._run_command", _fake_run)
        payload = fetch_precompute_artifact(
            report_date=REPORT_DATE,
            destination=tmp_path / "downloaded",
            repo="owner/repo",
            branch="main",
        )
        assert payload["precompute_bundle_found"] is False
        assert payload["bundle_status"] == "MISSING_PRECOMPUTE_BUNDLE"


# ---------------------------------------------------------------------------
# F. Current VM cron contract checks (Workstream H)
# ---------------------------------------------------------------------------

class TestWorkflowYAMLContracts:
    """Assert current VM cron invariants after GitHub trading workflows retired."""

    def test_retired_trading_workflows_are_absent(self) -> None:
        assert not Path(".github/workflows/daily-alpaca-precompute.yml").exists()
        assert not Path(".github/workflows/daily-alpaca-live.yml").exists()

    def test_cron_precompute_writes_date_scoped_bundle_and_validation(self) -> None:
        cron_precompute = Path("scripts/cron_precompute.sh").read_text(encoding="utf-8")
        assert 'BUNDLE_DIR="${REPO_ROOT}/outputs/precompute/${REPORT_DATE}"' in cron_precompute
        assert "precompute_bundle_validation.json" in cron_precompute
        assert "core.precompute_bundle_validation" in cron_precompute

    def test_cron_execute_validates_bundle_before_execution(self) -> None:
        cron_execute = Path("scripts/cron_execute.sh").read_text(encoding="utf-8")
        assert "execution_bundle_validation.json" in cron_execute
        assert "core.precompute_bundle_validation" in cron_execute
        # Failed validation (after self-heal) must halt before any engine call,
        # and the engine only runs after validation (compare code, not comments).
        assert "execution halted to avoid degraded bundle execution" in cron_execute
        code_lines = "\n".join(
            line for line in cron_execute.splitlines() if not line.lstrip().startswith("#")
        )
        assert code_lines.index("core.precompute_bundle_validation") < code_lines.index(
            "live_pilot_build_plan_from_precompute.py"
        )

    def test_cron_execute_uses_unified_engine_from_precompute(self) -> None:
        # Unified paper lane ("one engine, two endpoints"): the SAME engine as
        # the live pilot builds the plan from the validated precompute bundle
        # and executes it against the paper endpoint. The legacy exact-payload
        # path (run_precomputed_alpaca_execution) is dormant — files stay in
        # tree but the cron must not invoke it.
        cron_execute = Path("scripts/cron_execute.sh").read_text(encoding="utf-8")
        assert "scripts/live_pilot_build_plan_from_precompute.py" in cron_execute
        assert "scripts/live_pilot_execute.py" in cron_execute
        assert '--output-root "${PAPER_LANE_ROOT}"' in cron_execute
        code_lines = "\n".join(
            line for line in cron_execute.splitlines() if not line.lstrip().startswith("#")
        )
        assert "run_precomputed_alpaca_execution" not in code_lines

    def test_cron_precompute_self_heal_suppresses_noncritical_side_effects(self) -> None:
        cron_precompute = Path("scripts/cron_precompute.sh").read_text(encoding="utf-8")
        assert "SELF_HEAL_PRECOMPUTE_ONLY" in cron_precompute
        assert '"noncritical_side_effects_suppressed": true' in cron_precompute
        assert '"suppressed_side_effects": ["email", "shadow", "shadow_latest", "shadow_reconciliation"]' in cron_precompute

    def test_deprecated_wrapper_has_no_schedule(self) -> None:
        workflow = Path(".github/workflows/daily-alpaca-paper.yml").read_text(encoding="utf-8")
        assert "Deprecated Wrapper" in workflow
        assert "schedule:" not in workflow
        assert "Use VM cron in scripts/crontab.txt" in workflow

    def test_cron_helper_scripts_use_module_invocation_where_expected(self) -> None:
        cron_execute = Path("scripts/cron_execute.sh").read_text(encoding="utf-8")
        cron_precompute = Path("scripts/cron_precompute.sh").read_text(encoding="utf-8")
        assert "python3 -m core.precompute_bundle_validation" in cron_execute
        # Unified paper lane: the shared executor scripts are invoked by path
        # (same invocation style as the live lane); the dormant module
        # invocation of run_precomputed_alpaca_execution is gone.
        assert "scripts/live_pilot_execute.py" in cron_execute
        assert "python3 -m core.precompute_bundle_validation" in cron_precompute
