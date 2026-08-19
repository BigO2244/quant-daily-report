from __future__ import annotations

import json
from pathlib import Path

from core.generic_live_monday_rehearsal import (
    build_generic_live_monday_rehearsal,
    validate_generic_live_monday_rehearsal,
)


ROOT = Path(__file__).resolve().parents[1]


def test_monday_rehearsal_fails_closed_without_changing_lyra_economics():
    owner = json.loads((
        ROOT / "docs/governance/decision_records/"
        "generic_live_v1_dynamic_balance_owner_decision_20260824.json"
    ).read_text())
    result = build_generic_live_monday_rehearsal(
        owner_decision=owner,
        universe_freeze_hash="850e8104e0b5d913e96b6627d0b47219967802328a280e07a7c8f94e6835e434",
        rehearsed_at="2026-08-19T13:30:00+00:00",
    )
    assert result["requested_effective_session"] == "2026-08-24"
    assert result["completed_data_session_at_monday_precompute"] == "2026-08-21"
    assert result["first_governed_signal_session"] == "2026-08-24"
    assert result["first_executable_session_under_canonical_lyra_economics"] == "2026-08-25"
    assert result["status"] == "BLOCKED_NO_TRADE_REARMED"
    assert result["order_count"] == 0
    assert result["submission_allowed"] is False
    assert result["execution_authority"] is False


def test_sealed_monday_rehearsal_is_valid():
    payload = json.loads((
        ROOT / "docs/evidence/generic_live_v1_dynamic_monday_rehearsal_20260824.json"
    ).read_text())
    assert validate_generic_live_monday_rehearsal(payload) == payload
