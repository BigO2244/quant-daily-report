from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.paper_drill_epoch import (
    POLICY_SCHEMA_VERSION,
    claim_namespace,
    scoped_wal_root,
    validate_drill_epoch,
)


PAPER_HOST = "https://paper-api.alpaca.markets"


def _policy(path: Path) -> Path:
    path.write_text(json.dumps({
        "schema_version": POLICY_SCHEMA_VERSION,
        "trade_date": "2026-08-13",
        "paper_only": True,
        "live_eligible": False,
        "allowed_epochs": ["2026-08-13T1230ET", "2026-08-13T1330ET"],
        "safety_contract": {
            "account_date_mutex_remains_global": True,
            "epoch_reuse_is_idempotent_only": True,
            "unresolved_prior_epoch_blocks_submission": True,
            "wal_and_claim_records_are_append_only": True,
            "normal_hours_market_orders_after_close_prohibited": True,
        },
    }) + "\n", encoding="utf-8")
    return path


def test_governed_epoch_is_exactly_allowlisted_and_paper_only(tmp_path: Path):
    policy = _policy(tmp_path / "policy.json")
    assert validate_drill_epoch(
        "2026-08-13T1230ET", trade_date="2026-08-13",
        policy_path=policy, broker_paper=True, base_url=PAPER_HOST,
    ) == "2026-08-13T1230ET"
    assert validate_drill_epoch(
        None, trade_date="2026-08-13", policy_path=None,
        broker_paper=False, base_url="https://api.alpaca.markets",
    ) is None
    for epoch, paper, endpoint in (
        ("2026-08-13T1245ET", True, PAPER_HOST),
        ("2026-08-14T1230ET", True, PAPER_HOST),
        ("2026-08-13T1230ET", False, PAPER_HOST),
        ("2026-08-13T1230ET", True, "https://api.alpaca.markets"),
    ):
        with pytest.raises(RuntimeError):
            validate_drill_epoch(
                epoch, trade_date="2026-08-13", policy_path=policy,
                broker_paper=paper, base_url=endpoint,
            )


def test_epoch_namespaces_wal_and_claim_but_legacy_paths_are_unchanged():
    assert scoped_wal_root(Path("wal"), None) == Path("wal")
    assert claim_namespace(None) == Path()
    assert scoped_wal_root(Path("wal"), "2026-08-13T1230ET") == (
        Path("wal/epochs/2026-08-13T1230ET")
    )
    assert claim_namespace("2026-08-13T1230ET") == (
        Path("epochs/2026-08-13T1230ET")
    )
