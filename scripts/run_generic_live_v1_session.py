#!/usr/bin/env python3
"""Thin generic Live v1 runner; default is validation-only/no-write."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

from brokers.alpaca_broker import AlpacaBroker
from authority.lane_exact_plan import canonical_json
from core.generic_live_v1_activation import (
    recompute_generic_live_v1_activation_preflight,
    require_generic_live_v1_owner_current_at_execution,
)
from core.generic_live_v1_submission import (
    ensure_generic_live_v1_rearmed_after_failure,
    execute_generic_live_v1_session,
)
from core.generic_live_v1_ops import (
    reject_sensitive_payload,
    require_protected_mode,
    secure_path,
    secure_read_json,
)
from scripts.capture_generic_lyra_v2 import recompute_capture_from_explicit_paths


def _requires_immediate_external_rollback(status: object) -> bool:
    return status == "UNRESOLVED_ORDER_REARMED"


def _require_exact_env(preflight: dict, *, submit: bool) -> None:
    expected = {
        "CAERUS_GENERIC_LIVE_ACCOUNT_ID_HASH": preflight["account_id_hash"],
        "CAERUS_GENERIC_LIVE_CAPITAL_CEILING_USD": "460",
        "CAERUS_GENERIC_LIVE_MINIMUM_TRADE_USD": "100",
        "CAERUS_GENERIC_LIVE_MAX_ORDERS": "1",
        "CAERUS_GENERIC_LIVE_MAXIMUM_GROSS_FRACTION": "0.95",
        "CAERUS_GENERIC_LIVE_EFFECTIVE_SESSION": preflight["effective_session"],
        "CAERUS_GENERIC_LIVE_ADAPTER_CONTRACT": "CAERUS_GENERIC_LANE_V4",
        "CAERUS_GENERIC_LIVE_ELIGIBLE_SLEEVE": "caerus_lyra",
        "CAERUS_GENERIC_LIVE_OWNER_DECISION_HASH": preflight["owner_decision_hash"],
        "CAERUS_GENERIC_LIVE_PREFLIGHT_HASH": preflight["content_hash"],
        "CAERUS_GENERIC_LIVE_POSTTRADE_OBSERVATION_ENABLED": "1" if submit else "0",
        "CAERUS_GENERIC_LIVE_INPUT_ROOT": "/home/brettolson/.caerus/generic_live_v1_inputs",
        "CAERUS_GENERIC_LIVE_STATE_ROOT": "/home/brettolson/.caerus/generic_live_v1_state",
        "CAERUS_GENERIC_LIVE_SESSION_GATE_PATH": "/home/brettolson/.caerus/generic_live_v1_state/session_gate.json",
    }
    mismatches = [key for key, value in expected.items() if os.environ.get(key) != value]
    if mismatches:
        raise RuntimeError("generic Live v1 environment mismatch: " + ",".join(sorted(mismatches)))
    if os.environ.get("CAERUS_GENERIC_PAPER_CUTOVER", "0") != "0":
        raise RuntimeError("generic PAPER cutover must remain disabled")
    if os.environ.get("CAERUS_LEGACY_LIVE_EXECUTOR_ENABLED", "0") != "0":
        raise RuntimeError("legacy Live executor must remain disabled")
    if submit:
        for key in (
            "CAERUS_GENERIC_LIVE_OWNER_APPROVED",
            "CAERUS_GENERIC_LIVE_SUBMIT_APPROVED",
            "CAERUS_GENERIC_LIVE_SCHEDULE_ENABLED",
        ):
            if os.environ.get(key) != "1":
                raise RuntimeError(f"generic Live submission gate is not approved: {key}")
        if os.environ.get("ALPACA_PAPER") != "0" or os.environ.get("ALPACA_BASE_URL", "").rstrip("/") != "https://api.alpaca.markets":
            raise RuntimeError("generic Live submission requires canonical Alpaca Live environment")
        expected_repo = Path(os.environ.get("CAERUS_GENERIC_LIVE_REPO_ROOT", ""))
        expected_python = Path(os.environ.get("CAERUS_GENERIC_LIVE_PYTHON_BIN", ""))
        if (
            not expected_repo.is_absolute()
            or not expected_python.is_absolute()
            or expected_repo.resolve() != Path(__file__).resolve().parents[1]
            or expected_python.resolve() != Path(sys.executable).resolve()
            or Path.cwd().resolve() != expected_repo.resolve()
        ):
            raise RuntimeError("generic Live exact runtime path pins mismatch")
        expected_deployed = os.environ.get("CAERUS_GENERIC_LIVE_DEPLOYED_SHA", "")
        observed_deployed = subprocess.run(
            ["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True
        ).stdout.strip()
        if expected_deployed != observed_deployed or preflight["deployed_sha"] != observed_deployed:
            raise RuntimeError("generic Live deployed SHA pin mismatch")


def _require_source_pins(
    *, owner_decision: dict, account_observation: dict,
    lyra_decision: dict, lyra_capture_result: dict,
    lyra_raw_source_recompute: dict,
    operational_proofs: dict, plan: dict,
) -> None:
    operational_hash = hashlib.sha256(
        canonical_json(operational_proofs).encode("utf-8")
    ).hexdigest()
    expected = {
        "CAERUS_GENERIC_LIVE_OWNER_DECISION_HASH": owner_decision.get("content_hash"),
        "CAERUS_GENERIC_LIVE_ACCOUNT_OBSERVATION_HASH": account_observation.get("content_hash"),
        "CAERUS_GENERIC_LIVE_LYRA_DECISION_HASH": lyra_decision.get("content_hash"),
        "CAERUS_GENERIC_LIVE_LYRA_CAPTURE_HASH": lyra_capture_result.get("content_hash"),
        "CAERUS_GENERIC_LIVE_LYRA_RAW_SOURCE_RECOMPUTE_HASH": (
            lyra_raw_source_recompute.get("content_hash")
        ),
        "CAERUS_GENERIC_LIVE_OPERATIONAL_PROOFS_HASH": operational_hash,
        "CAERUS_GENERIC_LIVE_PLAN_HASH": plan.get("content_hash"),
    }
    mismatches = [key for key, value in expected.items() if os.environ.get(key) != value]
    if mismatches:
        raise RuntimeError(
            "generic Live exact protected source pins mismatch: "
            + ",".join(sorted(mismatches))
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--preflight", type=Path, required=True)
    parser.add_argument("--owner-decision", type=Path)
    parser.add_argument("--account-observation", type=Path)
    parser.add_argument("--lyra-decision", type=Path)
    parser.add_argument("--lyra-capture-result", type=Path)
    parser.add_argument("--lyra-source-session-manifest", type=Path)
    parser.add_argument("--lyra-evaluation-batch", type=Path)
    parser.add_argument("--lyra-legacy-decision-batch", type=Path)
    parser.add_argument("--lyra-source", type=Path)
    parser.add_argument("--lyra-prior-source", type=Path)
    parser.add_argument("--lyra-universe-freeze", type=Path)
    parser.add_argument("--lyra-universe", type=Path)
    parser.add_argument("--lyra-price-panel", type=Path)
    parser.add_argument("--lyra-risk-policy", type=Path)
    parser.add_argument("--lyra-risk-policy-proposal", type=Path)
    parser.add_argument("--lyra-risk-policy-owner-decision", type=Path)
    parser.add_argument("--operational-proofs", type=Path)
    parser.add_argument("--exact-plan", type=Path, required=True)
    parser.add_argument("--executed-at", required=True)
    parser.add_argument("--wal-directory", type=Path)
    parser.add_argument("--session-gate-path", type=Path)
    parser.add_argument("--result-path", type=Path)
    parser.add_argument("--submit-exact-session", action="store_true")
    args = parser.parse_args()
    preflight: dict = {}
    plan: dict = {}
    owner_decision: dict = {}
    account_observation: dict = {}
    lyra_decision: dict = {}
    lyra_capture_result: dict = {}
    operational_proofs: dict = {}
    lyra_raw_source_recompute: dict = {}
    safe_rearm_path: Path | None = None
    try:
        input_root = Path(os.environ.get("CAERUS_GENERIC_LIVE_INPUT_ROOT", ""))
        state_root = Path(os.environ.get("CAERUS_GENERIC_LIVE_STATE_ROOT", ""))
        if args.submit_exact_session and (not input_root.is_absolute() or not state_root.is_absolute()):
            raise RuntimeError("generic Live input/state roots must be absolute runtime pins")
        if args.submit_exact_session:
            secure_path(input_root, allowed_roots=[input_root], must_exist=True, kind="directory")
            secure_path(state_root, allowed_roots=[state_root], must_exist=True, kind="directory")
            require_protected_mode(input_root, directory=True)
            require_protected_mode(state_root, directory=True)
            if args.wal_directory is None or args.session_gate_path is None or args.result_path is None:
                raise RuntimeError("generic Live submission paths are required")
            secure_path(args.wal_directory, allowed_roots=[state_root], must_exist=False, kind="directory")
            safe_rearm_path = secure_path(
                args.session_gate_path, allowed_roots=[state_root], must_exist=False, kind="file"
            )
            secure_path(args.result_path, allowed_roots=[state_root], must_exist=False, kind="file")
        read_roots = [input_root] if args.submit_exact_session else [args.preflight.parent, args.exact_plan.parent]
        source_paths = {
            "owner decision": args.owner_decision,
            "account observation": args.account_observation,
            "Lyra decision": args.lyra_decision,
            "Lyra capture result": args.lyra_capture_result,
            "operational proofs": args.operational_proofs,
            "Lyra source session manifest": args.lyra_source_session_manifest,
            "Lyra evaluation batch": args.lyra_evaluation_batch,
            "Lyra legacy decision batch": args.lyra_legacy_decision_batch,
            "Lyra source": args.lyra_source,
            "Lyra prior source": args.lyra_prior_source,
            "Lyra universe freeze": args.lyra_universe_freeze,
            "Lyra universe bytes": args.lyra_universe,
            "Lyra price panel": args.lyra_price_panel,
            "Lyra risk policy": args.lyra_risk_policy,
            "Lyra risk policy proposal": args.lyra_risk_policy_proposal,
            "Lyra risk policy owner decision": (
                args.lyra_risk_policy_owner_decision
            ),
        }
        missing_sources = sorted(label for label, path in source_paths.items() if path is None)
        if missing_sources:
            raise RuntimeError(
                "generic Live exact protected source paths are required: "
                + ",".join(missing_sources)
            )
        preflight = secure_read_json(args.preflight, allowed_roots=read_roots)
        plan = secure_read_json(args.exact_plan, allowed_roots=read_roots)
        owner_decision = secure_read_json(args.owner_decision, allowed_roots=read_roots)
        account_observation = secure_read_json(args.account_observation, allowed_roots=read_roots)
        lyra_decision = secure_read_json(args.lyra_decision, allowed_roots=read_roots)
        lyra_capture_result = secure_read_json(
            args.lyra_capture_result, allowed_roots=read_roots
        )
        operational_proofs = secure_read_json(args.operational_proofs, allowed_roots=read_roots)
        raw_paths = [
            args.lyra_source_session_manifest, args.lyra_evaluation_batch,
            args.lyra_legacy_decision_batch, args.lyra_source,
            args.lyra_prior_source, args.lyra_universe_freeze,
            args.lyra_universe, args.lyra_price_panel, args.lyra_risk_policy,
            args.lyra_risk_policy_proposal,
            args.lyra_risk_policy_owner_decision,
        ]
        for raw_path in raw_paths:
            secure_path(
                raw_path, allowed_roots=read_roots, must_exist=True, kind="file"
            )
            if args.submit_exact_session:
                require_protected_mode(raw_path, directory=False)
        lyra_raw_source_recompute = recompute_capture_from_explicit_paths(
            expected_capture=lyra_capture_result,
            execution_session=lyra_capture_result["execution_session"],
            signal_as_of=lyra_capture_result["signal_as_of"],
            session_as_of=lyra_capture_result["session_snapshot"]["as_of"],
            captured_at=lyra_capture_result["captured_at"],
            source_session_manifest_path=args.lyra_source_session_manifest,
            evaluation_batch_path=args.lyra_evaluation_batch,
            legacy_decision_batch_path=args.lyra_legacy_decision_batch,
            lyra_source_path=args.lyra_source,
            prior_lyra_source_path=args.lyra_prior_source,
            universe_freeze_path=args.lyra_universe_freeze,
            universe_path=args.lyra_universe,
            forecast_risk_policy_path=args.lyra_risk_policy,
            forecast_risk_policy_proposal_path=args.lyra_risk_policy_proposal,
            forecast_risk_policy_owner_decision_path=(
                args.lyra_risk_policy_owner_decision
            ),
            price_panel_path=args.lyra_price_panel,
        )
        require_generic_live_v1_owner_current_at_execution(
            owner_decision=owner_decision, executed_at=args.executed_at,
        )
        _require_source_pins(
            owner_decision=owner_decision,
            account_observation=account_observation,
            lyra_decision=lyra_decision,
            lyra_capture_result=lyra_capture_result,
            lyra_raw_source_recompute=lyra_raw_source_recompute,
            operational_proofs=operational_proofs,
            plan=plan,
        )
        preflight = recompute_generic_live_v1_activation_preflight(
            expected_preflight=preflight,
            owner_decision=owner_decision,
            live_account_observation=account_observation,
            operational_proofs=operational_proofs,
            lyra_decision=lyra_decision,
            lyra_capture_result=lyra_capture_result,
            lyra_raw_source_recompute=lyra_raw_source_recompute,
            exact_plan=plan,
        )
        _require_exact_env(preflight, submit=args.submit_exact_session)
        broker = AlpacaBroker.from_env() if args.submit_exact_session else None
        result = execute_generic_live_v1_session(
            activation_preflight=preflight, exact_plan=plan,
            lyra_decision=lyra_decision,
            lyra_capture_result=lyra_capture_result,
            lyra_raw_source_recompute=lyra_raw_source_recompute,
            executed_at=args.executed_at, submit_enabled=args.submit_exact_session,
            broker=broker, wal_directory=args.wal_directory,
            rearm_state_path=args.session_gate_path,
            result_path=args.result_path,
        )
        if args.submit_exact_session and _requires_immediate_external_rollback(
            result.get("status")
        ):
            raise RuntimeError(
                "generic Live v1 unresolved order requires immediate external rollback"
            )
    except Exception:
        if args.submit_exact_session and safe_rearm_path is not None:
            ensure_generic_live_v1_rearmed_after_failure(
                state_path=safe_rearm_path,
                preflight_hash=preflight.get("content_hash"),
                plan_hash=plan.get("content_hash"),
                rearmed_at=dt.datetime.now(dt.timezone.utc).isoformat(),
            )
        raise
    reject_sensitive_payload(result)
    print(json.dumps(result, sort_keys=True))
    return 0


def safe_entrypoint() -> int:
    try:
        return main()
    except SystemExit as exc:
        return int(exc.code or 0)
    except BaseException:
        # Never emit exception text: broker/provider failures may echo secrets.
        print("generic Live v1 failed closed; rollback guard required", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(safe_entrypoint())
