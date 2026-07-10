from pathlib import Path


def test_workflow_inventory_matches_current_vm_cron_architecture() -> None:
    workflow_dir = Path(".github/workflows")
    actual = {path.name for path in workflow_dir.glob("*.yml")}
    expected = {
        "_archived_backtest_sleeve1_robustness.yml",
        "daily-alpaca-paper.yml",
        "nightly-agents-refresh.yml",
    }

    assert actual == expected


def test_retired_daily_alpaca_workflows_do_not_exist() -> None:
    """Primary trading execution moved from GitHub Actions to VM cron."""

    assert not Path(".github/workflows/daily-alpaca-precompute.yml").exists()
    assert not Path(".github/workflows/daily-alpaca-live.yml").exists()


def test_github_workflows_have_no_schedule_triggers() -> None:
    for path in Path(".github/workflows").glob("*.yml"):
        workflow = path.read_text(encoding="utf-8")
        assert "schedule:" not in workflow, f"{path} must not schedule production work"
        assert "cron:" not in workflow, f"{path} must not schedule production work"


def test_deprecated_alpaca_wrapper_points_to_vm_cron_authority() -> None:
    workflow = Path(".github/workflows/daily-alpaca-paper.yml").read_text(encoding="utf-8")

    assert "Deprecated Wrapper" in workflow
    assert "workflow_dispatch:" in workflow
    assert "Use VM cron in scripts/crontab.txt" in workflow
    assert "daily-alpaca-precompute" not in workflow
    assert "daily-alpaca-live" not in workflow


def test_vm_cron_defines_precompute_execution_and_confirmation_schedule() -> None:
    crontab = Path("scripts/crontab.txt").read_text(encoding="utf-8")

    assert "CRON_TZ=America/New_York" in crontab
    assert "0 7 * * 1-5 $HOME/quant-daily-report/scripts/cron_precompute.sh" in crontab
    assert "35 9 * * 1-5 $HOME/quant-daily-report/scripts/cron_execute.sh" in crontab
    assert "0 10 * * 1-5 $HOME/quant-daily-report/scripts/cron_confirm.sh" in crontab


def test_cron_execute_requires_validated_precompute_bundle_and_exact_plan() -> None:
    cron_execute = Path("scripts/cron_execute.sh").read_text(encoding="utf-8")

    assert "core.precompute_bundle_validation" in cron_execute
    assert "execution_bundle_validation.json" in cron_execute
    # Unified paper lane: the shared live-pilot engine executes the plan built
    # from the validated precompute bundle; the legacy exact-payload module
    # (run_precomputed_alpaca_execution) is dormant and no longer invoked.
    assert "scripts/live_pilot_build_plan_from_precompute.py" in cron_execute
    assert "scripts/live_pilot_execute.py" in cron_execute


def test_cron_confirm_remains_execution_result_email_authority() -> None:
    cron_confirm = Path("scripts/cron_confirm.sh").read_text(encoding="utf-8")

    assert "outputs/latest_run.json" in cron_confirm
    assert "python3 -m scripts.send_trading_confirmation_email" in cron_confirm


def test_daily_quant_report_inline_email_guard_remains_disabled_by_default() -> None:
    source = Path("daily_quant_report.py").read_text(encoding="utf-8")

    assert "def _deliver_inline_report_emails(" in source
    assert 'os.getenv("EMAIL_INLINE_REPORTS")' in source
    assert "[EMAIL] inline report delivery disabled; persisted workflow email job is authoritative" in source
