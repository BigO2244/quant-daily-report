from pathlib import Path
import os
import subprocess


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


def test_dashboard_refresh_service_is_resource_bounded_and_low_priority():
    service = Path("deploy/caerus-dashboard-refresh.service").read_text(
        encoding="utf-8"
    )

    assert "Type=oneshot" in service
    assert "ExecCondition=" in service
    assert "scripts/dashboard_refresh_condition.sh" in service
    assert "--require-live-broker" in service
    assert "TimeoutStartSec=120s" in service
    assert "TimeoutStopSec=15s" in service
    assert "KillMode=control-group" in service
    assert "Nice=10" in service
    assert "IOSchedulingClass=idle" in service
    assert "MemoryMax=320M" in service
    assert "TasksMax=64" in service


def test_dashboard_deploy_uses_canonical_vm_alias():
    script = Path("scripts/deploy_dashboard_vm.sh").read_text(encoding="utf-8")

    assert 'REMOTE_HOST="${REMOTE_HOST:-caerus-vm}"' in script
    assert '"${repo_root}/scripts/dashboard_refresh_condition.sh"' in script


def _condition_result(weekday: int, hhmm: str) -> int:
    env = os.environ.copy()
    env["CAERUS_CLOCK_WEEKDAY"] = str(weekday)
    env["CAERUS_CLOCK_HHMM"] = hhmm
    return subprocess.run(
        ["bash", "scripts/dashboard_refresh_condition.sh"],
        check=False,
        env=env,
    ).returncode


def test_dashboard_refresh_skips_production_windows():
    assert _condition_result(2, "0645") == 1
    assert _condition_result(1, "0800") == 1
    assert _condition_result(2, "0935") == 1
    assert _condition_result(2, "1830") == 1
    assert _condition_result(2, "1945") == 1
    assert _condition_result(2, "2100") == 1


def test_dashboard_refresh_runs_outside_production_windows():
    assert _condition_result(2, "0630") == 0
    assert _condition_result(2, "0730") == 0
    assert _condition_result(2, "1015") == 0
    assert _condition_result(2, "2030") == 0
    assert _condition_result(2, "2115") == 0
    assert _condition_result(7, "0935") == 0


def test_dashboard_refresh_timer_uses_fifteen_minute_cadence():
    timer = Path("deploy/caerus-dashboard-refresh.timer").read_text(
        encoding="utf-8"
    )

    assert "OnBootSec=2min" in timer
    assert "OnUnitActiveSec=15min" in timer
