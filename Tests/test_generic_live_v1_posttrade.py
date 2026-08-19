from __future__ import annotations

import copy

import pytest

from core import generic_live_v1_posttrade as subject


H = {
    name: f"{index:x}" * 64
    for index, name in enumerate(
        (
            "account", "plan", "submission", "reconciliation", "entry",
            "journal", "valuation", "performance", "daily", "aggregate",
            "dashboard", "base",
        ),
        start=1,
    )
}


def _inputs(tmp_path) -> dict:
    plan = {
        "account_id_hash": H["account"], "lane_id": "generic-live-v1",
        "lane_kind": "LIVE", "deployment_version": "live-v1",
        "content_hash": H["plan"],
    }
    reconciliation = {
        "content_hash": H["reconciliation"],
        "status": "PASS",
        "reconciled_fills": [{"fill_id": "fill:1"}],
    }
    journal = [{
        "fill_id": "fill:1", "source_hash": H["reconciliation"],
        "record_hash": H["entry"],
    }]
    valuation = {
        "valuation_id": "valuation:1", "valuation_date": "2026-08-19",
        "as_of": "2026-08-19T21:00:00Z", "account_id_hash": H["account"],
        "lane_id": "generic-live-v1", "lane_kind": "LIVE",
        "deployment_version": "live-v1", "performance_surface": "FACTUAL_LIVE",
        "journal_hash": H["journal"], "journal_entry_count": 1,
        "source_hashes": [H["reconciliation"]], "content_hash": H["valuation"],
    }
    performance = {
        "lane_id": "generic-live-v1", "lane_kind": "LIVE",
        "account_id_hash": H["account"],
        "latest_as_of": valuation["as_of"],
        "source_valuation_hashes": [H["valuation"]],
        "content_hash": H["performance"],
    }
    daily = {
        "lane_id": "generic-live-v1", "lane_kind": "LIVE",
        "account_id_hash": H["account"], "deployment_version": "live-v1",
        "as_of": valuation["as_of"], "status": "PASS",
        "source_hashes": [H["performance"]],
        "content_hash": H["daily"],
    }
    aggregate = {
        "as_of": valuation["as_of"], "lane_audit_hashes": [H["daily"]],
        "lane_audits": [{"lane_id": "generic-live-v1", "audit_hash": H["daily"]}],
        "content_hash": H["aggregate"],
    }
    dashboard = {
        "as_of": valuation["as_of"], "source_audit_hashes": [H["daily"]],
        "performance_surfaces": [{
            "lane_id": "generic-live-v1", "claim_status": "AVAILABLE",
            "source_hashes": [H["performance"]],
        }],
        "content_hash": H["dashboard"],
    }
    return {
        "submission_result": {"content_hash": H["submission"]},
        "exact_plan": plan, "order_lifecycle": {"content_hash": "f" * 64},
        "reconciliation": reconciliation, "journal_entries": journal,
        "valuations": [valuation], "performance": performance,
        "daily_lane_audit": daily, "all_lane_audit": aggregate,
        "dashboard_projection": dashboard,
        "finalized_at": "2026-08-19T21:01:00Z",
        "rearm_state_path": tmp_path / "gate.json",
        "base_result_path": tmp_path / "base" / "result.json",
        "closure_result_path": tmp_path / "closure" / "result.json",
        "rollback_handler": lambda trigger: {
            "status": "ROLLED_BACK_ARMED", "trigger": trigger,
            "paper_bytes_unchanged": True, "cron_exact_line_removed": True,
            "config_action": "ALREADY_ABSENT", "rearm_hash": "d" * 64,
        },
    }


@pytest.fixture
def validators(monkeypatch):
    monkeypatch.setattr(subject, "validate_lane_reconciliation", lambda value, exact_plan: value)
    monkeypatch.setattr(subject, "validate_accounting_journal", lambda value: value)
    monkeypatch.setattr(subject, "accounting_journal_hash", lambda value: H["journal"])
    monkeypatch.setattr(subject, "validate_lane_valuation", lambda value: value)
    monkeypatch.setattr(subject, "validate_lane_performance", lambda value: value)
    monkeypatch.setattr(subject, "validate_daily_lane_audit", lambda value: value)
    monkeypatch.setattr(subject, "validate_all_lane_audit", lambda value: value)
    monkeypatch.setattr(subject, "validate_dashboard_performance_surfaces", lambda value: value)
    monkeypatch.setattr(
        subject, "finalize_generic_live_v1_posttrade",
        lambda **kwargs: {
            "content_hash": H["base"], "status": "GREEN_REARMED",
            "finalized_at": kwargs["finalized_at"], "rollback_required": False,
        },
    )


def test_seals_exact_production_causal_chain_and_is_idempotent(tmp_path, validators) -> None:
    arguments = _inputs(tmp_path)
    first = subject.finalize_generic_live_v1_production_posttrade(**arguments)
    second = subject.finalize_generic_live_v1_production_posttrade(**arguments)

    assert first == second
    assert first["status"] == "GREEN_REARMED"
    assert first["reconciliation_hash"] == H["reconciliation"]
    assert first["journal_hash"] == H["journal"]
    assert first["valuation_hashes"] == [H["valuation"]]
    assert first["daily_lane_audit_hash"] == H["daily"]
    assert first["all_lane_audit_hash"] == H["aggregate"]
    assert first["dashboard_projection_hash"] == H["dashboard"]
    assert first["execution_authority"] is False
    assert first["activation_authority"] is False


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda values: values["journal_entries"].clear(), "journal history"),
        (
            lambda values: values["valuations"][0].update(source_hashes=[]),
            "journal/reconciliation",
        ),
        (
            lambda values: values["performance"].update(source_valuation_hashes=[]),
            "factual valuations",
        ),
        (
            lambda values: values["daily_lane_audit"].update(source_hashes=[]),
            "Live performance",
        ),
        (
            lambda values: values["all_lane_audit"].update(lane_audit_hashes=[]),
            "exact Live daily audit",
        ),
        (
            lambda values: values["dashboard_projection"].update(source_audit_hashes=[]),
            "Live audit/performance",
        ),
    ],
)
def test_rejects_any_missing_causal_link(tmp_path, validators, mutation, message) -> None:
    arguments = _inputs(tmp_path)
    mutation(arguments)
    with pytest.raises(subject.GenericLiveV1PosttradeError, match=message):
        subject.finalize_generic_live_v1_production_posttrade(**arguments)


def test_immutable_closure_path_rejects_different_evidence(tmp_path, validators) -> None:
    arguments = _inputs(tmp_path)
    subject.finalize_generic_live_v1_production_posttrade(**arguments)
    changed = copy.deepcopy(arguments)
    changed["all_lane_audit"]["content_hash"] = "e" * 64
    with pytest.raises(Exception, match="immutable artifact collision"):
        subject.finalize_generic_live_v1_production_posttrade(**changed)
