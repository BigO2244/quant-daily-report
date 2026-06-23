from pathlib import Path


def test_dashboard_terminal_health_mounts_exist():
    html = Path("web/dashboard/index.html").read_text(encoding="utf-8")

    assert "CAERUS EVIDENCE CONTROL TOWER" in html
    assert 'src="dashboard-data.js"' in html
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
    assert "function loadDashboardPayload" in js
    assert "window.DASHBOARD_V1" in js
    assert "function boot()" in js
    assert "function renderDecisionGrade" in js
    assert "function renderOperatorControlTower" in js
    assert "function renderEvidenceCollection" in js


def test_deploy_script_builds_dashboard_aliases_from_canonical_payload():
    script = Path("scripts/deploy_dashboard_vm.sh").read_text(encoding="utf-8")

    assert "dashboard_data.json" in script
    assert "dashboard-data.json" in script
    assert "dashboard-data.js" in script
    assert "payload = remote_web / 'dashboard_data.json'" in script
    assert "json_alias.write_text(text" in script
    assert "js_alias.write_text('window.DASHBOARD_V1 = '" in script
    assert '"${repo_root}/web/dashboard/dashboard-data.json"' not in script
    assert '"${repo_root}/web/dashboard/dashboard-data.js"' not in script
