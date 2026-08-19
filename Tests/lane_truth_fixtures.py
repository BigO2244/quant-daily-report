from __future__ import annotations

import hashlib

from core.accounting_journal import (
    ACCOUNTING_JOURNAL_ENTRY_SCHEMA,
    seal_journal_entry,
)
from core.deployment_policy import (
    DEPLOYMENT_POLICY_SCHEMA,
    seal_deployment_policy_payload,
)
from core.lane_performance import build_lane_performance
from core.lane_truth_status import build_truth_lineage_status
from core.lane_valuation import (
    RECONCILED_LANE_STATE_SCHEMA,
    THEORETICAL_LANE_STATE_SCHEMA,
    accounting_journal_hash,
    build_lane_valuation,
    seal_lane_state,
)


KNOWN_SLEEVES = {"caerus_orion", "caerus_lyra", "caerus_polaris"}
DEPLOYMENT_VERSION = "deployment-truth-v1"
PRIOR_VERSION = "deployment-truth-v0"
ROLLBACK_VERSION = "deployment-truth-safe"
AS_OF = "2026-08-18T21:00:00Z"


def digest(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def surface(kind: str) -> tuple[str, str]:
    return {
        "SHADOW": ("MODELED_SHADOW_NAV", "THEORETICAL_MODEL"),
        "PAPER": ("FACTUAL_PAPER", "BROKER_RECONCILED"),
        "LIVE": ("FACTUAL_LIVE", "BROKER_RECONCILED"),
    }[kind]


def lane(lane_id: str, kind: str, sleeve_id: str) -> dict:
    performance_surface, _ = surface(kind)
    return {
        "lane_id": lane_id,
        "lane_kind": kind,
        "enabled": True,
        "account_id_hash": digest(f"account:{lane_id}"),
        "broker_environment": f"TEST_{kind}",
        "performance_surface": performance_surface,
        "eligible_sleeves": [
            {
                "sleeve_id": sleeve_id,
                "minimum_weight": 0.0,
                "maximum_weight": 1.0,
                "initial_weight": 1.0,
                "allocation_eligible": True,
                "execution_eligible": kind != "SHADOW",
                "observation_enabled": True,
            }
        ],
        "allocator_policy": {"policy_id": "configured_risk_budget_v1"},
        "risk_policy": {"policy_id": "strict-risk-v1"},
        "capital_policy": {"owner_approved_ceiling": 500.0},
        "execution_policy": {"policy_id": "advisory-v1"},
        "reconciliation_policy": {"policy_id": "strict-v1"},
    }


def policy(*lanes: dict) -> dict:
    return seal_deployment_policy_payload(
        {
            "schema_version": DEPLOYMENT_POLICY_SCHEMA,
            "deployment_version": DEPLOYMENT_VERSION,
            "status": "ACTIVE",
            "approved_by": "Brett Olson",
            "owner_decision_id": "owner-decision-truth-v1",
            "approved_at": "2026-08-16T20:00:00Z",
            "effective_session": "2026-08-17",
            "prior_deployment_version": PRIOR_VERSION,
            "rollback_deployment_version": ROLLBACK_VERSION,
            "lanes": list(lanes),
        }
    )


def deployment_state(policy_payload: dict) -> dict:
    return {
        "active": {
            "deployment_version": DEPLOYMENT_VERSION,
            "state": "ACTIVE",
            "source_hash": policy_payload["content_hash"],
        },
        "prior": {
            "deployment_version": PRIOR_VERSION,
            "state": "SUPERSEDED",
            "source_hash": digest("prior-policy"),
        },
        "rollback": {
            "deployment_version": ROLLBACK_VERSION,
            "state": "ROLLBACK_READY",
            "source_hash": digest("rollback-policy"),
        },
    }


def capital() -> dict:
    return {
        "capital_ceiling_usd": 500.0,
        "effective_deployable_capital_usd": 460.0,
        "source_hash": digest("capital-state"),
    }


def _posting(identity: str, account: str, debit: float, credit: float, sleeve_id: str) -> dict:
    return {
        "posting_id": identity,
        "ledger_account": account,
        "sleeve_id": sleeve_id,
        "currency": "USD",
        "debit_amount": debit,
        "credit_amount": credit,
    }


def _entry_scope(lane_payload: dict, sleeve_id: str) -> dict:
    performance_surface, authority = surface(lane_payload["lane_kind"])
    return {
        "account_id_hash": lane_payload["account_id_hash"],
        "lane_id": lane_payload["lane_id"],
        "lane_kind": lane_payload["lane_kind"],
        "deployment_version": DEPLOYMENT_VERSION,
        "sleeve_id": sleeve_id,
        "counterparty_sleeve_id": None,
        "attribution_status": "ATTRIBUTED",
        "performance_surface": performance_surface,
        "economic_authority": authority,
    }


def journal(lane_payload: dict, sleeve_id: str) -> list[dict]:
    scope = _entry_scope(lane_payload, sleeve_id)
    opening = seal_journal_entry(
        {
            "schema_version": ACCOUNTING_JOURNAL_ENTRY_SCHEMA,
            "journal_entry_id": f"opening:{lane_payload['lane_id']}",
            "event_type": "OPENING_CAPITAL",
            "event_time": "2026-08-17T13:00:00Z",
            "trade_date": "2026-08-17",
            **scope,
            "symbol": None,
            "quantity": 0.0,
            "price": 0.0,
            "gross_amount": 1000.0,
            "fee_amount": 0.0,
            "net_amount": 1000.0,
            "session_id": None,
            "decision_id": None,
            "allocation_id": None,
            "plan_id": None,
            "broker_order_id": None,
            "fill_id": None,
            "source_hash": digest(f"opening-source:{lane_payload['lane_id']}"),
            "postings": [
                _posting("opening-cash", "ASSET:CASH", 1000.0, 0.0, sleeve_id),
                _posting("opening-equity", "EQUITY:OPENING_CAPITAL", 0.0, 1000.0, sleeve_id),
            ],
        }
    )
    buy = seal_journal_entry(
        {
            "schema_version": ACCOUNTING_JOURNAL_ENTRY_SCHEMA,
            "journal_entry_id": f"buy:{lane_payload['lane_id']}",
            "event_type": "BUY",
            "event_time": "2026-08-17T14:35:00Z",
            "trade_date": "2026-08-17",
            **scope,
            "symbol": "AAPL",
            "quantity": 10.0,
            "price": 10.0,
            "gross_amount": 100.0,
            "fee_amount": 1.0,
            "net_amount": -101.0,
            "session_id": "session-truth-v1",
            "decision_id": f"decision:{sleeve_id}",
            "allocation_id": f"allocation:{lane_payload['lane_id']}",
            "plan_id": f"plan:{lane_payload['lane_id']}",
            "broker_order_id": f"broker-order:{lane_payload['lane_id']}",
            "fill_id": f"fill:{lane_payload['lane_id']}",
            "source_hash": digest(f"fill-source:{lane_payload['lane_id']}"),
            "postings": [
                _posting("buy-security", "ASSET:SECURITY", 100.0, 0.0, sleeve_id),
                _posting("buy-fee", "EXPENSE:FEE", 1.0, 0.0, sleeve_id),
                _posting("buy-cash", "ASSET:CASH", 0.0, 101.0, sleeve_id),
            ],
        }
    )
    return [opening, buy]


def valuation_and_performance(lane_payload: dict, sleeve_id: str) -> tuple[dict, dict]:
    entries = journal(lane_payload, sleeve_id)
    performance_surface, authority = surface(lane_payload["lane_kind"])
    state_schema = (
        THEORETICAL_LANE_STATE_SCHEMA
        if lane_payload["lane_kind"] == "SHADOW"
        else RECONCILED_LANE_STATE_SCHEMA
    )
    state_status = "MODELED" if lane_payload["lane_kind"] == "SHADOW" else "PASS"
    valuations = []
    for date, as_of, price in (
        ("2026-08-17", "2026-08-17T21:00:00Z", 10.0),
        ("2026-08-18", AS_OF, 11.0),
    ):
        market_value = 10.0 * price
        state = seal_lane_state(
            {
                "schema_version": state_schema,
                "status": state_status,
                "as_of": as_of,
                "valuation_date": date,
                "account_id_hash": lane_payload["account_id_hash"],
                "lane_id": lane_payload["lane_id"],
                "lane_kind": lane_payload["lane_kind"],
                "deployment_version": DEPLOYMENT_VERSION,
                "performance_surface": performance_surface,
                "economic_authority": authority,
                "cash": 899.0,
                "equity": 899.0 + market_value,
                "positions": [
                    {
                        "symbol": "AAPL",
                        "quantity": 10.0,
                        "price": price,
                        "market_value": market_value,
                        "source_hash": digest(f"price:{lane_payload['lane_id']}:{date}"),
                    }
                ],
                "journal_hash": accounting_journal_hash(entries),
                "source_hash": digest(f"state:{lane_payload['lane_id']}:{date}"),
            }
        )
        valuations.append(build_lane_valuation(journal_entries=entries, lane_state=state))
    return valuations[-1], build_lane_performance(valuations)


def lineage(lane_payload: dict, evidence_type: str, status: str = "PASS", blockers=()) -> dict:
    return build_truth_lineage_status(
        evidence_type=evidence_type,
        status=status,
        as_of=AS_OF,
        lane_id=lane_payload["lane_id"],
        lane_kind=lane_payload["lane_kind"],
        deployment_version=DEPLOYMENT_VERSION,
        performance_surface=lane_payload["performance_surface"],
        source_hashes=[digest(f"{evidence_type}:{lane_payload['lane_id']}")],
        blocker_codes=blockers,
    )
