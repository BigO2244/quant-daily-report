from __future__ import annotations

import argparse
import datetime as dt
import json
import math
from pathlib import Path
from typing import Any

from core.precompute_contract import BUNDLE_REQUIRED_FILES


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


def validate_precompute_bundle(
    bundle_dir: Path,
    *,
    trade_date: str | None = None,
    required_files: tuple[str, ...] = BUNDLE_REQUIRED_FILES,
) -> dict[str, Any]:
    bundle_dir = Path(bundle_dir)
    present: list[str] = []
    missing: list[str] = []
    invalid_json: list[dict[str, str]] = []
    non_finite_values: list[dict[str, Any]] = []
    semantic_failures: list[str] = []
    file_summaries: dict[str, dict[str, Any]] = {}

    for name in required_files:
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
    return {
        "status": status,
        "bundle_dir": str(bundle_dir),
        "trade_date": str(trade_date) if trade_date is not None else None,
        "validated_at": _utc_now(),
        "required_files": list(required_files),
        "present_files": present,
        "missing_files": missing,
        "invalid_json": invalid_json,
        "non_finite_values": non_finite_values,
        "semantic_failures": semantic_failures,
        "trade_date_mismatches": trade_date_mismatches,
        "validation_failures": failures,
        "integrity_summary": {
            "required_count": len(required_files),
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
    validation = validate_precompute_bundle(Path(args.bundle_dir), trade_date=args.trade_date)
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
