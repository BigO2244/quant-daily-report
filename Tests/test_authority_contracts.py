from __future__ import annotations

import pytest

from authority.contracts import AuthorityContractError, build_decision_package, build_evidence_package, build_risk_package
from authority.pipeline import audit_execution_package, execution_package_from_dict, execution_package_from_risk, wrap_precompute_payload


def _rows():
    return [{"symbol": "AAPL", "side": "BUY", "shares": 2, "price": 100.0, "notional": 200.0}]


def test_handoff_chain_is_versioned_and_hash_linked():
    evidence = build_evidence_package(
        package_id="evidence:test", trade_date="2026-08-07", source_refs=["signals.json"], observations=_rows()
    )
    decision = build_decision_package(
        package_id="decision:test", trade_date="2026-08-07", evidence=evidence, target_rows=_rows(), source_refs=["signals.json"]
    )
    risk = build_risk_package(
        package_id="risk:test", decision=decision, approved_target_rows=_rows(), constraints={"max_weight": 1.0}, source_refs=["decision:decision:test"]
    )
    execution = execution_package_from_risk(risk)
    assert decision.evidence_hash == evidence.content_hash
    assert risk.decision_hash == decision.content_hash
    assert execution.risk_hash == risk.content_hash
    assert execution.approved_target_rows == risk.approved_target_rows
    assert execution_package_from_dict(execution.to_dict()).content_hash == execution.content_hash


def test_risk_cannot_invent_alpha_symbols():
    evidence = build_evidence_package(
        package_id="evidence:test", trade_date="2026-08-07", source_refs=["signals.json"], observations=_rows()
    )
    decision = build_decision_package(
        package_id="decision:test", trade_date="2026-08-07", evidence=evidence, target_rows=_rows(), source_refs=["signals.json"]
    )
    with pytest.raises(AuthorityContractError, match="cannot invent symbols"):
        build_risk_package(
            package_id="risk:test", decision=decision,
            approved_target_rows=[{"symbol": "MSFT", "side": "BUY", "shares": 1, "price": 100}],
            constraints={}, source_refs=["decision:decision:test"],
        )


def test_risk_cannot_reverse_or_increase_decision_targets():
    evidence = build_evidence_package(
        package_id="evidence:test", trade_date="2026-08-07", source_refs=["signals.json"], observations=_rows()
    )
    decision = build_decision_package(
        package_id="decision:test", trade_date="2026-08-07", evidence=evidence, target_rows=_rows(), source_refs=["signals.json"]
    )
    with pytest.raises(AuthorityContractError, match="reverse"):
        build_risk_package(
            package_id="risk:test", decision=decision,
            approved_target_rows=[{"symbol": "AAPL", "side": "SELL", "shares": 1}],
            constraints={}, source_refs=["decision:decision:test"],
        )
    with pytest.raises(AuthorityContractError, match="not increase"):
        build_risk_package(
            package_id="risk:test", decision=decision,
            approved_target_rows=[{"symbol": "AAPL", "side": "BUY", "shares": 3}],
            constraints={}, source_refs=["decision:decision:test"],
        )


def test_risk_cannot_reduce_decision_cash_reserve():
    rows = [{"symbol": "AAPL", "target_weight": 0.8}]
    evidence = build_evidence_package(
        package_id="evidence:test", trade_date="2026-08-07", source_refs=["signals.json"], observations=rows
    )
    decision = build_decision_package(
        package_id="decision:test", trade_date="2026-08-07", evidence=evidence,
        target_rows=rows, target_cash_weight=0.2, source_refs=["signals.json"],
    )
    with pytest.raises(AuthorityContractError, match="not reduce"):
        build_risk_package(
            package_id="risk:test", decision=decision, approved_target_rows=rows,
            approved_cash_weight=0.1, constraints={}, source_refs=["decision:decision:test"],
        )
def test_package_rows_are_deeply_immutable():
    evidence = build_evidence_package(
        package_id="evidence:test", trade_date="2026-08-07", source_refs=["signals.json"],
        observations=[{"symbol": "AAPL", "metadata": {"score": 1}}],
    )
    with pytest.raises(TypeError):
        evidence.observations[0]["symbol"] = "MSFT"
    with pytest.raises(TypeError):
        evidence.observations[0]["metadata"]["score"] = 2


def test_wrap_precompute_requires_explicit_targets_and_auditor_is_read_only():
    evidence, decision, risk, execution = wrap_precompute_payload(
        {"trade_date": "2026-08-07", "target_portfolio": _rows()},
        evidence_refs=["outputs/precompute/2026-08-07/signals.json"],
        decision_id="decision:2026-08-07",
        risk_id="risk:2026-08-07",
    )
    assert decision.evidence_hash == evidence.content_hash
    assert decision.package_id.startswith("decision:")
    audit = audit_execution_package(execution, _rows() + [{"symbol": "MSFT", "side": "BUY", "shares": 1}])
    assert audit.findings == ("UNAPPROVED_SYMBOL:MSFT",)
    with pytest.raises(AuthorityContractError):
        wrap_precompute_payload({"trade_date": "2026-08-07"}, evidence_refs=["x"], decision_id="d", risk_id="r")


def test_auditor_accepts_mechanical_exit_of_pretrade_holding():
    _, _, _, execution = wrap_precompute_payload(
        {"trade_date": "2026-08-07", "target_portfolio": _rows()},
        evidence_refs=["signals.json"],
        decision_id="decision:test",
        risk_id="risk:test",
    )
    audit = audit_execution_package(
        execution,
        [{"symbol": "MSFT", "side": "SELL", "shares": 1}],
        authorized_exit_symbols=["MSFT"],
    )
    assert audit.findings == ()


def test_wrap_precompute_preserves_explicit_no_action_decision():
    evidence, decision, risk, execution = wrap_precompute_payload(
        {"trade_date": "2026-08-07", "target_portfolio": []},
        evidence_refs=["signals.json"], decision_id="decision:none", risk_id="risk:none",
    )
    assert not evidence.observations
    assert not decision.target_rows
    assert not risk.approved_target_rows
    assert not execution.approved_target_rows
