import datetime as dt
import os
from pathlib import Path
import subprocess
from zoneinfo import ZoneInfo

import pytest

from core.workflow_status import classify_precompute_window

ROOT = Path(__file__).resolve().parents[1]
ET = ZoneInfo('America/New_York')


@pytest.mark.parametrize('month,utc_hour', [(1, 10), (9, 9)])
def test_0500_start_tracks_dst(month, utc_hour):
    result = classify_precompute_window(now=dt.datetime(2026, month, 14, utc_hour, tzinfo=dt.timezone.utc))
    assert result['allow_run']
    assert 'T05:00:' in result['precompute_start_et']


@pytest.mark.parametrize('hour,minute,allowed', [(4, 59, False), (5, 0, True), (5, 1, False), (7, 0, False), (9, 35, False)])
def test_only_scheduled_minute_admitted_even_with_force(hour, minute, allowed):
    result = classify_precompute_window(now=dt.datetime(2026, 9, 9, hour, minute, 59, tzinfo=ET), force_refresh=True, event_name='workflow_dispatch')
    assert result['allow_run'] is allowed


def test_weekend_does_not_admit_precompute():
    assert not classify_precompute_window(now=dt.datetime(2026, 9, 12, 5, tzinfo=ET))['allow_run']


def test_direct_planner_rejects_off_schedule_before_initializing_run(monkeypatch):
    import daily_quant_report as report
    monkeypatch.setattr('core.workflow_status.classify_precompute_window', lambda: {'allow_run': False, 'reason': 'outside_0500_precompute_start'})
    monkeypatch.setattr(report, '_init_run_context', lambda **kw: pytest.fail('must reject before output/network activity'))
    with pytest.raises(RuntimeError, match='outside_0500'):
        report.main(['--plan-only', '--write-precompute-bundle'])


@pytest.mark.parametrize('clock,exit_code', [('0434', 0), ('0435', 1), ('0445', 1), ('0500', 1), ('0559', 1), ('0600', 0), ('0920', 1), ('1000', 1)])
def test_dashboard_yields_for_earlier_pipeline(clock, exit_code):
    result = subprocess.run(['bash', str(ROOT / 'scripts/dashboard_refresh_condition.sh')], env={**os.environ, 'CAERUS_CLOCK_WEEKDAY': '3', 'CAERUS_CLOCK_HHMM': clock}, capture_output=True)
    assert result.returncode == exit_code


def test_single_precompute_cron_and_prerequisite_order():
    lines = [x for x in (ROOT / 'scripts/crontab.txt').read_text().splitlines() if x and not x.startswith('#')]
    precompute = [x for x in lines if '/scripts/cron_precompute.sh' in x]
    security = [x for x in lines if '/scripts/cron_security_master.sh' in x]
    assert len(precompute) == len(security) == 1
    assert precompute[0].startswith('0 5 * * 1-5 ')
    assert security[0].startswith('45 4 * * 1-5 ')
    # The execution wrapper may only validate the sealed bundle, never launch a producer.
    execute = (ROOT / 'scripts/cron_execute.sh').read_text()
    assert '/scripts/cron_precompute.sh' not in execute
    assert '--require-sealed-paper-target' in execute
