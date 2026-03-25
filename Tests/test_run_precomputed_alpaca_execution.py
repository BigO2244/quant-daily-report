from __future__ import annotations

from scripts.run_precomputed_alpaca_execution import _precompute_reconciliation_halt_reason


def test_precompute_reconciliation_halt_reason_pass_allows_execution() -> None:
    assert _precompute_reconciliation_halt_reason({"reconciliation_decision": "PASS"}) is None


def test_precompute_reconciliation_halt_reason_blocks_self_heal() -> None:
    assert (
        _precompute_reconciliation_halt_reason({"reconciliation_decision": "SELF_HEAL"})
        == "precompute_reconciliation_self_heal"
    )


def test_precompute_reconciliation_halt_reason_preserves_block_reason() -> None:
    assert (
        _precompute_reconciliation_halt_reason(
            {
                "reconciliation_decision": "BLOCK",
                "block_reason": "pretrade_blocked_reconciliation",
            }
        )
        == "pretrade_blocked_reconciliation"
    )
