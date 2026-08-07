from __future__ import annotations

import json
import subprocess
import sys


def test_build_authority_packages_cli(tmp_path):
    payload = tmp_path / "payload.json"
    payload.write_text(json.dumps({
        "trade_date": "2026-08-07",
        "trades": [{"symbol": "AAPL", "side": "BUY", "shares": 1, "price": 100}],
    }))
    out = tmp_path / "authority"
    result = subprocess.run(
        [sys.executable, "scripts/build_authority_packages.py", str(payload), "--outdir", str(out), "--decision-id", "decision:test", "--risk-id", "risk:test"],
        check=False, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    evidence = json.loads((out / "evidence_package.json").read_text())
    decision = json.loads((out / "decision_package.json").read_text())
    assert decision["evidence_hash"] == evidence["content_hash"]
    assert json.loads((out / "execution_package.json").read_text())["risk_package_id"] == "risk:test"
