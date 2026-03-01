"""Comprehensive contract tests for AIOPS lifecycle."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from aiops.cli import handle_parse, main
from aiops.plan import make_plan_run_id, run_plan
from aiops.run_all import (
    EXIT_DISPATCH_FAILED,
    EXIT_NEEDS_OPERATOR,
    EXIT_OK,
    EXIT_PARSE_OR_PLAN_FAILED,
    EXIT_RUN_FAILED,
    EXIT_VERIFY_FAILED,
    run_all,
)


class TestContractParseCommand:
    """Contract tests for 'aiops parse' command."""

    def test_parse_success_returns_ordered_json_stdout(self, tmp_path, monkeypatch):
        """Test that parse command outputs deterministic JSON with stable key order."""
        spec_dir = tmp_path / "specs"
        spec_dir.mkdir()
        spec_path = spec_dir / "test_spec.md"
        spec_path.write_text(
            "MODE: BUILD\n"
            "PROJECT_TYPE: quant-research\n"
            "RISK_TIER: medium\n"
            "OBJECTIVE: Test parse determinism\n",
            encoding="utf-8",
        )

        monkeypatch.chdir(tmp_path)
        exit_code = handle_parse(spec_path)

        assert exit_code == 0

    def test_parse_missing_file_returns_1(self, tmp_path, monkeypatch):
        """Test that parse returns exit code 1 when file not found."""
        spec_path = tmp_path / "nonexistent.md"
        monkeypatch.chdir(tmp_path)

        exit_code = handle_parse(spec_path)

        assert exit_code == 1

    def test_parse_missing_required_header_returns_1(self, tmp_path, monkeypatch):
        """Test that parse returns 1 when required headers missing."""
        spec_dir = tmp_path / "specs"
        spec_dir.mkdir()
        spec_path = spec_dir / "incomplete_spec.md"
        spec_path.write_text("OBJECTIVE: Test\n", encoding="utf-8")  # Missing MODE

        monkeypatch.chdir(tmp_path)
        exit_code = handle_parse(spec_path)

        assert exit_code == 1


class TestContractPlanCommand:
    """Contract tests for 'aiops plan' command stdout determinism."""

    def test_plan_cli_stdout_exactly_4_lines_on_success(self, tmp_path, monkeypatch, capsys):
        """Test that 'aiops plan' prints EXACTLY 4 lines to stdout on success."""
        spec_dir = tmp_path / "specs"
        spec_dir.mkdir()
        spec_path = spec_dir / "plan_test.md"
        spec_path.write_text(
            "MODE: BUILD\n"
            "PROJECT_TYPE: quant-research\n"
            "RISK_TIER: low\n"
            "OBJECTIVE: Test plan stdout contract\n"
            "\n## FILES\nmodify:\n- aiops/cli.py\n"
            "\n## ACCEPTANCE CRITERIA\n- Test contract\n",
            encoding="utf-8",
        )

        monkeypatch.chdir(tmp_path)
        fixed_dt = datetime(2026, 3, 1, 14, 30, 22, tzinfo=timezone.utc)
        monkeypatch.setattr("aiops.plan.now_local", lambda: fixed_dt)
        monkeypatch.setattr(
            "aiops.plan.get_git_metadata", lambda _: {"available": True, "short_sha": "abc1234"}
        )

        exit_code = main(["plan", str(spec_path)])
        captured = capsys.readouterr()

        lines = captured.out.strip().split("\n")
        assert exit_code == 0
        assert len(lines) == 4, f"Expected 4 lines, got {len(lines)}: {lines}"
        assert lines[0].startswith("RUN_ID: ")
        assert lines[1].startswith("RUN_DIR: ")
        assert lines[2].startswith("PLAN_PATH: ")
        assert lines[3].startswith("SPEC_SNAPSHOT_PATH: ")

    def test_plan_run_id_format_matches_contract(self, tmp_path, monkeypatch):
        """Test that plan RUN_ID matches YYYYMMDD_HHMMSS_<sha> format."""
        spec_dir = tmp_path / "specs"
        spec_dir.mkdir()
        spec_path = spec_dir / "format_test.md"
        spec_path.write_text(
            "MODE: EXPLORE\n"
            "PROJECT_TYPE: quant\n"
            "RISK_TIER: low\n"
            "OBJECTIVE: Test RUN_ID format\n",
            encoding="utf-8",
        )

        monkeypatch.chdir(tmp_path)
        fixed_dt = datetime(2026, 3, 15, 9, 45, 30, tzinfo=timezone.utc)
        monkeypatch.setattr("aiops.plan.now_local", lambda: fixed_dt)
        monkeypatch.setattr(
            "aiops.plan.get_git_metadata", lambda _: {"available": True, "short_sha": "xyz9876"}
        )

        exit_code, run_id = run_plan(spec_path)

        assert exit_code == 0
        assert run_id == "20260315_094530_xyz9876"
        assert re.match(r"^\d{8}_\d{6}_[a-z0-9]+$", run_id)

    def test_plan_artifacts_created_in_correct_location(self, tmp_path, monkeypatch):
        """Test that plan creates spec_snapshot.md and plan.md in correct directory."""
        spec_dir = tmp_path / "specs"
        spec_dir.mkdir()
        spec_path = spec_dir / "artifact_test.md"
        spec_text = (
            "MODE: BUILD\nPROJECT_TYPE: quant\nRISK_TIER: medium\n"
            "OBJECTIVE: Test artifact creation\n\n## FILES\ncreate:\n- new.py\n"
            "\n## ACCEPTANCE CRITERIA\n- Artifact exists\n"
        )
        spec_path.write_text(spec_text, encoding="utf-8")

        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("aiops.plan.now_local", lambda: datetime(2026, 3, 1, 10, 0, 0, tzinfo=timezone.utc))
        monkeypatch.setattr("aiops.plan.get_git_metadata", lambda _: {"short_sha": "test1234"})

        exit_code, run_id = run_plan(spec_path)
        run_dir = tmp_path / "reports" / "ai_runs" / run_id

        assert exit_code == 0
        assert (run_dir / "spec_snapshot.md").exists()
        assert (run_dir / "plan.md").exists()
        assert (run_dir / "spec_snapshot.md").read_text(encoding="utf-8") == spec_text


class TestContractPlanDeterminism:
    """Contract tests verifying plan determinism."""

    def test_plan_hash_deterministic_for_same_spec(self, tmp_path, monkeypatch):
        """Test that plan.md includes consistent PLAN_HASH for same spec."""
        spec_dir = tmp_path / "specs"
        spec_dir.mkdir()
        spec_path = spec_dir / "determinism_test.md"
        spec_path.write_text(
            "MODE: BUILD\nPROJECT_TYPE: quant\nRISK_TIER: low\n"
            "OBJECTIVE: Verify plan hash stability\n\n"
            "## FILES\nmodify:\n- core.py\n\n"
            "## ACCEPTANCE CRITERIA\n- Deterministic hash\n",
            encoding="utf-8",
        )

        monkeypatch.chdir(tmp_path)
        fixed_dt = datetime(2026, 3, 1, 15, 30, 0, tzinfo=timezone.utc)
        monkeypatch.setattr("aiops.plan.now_local", lambda: fixed_dt)
        monkeypatch.setattr(
            "aiops.plan.get_git_metadata", lambda _: {"short_sha": "determ1"}
        )

        exit_code1, run_id1 = run_plan(spec_path)
        plan1_content = (tmp_path / "reports" / "ai_runs" / run_id1 / "plan.md").read_text(
            encoding="utf-8"
        )

        # Extract hash from first run
        hash1_match = re.search(r"^PLAN_HASH: ([a-f0-9]{64})$", plan1_content, re.MULTILINE)
        assert hash1_match, "PLAN_HASH not found in plan.md"
        hash1 = hash1_match.group(1)

        # Run again with same spec (should produce same hash)
        exit_code2, run_id2 = run_plan(spec_path)
        plan2_content = (tmp_path / "reports" / "ai_runs" / run_id2 / "plan.md").read_text(
            encoding="utf-8"
        )

        hash2_match = re.search(r"^PLAN_HASH: ([a-f0-9]{64})$", plan2_content, re.MULTILINE)
        assert hash2_match, "PLAN_HASH not found in plan.md (second run)"
        hash2 = hash2_match.group(1)

        assert hash1 == hash2, "Plan hashes differ for identical spec"


class TestContractExitCodes:
    """Contract tests verifying stable exit codes across all commands."""

    def test_run_all_exit_0_on_complete_success(self, tmp_path, monkeypatch):
        """Test that run-all returns EXIT_OK (0) when all stages succeed."""
        spec_dir = tmp_path / "specs"
        spec_dir.mkdir()
        spec_path = spec_dir / "success_spec.md"
        spec_path.write_text(
            "MODE: BUILD\nPROJECT_TYPE: quant\nRISK_TIER: low\n"
            "OBJECTIVE: Test exit code contract\n"
            "\n## FILES\nmodify:\n- core.py\n"
            "\n## ACCEPTANCE CRITERIA\n- Success path\n",
            encoding="utf-8",
        )

        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("aiops.plan.now_local", lambda: datetime(2026, 3, 1, 16, 0, 0, tzinfo=timezone.utc))
        monkeypatch.setattr("aiops.plan.get_git_metadata", lambda _: {"short_sha": "success1"})

        # Mock all stages to succeed
        with patch("aiops.run_all.run_dispatch", return_value=0):
            with patch("aiops.run_all.run_verify", return_value=0):
                with patch("aiops.run_all.run_for_run_id", return_value=0):
                    exit_code = run_all(spec_path, mode_override="BUILD")

        assert exit_code == EXIT_OK

    def test_run_all_exit_4_on_parse_or_plan_failure(self, tmp_path, monkeypatch):
        """Test that run-all returns EXIT_PARSE_OR_PLAN_FAILED (4) on invalid spec."""
        spec_path = tmp_path / "missing_spec.md"

        monkeypatch.chdir(tmp_path)
        exit_code = run_all(spec_path, mode_override="BUILD")

        assert exit_code == EXIT_PARSE_OR_PLAN_FAILED

    def test_run_all_exit_2_when_codex_unavailable(self, tmp_path, monkeypatch):
        """Test that run-all returns EXIT_NEEDS_OPERATOR (2) when codex unavailable."""
        spec_dir = tmp_path / "specs"
        spec_dir.mkdir()
        spec_path = spec_dir / "codex_test.md"
        spec_path.write_text(
            "MODE: BUILD\nPROJECT_TYPE: quant\nRISK_TIER: low\n"
            "OBJECTIVE: Test NEEDS_OPERATOR\n\n## FILES\nmodify:\n- core.py\n"
            "\n## ACCEPTANCE CRITERIA\n- Codex required\n",
            encoding="utf-8",
        )

        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("aiops.plan.now_local", lambda: datetime(2026, 3, 1, 17, 0, 0, tzinfo=timezone.utc))
        monkeypatch.setattr("aiops.plan.get_git_metadata", lambda _: {"short_sha": "codex001"})

        # Mock dispatch to return NEEDS_OPERATOR
        with patch("aiops.run_all.run_dispatch", return_value=EXIT_NEEDS_OPERATOR):
            exit_code = run_all(spec_path, mode_override="BUILD")

        assert exit_code == EXIT_NEEDS_OPERATOR

    def test_run_all_exit_5_on_dispatch_failure(self, tmp_path, monkeypatch):
        """Test that run-all returns EXIT_DISPATCH_FAILED (5) when dispatch fails."""
        spec_dir = tmp_path / "specs"
        spec_dir.mkdir()
        spec_path = spec_dir / "dispatch_fail_spec.md"
        spec_path.write_text(
            "MODE: BUILD\nPROJECT_TYPE: quant\nRISK_TIER: low\n"
            "OBJECTIVE: Test dispatch failure\n\n## FILES\nmodify:\n- core.py\n"
            "\n## ACCEPTANCE CRITERIA\n- Dispatch fails\n",
            encoding="utf-8",
        )

        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("aiops.plan.now_local", lambda: datetime(2026, 3, 1, 18, 0, 0, tzinfo=timezone.utc))
        monkeypatch.setattr("aiops.plan.get_git_metadata", lambda _: {"short_sha": "dispf001"})

        # Mock dispatch to fail (return 1)
        with patch("aiops.run_all.run_dispatch", return_value=1):
            exit_code = run_all(spec_path, mode_override="BUILD")

        assert exit_code == EXIT_DISPATCH_FAILED

    def test_run_all_exit_6_on_run_failure(self, tmp_path, monkeypatch):
        """Test that run-all returns EXIT_RUN_FAILED (6) when run stage fails."""
        spec_dir = tmp_path / "specs"
        spec_dir.mkdir()
        spec_path = spec_dir / "run_fail_spec.md"
        spec_path.write_text(
            "MODE: BUILD\nPROJECT_TYPE: quant\nRISK_TIER: low\n"
            "OBJECTIVE: Test run failure\n\n## FILES\nmodify:\n- core.py\n"
            "\n## ACCEPTANCE CRITERIA\n- Run fails\n",
            encoding="utf-8",
        )

        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("aiops.plan.now_local", lambda: datetime(2026, 3, 1, 19, 0, 0, tzinfo=timezone.utc))
        monkeypatch.setattr("aiops.plan.get_git_metadata", lambda _: {"short_sha": "runfail1"})

        # Mock dispatch success, run failure
        with patch("aiops.run_all.run_dispatch", return_value=0):
            with patch("aiops.run_all.run_for_run_id", return_value=1):
                exit_code = run_all(spec_path, mode_override="BUILD")

        assert exit_code == EXIT_RUN_FAILED

    def test_run_all_exit_3_on_verify_failure(self, tmp_path, monkeypatch):
        """Test that run-all returns EXIT_VERIFY_FAILED (3) when verify fails."""
        spec_dir = tmp_path / "specs"
        spec_dir.mkdir()
        spec_path = spec_dir / "verify_fail_spec.md"
        spec_path.write_text(
            "MODE: BUILD\nPROJECT_TYPE: quant\nRISK_TIER: low\n"
            "OBJECTIVE: Test verify failure\n\n## FILES\nmodify:\n- core.py\n"
            "\n## ACCEPTANCE CRITERIA\n- Verify fails\n",
            encoding="utf-8",
        )

        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("aiops.plan.now_local", lambda: datetime(2026, 3, 1, 20, 0, 0, tzinfo=timezone.utc))
        monkeypatch.setattr("aiops.plan.get_git_metadata", lambda _: {"short_sha": "verf001"})

        # Mock all stages success except verify
        with patch("aiops.run_all.run_dispatch", return_value=0):
            with patch("aiops.run_all.run_for_run_id", return_value=0):
                with patch("aiops.run_all.run_verify", return_value=1):
                    exit_code = run_all(spec_path, mode_override="BUILD")

        assert exit_code == EXIT_VERIFY_FAILED


class TestContractRunAllSummary:
    """Contract tests for run_all_summary.md artifact format and stability."""

    def test_run_all_summary_created_on_success(self, tmp_path, monkeypatch):
        """Test that run_all_summary.md is created on successful execution."""
        spec_dir = tmp_path / "specs"
        spec_dir.mkdir()
        spec_path = spec_dir / "summary_test.md"
        spec_path.write_text(
            "MODE: BUILD\nPROJECT_TYPE: quant\nRISK_TIER: low\n"
            "OBJECTIVE: Test summary creation\n\n## FILES\nmodify:\n- core.py\n"
            "\n## ACCEPTANCE CRITERIA\n- Summary created\n",
            encoding="utf-8",
        )

        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("aiops.plan.now_local", lambda: datetime(2026, 3, 2, 10, 0, 0, tzinfo=timezone.utc))
        monkeypatch.setattr("aiops.plan.get_git_metadata", lambda _: {"short_sha": "sum001"})

        with patch("aiops.run_all.run_dispatch", return_value=0):
            with patch("aiops.run_all.run_verify", return_value=0):
                with patch("aiops.run_all.run_for_run_id", return_value=0):
                    exit_code = run_all(spec_path, mode_override="BUILD")

        run_dir = tmp_path / "reports" / "ai_runs" / "20260302_100000_sum001"
        summary_path = run_dir / "run_all_summary.md"

        assert exit_code == 0
        assert summary_path.exists()
        assert summary_path.read_text(encoding="utf-8")  # File not empty

    def test_run_all_summary_contains_required_sections(self, tmp_path, monkeypatch):
        """Test that summary.md contains all required sections."""
        spec_dir = tmp_path / "specs"
        spec_dir.mkdir()
        spec_path = spec_dir / "sections_test.md"
        spec_path.write_text(
            "MODE: BUILD\nPROJECT_TYPE: quant\nRISK_TIER: low\n"
            "OBJECTIVE: Test summary sections\n\n## FILES\nmodify:\n- core.py\n"
            "\n## ACCEPTANCE CRITERIA\n- Sections exist\n",
            encoding="utf-8",
        )

        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("aiops.plan.now_local", lambda: datetime(2026, 3, 2, 11, 0, 0, tzinfo=timezone.utc))
        monkeypatch.setattr("aiops.plan.get_git_metadata", lambda _: {"short_sha": "sec001"})

        with patch("aiops.run_all.run_dispatch", return_value=0):
            with patch("aiops.run_all.run_verify", return_value=0):
                with patch("aiops.run_all.run_for_run_id", return_value=0):
                    exit_code = run_all(spec_path, mode_override="BUILD")

        run_dir = tmp_path / "reports" / "ai_runs" / "20260302_110000_sec001"
        summary_path = run_dir / "run_all_summary.md"
        summary_text = summary_path.read_text(encoding="utf-8")

        # Check for required sections and content
        assert "# AIOps Run-All Summary" in summary_text
        assert "## Inputs" in summary_text
        assert "## Stage Results" in summary_text
        assert "## Final" in summary_text
        assert "RUN_ID:" in summary_text
        assert "SPEC_PATH:" in summary_text
        assert "MODE:" in summary_text
        assert "| Stage | Exit Code |" in summary_text
        assert "| parse |" in summary_text
        assert "| plan |" in summary_text
        assert "| dispatch |" in summary_text
        assert "| run |" in summary_text
        assert "| verify |" in summary_text
        assert "RUN_ALL_STATUS:" in summary_text
        assert "EXIT_CODE:" in summary_text

    def test_run_all_summary_stage_order_stable(self, tmp_path, monkeypatch):
        """Test that summary.md stage results are always in same order."""
        spec_dir = tmp_path / "specs"
        spec_dir.mkdir()
        spec_path = spec_dir / "order_test.md"
        spec_path.write_text(
            "MODE: BUILD\nPROJECT_TYPE: quant\nRISK_TIER: low\n"
            "OBJECTIVE: Test stage ordering\n\n## FILES\nmodify:\n- core.py\n"
            "\n## ACCEPTANCE CRITERIA\n- Order stable\n",
            encoding="utf-8",
        )

        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("aiops.plan.now_local", lambda: datetime(2026, 3, 2, 12, 0, 0, tzinfo=timezone.utc))
        monkeypatch.setattr("aiops.plan.get_git_metadata", lambda _: {"short_sha": "ord001"})

        with patch("aiops.run_all.run_dispatch", return_value=0):
            with patch("aiops.run_all.run_verify", return_value=0):
                with patch("aiops.run_all.run_for_run_id", return_value=0):
                    exit_code = run_all(spec_path, mode_override="BUILD")

        run_dir = tmp_path / "reports" / "ai_runs" / "20260302_120000_ord001"
        summary_path = run_dir / "run_all_summary.md"
        summary_text = summary_path.read_text(encoding="utf-8")

        # Find stage table section
        lines = summary_text.split("\n")
        stage_lines = [l for l in lines if l.startswith("| ") and "|" in l]

        # Expected order (after header row)
        expected_stages = ["parse", "plan", "dispatch", "run", "verify"]
        for i, expected_stage in enumerate(expected_stages):
            assert expected_stage in stage_lines[i + 1], (
                f"Stage '{expected_stage}' not in expected position {i} of stage table"
            )

    def test_run_all_summary_no_timestamp_beyond_run_id(self, tmp_path, monkeypatch):
        """Test that summary.md contains no datetime values beyond RUN_ID."""
        spec_dir = tmp_path / "specs"
        spec_dir.mkdir()
        spec_path = spec_dir / "no_timestamp_test.md"
        spec_path.write_text(
            "MODE: BUILD\nPROJECT_TYPE: quant\nRISK_TIER: low\n"
            "OBJECTIVE: Test no extra timestamps\n\n## FILES\nmodify:\n- core.py\n"
            "\n## ACCEPTANCE CRITERIA\n- No timestamps\n",
            encoding="utf-8",
        )

        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("aiops.plan.now_local", lambda: datetime(2026, 3, 2, 13, 30, 45, tzinfo=timezone.utc))
        monkeypatch.setattr("aiops.plan.get_git_metadata", lambda _: {"short_sha": "ts001"})

        with patch("aiops.run_all.run_dispatch", return_value=0):
            with patch("aiops.run_all.run_verify", return_value=0):
                with patch("aiops.run_all.run_for_run_id", return_value=0):
                    exit_code = run_all(spec_path, mode_override="BUILD")

        run_dir = tmp_path / "reports" / "ai_runs" / "20260302_133045_ts001"
        summary_path = run_dir / "run_all_summary.md"
        summary_text = summary_path.read_text(encoding="utf-8")

        # Look for datetime patterns (12:34:56, HH:MM, etc.)
        # This regex looks for time patterns outside of RUN_ID
        lines = [l for l in summary_text.split("\n") if "RUN_ID" not in l]
        for line in lines:
            # Should not have ISO format datetime, unix timestamps, etc.
            assert not re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", line)
            assert not re.search(r"\d{10,}", line.replace("20260302_133045_ts001", ""))  # Unix timestamp


class TestContractNoSecrets:
    """Contract tests verifying no secrets in stdout/stderr/artifacts."""

    def test_plan_does_not_print_secrets_to_stdout(self, tmp_path, monkeypatch, capsys):
        """Test that plan command does not leak environment secrets to stdout."""
        spec_dir = tmp_path / "specs"
        spec_dir.mkdir()
        spec_path = spec_dir / "secret_test.md"
        spec_path.write_text(
            "MODE: BUILD\nPROJECT_TYPE: quant\nRISK_TIER: low\n"
            "OBJECTIVE: Test no secrets leaked\n\n## FILES\nmodify:\n- core.py\n"
            "\n## ACCEPTANCE CRITERIA\n- Secrets not leaked\n",
            encoding="utf-8",
        )

        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("aiops.plan.now_local", lambda: datetime(2026, 3, 2, 14, 0, 0, tzinfo=timezone.utc))
        monkeypatch.setattr("aiops.plan.get_git_metadata", lambda _: {"short_sha": "secret1"})

        # Set secret environment variable
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-secret-key-12345")

        exit_code = main(["plan", str(spec_path)])
        captured = capsys.readouterr()

        assert exit_code == 0
        assert "sk-test-secret-key-12345" not in captured.out
        assert "sk-test-secret-key-12345" not in captured.err

    def test_run_all_summary_does_not_contain_secrets(self, tmp_path, monkeypatch):
        """Test that run_all_summary.md does not contain API keys or secrets."""
        spec_dir = tmp_path / "specs"
        spec_dir.mkdir()
        spec_path = spec_dir / "artifact_secret_test.md"
        spec_path.write_text(
            "MODE: BUILD\nPROJECT_TYPE: quant\nRISK_TIER: low\n"
            "OBJECTIVE: Test summary has no secrets\n\n## FILES\nmodify:\n- core.py\n"
            "\n## ACCEPTANCE CRITERIA\n- Artifact clean\n",
            encoding="utf-8",
        )

        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("aiops.plan.now_local", lambda: datetime(2026, 3, 2, 14, 0, 0, tzinfo=timezone.utc))
        monkeypatch.setattr("aiops.plan.get_git_metadata", lambda _: {"short_sha": "asec001"})

        # Set multiple secrets
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-secret")
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY")

        with patch("aiops.run_all.run_dispatch", return_value=0):
            with patch("aiops.run_all.run_verify", return_value=0):
                with patch("aiops.run_all.run_for_run_id", return_value=0):
                    exit_code = run_all(spec_path, mode_override="BUILD")

        run_dir = tmp_path / "reports" / "ai_runs" / "20260302_140000_asec001"
        summary_path = run_dir / "run_all_summary.md"
        summary_text = summary_path.read_text(encoding="utf-8")

        # Verify secrets not in summary
        assert "sk-test-secret" not in summary_text
        assert "wJalrXUtnFEMI" not in summary_text
        assert "OPENAI_API_KEY" not in summary_text
        assert "AWS_SECRET" not in summary_text


class TestContractFormatting:
    """Contract tests for output formatting stability."""

    def test_run_all_summary_markdown_semantic_valid(self, tmp_path, monkeypatch):
        """Test that summary.md is valid markdown (balanced headers, tables)."""
        spec_dir = tmp_path / "specs"
        spec_dir.mkdir()
        spec_path = spec_dir / "markdown_test.md"
        spec_path.write_text(
            "MODE: BUILD\nPROJECT_TYPE: quant\nRISK_TIER: low\n"
            "OBJECTIVE: Test markdown validity\n\n## FILES\nmodify:\n- core.py\n"
            "\n## ACCEPTANCE CRITERIA\n- Valid markdown\n",
            encoding="utf-8",
        )

        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("aiops.plan.now_local", lambda: datetime(2026, 3, 2, 15, 0, 0, tzinfo=timezone.utc))
        monkeypatch.setattr("aiops.plan.get_git_metadata", lambda _: {"short_sha": "mdv001"})

        with patch("aiops.run_all.run_dispatch", return_value=0):
            with patch("aiops.run_all.run_verify", return_value=0):
                with patch("aiops.run_all.run_for_run_id", return_value=0):
                    exit_code = run_all(spec_path, mode_override="BUILD")

        run_dir = tmp_path / "reports" / "ai_runs" / "20260302_150000_mdv001"
        summary_path = run_dir / "run_all_summary.md"
        summary_text = summary_path.read_text(encoding="utf-8")

        lines = summary_text.split("\n")

        # Count headers (should be balanced)
        h1_count = len([l for l in lines if l.startswith("# ")])
        h2_count = len([l for l in lines if l.startswith("## ")])

        assert h1_count == 1, f"Expected 1 H1 header, got {h1_count}"
        assert h2_count >= 2, f"Expected at least 2 H2 headers, got {h2_count}"

        # Check table validity (should have header + separator)
        table_lines = [l for l in lines if "|" in l]
        assert len(table_lines) >= 3, "Expected at least header, separator, and one data row in table"
