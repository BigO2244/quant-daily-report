#!/usr/bin/env python3
"""
Send the precompute-complete email after the 7 AM planner finishes.

Calls format_precompute_email to build the body, then ships it via
core.quant_report.send_email.  Called from cron_precompute.sh after
bundle validation succeeds.

Usage:
    python3 -m scripts.send_precompute_email
    REPORT_DATE=2026-03-23 python3 -m scripts.send_precompute_email
"""
from __future__ import annotations

import datetime
import os
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Load .env explicitly so credentials are available regardless of how this
# script is invoked (cron, SSH one-liner, direct python call).
from pathlib import Path as _Path
_env_path = _REPO_ROOT / ".env"
if _env_path.exists():
    with open(_env_path) as _f:
        for _line in _f:
            _line = _line.strip()
            if not _line or _line.startswith("#") or "=" not in _line:
                continue
            _k, _, _v = _line.partition("=")
            _v = _v.strip().strip('"').strip("'")
            os.environ.setdefault(_k.strip(), _v)

from core.quant_report import send_email


def main() -> None:
    trade_date = os.environ.get("REPORT_DATE") or datetime.date.today().isoformat()

    # Build the plain-text body by calling format_precompute_email as a subprocess
    # so its sys.path setup is clean and it can be tested independently.
    result = subprocess.run(
        [sys.executable, "-m", "scripts.format_precompute_email"],
        capture_output=True,
        text=True,
        env={**os.environ, "REPORT_DATE": trade_date},
        cwd=str(_REPO_ROOT),
    )
    if result.returncode != 0:
        print(
            f"[PRECOMPUTE_EMAIL][ERROR] format_precompute_email failed "
            f"(exit {result.returncode}):\n{result.stderr}",
            file=sys.stderr,
        )
        sys.exit(result.returncode)

    body = result.stdout.strip()
    if not body:
        print("[PRECOMPUTE_EMAIL][ERROR] format_precompute_email produced no output", file=sys.stderr)
        sys.exit(1)

    subject = f"\u2705 [Alpha Stack] Precompute complete \u2014 {trade_date}"

    send_email(subject=subject, body_text=body)
    print(f"[PRECOMPUTE_EMAIL][OK] sent: {subject}")


if __name__ == "__main__":
    main()
