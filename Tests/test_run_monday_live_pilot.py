from __future__ import annotations

import subprocess
from pathlib import Path


SCRIPT = Path("scripts/run_monday_live_pilot.sh")


def test_monday_live_pilot_runner_bash_syntax() -> None:
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)


def test_monday_live_pilot_runner_contract() -> None:
    text = SCRIPT.read_text(encoding="utf-8")

    assert 'cd "${REPO_ROOT}"' in text
    assert 'source "${ENV_FILE}"' in text
    assert "require_eq TRADING_MODE live_pilot" in text
    assert "require_eq ALPACA_PAPER 0" in text
    assert "https://api.alpaca.markets" in text
    assert "CAERUS_LIVE_PILOT_CAPITAL_CAP:-100" in text
    assert "CAERUS_LIVE_PILOT_MAX_ORDERS:-1" in text
    assert "CAERUS_LIVE_PILOT_SLEEVE_ID:-orion" in text
    assert "CAERUS_LIVE_PILOT_ACCOUNT_ID_HASH:-cfdc5d0aa0e3fdc38adadc78f1ebc30cbc83df187a4223c22597e787cd8a7c85" in text
    assert "scripts/live_pilot_build_plan_from_precompute.py" in text
    assert "CAERUS_LIVE_PILOT_DRY_RUN=1" in text
    assert "CAERUS_LIVE_PILOT_DRY_RUN=0" in text
    assert text.index("CAERUS_LIVE_PILOT_DRY_RUN=1") < text.index("CAERUS_LIVE_PILOT_DRY_RUN=0")
    assert "scripts/cron_" not in text
    assert "scripts/run_precomputed_alpaca_execution.py" not in text
