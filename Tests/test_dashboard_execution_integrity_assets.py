from pathlib import Path


def test_dashboard_html_contains_execution_integrity_mount():
    html = Path("web/dashboard/quant_daily_executive.html").read_text(encoding="utf-8")
    assert 'id="execution-integrity"' in html
    assert "Execution Integrity" in html


def test_dashboard_js_renders_execution_integrity_panel():
    js = Path("web/dashboard/quant_daily_executive.js").read_text(encoding="utf-8")
    assert "function renderExecutionIntegrity(d)" in js
    assert "getElementById('execution-integrity')" in js
    assert "duplicate_guard_status" in js
    assert "post_execution_recon_status" in js
