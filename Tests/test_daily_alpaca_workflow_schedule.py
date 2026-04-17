from pathlib import Path


def test_daily_alpaca_precompute_workflow_is_dispatch_only() -> None:
    workflow = Path(".github/workflows/daily-alpaca-precompute.yml").read_text(encoding="utf-8")

    assert "workflow_dispatch:" in workflow
    assert "schedule:" not in workflow
    assert "cron:" not in workflow


def test_daily_alpaca_live_workflow_is_dispatch_only() -> None:
    workflow = Path(".github/workflows/daily-alpaca-live.yml").read_text(encoding="utf-8")

    assert "workflow_dispatch:" in workflow
    assert "schedule:" not in workflow
    assert "cron:" not in workflow


def test_vm_crontab_owns_daily_production_schedule() -> None:
    crontab = Path("scripts/crontab.txt").read_text(encoding="utf-8")

    assert "CRON_TZ=America/New_York" in crontab
    assert "0 7 * * 1-5 $HOME/quant-daily-report/scripts/cron_precompute.sh" in crontab
    assert "35 9 * * 1-5 $HOME/quant-daily-report/scripts/cron_execute.sh" in crontab
    assert "0 10 * * 1-5 $HOME/quant-daily-report/scripts/cron_confirm.sh" in crontab


def test_deprecated_daily_alpaca_wrapper_has_no_schedule_trigger() -> None:
    workflow = Path(".github/workflows/daily-alpaca-paper.yml").read_text(encoding="utf-8")

    assert "Deprecated Wrapper" in workflow
    assert "workflow_dispatch:" in workflow
    assert "schedule:" not in workflow
    assert "Use Daily Alpaca Precompute" in workflow


def test_no_github_workflow_has_a_schedule_trigger() -> None:
    for path in Path(".github/workflows").glob("*.yml"):
        workflow = path.read_text(encoding="utf-8")
        assert "workflow_dispatch:" in workflow or path.name.startswith("_archived_")
        assert "schedule:" not in workflow, f"{path} must not schedule production work"
        assert "cron:" not in workflow, f"{path} must not schedule production work"
