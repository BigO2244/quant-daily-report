"""Production causal closure for generic Live v1 posttrade evidence."""

from __future__ import annotations

import copy
import hashlib
from pathlib import Path
from typing import Any, Callable, Mapping

from authority.lane_exact_plan import canonical_json
from core.accounting_journal import validate_accounting_journal
from core.generic_live_v1_submission import (
    _write_exclusive,
    finalize_generic_live_v1_posttrade,
    validate_generic_live_v1_order_lifecycle,
    validate_generic_live_v1_submission_result,
)
from core.lane_factual_reporting_inputs import build_lane_factual_reporting_inputs
from core.lane_oms import build_lane_oms_intents
from core.lane_performance import validate_lane_performance
from core.lane_reconciliation import build_lane_reconciliation, validate_lane_reconciliation
from core.reconciled_fill_accounting import build_reconciled_fill_journal_entries
from core.lane_truth_status import (
    build_all_lane_audit,
    build_daily_lane_audit,
    build_dashboard_performance_surfaces,
    build_truth_lineage_status,
    validate_all_lane_audit,
    validate_daily_lane_audit,
    validate_dashboard_performance_surfaces,
)
from core.lane_valuation import accounting_journal_hash, validate_lane_valuation


GENERIC_LIVE_V1_PRODUCTION_CLOSURE_SCHEMA = (
    "caerus.generic_live_v1_production_posttrade_closure.v1"
)


class GenericLiveV1PosttradeError(RuntimeError):
    """Raised when the exact production causal chain is incomplete."""


def _hash(payload: Mapping[str, Any]) -> str:
    body = copy.deepcopy(dict(payload))
    body.pop("content_hash", None)
    return hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()


def finalize_generic_live_v1_production_posttrade(
    *, submission_result: Mapping[str, Any], exact_plan: Mapping[str, Any],
    order_lifecycle: Mapping[str, Any], reconciliation: Mapping[str, Any],
    journal_entries: list[Mapping[str, Any]],
    valuations: list[Mapping[str, Any]], performance: Mapping[str, Any] | None,
    daily_lane_audit: Mapping[str, Any], all_lane_audit: Mapping[str, Any],
    dashboard_projection: Mapping[str, Any], finalized_at: str,
    rearm_state_path: Path | str, base_result_path: Path | str,
    closure_result_path: Path | str,
    rollback_handler: Callable[[str], Mapping[str, Any]],
) -> dict[str, Any]:
    """Validate and durably bind reconciliation through published truth surfaces."""

    rolled_back = False

    def rollback_break(trigger: str) -> Mapping[str, Any]:
        nonlocal rolled_back
        evidence = rollback_handler(trigger)
        if (
            not isinstance(evidence, Mapping)
            or evidence.get("status") != "ROLLED_BACK_ARMED"
            or evidence.get("trigger") != trigger
            or evidence.get("paper_bytes_unchanged") is not True
            or evidence.get("cron_exact_line_removed") is not True
            or evidence.get("config_action") not in {
                "RESTORED_BACKUP", "REMOVED_NO_PRIOR_CONFIG", "ALREADY_ABSENT",
            }
            or not evidence.get("rearm_hash")
        ):
            raise GenericLiveV1PosttradeError(
                f"{trigger} rollback evidence is incomplete"
            )
        rolled_back = True
        return evidence

    try:
        reconciled = validate_lane_reconciliation(reconciliation, exact_plan=exact_plan)
    except Exception:
        try:
            rollback_break("RECONCILIATION_BREAK")
        finally:
            raise
    try:
        journal = validate_accounting_journal(journal_entries)
        if not journal:
            raise GenericLiveV1PosttradeError("production Live valuation requires journal history")
        session_rows = [
            row for row in journal if row["source_hash"] == reconciled["content_hash"]
        ]
        reconciled_fill_ids = {row["fill_id"] for row in reconciled["reconciled_fills"]}
        if {row["fill_id"] for row in session_rows} != reconciled_fill_ids:
            raise GenericLiveV1PosttradeError("journal does not exactly cover reconciled session fills")
    except Exception:
        try:
            rollback_break("ACCOUNTING_BREAK")
        finally:
            raise
    try:
        observed_journal_hash = accounting_journal_hash(journal)
        daily = validate_daily_lane_audit(daily_lane_audit)
        if (
            daily["lane_id"] != "generic-live-v1"
            or daily["lane_kind"] != "LIVE"
            or daily["account_id_hash"] != exact_plan["account_id_hash"]
            or daily["deployment_version"] != exact_plan["deployment_version"]
        ):
            raise GenericLiveV1PosttradeError("daily audit scope differs from Live plan")
        green_reporting = reconciled["status"] == "PASS"
        if green_reporting:
            checked_valuations = [validate_lane_valuation(row) for row in valuations]
            if not checked_valuations or performance is None:
                raise GenericLiveV1PosttradeError("PASS session requires factual valuation/performance")
            if checked_valuations != sorted(checked_valuations, key=lambda row: (row["as_of"], row["valuation_id"])):
                raise GenericLiveV1PosttradeError("valuations must be in deterministic as-of order")
            for valuation in checked_valuations:
                if (
                    valuation["lane_id"] != "generic-live-v1"
                    or valuation["lane_kind"] != "LIVE"
                    or valuation["account_id_hash"] != exact_plan["account_id_hash"]
                    or valuation["deployment_version"] != exact_plan["deployment_version"]
                    or valuation["performance_surface"] != "FACTUAL_LIVE"
                ):
                    raise GenericLiveV1PosttradeError("valuation scope differs from exact Live plan")
            latest_valuation = checked_valuations[-1]
            if (
                latest_valuation["journal_hash"] != observed_journal_hash
                or latest_valuation["journal_entry_count"] != len(journal)
                or reconciled["content_hash"] not in latest_valuation["source_hashes"]
            ):
                raise GenericLiveV1PosttradeError("latest valuation does not bind journal/reconciliation")
            perf = validate_lane_performance(performance)
            valuation_hashes = [row["content_hash"] for row in checked_valuations]
            if (
                perf["source_valuation_hashes"] != valuation_hashes
                or perf["latest_as_of"] != latest_valuation["as_of"]
                or perf["lane_id"] != "generic-live-v1"
                or perf["lane_kind"] != "LIVE"
                or perf["account_id_hash"] != exact_plan["account_id_hash"]
            ):
                raise GenericLiveV1PosttradeError(
                    "Live performance does not bind the exact factual valuations"
                )
            if (
                daily["as_of"] != perf["latest_as_of"]
                or perf["content_hash"] not in daily["source_hashes"]
                or daily["status"] != "PASS"
            ):
                raise GenericLiveV1PosttradeError(
                    "daily audit does not bind exact Live performance"
                )
        else:
            if valuations or performance is not None or daily["status"] != "BLOCKED" or any(
                claim["claim_status"] != "SUPPRESSED" or not claim["blocker_codes"]
                for claim in daily["return_claims"]
            ):
                raise GenericLiveV1PosttradeError(
                    "non-PASS session must suppress factual valuation/performance claims"
                )
            checked_valuations = []
            valuation_hashes = []
            perf = None
        aggregate = validate_all_lane_audit(all_lane_audit)
        matching_summaries = [
            row for row in aggregate["lane_audits"] if row["lane_id"] == "generic-live-v1"
        ]
        if (
            len(matching_summaries) != 1
            or matching_summaries[0]["audit_hash"] != daily["content_hash"]
            or daily["content_hash"] not in aggregate["lane_audit_hashes"]
            or aggregate["as_of"] != daily["as_of"]
        ):
            raise GenericLiveV1PosttradeError("all-lane audit omits exact Live daily audit")
        dashboard = validate_dashboard_performance_surfaces(dashboard_projection)
        live_surfaces = [
            row for row in dashboard["performance_surfaces"]
            if row["lane_id"] == "generic-live-v1"
        ]
        if (
            daily["content_hash"] not in dashboard["source_audit_hashes"]
            or dashboard["as_of"] != daily["as_of"]
            or not live_surfaces
            or any(
                (row["claim_status"] != "AVAILABLE" if green_reporting else row["claim_status"] != "SUPPRESSED")
                for row in live_surfaces
            )
            or (
                green_reporting
                and any(perf["content_hash"] not in row["source_hashes"] for row in live_surfaces)
            )
        ):
            raise GenericLiveV1PosttradeError("dashboard omits exact Live audit/performance lineage")
    except Exception:
        try:
            rollback_break("REPORTING_BREAK")
        finally:
            raise
    try:
        base = finalize_generic_live_v1_posttrade(
            submission_result=submission_result, exact_plan=exact_plan,
            order_lifecycle=order_lifecycle, reconciliation=reconciled,
            journal_entries=journal, performance=perf,
            dashboard_projection=dashboard, finalized_at=finalized_at,
            rearm_state_path=rearm_state_path, result_path=base_result_path,
            rollback_handler=rollback_break,
        )
    except Exception:
        if not rolled_back:
            try:
                rollback_break("REPORTING_BREAK")
            finally:
                raise
        raise
    evidence_hashes = sorted({
        base["content_hash"], reconciled["content_hash"],
        *[row["record_hash"] for row in session_rows], *valuation_hashes,
        *([] if perf is None else [perf["content_hash"]]),
        daily["content_hash"], aggregate["content_hash"],
        dashboard["content_hash"],
    })
    body = {
        "schema_version": GENERIC_LIVE_V1_PRODUCTION_CLOSURE_SCHEMA,
        "status": base["status"],
        "finalized_at": base["finalized_at"],
        "submission_result_hash": submission_result["content_hash"],
        "plan_hash": exact_plan["content_hash"],
        "reconciliation_hash": reconciled["content_hash"],
        "journal_hash": observed_journal_hash,
        "valuation_hashes": valuation_hashes,
        "performance_hash": None if perf is None else perf["content_hash"],
        "daily_lane_audit_hash": daily["content_hash"],
        "all_lane_audit_hash": aggregate["content_hash"],
        "dashboard_projection_hash": dashboard["content_hash"],
        "base_posttrade_result_hash": base["content_hash"],
        "evidence_hashes": evidence_hashes,
        "rollback_required": base["rollback_required"],
        "generic_kill_switch_state": "ARMED",
        "broker_call_performed": False,
        "broker_submission_allowed": False,
        "execution_authority": False,
        "activation_authority": False,
    }
    body["content_hash"] = _hash(body)
    try:
        _write_exclusive(Path(closure_result_path), body)
    except Exception:
        if not rolled_back:
            try:
                rollback_break("REPORTING_BREAK")
            finally:
                raise
        raise
    return body


def build_and_finalize_generic_live_v1_production_posttrade(
    *, submission_result: Mapping[str, Any], exact_plan: Mapping[str, Any],
    order_lifecycle: Mapping[str, Any], broker_orders: list[Mapping[str, Any]],
    broker_fills: list[Mapping[str, Any]], ending_state: Mapping[str, Any],
    existing_journal_entries: list[Mapping[str, Any]],
    prior_valuations: list[Mapping[str, Any]], deployment_policy: Mapping[str, Any],
    known_sleeve_ids: list[str], deployment_state: Mapping[str, Any],
    capital: Mapping[str, Any], other_lane_audits: list[Mapping[str, Any]],
    reconciled_at: str, valuation_date: str, finalized_at: str,
    reporting_artifact_directory: Path | str,
    rearm_state_path: Path | str, base_result_path: Path | str,
    closure_result_path: Path | str,
    rollback_handler: Callable[[str], Mapping[str, Any]],
) -> dict[str, Any]:
    """Build and persist the exact factual chain; never read a broker or registry."""

    rollback_triggers: set[str] = set()

    def rollback_break(trigger: str) -> Mapping[str, Any]:
        evidence = rollback_handler(trigger)
        if (
            not isinstance(evidence, Mapping)
            or evidence.get("status") != "ROLLED_BACK_ARMED"
            or evidence.get("trigger") != trigger
            or evidence.get("paper_bytes_unchanged") is not True
            or evidence.get("cron_exact_line_removed") is not True
            or evidence.get("config_action") not in {
                "RESTORED_BACKUP", "REMOVED_NO_PRIOR_CONFIG", "ALREADY_ABSENT",
            }
            or not evidence.get("rearm_hash")
        ):
            raise GenericLiveV1PosttradeError(
                f"{trigger} rollback evidence is incomplete"
            )
        rollback_triggers.add(trigger)
        return evidence

    current_trigger = "ORDER_BREAK"
    try:
        checked_submission = validate_generic_live_v1_submission_result(
            submission_result
        )
        checked_lifecycle = validate_generic_live_v1_order_lifecycle(
            order_lifecycle, submission_result=checked_submission,
        )
        current_trigger = "RECONCILIATION_BREAK"
        reconciliation = build_lane_reconciliation(
            exact_plan=exact_plan, wal_intents=build_lane_oms_intents(exact_plan),
            broker_orders=broker_orders, broker_fills=broker_fills,
            ending_state=ending_state, reconciled_at=reconciled_at,
        )
        current_trigger = "ACCOUNTING_BREAK"
        additions = (
            build_reconciled_fill_journal_entries(
                reconciliation, exact_plan=exact_plan,
                existing_entries=existing_journal_entries,
            )
            if reconciliation["accounting_ready"] is True
            else []
        )
        journal = validate_accounting_journal([*existing_journal_entries, *additions])
        current_trigger = "REPORTING_BREAK"
        if reconciliation["status"] == "PASS":
            factual = build_lane_factual_reporting_inputs(
                exact_plan=exact_plan, reconciliation=reconciliation,
                ending_state=ending_state, journal_entries=journal,
                prior_valuations=prior_valuations, valuation_date=valuation_date,
            )
            valuation = factual["valuation"]
            performance = factual["performance"]
            valuations = [*prior_valuations, valuation]
            journal_status = factual["journal_status"]
            reconciliation_status = factual["reconciliation_status"]
            as_of = factual["as_of"]
        else:
            valuation = None
            performance = None
            valuations = []
            as_of = str(ending_state["as_of"])
            surface = "FACTUAL_LIVE"
            journal_status = build_truth_lineage_status(
                evidence_type="JOURNAL", status="PASS" if journal else "MISSING",
                as_of=as_of, lane_id="generic-live-v1", lane_kind="LIVE",
                deployment_version=exact_plan["deployment_version"],
                performance_surface=surface,
                source_hashes=(
                    [accounting_journal_hash(journal), *[row["record_hash"] for row in journal]]
                    if journal else [reconciliation["content_hash"]]
                ),
                blocker_codes=[] if journal else ["JOURNAL_HISTORY_MISSING"],
            )
            reconciliation_status = build_truth_lineage_status(
                evidence_type="RECONCILIATION", status="FAIL", as_of=as_of,
                lane_id="generic-live-v1", lane_kind="LIVE",
                deployment_version=exact_plan["deployment_version"],
                performance_surface=surface,
                source_hashes=[reconciliation["content_hash"], ending_state["content_hash"]],
                blocker_codes=[f"RECONCILIATION_{reconciliation['status']}_ROLLBACK_REQUIRED"],
            )
        daily = build_daily_lane_audit(
            deployment_policy=deployment_policy, known_sleeve_ids=known_sleeve_ids,
            lane_id="generic-live-v1", as_of=as_of,
            deployment_state=deployment_state, capital=capital,
            journal_status=journal_status,
            reconciliation_status=reconciliation_status,
            valuation=valuation, performance=performance,
        )
        lane_audits = sorted([*other_lane_audits, daily], key=lambda row: row["lane_id"])
        aggregate = build_all_lane_audit(
            deployment_policy=deployment_policy,
            known_sleeve_ids=known_sleeve_ids, lane_audits=lane_audits,
        )
        dashboard = build_dashboard_performance_surfaces(lane_audits)
        artifacts = [
            reconciliation, *additions, journal_status, reconciliation_status,
            *([] if valuation is None else [valuation]),
            *([] if performance is None else [performance]),
            daily, aggregate, dashboard,
        ]
        root = Path(reporting_artifact_directory)
        for artifact in artifacts:
            identity = artifact.get("content_hash") or artifact.get("record_hash")
            if not isinstance(identity, str) or len(identity) != 64:
                raise GenericLiveV1PosttradeError(
                    "reporting artifact lacks immutable hash"
                )
            _write_exclusive(root / f"{identity}.json", artifact)
        result = finalize_generic_live_v1_production_posttrade(
            submission_result=checked_submission, exact_plan=exact_plan,
            order_lifecycle=checked_lifecycle, reconciliation=reconciliation,
            journal_entries=journal, valuations=valuations,
            performance=performance, daily_lane_audit=daily,
            all_lane_audit=aggregate, dashboard_projection=dashboard,
            finalized_at=finalized_at, rearm_state_path=rearm_state_path,
            base_result_path=base_result_path,
            closure_result_path=closure_result_path,
            rollback_handler=rollback_break,
        )
        return result
    except BaseException:
        if not rollback_triggers:
            rollback_break(current_trigger)
        raise


__all__ = [
    "GENERIC_LIVE_V1_PRODUCTION_CLOSURE_SCHEMA", "GenericLiveV1PosttradeError",
    "build_and_finalize_generic_live_v1_production_posttrade",
    "finalize_generic_live_v1_production_posttrade",
]
