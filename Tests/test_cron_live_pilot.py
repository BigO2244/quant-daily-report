from __future__ import annotations

import subprocess
from pathlib import Path


EXECUTE_SCRIPT = Path("scripts/cron_live_pilot_execute.sh")
CONFIRM_SCRIPT = Path("scripts/cron_live_pilot_confirm.sh")
CRONTAB = Path("scripts/crontab.txt")


def test_live_pilot_cron_scripts_have_valid_bash_syntax() -> None:
    subprocess.run(["bash", "-n", str(EXECUTE_SCRIPT)], check=True)
    subprocess.run(["bash", "-n", str(CONFIRM_SCRIPT)], check=True)


def test_live_pilot_cron_execute_preserves_live_safety_gates() -> None:
    text = EXECUTE_SCRIPT.read_text(encoding="utf-8")

    assert 'source "${ENV_FILE}"' in text
    assert 'export MODE="live_pilot"' in text
    assert 'export TRADING_MODE="live_pilot"' in text
    assert 'export WORKFLOW_KIND="live_pilot"' in text
    assert 'export CAERUS_LIVE_PILOT_CRON_CONTEXT="1"' in text
    assert 'export ALPACA_PAPER="0"' in text
    assert 'export ALPACA_BASE_URL="https://api.alpaca.markets"' in text
    assert 'APPROVED_LIVE_PILOT_CAP_USD="${APPROVED_LIVE_PILOT_CAP_USD:-500}"' in text
    assert 'CAERUS_LIVE_PILOT_CAPITAL_CAP="${CAERUS_LIVE_PILOT_CAPITAL_CAP:-}"' in text
    assert 'CAERUS_LIVE_PILOT_APPROVED="${CAERUS_LIVE_PILOT_APPROVED:-0}"' in text
    assert 'CAERUS_LIVE_PILOT_SCHEDULE_ENABLED="${CAERUS_LIVE_PILOT_SCHEDULE_ENABLED:-0}"' in text
    assert 'CAERUS_LIVE_PILOT_CRON_APPROVED="${CAERUS_LIVE_PILOT_CRON_APPROVED:-0}"' in text
    assert 'CAERUS_LIVE_PILOT_SUBMIT_APPROVED="${CAERUS_LIVE_PILOT_SUBMIT_APPROVED:-0}"' in text
    assert "CAERUS_LIVE_PILOT_CAPITAL_CAP must be > 0 and <= {approved_cap:.0f}" in text
    assert "missing_live_pilot_approval" in text
    assert "missing_positive_live_pilot_capital_cap" in text
    assert "live_pilot_capital_cap_invalid" in text
    assert "live_pilot_kill_switch_enabled" in text
    assert "scripts/live_pilot_write_gate_state.py" in text
    assert "write_gate_state_blocked" in text
    assert "core.precompute_bundle_validation" in text
    assert "scripts/live_pilot_build_plan_from_precompute.py" in text
    assert "--trade-date \"${REPORT_DATE}\"" in text
    assert "CAERUS_LIVE_PILOT_DRY_RUN=1" in text
    assert "CAERUS_LIVE_PILOT_DRY_RUN=0" in text
    assert text.index("CAERUS_LIVE_PILOT_DRY_RUN=1") < text.index("CAERUS_LIVE_PILOT_DRY_RUN=0")
    assert 'if [[ "${CAERUS_LIVE_PILOT_SUBMIT_APPROVED}" != "1" ]]; then' in text
    assert 'Path("outputs") / "workflow" / trade_date / "live_pilot_execution.json"' in text
    assert text.index("missing_live_pilot_approval") < text.index("scripts/live_pilot_build_plan_from_precompute.py")
    assert text.index("missing_positive_live_pilot_capital_cap") < text.index("scripts/live_pilot_build_plan_from_precompute.py")


def test_live_pilot_cron_confirm_uses_explicit_results_path_not_paper_pointer() -> None:
    text = CONFIRM_SCRIPT.read_text(encoding="utf-8")

    assert 'export MODE="live_pilot"' in text
    assert 'export TRADING_MODE="live_pilot"' in text
    assert 'export TRADING_CONFIRMATION_RESULTS_PATH="${RESULTS_PATH}"' in text
    assert 'export TRADING_CONFIRMATION_RUN_ROOT="${RUN_ROOT}"' in text
    assert "python3 -m scripts.send_trading_confirmation_email" in text
    assert "outputs/workflow/${REPORT_DATE}/execution.json" not in text


def test_vm_crontab_includes_live_pilot_automation_lane() -> None:
    text = CRONTAB.read_text(encoding="utf-8")

    assert "36 9 * * 1-5 $HOME/quant-daily-report/scripts/cron_live_pilot_execute.sh" in text
    assert "45 9 * * 1-5 $HOME/quant-daily-report/scripts/cron_live_pilot_confirm.sh" in text
    assert text.index("scripts/cron_execute.sh") < text.index("scripts/cron_live_pilot_execute.sh")
    assert text.index("scripts/cron_live_pilot_confirm.sh") < text.index("scripts/cron_confirm.sh")
