from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.portfolio_operating_model import (
    ALLOCATION_POLICY_SCHEMA,
    PortfolioOperatingModelError,
    allocate_portfolio,
    build_session_manifest,
    content_hash,
    validate_operating_model_lineage,
)


def _decision(
    sleeve_id: str,
    weights: dict[str, float],
    *,
    outcome: str = "RECOMMENDATION",
) -> dict:
    body = {
        "schema_version": "caerus.sleeve_decision.v1",
        "trade_date": "2026-08-14",
        "session_id": "session:test",
        "session_hash": "a" * 64,
        "sleeve_id": sleeve_id,
        "display_name": sleeve_id,
        "strategy_type": "security_selection",
        "family": "test",
        "lifecycle_status": "paper",
        "mode": "PAPER",
        "outcome": outcome,
        "capital_eligible": True,
        "execution_eligible": True,
        "effective_as_of": "2026-08-14",
        "source_variant": "test",
        "source_cash_weight": 0.0,
        "target_rows": [
            {"symbol": symbol, "target_weight": weight}
            for symbol, weight in sorted(weights.items())
        ],
        "allocation_hint": None,
        "source_artifacts": [],
        "reason_codes": [],
        "message": "test",
    }
    seed = content_hash(body)
    body["decision_id"] = f"sleeve-decision:2026-08-14:{sleeve_id}:{seed[:24]}"
    body["content_hash"] = content_hash(body)
    return body


def _batch(*decisions: dict) -> dict:
    return {
        "schema_version": "caerus.sleeve_decision_batch.v1",
        "trade_date": "2026-08-14",
        "session_id": "session:test",
        "session_hash": "a" * 64,
        "generated_at": "2026-08-14T11:00:00+00:00",
        "complete_registry_coverage": True,
        "expected_sleeve_ids": [row["sleeve_id"] for row in decisions],
        "outcome_counts": {},
        "decisions": list(decisions),
        "content_hash": content_hash(list(decisions)),
    }


def _policy(**budgets: float) -> dict:
    return {
        "schema_version": ALLOCATION_POLICY_SCHEMA,
        "allocator_id": "caerus_paper_allocator",
        "allocator_version": "test",
        "method": "configured_risk_budget",
        "unavailable_policy": "fail_closed",
        "target_cash_weight": 0.05,
        "sleeve_risk_budgets": budgets,
    }


def test_allocator_nets_symbols_and_preserves_causal_sleeve_contributions() -> None:
    batch = _batch(
        _decision("sleeve_a", {"AAPL": 0.5, "MSFT": 0.5}),
        _decision("sleeve_b", {"AAPL": 1.0}),
    )

    allocation = allocate_portfolio(
        decision_batch=batch,
        allocation_policy=_policy(sleeve_a=0.6, sleeve_b=0.4),
        allocated_at="2026-08-14T11:00:00+00:00",
    )

    by_symbol = {row["symbol"]: row for row in allocation["targets"]}
    assert by_symbol["AAPL"]["target_weight"] == pytest.approx(0.665)
    assert by_symbol["MSFT"]["target_weight"] == pytest.approx(0.285)
    assert {
        row["sleeve_id"] for row in by_symbol["AAPL"]["sleeve_contributions"]
    } == {"sleeve_a", "sleeve_b"}
    assert sum(row["target_weight"] for row in allocation["targets"]) == pytest.approx(
        0.95
    )


def test_allocator_does_not_silently_redistribute_an_unavailable_sleeve() -> None:
    batch = _batch(
        _decision("sleeve_a", {"AAPL": 1.0}),
        _decision("sleeve_b", {}, outcome="UNAVAILABLE"),
    )

    with pytest.raises(PortfolioOperatingModelError, match="not recommendation-ready"):
        allocate_portfolio(
            decision_batch=batch,
            allocation_policy=_policy(sleeve_a=0.5, sleeve_b=0.5),
        )


def test_session_manifest_hashes_every_admitted_file(tmp_path: Path) -> None:
    source = tmp_path / "source.json"
    source.write_text(json.dumps({"price": 10}) + "\n", encoding="utf-8")

    session = build_session_manifest(
        trade_date="2026-08-14",
        run_id="run-1",
        as_of="2026-08-14T11:00:00+00:00",
        repo_root=tmp_path,
        inputs=({"name": "prices", "path": source, "required": True},),
        created_at="2026-08-14T11:00:00+00:00",
    )

    assert session["inputs"][0]["exists"] is True
    assert len(session["inputs"][0]["sha256"]) == 64
    body = dict(session)
    declared = body.pop("content_hash")
    assert declared == content_hash(body)


def test_lineage_validator_detects_allocation_tamper() -> None:
    decision = _decision("sleeve_a", {"AAPL": 1.0})
    batch = _batch(decision)
    allocation = allocate_portfolio(
        decision_batch=batch,
        allocation_policy=_policy(sleeve_a=1.0),
    )
    allocation["targets"][0]["target_weight"] = 0.5

    failures = validate_operating_model_lineage(
        session_manifest={
            "schema_version": "caerus.session_manifest.v1",
            "session_id": "session:test",
            "trade_date": "2026-08-14",
            "run_id": "run",
            "as_of": "2026-08-14T11:00:00+00:00",
            "created_at": "2026-08-14T11:00:00+00:00",
            "inputs": [],
            "content_hash": "a" * 64,
        },
        decision_batch=batch,
        allocation=allocation,
    )

    assert "operating_model:allocation_hash" in failures


def test_aquila_hold_preserves_quantity_and_shared_symbol_contributions():
    from core.aquila_monthly import SCHEMA
    quantities = {"AAPL": 5, "MSFT": 5}
    contract = dict(schema_version=SCHEMA, trade_date="2026-08-14", generated_at="2026-08-14T11:00:00+00:00", action="HOLD_NO_REBALANCE",
                    sizing_mode="FIXED_QUANTITY", account_equity=2000,
                    previous_session="2026-08-13", marks_as_of="2026-08-13T20:00:00+00:00",
                    marks={"AAPL": 100, "MSFT": 50}, target_quantities=quantities,
                    formation_id="aug", formation_hash="a"*64, formation_session="2026-07-31", ownership_snapshot_hash="b"*64,
                    ownership_snapshot_path="immutable/ownership.json", ownership_snapshot_sha256="d"*64,
                    monthly_plan_sha256="c"*64)
    contract["content_hash"] = content_hash(contract)
    aquila = _decision("caerus_aquila", {"AAPL": 2/3, "MSFT": 1/3})
    aquila["quantity_contract"] = contract
    allocation = allocate_portfolio(decision_batch=_batch(aquila, _decision("caerus_orion", {"AAPL": 1})),
                                    allocation_policy=_policy(caerus_aquila=.5/.95, caerus_orion=.45/.95))
    aapl = next(r for r in allocation["targets"] if r["symbol"] == "AAPL")
    own = {r["sleeve_id"]: r for r in aapl["sleeve_contributions"]}
    assert own["caerus_aquila"]["target_quantity"] == 5
    assert own["caerus_aquila"]["target_weight"] == .25
    assert own["caerus_orion"]["target_weight"] == pytest.approx(.575)
    assert allocation["quantity_contracts"]["caerus_aquila"] == contract
