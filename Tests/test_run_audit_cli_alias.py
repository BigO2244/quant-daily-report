from __future__ import annotations

import subprocess
import sys


def test_run_audit_help_includes_audit_run_id_prefix_alias() -> None:
    proc = subprocess.run(
        [sys.executable, "scripts/run_audit_2022_and_worst.py", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 0
    assert "--audit-run-id-prefix" in proc.stdout
