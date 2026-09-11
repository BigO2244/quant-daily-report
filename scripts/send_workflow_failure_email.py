#!/usr/bin/env python3
"""Send a bounded operational alert; retain SMTP acceptance, not inbox claims.

No log contents or exception messages enter the email or receipt. Successful
notifications are deduplicated by workflow/date/stage/reason/exit status. A
failed attempt remains retryable. The receipt is diagnostic, never readiness.
"""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sys
import signal
import shlex
import tempfile

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = {"precompute", "broker_ledger"}
STAGES = {"bootstrap", "credentials", "runtime", "time_guard", "dependency_guard", "planner", "seal", "bundle_validation", "certification", "finalize", "capture", "ownership", "valuation", "accounting", "audit"}
REASONS = {"workflow_failed", "prerequisite_failed", "command_failed", "validation_failed"}
EMAIL_ENV_KEYS = {
    "EMAIL_SENDER", "SMTP_USER", "REPORT_EMAIL_FROM", "EMAIL_APP_PASSWORD",
    "SMTP_PASSWORD", "EMAIL_RECIPIENT", "REPORT_TO_EMAIL", "REPORT_EMAIL_TO",
    "SMTP_HOST", "SMTP_PORT",
}


def _load_email_env(repo_root: Path) -> None:
    """Read only email configuration; never execute .env or expand references."""
    env_path = repo_root / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export") and len(line) > 6 and line[6].isspace():
            line = line[6:].lstrip()
        key, separator, value = line.partition("=")
        key = key.strip()
        # Filter before parsing values: broker configuration is irrelevant,
        # including malformed broker lines, and must never enter the environment.
        if not separator or key not in EMAIL_ENV_KEYS or key in os.environ:
            continue
        pieces = shlex.split(value, comments=True, posix=True)
        if len(pieces) > 1:
            raise ValueError("Email environment values containing spaces must be quoted")
        os.environ[key] = pieces[0] if pieces else ""


def _write_json(path: Path, payload: dict) -> None:
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(temporary, path)


def notify(*, repo_root: Path, workflow: str, report_date: str, stage: str,
           reason_code: str, exit_code: int, log_path: str, sender=None) -> dict:
    if workflow not in WORKFLOWS or stage not in STAGES or reason_code not in REASONS:
        raise ValueError("Unknown operational alert label")
    if dt.date.fromisoformat(report_date).isoformat() != report_date or not 1 <= exit_code <= 255:
        raise ValueError("Invalid failure date or exit code")
    # Accept only the canonical local log reference, never an arbitrary string,
    # URL, credential, exception text, command line or log excerpt.
    log_name = "cron_broker_ledger.log" if workflow == "broker_ledger" else f"precompute_{report_date}.log"
    expected_log = repo_root / "logs" / log_name
    if Path(log_path).resolve() != expected_log.resolve():
        raise ValueError("Unexpected workflow log path")
    safe_log = f"logs/{log_name}"
    status_path = repo_root / "outputs" / "workflow" / report_date / f"{workflow}_failure_email.json"
    status_path.parent.mkdir(parents=True, exist_ok=True)
    event = dict(workflow=workflow, report_date=report_date, stage=stage,
                 reason_code=reason_code, exit_code=exit_code, log_path=safe_log)
    event_id = hashlib.sha256(json.dumps(event, sort_keys=True).encode()).hexdigest()
    with status_path.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            previous = json.loads(status_path.read_text())
        except (FileNotFoundError, ValueError):
            previous = {}
        accepted = list(previous.get("accepted_event_ids") or [])
        if event_id in accepted:
            return {**event, "event_id": event_id, "status": "ALREADY_ACCEPTED", "smtp_accepted": True, "inbox_delivery_verified": False}
        receipt = {**event, "schema_version": 1, "event_id": event_id,
                   "attempted_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
                   "smtp_accepted": False, "inbox_delivery_verified": False,
                   "accepted_event_ids": accepted, "status": "ATTEMPTING"}
        _write_json(status_path, receipt)
        title = "Precompute" if workflow == "precompute" else "Broker accounting"
        subject = f"[Caerus] {title} failed — {report_date}"
        body = (f"{title} did not finish successfully for {report_date}.\n\n"
                f"Failed stage: {stage.replace('_', ' ')}\n"
                f"Reason: {reason_code.replace('_', ' ')}\nExit status: {exit_code}\n"
                f"Diagnostic log on the scheduler: {safe_log}\n\n"
                "The workflow needs operator review. Existing readiness and execution "
                "checks still apply; this alert does not authorize trading or mark recovery complete.\n")
        try:
            if sender is None:
                # Use the established canonical/legacy email environment contract.
                _load_email_env(repo_root)
                from core.quant_report import send_email
                sender = send_email
            result = sender(subject=subject, body_text=body)
            # Existing send_email returns None only after SMTP sendmail reports
            # no refused recipients. Reject explicit unsuccessful alternate returns.
            if result is not None and result is not True:
                raise RuntimeError("Email transport did not confirm acceptance")
        except Exception as exc:
            receipt.update(status="FAILED", error_type=type(exc).__name__)
        else:
            receipt.update(status="SMTP_ACCEPTED", smtp_accepted=True,
                           accepted_event_ids=[*accepted, event_id])
        _write_json(status_path, receipt)
        return receipt


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--workflow", choices=sorted(WORKFLOWS), required=True)
    parser.add_argument("--report-date", required=True)
    parser.add_argument("--stage", choices=sorted(STAGES), required=True)
    parser.add_argument("--reason-code", choices=sorted(REASONS), default="workflow_failed")
    parser.add_argument("--exit-code", type=int, required=True)
    parser.add_argument("--log-path", required=True)
    args = parser.parse_args(argv)
    def timeout_handler(signum, frame):
        raise TimeoutError("Notification exceeded 30-second deadline")

    signal.signal(signal.SIGALRM, timeout_handler)
    signal.alarm(30)
    try:
        receipt = notify(**vars(args))
    except Exception as exc:
        print(json.dumps({"status": "FAILED", "smtp_accepted": False, "error_type": type(exc).__name__}))
        return 1
    finally:
        signal.alarm(0)
    print(json.dumps(receipt, sort_keys=True))
    return 0 if receipt["smtp_accepted"] else 1


if __name__ == "__main__":
    sys.exit(main())
