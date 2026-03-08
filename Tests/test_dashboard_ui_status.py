from pathlib import Path


def test_run_status_wrapping_rules_present():
    repo_root = Path(__file__).resolve().parents[1]
    js = (repo_root / "web/dashboard/quant_daily_executive.js").read_text(encoding="utf-8")
    css = (repo_root / "web/dashboard/quant_daily_executive.css").read_text(encoding="utf-8")

    assert "function statusLengthClass" in js
    assert "status-wrap" in js
    assert "status-long" in js
    assert "status-very-long" in js

    assert ".kpi-value.status-wrap" in css
    assert ".kpi-value.status-long" in css
    assert ".kpi-value.status-very-long" in css
