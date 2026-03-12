from __future__ import annotations

import json
from pathlib import Path

from reconciliation import refresh_canonical_snapshot_from_posttrade_snapshot


def test_refresh_canonical_snapshot_from_posttrade_snapshot_writes_canonical(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    ok = refresh_canonical_snapshot_from_posttrade_snapshot(
        positions_snapshot={
            "positions": [
                {"symbol": "AAPL", "qty": "3"},
                {"symbol": "MSFT", "qty": "2"},
            ]
        },
        account_snapshot={
            "cash": "1250.50",
            "equity": "10420.75",
        },
        run_date="2026-03-12",
    )

    assert ok is True

    canonical_path = Path("outputs/paper_state/canonical_positions.json")
    payload = json.loads(canonical_path.read_text(encoding="utf-8"))

    assert payload["positions"] == {"AAPL": 3.0, "MSFT": 2.0}
    assert payload["cash"] == 1250.50
    assert payload["equity"] == 10420.75
    assert payload["reason"] == "posttrade_refresh_from_snapshot"
