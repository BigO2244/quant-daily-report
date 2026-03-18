from pathlib import Path


def test_workflow_inventory_matches_repo_files() -> None:
    workflow_dir = Path(".github/workflows")
    actual = {path.name for path in workflow_dir.glob("*.yml")}
    expected = {
        "_archived_backtest_sleeve1.yml",
        "_archived_backtest_sleeve1_robustness.yml",
        "_archived_backtest_sleeve2.yml",
        "alpha_daily.yml",
        "daily-alpaca-live.yml",
        "daily-alpaca-paper.yml",
        "daily-alpaca-precompute.yml",
        "export-broker-snapshot.yml",
        "research-digest.yml",
    }

    assert actual == expected


def test_daily_alpaca_email_steps_and_debug_defaults_are_pinned() -> None:
    workflow = Path(".github/workflows/daily-alpaca-live.yml").read_text(encoding="utf-8")

    assert 'EMAIL_INLINE_REPORTS: "0"' in workflow
    assert 'EMAIL_INTERNAL_DEBUG: "0"' in workflow
    assert "Send pre-trade execution status email" in workflow
    assert "Send trading confirmation email from execution results" in workflow
    assert "outputs/execution_email/${{ env.REPORT_DATE }}.json" in workflow


def test_daily_alpaca_workflow_has_only_schedule_and_dispatch_triggers() -> None:
    workflow = Path(".github/workflows/daily-alpaca-live.yml").read_text(encoding="utf-8")

    assert "workflow_dispatch:" in workflow
    assert "schedule:" in workflow
    assert "workflow_call:" not in workflow
    assert "repository_dispatch:" not in workflow
    assert "pull_request:" not in workflow
    assert "push:" not in workflow


def test_daily_alpaca_live_retry_chain_uses_always_for_failed_primary_handoff() -> None:
    workflow = Path(".github/workflows/daily-alpaca-live.yml").read_text(encoding="utf-8")

    assert "if: ${{ always() && needs.live_primary.outputs.retry_eligible == 'true' }}" in workflow
    assert "if: ${{ always() && needs.live_primary.outputs.retry_eligible == 'true' && needs.retry_precompute.result == 'success' }}" in workflow
    assert "if: ${{ always() && (needs.live_retry.outputs.continuation_eligible == 'true' || needs.live_primary.outputs.continuation_eligible == 'true') }}" in workflow


def test_precompute_and_live_workflows_are_stage_separated() -> None:
    precompute = Path(".github/workflows/daily-alpaca-precompute.yml").read_text(encoding="utf-8")
    live = Path(".github/workflows/daily-alpaca-live.yml").read_text(encoding="utf-8")

    assert "classify_stage" not in precompute
    assert "classify_stage" not in live
    assert "python3 daily_quant_report.py --plan-only --write-precompute-bundle" in precompute
    assert live.count("python3 daily_quant_report.py --plan-only --write-precompute-bundle") == 1
    assert "python3 -m scripts.run_precomputed_alpaca_execution --retry-attempt 0" not in precompute
    assert "python3 -m scripts.run_precomputed_alpaca_execution --retry-attempt 0" in live


def test_precompute_artifact_handoff_is_report_date_specific_and_always_uploaded() -> None:
    precompute = Path(".github/workflows/daily-alpaca-precompute.yml").read_text(encoding="utf-8")

    assert "name: alpaca-precompute-${{ env.REPORT_DATE }}" in precompute
    assert "if: always()" in precompute
    assert "outputs/precompute/${{ env.REPORT_DATE }}/**" in precompute
    assert "outputs/workflow_status/${{ env.REPORT_DATE }}/**" in precompute


def test_live_workflow_requires_authoritative_precompute_artifact_before_execution() -> None:
    live = Path(".github/workflows/daily-alpaca-live.yml").read_text(encoding="utf-8")

    assert "python3 -m scripts.fetch_precompute_artifact --report-date \"$REPORT_DATE\" --destination downloaded_precompute" in live
    assert "python3 -m scripts.precompute_bundle_status --report-date \"$REPORT_DATE\" --require-valid" in live
    assert "Gate live execution on authoritative precompute bundle" in live
    assert "MISSING_PRECOMPUTE_BUNDLE" in live
    assert "if: always() && steps.bundle_gate.outputs.bundle_ready == 'true'" in live


def test_workflow_helpers_use_module_invocation() -> None:
    precompute = Path(".github/workflows/daily-alpaca-precompute.yml").read_text(encoding="utf-8")
    live = Path(".github/workflows/daily-alpaca-live.yml").read_text(encoding="utf-8")

    assert "python3 -m scripts.workflow_time_guard" in precompute
    assert "python3 -m scripts.precompute_bundle_status" in precompute
    assert "python3 -m scripts.workflow_time_guard" in live
    assert "python3 -m scripts.precompute_bundle_status" in live
    assert "python3 -m scripts.fetch_precompute_artifact" in live


def test_daily_quant_report_has_inline_email_guard_for_workflow_dedupe() -> None:
    source = Path("daily_quant_report.py").read_text(encoding="utf-8")

    assert "def _deliver_inline_report_emails(" in source
    assert 'os.getenv("EMAIL_INLINE_REPORTS")' in source
    assert "[EMAIL] inline report delivery disabled; persisted workflow email job is authoritative" in source
