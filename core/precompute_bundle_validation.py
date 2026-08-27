from __future__ import annotations

import argparse
from collections import Counter
import datetime as dt
import hashlib
import json
import math
from pathlib import Path
from typing import Any

from core.precompute_contract import BUNDLE_REQUIRED_FILES
from core.sleeve_control_plane import (
    BATCH_SCHEMA_VERSION,
    ENVELOPE_SCHEMA_VERSION,
    TERMINAL_STATUSES,
    load_sleeve_control_registry,
)


DEFAULT_SUPPRESSED_SIDE_EFFECTS = (
    "email",
    "shadow",
    "shadow_latest",
    "shadow_reconciliation",
)


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _read_json(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except Exception as exc:
        return None, str(exc)


def _non_finite_paths(value: Any, *, path: str = "$") -> list[str]:
    failures: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            failures.extend(_non_finite_paths(item, path=f"{path}.{key}"))
    elif isinstance(value, list):
        for idx, item in enumerate(value):
            failures.extend(_non_finite_paths(item, path=f"{path}[{idx}]"))
    elif isinstance(value, float) and not math.isfinite(value):
        failures.append(path)
    return failures


def _positive_finite(value: Any) -> bool:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(numeric) and numeric > 0.0


def _planned_trade_failures(payload: Any) -> list[str]:
    if not isinstance(payload, dict):
        return ["planned_execution_payload:not_object"]
    trades = payload.get("trades")
    if not isinstance(trades, list):
        return ["planned_execution_payload:trades_not_list"]
    failures: list[str] = []
    for idx, trade in enumerate(trades):
        if not isinstance(trade, dict):
            failures.append(f"planned_execution_payload:trade[{idx}]:not_object")
            continue
        ticker = str(trade.get("ticker") or trade.get("symbol") or "").strip().upper()
        side = str(trade.get("side") or trade.get("action") or "").strip().upper()
        quantity = trade.get("shares", trade.get("quantity", trade.get("qty")))
        price = trade.get("price", trade.get("entry_price"))
        notional = trade.get("notional")
        if not ticker:
            failures.append(f"planned_execution_payload:trade[{idx}]:missing_ticker")
        if side not in {"BUY", "SELL", "CLOSE", "REDUCE"}:
            failures.append(f"planned_execution_payload:trade[{idx}]:invalid_side")
        if not _positive_finite(quantity):
            failures.append(f"planned_execution_payload:trade[{idx}]:invalid_quantity")
        if not _positive_finite(price):
            failures.append(f"planned_execution_payload:trade[{idx}]:missing_finite_price")
        if not _positive_finite(notional):
            failures.append(f"planned_execution_payload:trade[{idx}]:missing_finite_notional")
    return failures


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sleeve_evaluation_failures(
    payload: Any,
    *,
    trade_date: str | None,
) -> list[str]:
    """Validate complete control-plane coverage without grading research alpha.

    A terminal ``BLOCKED`` or ``FAILED`` envelope is valid evidence: the
    dispatcher made the sleeve and its reason visible.  Bundle integrity is
    concerned with registry coverage, authority, provenance, and terminal
    shape—not whether an evaluation-only research sleeve found an opportunity.
    """
    prefix = "sleeve_evaluations"
    if not isinstance(payload, dict):
        return [f"{prefix}:not_object"]

    failures: list[str] = []
    if payload.get("schema_version") != BATCH_SCHEMA_VERSION:
        failures.append(f"{prefix}:invalid_schema_version")
    if payload.get("all_non_frozen_evaluated") is not True:
        failures.append(f"{prefix}:coverage_not_complete")
    if trade_date is not None and str(payload.get("trade_date") or "") != str(
        trade_date
    ):
        failures.append(f"{prefix}:trade_date_mismatch")

    try:
        control = load_sleeve_control_registry()
    except Exception as exc:
        failures.append(
            f"{prefix}:registry_integrity_error:{type(exc).__name__}"
        )
        return failures

    expected_definitions = control.evaluated_definitions()
    expected_ids = [item.sleeve_id for item in expected_definitions]
    expected_by_id = {item.sleeve_id: item for item in expected_definitions}
    declared_ids = payload.get("expected_non_frozen_sleeve_ids")
    if declared_ids != expected_ids:
        failures.append(f"{prefix}:expected_sleeve_ids_mismatch")

    envelopes = payload.get("envelopes")
    if not isinstance(envelopes, list):
        failures.append(f"{prefix}:envelopes_not_list")
        return failures

    actual_ids: list[str] = []
    status_counts: Counter[str] = Counter()
    for idx, envelope in enumerate(envelopes):
        item_prefix = f"{prefix}:envelope[{idx}]"
        if not isinstance(envelope, dict):
            failures.append(f"{item_prefix}:not_object")
            continue
        sleeve_id = str(envelope.get("sleeve_id") or "")
        actual_ids.append(sleeve_id)
        definition = expected_by_id.get(sleeve_id)
        if definition is None:
            failures.append(f"{item_prefix}:unregistered_sleeve")
            continue
        if envelope.get("schema_version") != ENVELOPE_SCHEMA_VERSION:
            failures.append(f"{item_prefix}:invalid_schema_version")
        if trade_date is not None and str(envelope.get("trade_date") or "") != str(
            trade_date
        ):
            failures.append(f"{item_prefix}:trade_date_mismatch")
        evaluation = envelope.get("evaluation")
        status = ""
        if not isinstance(evaluation, dict):
            failures.append(f"{item_prefix}:evaluation_not_object")
        else:
            status = str(evaluation.get("status") or "")
            if status not in TERMINAL_STATUSES:
                failures.append(f"{item_prefix}:non_terminal_status")
            else:
                status_counts[status] += 1
        lifecycle = envelope.get("lifecycle")
        if not isinstance(lifecycle, dict):
            failures.append(f"{item_prefix}:lifecycle_not_object")
        elif (
            lifecycle.get("status") != definition.lifecycle_status
            or lifecycle.get("frozen") is not False
        ):
            failures.append(f"{item_prefix}:lifecycle_mismatch")
        eligibility = envelope.get("eligibility")
        if not isinstance(eligibility, dict):
            failures.append(f"{item_prefix}:eligibility_not_object")
        else:
            expected_eligibility = {
                "evaluation_eligible": True,
                "evaluation_only": definition.evaluation_only,
                "capital_eligible": definition.capital_eligible,
                "paper_execution_eligible": definition.execution_eligible,
                "live_execution_eligible": False,
                "execution_impact": definition.execution_impact,
            }
            if any(
                eligibility.get(key) != value
                for key, value in expected_eligibility.items()
            ):
                failures.append(f"{item_prefix}:eligibility_mismatch")
            if (
                not definition.capital_eligible
                and eligibility.get("evaluation_usable_for_capital") is not False
            ):
                failures.append(f"{item_prefix}:unauthorized_capital_use")
            if definition.capital_eligible:
                opportunity = envelope.get("opportunity")
                reason_codes = envelope.get("reason_codes") or []
                source_artifacts = (
                    (envelope.get("provenance") or {}).get("source_artifacts")
                    if isinstance(envelope.get("provenance"), dict)
                    else None
                )
                if status != "OK":
                    failures.append(f"{item_prefix}:capital_authority_not_ok")
                if eligibility.get("evaluation_usable_for_capital") is not True:
                    failures.append(f"{item_prefix}:capital_authority_not_usable")
                if (
                    not isinstance(opportunity, dict)
                    or opportunity.get("decision_eligible") is not True
                ):
                    failures.append(f"{item_prefix}:capital_authority_not_decision_eligible")
                if not isinstance(source_artifacts, list) or not source_artifacts:
                    failures.append(f"{item_prefix}:capital_authority_source_missing")
                if "STALE_DECISION_SUSPECTED" in reason_codes or (
                    isinstance(opportunity, dict)
                    and opportunity.get("decision_status")
                    == "STALE_DECISION_SUSPECTED"
                ):
                    failures.append(f"{item_prefix}:stale_decision_suspected")

    if actual_ids != expected_ids:
        failures.append(f"{prefix}:envelope_coverage_mismatch")
    if len(actual_ids) != len(set(actual_ids)):
        failures.append(f"{prefix}:duplicate_envelope")

    summary = payload.get("summary")
    if not isinstance(summary, dict):
        failures.append(f"{prefix}:summary_not_object")
    else:
        if summary.get("expected_count") != len(expected_ids):
            failures.append(f"{prefix}:summary_expected_count_mismatch")
        if summary.get("envelope_count") != len(envelopes):
            failures.append(f"{prefix}:summary_envelope_count_mismatch")
        expected_capital = [
            item.sleeve_id for item in expected_definitions if item.capital_eligible
        ]
        if summary.get("capital_eligible_sleeve_ids") != expected_capital:
            failures.append(f"{prefix}:capital_authority_mismatch")
        if summary.get("execution_eligible_sleeve_ids") != expected_capital:
            failures.append(f"{prefix}:execution_authority_mismatch")
        declared_counts = summary.get("terminal_status_counts")
        expected_counts = {
            status: int(status_counts.get(status, 0))
            for status in TERMINAL_STATUSES
        }
        if declared_counts != expected_counts:
            failures.append(f"{prefix}:terminal_status_counts_mismatch")

    if payload.get("paper_capital_authority") != control.paper_capital_authority:
        failures.append(f"{prefix}:paper_capital_authority_mismatch")
    expected_frozen = [
        {"sleeve_id": item.sleeve_id, "reason": item.frozen_reason}
        for item in control.frozen_definitions()
    ]
    if payload.get("frozen_sleeves") != expected_frozen:
        failures.append(f"{prefix}:frozen_sleeves_mismatch")
    expected_retired = [
        item.sleeve_id
        for item in control.definitions
        if item.lifecycle_status == "retired"
    ]
    if payload.get("retired_sleeve_ids") != expected_retired:
        failures.append(f"{prefix}:retired_sleeves_mismatch")

    registry = payload.get("registry")
    if not isinstance(registry, dict):
        failures.append(f"{prefix}:registry_provenance_not_object")
    else:
        expected_registry_sha = _sha256(control.registry_path)
        expected_manifest_sha = _sha256(control.manifest_path)
        if registry.get("sha256") != expected_registry_sha:
            failures.append(f"{prefix}:registry_hash_mismatch")
        if registry.get("manifest_sha256") != expected_manifest_sha:
            failures.append(f"{prefix}:manifest_hash_mismatch")
        for idx, envelope in enumerate(envelopes):
            if not isinstance(envelope, dict):
                continue
            provenance = envelope.get("provenance")
            if not isinstance(provenance, dict):
                failures.append(
                    f"{prefix}:envelope[{idx}]:provenance_not_object"
                )
                continue
            if (
                provenance.get("registry_sha256") != expected_registry_sha
                or provenance.get("manifest_sha256") != expected_manifest_sha
            ):
                failures.append(f"{prefix}:envelope[{idx}]:provenance_hash_mismatch")

    return failures


def validate_sleeve_evaluation_payload(
    payload: Any, *, trade_date: str
) -> list[str]:
    """Public semantic validator used by both bundle and exact authority."""
    return _sleeve_evaluation_failures(payload, trade_date=trade_date)


def validate_precompute_bundle(
    bundle_dir: Path,
    *,
    trade_date: str | None = None,
    required_files: tuple[str, ...] = BUNDLE_REQUIRED_FILES,
    require_sealed_paper_target: bool = False,
) -> dict[str, Any]:
    bundle_dir = Path(bundle_dir)
    contract_payload, _contract_error = _read_json(bundle_dir / "contract.json")
    sealed_contract = bool(
        isinstance(contract_payload, dict)
        and int(contract_payload.get("schema_version") or 0) in {2, 3}
    )
    effective_required_files = tuple(required_files)
    if sealed_contract:
        declared_files = contract_payload.get("files") or {}
        declared_names = tuple(
            str(name) for name in declared_files.values() if str(name).strip()
        )
        if declared_names:
            effective_required_files = tuple(dict.fromkeys(declared_names))
        elif "paper_target_package.json" not in effective_required_files:
            effective_required_files = (*effective_required_files, "paper_target_package.json")
    present: list[str] = []
    missing: list[str] = []
    invalid_json: list[dict[str, str]] = []
    non_finite_values: list[dict[str, Any]] = []
    semantic_failures: list[str] = []
    file_summaries: dict[str, dict[str, Any]] = {}

    for name in effective_required_files:
        path = bundle_dir / name
        if not path.is_file():
            missing.append(name)
            file_summaries[name] = {"exists": False, "valid_json": False}
            continue
        present.append(name)
        payload, error = _read_json(path)
        file_summaries[name] = {
            "exists": True,
            "valid_json": payload is not None,
            "size_bytes": path.stat().st_size,
        }
        if error is not None:
            invalid_json.append({"file": name, "error": error})
            continue
        non_finite = _non_finite_paths(payload)
        if non_finite:
            non_finite_values.append({"file": name, "paths": non_finite})
        if name == "planned_execution_payload.json":
            semantic_failures.extend(_planned_trade_failures(payload))
        if name == "sleeve_evaluations.json":
            semantic_failures.extend(
                _sleeve_evaluation_failures(payload, trade_date=trade_date)
            )

    if require_sealed_paper_target and not sealed_contract:
        semantic_failures.append("paper_target:unsealed_precompute_contract")
    if sealed_contract:
        from core.paper_target_authority import validate_sealed_paper_target_bundle

        semantic_failures.extend(
            validate_sealed_paper_target_bundle(
                bundle_dir=bundle_dir,
                trade_date=str(trade_date or contract_payload.get("trade_date") or ""),
                repo_root=bundle_dir.resolve().parents[2],
            )
        )

    trade_date_mismatches: list[dict[str, str]] = []
    if trade_date:
        for name in present:
            payload, _ = _read_json(bundle_dir / name)
            if not isinstance(payload, dict) or "trade_date" not in payload:
                continue
            if str(payload.get("trade_date")) != str(trade_date):
                trade_date_mismatches.append(
                    {
                        "file": name,
                        "expected": str(trade_date),
                        "actual": str(payload.get("trade_date")),
                    }
                )

    failures: list[str] = []
    failures.extend(f"missing:{name}" for name in missing)
    failures.extend(f"invalid_json:{item['file']}" for item in invalid_json)
    failures.extend(f"non_finite_json:{item['file']}:{path}" for item in non_finite_values for path in item["paths"])
    failures.extend(semantic_failures)
    failures.extend(f"trade_date_mismatch:{item['file']}" for item in trade_date_mismatches)

    status = "OK" if not failures else "FAILED"
    stale_decision_suspected = any(
        "stale_decision" in failure.lower() or "orion_lineage" in failure.lower()
        for failure in failures
    )
    return {
        "status": status,
        "decision_freshness_status": (
            "STALE_DECISION_SUSPECTED"
            if stale_decision_suspected
            else "VERIFIED"
            if status == "OK" and sealed_contract
            else "BLOCKED"
        ),
        "bundle_dir": str(bundle_dir),
        "trade_date": str(trade_date) if trade_date is not None else None,
        "validated_at": _utc_now(),
        "required_files": list(effective_required_files),
        "present_files": present,
        "missing_files": missing,
        "invalid_json": invalid_json,
        "non_finite_values": non_finite_values,
        "semantic_failures": semantic_failures,
        "trade_date_mismatches": trade_date_mismatches,
        "validation_failures": failures,
        "integrity_summary": {
            "required_count": len(effective_required_files),
            "present_count": len(present),
            "missing_count": len(missing),
            "invalid_json_count": len(invalid_json),
            "non_finite_value_count": sum(len(item["paths"]) for item in non_finite_values),
            "semantic_failure_count": len(semantic_failures),
            "trade_date_mismatch_count": len(trade_date_mismatches),
        },
        "files": file_summaries,
    }


def _load_previous_attempt_count(path: Path | None, trade_date: str | None) -> int:
    if path is None or not path.is_file():
        return 0
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return 0
    if trade_date is not None and str(payload.get("trade_date") or "") != str(trade_date):
        return 0
    try:
        return int(payload.get("recovery_attempt_count") or 0)
    except (TypeError, ValueError):
        return 0


def build_execution_self_heal_status(
    *,
    validation: dict[str, Any],
    recovery_attempted: bool,
    recovery_result: str,
    execution_continued: bool,
    previous_status_path: Path | None = None,
    recovery_started_at: str | None = None,
    recovery_finished_at: str | None = None,
    suppressed_side_effects: tuple[str, ...] = DEFAULT_SUPPRESSED_SIDE_EFFECTS,
) -> dict[str, Any]:
    trade_date = validation.get("trade_date")
    previous_count = _load_previous_attempt_count(previous_status_path, str(trade_date) if trade_date else None)
    attempt_count = previous_count + 1 if recovery_attempted else previous_count
    return {
        "trade_date": trade_date,
        "created_at": _utc_now(),
        "recovery_attempted": recovery_attempted,
        "recovery_attempt_count": attempt_count,
        "recovery_result": recovery_result,
        "bundle_validation_result": validation.get("status"),
        "execution_continued": execution_continued,
        "suppressed_side_effects": list(suppressed_side_effects),
        "validation_failures": validation.get("validation_failures") or [],
        "timestamps": {
            "recovery_started_at": recovery_started_at,
            "recovery_finished_at": recovery_finished_at,
            "bundle_validated_at": validation.get("validated_at"),
        },
        "stale_degraded_visibility": {
            "shadow_generation_suppressed": recovery_attempted,
            "shadow_latest_not_refreshed": recovery_attempted,
            "potentially_stale_latest_shadow_artifacts": recovery_attempted,
        },
        "bundle_validation": validation,
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate Alpaca precompute bundle integrity.")
    parser.add_argument("--bundle-dir", required=True)
    parser.add_argument("--trade-date", required=True)
    parser.add_argument("--require-sealed-paper-target", action="store_true")
    parser.add_argument("--json-output")
    parser.add_argument("--recovery-status-output")
    parser.add_argument("--previous-recovery-status")
    parser.add_argument("--recovery-attempted", action="store_true")
    parser.add_argument("--recovery-result", default="not_attempted")
    parser.add_argument("--execution-continued", choices=("true", "false"), default="false")
    parser.add_argument("--recovery-started-at")
    parser.add_argument("--recovery-finished-at")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    validation = validate_precompute_bundle(
        Path(args.bundle_dir),
        trade_date=args.trade_date,
        require_sealed_paper_target=bool(args.require_sealed_paper_target),
    )
    if args.json_output:
        _write_json(Path(args.json_output), validation)
    else:
        print(json.dumps(validation, indent=2, sort_keys=True))

    if args.recovery_status_output:
        previous = Path(args.previous_recovery_status) if args.previous_recovery_status else Path(args.recovery_status_output)
        status = build_execution_self_heal_status(
            validation=validation,
            recovery_attempted=bool(args.recovery_attempted),
            recovery_result=str(args.recovery_result),
            execution_continued=args.execution_continued == "true",
            previous_status_path=previous,
            recovery_started_at=args.recovery_started_at,
            recovery_finished_at=args.recovery_finished_at,
        )
        _write_json(Path(args.recovery_status_output), status)

    return 0 if validation["status"] == "OK" else 1


if __name__ == "__main__":
    raise SystemExit(main())
