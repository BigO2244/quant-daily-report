from pathlib import Path


def test_daily_alpaca_workflow_uses_explicit_precompute_and_live_dst_windows() -> None:
    workflow = Path(".github/workflows/daily-alpaca-paper.yml").read_text(encoding="utf-8")

    assert "Precompute target: 7:30 AM America/New_York." in workflow
    assert '- cron: "30 12 * 1,2,12 1-5"' in workflow
    assert '- cron: "30 12 1-7 3 1-5"' in workflow
    assert '- cron: "30 11 8-31 3 1-5"' in workflow
    assert '- cron: "30 11 * 4-10 1-5"' in workflow
    assert '- cron: "30 11 1-7 11 1-5"' in workflow
    assert '- cron: "30 12 8-30 11 1-5"' in workflow

    assert "Live execution target: 9:35 AM America/New_York." in workflow
    assert '- cron: "35 14 * 1,2,12 1-5"' in workflow
    assert '- cron: "35 14 1-7 3 1-5"' in workflow
    assert '- cron: "35 13 8-31 3 1-5"' in workflow
    assert '- cron: "35 13 * 4-10 1-5"' in workflow
    assert '- cron: "35 13 1-7 11 1-5"' in workflow
    assert '- cron: "35 14 8-30 11 1-5"' in workflow
    assert "workflow_dispatch:" in workflow
