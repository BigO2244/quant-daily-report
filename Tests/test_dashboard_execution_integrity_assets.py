from pathlib import Path


def test_dashboard_terminal_health_mounts_exist():
    html = Path("web/dashboard/index.html").read_text(encoding="utf-8")

    assert "CAERUS EVIDENCE CONTROL TOWER" in html
    assert 'id="system-health-console"' in html
    assert 'id="health-matrix"' in html
    assert 'id="meta-status"' in html
    assert 'id="decision-grade-list"' in html
    assert 'id="live-pilot-orders-body"' in html
    assert 'id="operator-action-summary"' in html
    assert 'id="account-layers-body"' in html


def test_dashboard_js_loads_broker_authoritative_data():
    js = Path("web/dashboard/quant_daily_executive.js").read_text(encoding="utf-8")

    assert "params.get('data')" in js
    assert "dashboard_data.json" in js
    assert "cache: 'no-store'" in js
    assert "function boot()" in js
    assert "function renderDecisionGrade" in js
    assert "function renderOperatorControlTower" in js
    assert "function renderEvidenceCollection" in js
