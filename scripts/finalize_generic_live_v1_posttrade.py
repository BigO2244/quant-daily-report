#!/usr/bin/env python3
"""Finalize a generic Live v1 causal chain from explicit protected artifacts."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
from pathlib import Path
from typing import Any

from core.generic_live_v1_ops import (
    perform_generic_live_v1_rollback,
    require_protected_mode,
    secure_path,
    secure_read_json,
)
from core.generic_live_v1_posttrade import (
    build_and_finalize_generic_live_v1_production_posttrade,
)
from core.generic_live_v1_submission import (
    ensure_generic_live_v1_rearmed_after_failure,
    rearm_generic_live_v1_session,
)


CANONICAL_OPS_ROOT = Path("/home/brettolson/.caerus")
CANONICAL_ACTIVE_CONFIG = CANONICAL_OPS_ROOT / "generic_live_v1.env"
CANONICAL_BACKUP_CONFIG = CANONICAL_OPS_ROOT / "generic_live_v1.env.rollback"


def _array(payload: Any, *, key: str) -> list[dict]:
    value = payload.get(key) if isinstance(payload, dict) else payload
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise RuntimeError(f"{key} input must be an object array or {{{key!r}: [...]}}")
    return value


def _strings(payload: Any, *, key: str) -> list[str]:
    value = payload.get(key) if isinstance(payload, dict) else payload
    if (
        not isinstance(value, list) or not value
        or any(not isinstance(row, str) or not row for row in value)
        or value != sorted(set(value))
    ):
        raise RuntimeError(f"{key} input must be a sorted unique string array")
    return value


def _require_canonical_config_paths(active: Path, backup: Path) -> None:
    if active != CANONICAL_ACTIVE_CONFIG or backup != CANONICAL_BACKUP_CONFIG:
        raise RuntimeError("generic Live v1 posttrade config paths are fixed")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--submission-result", type=Path, required=True)
    parser.add_argument("--exact-plan", type=Path, required=True)
    parser.add_argument("--order-lifecycle", type=Path, required=True)
    parser.add_argument("--broker-orders", type=Path, required=True)
    parser.add_argument("--broker-fills", type=Path, required=True)
    parser.add_argument("--ending-state", type=Path, required=True)
    parser.add_argument("--existing-journal", type=Path, required=True)
    parser.add_argument("--prior-valuations", type=Path, required=True)
    parser.add_argument("--deployment-policy", type=Path, required=True)
    parser.add_argument("--known-sleeve-ids", type=Path, required=True)
    parser.add_argument("--deployment-state", type=Path, required=True)
    parser.add_argument("--capital", type=Path, required=True)
    parser.add_argument("--other-lane-audits", type=Path, required=True)
    parser.add_argument("--session-gate-path", type=Path, required=True)
    parser.add_argument("--base-result-path", type=Path, required=True)
    parser.add_argument("--closure-result-path", type=Path, required=True)
    parser.add_argument("--reporting-artifact-directory", type=Path, required=True)
    parser.add_argument("--exact-cron-line", required=True)
    parser.add_argument("--active-config-path", type=Path, required=True)
    parser.add_argument("--backup-config-path", type=Path, required=True)
    parser.add_argument("--paper-path", action="append", type=Path, required=True)
    parser.add_argument("--paper-root", type=Path, required=True)
    parser.add_argument("--rollback-evidence-directory", type=Path, required=True)
    parser.add_argument("--finalized-at", required=True)
    parser.add_argument("--reconciled-at", required=True)
    parser.add_argument("--valuation-date", required=True)
    args = parser.parse_args(argv)
    submission: dict = {}
    plan: dict = {}
    rollback_results: dict[str, dict] = {}
    failure_trigger = "PREFLIGHT_BREAK"

    def rollback_handler(trigger: str) -> dict:
        if trigger in rollback_results:
            return rollback_results[trigger]
        state_root = secure_path(
            args.state_root, allowed_roots=[args.state_root], must_exist=True,
            kind="directory",
        )
        paper_root = secure_path(
            args.paper_root, allowed_roots=[args.paper_root], must_exist=True,
            kind="directory",
        )
        ops_root = secure_path(
            CANONICAL_OPS_ROOT, allowed_roots=[CANONICAL_OPS_ROOT],
            must_exist=True, kind="directory",
        )
        for root in (state_root, paper_root, ops_root):
            require_protected_mode(root, directory=True)
        gate = secure_path(
            args.session_gate_path, allowed_roots=[state_root], must_exist=False,
            kind="file",
        )
        rollback_directory = secure_path(
            args.rollback_evidence_directory, allowed_roots=[state_root],
            must_exist=True, kind="directory",
        )
        paper_paths = [
            secure_path(path, allowed_roots=[paper_root], must_exist=True, kind="file")
            for path in args.paper_path
        ]
        active_config = secure_path(
            CANONICAL_ACTIVE_CONFIG, allowed_roots=[ops_root], must_exist=False,
            kind="file",
        )
        backup_config = secure_path(
            CANONICAL_BACKUP_CONFIG, allowed_roots=[ops_root], must_exist=False,
            kind="file",
        )
        current = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
        if current.returncode not in {0, 1}:
            raise RuntimeError("could not read crontab for named-break rollback")

        def apply_crontab(updated: str) -> None:
            subprocess.run(["crontab", "-"], input=updated, text=True, check=True)

        rollback_at = dt.datetime.now(dt.timezone.utc).isoformat()

        def rearm(observed_trigger: str) -> dict:
            preflight_hash = submission.get("preflight_hash")
            plan_hash = plan.get("content_hash")
            if all(
                isinstance(value, str) and len(value) == 64
                and all(character in "0123456789abcdef" for character in value)
                for value in (preflight_hash, plan_hash)
            ):
                return rearm_generic_live_v1_session(
                    state_path=gate, preflight_hash=preflight_hash,
                    plan_hash=plan_hash, rearmed_at=rollback_at,
                    trigger=observed_trigger,
                )
            return ensure_generic_live_v1_rearmed_after_failure(
                state_path=gate, preflight_hash=preflight_hash,
                plan_hash=plan_hash, rearmed_at=rollback_at,
            )

        result = perform_generic_live_v1_rollback(
            trigger=trigger, rearm_action=rearm,
            current_crontab=current.stdout if current.returncode == 0 else "",
            exact_cron_line=args.exact_cron_line, apply_crontab=apply_crontab,
            active_config_path=active_config, backup_config_path=backup_config,
            paper_paths=paper_paths,
            evidence_path=rollback_directory / f"{trigger.lower()}.json",
            allowed_roots=[state_root, paper_root, ops_root],
            rolled_back_at=rollback_at,
        )
        rollback_results[trigger] = result
        return result

    try:
        _require_canonical_config_paths(args.active_config_path, args.backup_config_path)
        for root in (args.input_root, args.state_root, args.paper_root, CANONICAL_OPS_ROOT):
            require_protected_mode(root, directory=True)
        gate = secure_path(
            args.session_gate_path, allowed_roots=[args.state_root],
            must_exist=False, kind="file",
        )
        for path, kind in (
            (args.base_result_path, "file"),
            (args.closure_result_path, "file"),
            (args.reporting_artifact_directory, "directory"),
        ):
            secure_path(path, allowed_roots=[args.state_root], must_exist=False, kind=kind)
        secure_path(
            args.rollback_evidence_directory, allowed_roots=[args.state_root],
            must_exist=True, kind="directory",
        )
        for path in args.paper_path:
            secure_path(path, allowed_roots=[args.paper_root], must_exist=True, kind="file")

        input_specs = (
            ("submission", args.submission_result, "ORDER_BREAK"),
            ("plan", args.exact_plan, "ORDER_BREAK"),
            ("order_lifecycle", args.order_lifecycle, "ORDER_BREAK"),
            ("broker_orders", args.broker_orders, "RECONCILIATION_BREAK"),
            ("broker_fills", args.broker_fills, "RECONCILIATION_BREAK"),
            ("ending_state", args.ending_state, "RECONCILIATION_BREAK"),
            ("existing_journal", args.existing_journal, "ACCOUNTING_BREAK"),
            ("prior_valuations", args.prior_valuations, "ACCOUNTING_BREAK"),
            ("deployment_policy", args.deployment_policy, "REPORTING_BREAK"),
            ("known_sleeve_ids", args.known_sleeve_ids, "REPORTING_BREAK"),
            ("deployment_state", args.deployment_state, "REPORTING_BREAK"),
            ("capital", args.capital, "REPORTING_BREAK"),
            ("other_lane_audits", args.other_lane_audits, "REPORTING_BREAK"),
        )
        loaded: dict[str, Any] = {}
        for label, path, trigger in input_specs:
            failure_trigger = trigger
            secure_path(path, allowed_roots=[args.input_root], must_exist=True, kind="file")
            loaded[label] = secure_read_json(path, allowed_roots=[args.input_root])
            if label == "submission":
                submission = loaded[label]
            elif label == "plan":
                plan = loaded[label]

        failure_trigger = "RECONCILIATION_BREAK"
        broker_orders = _array(loaded["broker_orders"], key="broker_orders")
        broker_fills = _array(loaded["broker_fills"], key="broker_fills")
        failure_trigger = "ACCOUNTING_BREAK"
        existing_journal = _array(loaded["existing_journal"], key="journal_entries")
        prior_valuations = _array(loaded["prior_valuations"], key="valuations")
        failure_trigger = "REPORTING_BREAK"
        known_sleeve_ids = _strings(loaded["known_sleeve_ids"], key="known_sleeve_ids")
        other_lane_audits = _array(loaded["other_lane_audits"], key="lane_audits")

        built = build_and_finalize_generic_live_v1_production_posttrade(
            submission_result=submission,
            exact_plan=plan,
            order_lifecycle=loaded["order_lifecycle"],
            broker_orders=broker_orders, broker_fills=broker_fills,
            ending_state=loaded["ending_state"],
            existing_journal_entries=existing_journal,
            prior_valuations=prior_valuations,
            deployment_policy=loaded["deployment_policy"],
            known_sleeve_ids=known_sleeve_ids,
            deployment_state=loaded["deployment_state"], capital=loaded["capital"],
            other_lane_audits=other_lane_audits,
            reconciled_at=args.reconciled_at,
            valuation_date=args.valuation_date,
            finalized_at=args.finalized_at,
            reporting_artifact_directory=args.reporting_artifact_directory,
            rearm_state_path=gate,
            base_result_path=args.base_result_path,
            closure_result_path=args.closure_result_path,
            rollback_handler=rollback_handler,
        )
    except Exception:
        if not rollback_results:
            rollback_handler(failure_trigger)
        raise
    print(json.dumps(built, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
