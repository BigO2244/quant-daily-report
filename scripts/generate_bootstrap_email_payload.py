"""
scripts/generate_bootstrap_email_payload.py

Writes the execution email JSON payload for an auto-bootstrap run.
Called by the GitHub Actions auto-bootstrap step to avoid embedding
unindented Python inside a YAML block scalar (which breaks YAML parsers).

Environment variables consumed:
  REPORT_DATE  - trade date (YYYY-MM-DD)
  RUN_ID       - workflow run identifier
  recon_data   - JSON string of reconciliation diffs (optional)
"""
import json
import os
from pathlib import Path

report_date = os.environ["REPORT_DATE"]
run_id = os.environ["RUN_ID"]
recon_data_str = os.environ.get("recon_data", "{}")

try:
    recon_data = json.loads(recon_data_str)
except Exception:
    recon_data = {}

payload = {
    "trade_date": report_date,
    "mode": "ALPACA",
    "execution_status": "HALTED",
    "halt_reason": "PRETRADE RECONCILIATION FAILED \u2014 AUTO BOOTSTRAP TRIGGERED",
    "status_label": "RECON_FAIL_AUTO_BOOTSTRAP",
    "status_reason": (
        "Broker/model positions diverged; canonical snapshot auto-refreshed "
        "from broker. Next run should succeed."
    ),
    "trades": [],
    "run_id": run_id,
    "order_ids": [],
    "auto_bootstrap_triggered": True,
    "recon_failure": True,
    "recon_verdict": recon_data.get("verdict", "FAIL"),
    "recon_diffs": recon_data.get("diffs", {}),
    "market_status": "CLOSED",
    "execution_notes": [
        "\u26a0\ufe0f NO TRADES SENT \u2014 PRETRADE RECONCILIATION FAILED",
        "Auto-bootstrap refreshed canonical model snapshot from broker positions.",
        "Next scheduled run should pass reconciliation and resume normal trading.",
        "Review recon diffs below for details on position mismatches.",
    ],
}

out_path = Path("outputs/execution_email") / f"{report_date}.json"
out_path.parent.mkdir(parents=True, exist_ok=True)
out_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
print(f"[AUTO_BOOTSTRAP] Execution email payload written: {out_path}")
