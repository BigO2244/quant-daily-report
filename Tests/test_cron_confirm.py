from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_cron_confirm_alerts_on_missing_execution_pointer() -> None:
    script_text = (REPO_ROOT / "scripts" / "cron_confirm.sh").read_text(encoding="utf-8")

    assert 'if [[ -z "${EXECUTION_RUN_ROOT}" || -z "${EXECUTION_POINTER_STATUS}" ]]; then' in script_text
    assert 'POINTER_CONFIRMABLE=0' in script_text
    assert '"❌ [Alpha Stack] Execution pointer missing — ${REPORT_DATE}"' in script_text
    assert "send_pointer_alert \\" in script_text


def test_cron_confirm_alerts_on_running_execution_pointer() -> None:
    script_text = (REPO_ROOT / "scripts" / "cron_confirm.sh").read_text(encoding="utf-8")

    assert '"${EXECUTION_POINTER_STATUS,,}" != "success"' in script_text
    assert '"${EXECUTION_POINTER_STATUS,,}" != "no_action"' in script_text
    assert '"⚠️ [Alpha Stack] Execution not confirmable — ${REPORT_DATE}"' in script_text
    assert 'skipping normal confirmation email' in script_text
    assert "send_pointer_alert \\" in script_text
