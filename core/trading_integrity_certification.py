"""Fail-closed 20-session trading-integrity certification.

This module is deliberately read-only.  It converts persisted production
evidence into the six binary controls required by the CIO operating model.  A
missing, malformed, stale, or non-decision-grade input is a failed control; the
certifier never infers a pass from file existence or a successful top-level
workflow pointer alone.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from core.orion_decision_lineage import canonical_hash
from paper.trading_calendar import is_trading_day, prev_trading_day


SCHEMA_VERSION = "caerus.trading_integrity_certification.v1"
CONTROL_NAMES = (
    "data_freshness_pit_validity",
    "compute_recomputed",
    "decision_from_certified_compute",
    "precompute_immutable_hashed",
    "execution_consumed_exact_artifact",
    "broker_reconciliation",
)


def _read(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _content_hash(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            payload, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    ).hexdigest()


def trading_sessions_ending(through_date: str, count: int) -> list[str]:
    if count < 1:
        raise ValueError("count must be positive")
    cursor = str(through_date)
    if not is_trading_day(cursor):
        cursor = prev_trading_day(cursor)
    sessions = [cursor]
    while len(sessions) < count:
        cursor = prev_trading_day(cursor)
        sessions.append(cursor)
    return list(reversed(sessions))


def _control(passed: bool, reasons: Iterable[str], evidence: Iterable[str]) -> dict[str, Any]:
    return {
        "pass": bool(passed),
        "reasons": sorted(set(str(item) for item in reasons if item)),
        "evidence": sorted(set(str(item) for item in evidence if item)),
    }


def _orion_envelope(payload: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    for envelope in (payload or {}).get("envelopes") or []:
        if isinstance(envelope, Mapping) and envelope.get("sleeve_id") == "caerus_orion":
            return envelope
    return None


def _verify_ref(root: Path, ref: Mapping[str, Any] | None) -> tuple[bool, Path | None]:
    if not isinstance(ref, Mapping):
        return False, None
    raw = str(ref.get("path") or ref.get("source_path") or "").strip()
    expected = str(ref.get("sha256") or ref.get("source_sha256") or "").strip()
    if not raw or len(expected) != 64:
        return False, None
    path = Path(raw)
    if not path.is_absolute():
        path = root / path
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False, path
    return path.is_file() and _sha256(path) == expected, path


def _find_submit_run(root: Path, trade_date: str, workflow: Mapping[str, Any] | None) -> Path | None:
    raw = str((workflow or {}).get("run_root") or "").strip()
    if raw:
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = root / candidate
        if candidate.is_dir() and "paper" in candidate.as_posix().lower():
            return candidate
    candidates = sorted(
        (root / "outputs" / "paper_lane" / "runs").glob(
            f"{trade_date}*paper_cron_submit"
        )
    )
    return candidates[-1] if candidates else None


def _same_positions(left: Any, right: Any) -> bool:
    def normalize(rows: Any) -> dict[str, float] | None:
        if not isinstance(rows, list):
            return None
        result: dict[str, float] = {}
        for row in rows:
            if not isinstance(row, Mapping):
                return None
            symbol = str(row.get("symbol") or row.get("ticker") or "").upper()
            quantity = row.get("quantity", row.get("qty", row.get("shares")))
            try:
                result[symbol] = float(quantity)
            except (TypeError, ValueError):
                return None
        return result

    return normalize(left) is not None and normalize(left) == normalize(right)


def certify_session(*, repo_root: Path, trade_date: str) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    bundle = root / "outputs" / "precompute" / trade_date
    workflow_dir = root / "outputs" / "workflow" / trade_date
    contract = _read(bundle / "contract.json")
    package = _read(bundle / "paper_target_package.json")
    signals = _read(bundle / "signals.json")
    evaluations = _read(bundle / "sleeve_evaluations.json")
    manifest = _read(bundle / "audit_manifest.json")
    precompute_guard = _read(workflow_dir / "orion_precompute_dependency.json")
    precompute_validation = _read(workflow_dir / "precompute_bundle_validation.json")
    execution_validation = _read(workflow_dir / "execution_bundle_validation.json")
    workflow_execution = _read(workflow_dir / "execution.json")
    envelope = _orion_envelope(evaluations)
    opportunity = (envelope or {}).get("opportunity")
    opportunity = opportunity if isinstance(opportunity, Mapping) else {}
    lineage = (contract or {}).get("decision_lineage")
    lineage = lineage if isinstance(lineage, Mapping) else None

    data_reasons: list[str] = []
    data_evidence: list[str] = []
    universe = (envelope or {}).get("universe")
    universe = universe if isinstance(universe, Mapping) else {}
    reason_codes = set(str(item) for item in ((envelope or {}).get("reason_codes") or []))
    coverage = (lineage or {}).get("coverage")
    coverage = coverage if isinstance(coverage, Mapping) else {}
    effective_date = str((lineage or {}).get("effective_trade_date") or "")
    expected_effective = prev_trading_day(trade_date)
    if lineage is None:
        data_reasons.append("decision_lineage_missing")
    else:
        data_evidence.append(f"outputs/precompute/{trade_date}/contract.json#decision_lineage")
    if coverage.get("status") != "OK":
        data_reasons.append("market_coverage_not_ok")
    if effective_date != expected_effective or (lineage or {}).get("market_data_asof") != expected_effective:
        data_reasons.append("market_data_not_latest_completed_session")
    if universe.get("source_available") is not True:
        data_reasons.append("universe_source_unavailable")
    if universe.get("method") in {None, "", "legacy_current_universe"}:
        data_reasons.append("pit_universe_not_decision_grade")
    if "NON_DECISION_GRADE_UNIVERSE" in reason_codes:
        data_reasons.append("non_decision_grade_universe_reason_code")
    data_pass = not data_reasons

    compute_reasons: list[str] = []
    compute_evidence: list[str] = []
    prior_binding = (contract or {}).get("prior_decision_lineage")
    prior_ok, prior_path = _verify_ref(root, prior_binding if isinstance(prior_binding, Mapping) else None)
    prior_payload = _read(prior_path) if prior_ok and prior_path is not None else None
    prior_lineage = (prior_payload or {}).get("decision_lineage")
    prior_lineage = prior_lineage if isinstance(prior_lineage, Mapping) else None
    if lineage is None:
        compute_reasons.append("current_compute_lineage_missing")
    if not prior_ok or prior_lineage is None:
        compute_reasons.append("prior_compute_lineage_unproven")
    elif (prior_binding or {}).get("decision_lineage_hash") != canonical_hash(prior_lineage):
        compute_reasons.append("prior_compute_lineage_hash_mismatch")
    else:
        compute_evidence.append(str(prior_path.relative_to(root)))
    recompute_keys = ("market_data_hash", "feature_hash", "full_rank_history_hash", "rank_table_hash")
    if lineage is not None and prior_lineage is not None:
        if not all(str(lineage.get(key) or "") for key in recompute_keys):
            compute_reasons.append("current_compute_stage_hash_missing")
        if not any(lineage.get(key) != prior_lineage.get(key) for key in recompute_keys):
            compute_reasons.append("upstream_compute_hashes_unchanged")
    compute_pass = not compute_reasons

    decision_reasons: list[str] = []
    decision_evidence: list[str] = []
    if not compute_pass:
        decision_reasons.append("compute_not_certified")
    if (contract or {}).get("decision_freshness_status") != "VERIFIED":
        decision_reasons.append("contract_decision_freshness_not_verified")
    if opportunity.get("freshness_status") != "VERIFIED" or opportunity.get("decision_eligible") is not True:
        decision_reasons.append("orion_opportunity_not_verified_and_eligible")
    if lineage is None or any(
        payload is None or payload.get("decision_lineage") != lineage
        for payload in (package, signals)
    ):
        decision_reasons.append("decision_lineage_surface_mismatch")
    if precompute_guard is None:
        decision_reasons.append("morning_precompute_dependency_evidence_missing")
    elif precompute_guard.get("status") != "READY" or precompute_guard.get("decision_status") != "VERIFIED":
        decision_reasons.append("morning_precompute_dependency_not_verified")
    else:
        decision_evidence.append(f"outputs/workflow/{trade_date}/orion_precompute_dependency.json")
    decision_pass = not decision_reasons

    artifact_reasons: list[str] = []
    artifact_evidence: list[str] = []
    if manifest is None or manifest.get("status") != "SEALED":
        artifact_reasons.append("sealed_audit_manifest_missing_or_invalid")
    else:
        for item in manifest.get("artifacts") or []:
            ok, _ = _verify_ref(root, item if isinstance(item, Mapping) else None)
            if not ok:
                artifact_reasons.append(f"manifest_artifact_hash_mismatch:{(item or {}).get('name', 'unknown')}")
        artifact_evidence.append(f"outputs/precompute/{trade_date}/audit_manifest.json")
    # Historical certification uses the validation persisted at decision time.
    # Revalidating an old bundle against today's registry can create false
    # failures after an unrelated governed registry change.
    if precompute_validation is None or precompute_validation.get("status") != "OK":
        artifact_reasons.append("persisted_precompute_validation_not_ok")
    artifact_pass = not artifact_reasons

    execution_reasons: list[str] = []
    execution_evidence: list[str] = []
    if execution_validation is None or execution_validation.get("status") != "OK":
        execution_reasons.append("execution_bundle_validation_not_ok")
    run_root = _find_submit_run(root, trade_date, workflow_execution)
    execution_payload = _read(run_root / "execution_payload.json") if run_root else None
    exact_plan = (execution_payload or {}).get("exact_execution_plan")
    exact_plan = exact_plan if isinstance(exact_plan, Mapping) else None
    if execution_payload is None or exact_plan is None:
        execution_reasons.append("exact_execution_payload_missing")
    else:
        plan_body = dict(exact_plan)
        declared_plan_hash = str(plan_body.pop("content_hash", ""))
        if declared_plan_hash != _content_hash(plan_body):
            execution_reasons.append("exact_execution_plan_hash_invalid")
        if execution_payload.get("exact_execution_plan_hash") != declared_plan_hash:
            execution_reasons.append("execution_payload_plan_hash_mismatch")
        if execution_payload.get("execution_source") != "exact_execution_plan_v3":
            execution_reasons.append("executor_rebuilt_or_used_unapproved_source")
        source_hashes = exact_plan.get("source_artifact_hashes") or {}
        observed_hashes = set(source_hashes.values()) if isinstance(source_hashes, Mapping) else set()
        for name in ("paper_target_package.json", "portfolio_allocation.json", "session_manifest.json", "sleeve_decisions.json"):
            path = bundle / name
            if not path.is_file() or _sha256(path) not in observed_hashes:
                execution_reasons.append(f"exact_plan_missing_bundle_hash:{name}")
        if str((workflow_execution or {}).get("status") or "").lower() not in {"success", "no_action"}:
            execution_reasons.append("execution_not_terminal_green")
        execution_evidence.append(str((run_root / "execution_payload.json").relative_to(root)))
    execution_pass = not execution_reasons

    recon_reasons: list[str] = []
    recon_evidence: list[str] = []
    lane_recon = _read(run_root / "live_pilot_reconciliation.json") if run_root else None
    broker_path = root / "outputs" / "broker" / f"recon_posttrade_{trade_date}.json"
    broker_recon = _read(broker_path)
    if lane_recon is None or lane_recon.get("status") not in {"CLEAN", "NOT_APPLICABLE_NO_TRADE"}:
        recon_reasons.append("paper_lane_reconciliation_not_clean")
    if broker_recon is None or broker_recon.get("verdict") != "PASS":
        recon_reasons.append("broker_reconciliation_not_pass")
    elif (
        broker_recon.get("manual_intervention_required") is not False
        or broker_recon.get("qty_mismatches")
        or broker_recon.get("missing_in_actual")
        or broker_recon.get("missing_in_expected")
        or broker_recon.get("unexpected_short_positions")
    ):
        recon_reasons.append("broker_reconciliation_has_exception")
    if exact_plan is not None and lane_recon is not None:
        if not _same_positions(
            exact_plan.get("expected_posttrade_positions"), lane_recon.get("final_positions")
        ):
            recon_reasons.append("intended_vs_reconciled_positions_mismatch")
        try:
            cash_delta = abs(float(exact_plan.get("expected_posttrade_cash")) - float(lane_recon.get("final_cash")))
        except (TypeError, ValueError):
            recon_reasons.append("intended_vs_reconciled_cash_unavailable")
        else:
            tolerance = float(((exact_plan.get("constraints") or {}).get("cash_reconciliation_tolerance_usd") or 1.0))
            if cash_delta > tolerance:
                recon_reasons.append("intended_vs_reconciled_cash_mismatch")
    if run_root:
        recon_evidence.append(str((run_root / "live_pilot_reconciliation.json").relative_to(root)))
    if broker_path.is_file():
        recon_evidence.append(str(broker_path.relative_to(root)))
    recon_pass = not recon_reasons

    controls = {
        "data_freshness_pit_validity": _control(data_pass, data_reasons, data_evidence),
        "compute_recomputed": _control(compute_pass, compute_reasons, compute_evidence),
        "decision_from_certified_compute": _control(decision_pass, decision_reasons, decision_evidence),
        "precompute_immutable_hashed": _control(artifact_pass, artifact_reasons, artifact_evidence),
        "execution_consumed_exact_artifact": _control(execution_pass, execution_reasons, execution_evidence),
        "broker_reconciliation": _control(recon_pass, recon_reasons, recon_evidence),
    }
    passed_count = sum(1 for item in controls.values() if item["pass"])
    return {
        "trade_date": trade_date,
        "certified": passed_count == len(CONTROL_NAMES),
        "controls_passed": passed_count,
        "controls_expected": len(CONTROL_NAMES),
        "controls": controls,
    }


def certify_window(*, repo_root: Path, through_date: str, sessions: int = 20) -> dict[str, Any]:
    dates = trading_sessions_ending(through_date, sessions)
    rows = [certify_session(repo_root=repo_root, trade_date=date) for date in dates]
    certified = sum(1 for row in rows if row["certified"])
    control_observations_passed = sum(row["controls_passed"] for row in rows)
    exception_counts = {
        name: sum(1 for row in rows if not row["controls"][name]["pass"])
        for name in CONTROL_NAMES
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "through_date": through_date,
        "expected_sessions": sessions,
        "certified_sessions": certified,
        "trading_integrity_rate": certified / sessions,
        "control_observations_passed": control_observations_passed,
        "control_observations_expected": sessions * len(CONTROL_NAMES),
        "status": "GREEN" if certified == sessions else "RED",
        "exception_counts": exception_counts,
        "sessions": rows,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--through-date", required=True)
    parser.add_argument("--sessions", type=int, default=20)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    result = certify_window(
        repo_root=args.repo_root,
        through_date=args.through_date,
        sessions=args.sessions,
    )
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if result["status"] == "GREEN" else 1


if __name__ == "__main__":
    raise SystemExit(main())
