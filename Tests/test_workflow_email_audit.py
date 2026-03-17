from pathlib import Path


def test_workflow_inventory_matches_repo_files() -> None:
    workflow_dir = Path(".github/workflows")
    actual = {path.name for path in workflow_dir.glob("*.yml")}
    expected = {
        "_archived_backtest_sleeve1.yml",
        "_archived_backtest_sleeve1_robustness.yml",
        "_archived_backtest_sleeve2.yml",
        "alpha_daily.yml",
        "daily-alpaca-paper.yml",
        "export-broker-snapshot.yml",
        "research-digest.yml",
    }

    assert actual == expected


def test_daily_alpaca_email_steps_and_debug_defaults_are_pinned() -> None:
    workflow = Path(".github/workflows/daily-alpaca-paper.yml").read_text(encoding="utf-8")

    assert 'EMAIL_INLINE_REPORTS: "0"' in workflow
    assert 'EMAIL_INTERNAL_DEBUG: "0"' in workflow
    assert "Send pre-trade execution status email" in workflow
    assert "Send trading confirmation email from execution results" in workflow
    assert "outputs/execution_email/${{ env.REPORT_DATE }}.json" in workflow


def test_daily_alpaca_workflow_has_only_schedule_and_dispatch_triggers() -> None:
    workflow = Path(".github/workflows/daily-alpaca-paper.yml").read_text(encoding="utf-8")

    assert "workflow_dispatch:" in workflow
    assert "schedule:" in workflow
    assert "workflow_call:" not in workflow
    assert "repository_dispatch:" not in workflow
    assert "pull_request:" not in workflow
    assert "push:" not in workflow


def test_daily_alpaca_retry_chain_uses_always_for_failed_primary_handoff() -> None:
    workflow = Path(".github/workflows/daily-alpaca-paper.yml").read_text(encoding="utf-8")

    assert "if: ${{ always() && needs.classify_stage.outputs.workflow_stage == 'live' && needs.live_primary.outputs.retry_eligible == 'true' }}" in workflow
    assert "if: ${{ always() && needs.classify_stage.outputs.workflow_stage == 'live' && needs.live_primary.outputs.retry_eligible == 'true' && needs.retry_precompute.result == 'success' }}" in workflow
    assert "if: ${{ always() && needs.classify_stage.outputs.workflow_stage == 'live' && (needs.live_retry.outputs.continuation_eligible == 'true' || needs.live_primary.outputs.continuation_eligible == 'true') }}" in workflow


def test_daily_quant_report_has_inline_email_guard_for_workflow_dedupe() -> None:
    source = Path("daily_quant_report.py").read_text(encoding="utf-8")

    assert "def _deliver_inline_report_emails(" in source
    assert 'os.getenv("EMAIL_INLINE_REPORTS")' in source
    assert "[EMAIL] inline report delivery disabled; persisted workflow email job is authoritative" in source
