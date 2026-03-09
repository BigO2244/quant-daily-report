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

from core.execution_payload import STATUS_HALTED, normalize_status, write_canonical_execution_payload
from core.operator_summary import write_operator_summary

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

# Ensure canonical status field is present and normalized.
payload["status"] = normalize_status(
    execution_status=payload.get("execution_status"),
    halt_reason=payload.get("halt_reason"),
    executable_trades_count=int(payload.get("executable_trades_count") or 0),
)
payload["execution_status"] = payload["status"]

out_path = Path("outputs/execution_email") / f"{report_date}.json"
out_path.parent.mkdir(parents=True, exist_ok=True)
out_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
print(f"[AUTO_BOOTSTRAP] Execution email payload written: {out_path}")

# Canonical write when run context/canonical run root is available.
canonical_path = None
run_root = None
latest_run_path = Path("outputs/latest_run.json")
if latest_run_path.exists():
    try:
        latest = json.loads(latest_run_path.read_text(encoding="utf-8"))
        run_root_raw = str(latest.get("run_root") or "").strip()
        if run_root_raw:
            run_root = Path(run_root_raw)
    except Exception:
        run_root = None

if run_root is None:
    # Fallback to conventional run root for this RUN_ID when pointer is not available.
    run_root = Path("outputs") / "runs" / run_id

try:
    run_root.mkdir(parents=True, exist_ok=True)
    canonical_path = write_canonical_execution_payload(
        payload,
        report_date,
        run_root=run_root,
        allow_overwrite=True,
    )
    write_operator_summary(
        run_root,
        run_id=run_id,
        trade_date=report_date,
        mode=str(payload.get("mode") or "ALPACA"),
        pretrade_status=STATUS_HALTED,
        pretrade_halt_reason=str(payload.get("halt_reason") or ""),
        proposed_trades_count=0,
        executable_trades_count=0,
        planner_completed=True,
    )
    print(f"[AUTO_BOOTSTRAP] Canonical execution payload written: {canonical_path}")
    print(f"[AUTO_BOOTSTRAP] Canonical operator summary written: {run_root / 'operator_summary.json'}")
except Exception as exc:
    print(f"[AUTO_BOOTSTRAP][WARN] canonical payload write failed: {exc}")

print(f"[AUTO_BOOTSTRAP] Legacy payload path: {out_path}")
if canonical_path is not None:
    print(f"[AUTO_BOOTSTRAP] Canonical payload path: {canonical_path}")
