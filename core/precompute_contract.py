from __future__ import annotations

import datetime as dt
import json
import math
import numbers
from pathlib import Path
from typing import Any

from core.strategy_identity import strategy_identity_metadata
from core.target_book_metrics import build_target_book_metrics, latest_prior_signals
from core.trading_mode import canonical_trading_mode_label
from paper.run_manager import safe_write_text

PRECOMPUTE_ROOT = Path("outputs/precompute")
PRECOMPUTE_SCHEMA_VERSION = 1
PRECOMPUTE_ARTIFACT_TYPE = "alpaca_precompute_bundle"
PRECOMPUTE_STATUS_COMPLETE = "complete"
PRECOMPUTE_STATUS_INCOMPLETE = "incomplete"
PRECOMPUTE_STATUS_INVALID = "invalid"

REASON_PRECOMPUTE_MISSING = "precompute_missing"
REASON_PRECOMPUTE_WRONG_TRADE_DATE = "precompute_wrong_trade_date"
REASON_PRECOMPUTE_INCOMPLETE = "precompute_incomplete"
REASON_PRECOMPUTE_INVALID = "precompute_invalid"
REASON_PRECOMPUTE_VALIDATION_FAILED = "precompute_validation_failed"


def _json_safe(value: Any) -> Any:
    """Return strict-JSON-safe data without changing collection shape.

    Python's json encoder otherwise emits ``NaN``/``Infinity`` tokens by
    default. Those tokens are not valid JSON and, more importantly, can evade
    ordinary positive-price checks because comparisons with NaN are false.
    Missing numeric evidence is represented explicitly as ``null``.
    """
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, numbers.Integral):
        return int(value)
    if isinstance(value, numbers.Real):
        numeric = float(value)
        return numeric if math.isfinite(numeric) else None
    return value


def _strict_json(payload: dict[str, Any]) -> str:
    return json.dumps(
        _json_safe(payload),
        indent=2,
        default=str,
        allow_nan=False,
    ) + "\n"


def _execution_payload_validation_failures(
    execution_payload: dict[str, Any],
) -> list[str]:
    failures: list[str] = []
    trades = execution_payload.get("trades")
    if not isinstance(trades, list):
        return ["planned_execution_payload:trades_not_list"]
    for idx, trade in enumerate(trades):
        if not isinstance(trade, dict):
            failures.append(f"planned_execution_payload:trade[{idx}]:not_object")
            continue
        ticker = str(trade.get("ticker") or trade.get("symbol") or "").strip()
        side = str(trade.get("side") or trade.get("action") or "").strip().upper()
        if not ticker:
            failures.append(
                f"planned_execution_payload:trade[{idx}]:missing_ticker"
            )
        if side not in {"BUY", "SELL", "CLOSE", "REDUCE"}:
            failures.append(
                f"planned_execution_payload:trade[{idx}]:invalid_side"
            )
        numeric_fields = {
            "quantity": trade.get(
                "shares",
                trade.get("quantity", trade.get("qty")),
            ),
            "price": trade.get("price", trade.get("entry_price")),
            "notional": trade.get("notional"),
        }
        for key, value in numeric_fields.items():
            try:
                numeric = float(value)
            except (TypeError, ValueError):
                numeric = float("nan")
            if not math.isfinite(numeric) or numeric <= 0.0:
                failures.append(
                    f"planned_execution_payload:trade[{idx}]:invalid_{key}"
                )
    return failures


def precompute_bundle_dir(trade_date: str) -> Path:
    return PRECOMPUTE_ROOT / str(trade_date)


def precompute_contract_path(trade_date: str) -> Path:
    return precompute_bundle_dir(trade_date) / "contract.json"


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return None


def build_precompute_contract(
    *,
    trade_date: str,
    run_id: str,
    mode: str,
    daily_snapshot: dict[str, Any],
    execution_payload: dict[str, Any],
    signals_filename: str = "signals.json",
    snapshot_filename: str = "daily_snapshot.json",
    payload_filename: str = "planned_execution_payload.json",
    sleeve_evaluations_filename: str = "sleeve_evaluations.json",
) -> dict[str, Any]:
    validation_failures = _execution_payload_validation_failures(
        execution_payload or {}
    )
    executable_count = int(
        (execution_payload or {}).get("execution_eligible_trades_count")
        or (execution_payload or {}).get("executable_trades_count")
        or len((execution_payload or {}).get("trades") or [])
        or 0
    )
    planner_intended = int(
        (execution_payload or {}).get("planner_intended_trades_count")
        or (execution_payload or {}).get("proposed_trades_intent_count")
        or (execution_payload or {}).get("proposed_trades_intent")
        or len((daily_snapshot or {}).get("proposed_trades") or [])
        or 0
    )
    return {
        "artifact_type": PRECOMPUTE_ARTIFACT_TYPE,
        "schema_version": PRECOMPUTE_SCHEMA_VERSION,
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "trade_date": str(trade_date),
        "mode": canonical_trading_mode_label(mode),
        "source_run_id": str(run_id),
        "status": (
            PRECOMPUTE_STATUS_COMPLETE
            if not validation_failures
            else PRECOMPUTE_STATUS_INVALID
        ),
        "validation_reason": (
            None if not validation_failures else validation_failures[0]
        ),
        "validation_failures": validation_failures,
        "validated_for_execution": not validation_failures,
        "workflow_stage": "precompute",
        "compatibility": {
            "trading_mode": canonical_trading_mode_label(mode),
            "execution_flow": "precompute_before_open_v1",
        },
        "files": {
            "daily_snapshot": snapshot_filename,
            "signals": signals_filename,
            "planned_execution_payload": payload_filename,
            "sleeve_evaluations": sleeve_evaluations_filename,
        },
        "summary": {
            "execution_status": str((execution_payload or {}).get("execution_status") or ""),
            "market_status": str((execution_payload or {}).get("market_status") or ""),
            "planner_intended_trades_count": planner_intended,
            "execution_eligible_trades_count": executable_count,
            "trade_plan_count": len((execution_payload or {}).get("trades") or []),
        },
    }


def write_precompute_bundle(
    *,
    trade_date: str,
    run_id: str,
    mode: str,
    daily_snapshot: dict[str, Any],
    signals_payload: dict[str, Any],
    execution_payload: dict[str, Any],
    allow_overwrite: bool = True,
) -> Path:
    bundle_dir = precompute_bundle_dir(trade_date)
    bundle_dir.mkdir(parents=True, exist_ok=True)
    identity = strategy_identity_metadata(trade_date)
    daily_snapshot = dict(daily_snapshot or {})
    signals_payload = dict(signals_payload or {})
    execution_payload = dict(execution_payload or {})
    # Validate the canonical sleeve inventory before writing any bundle member.
    # Registry/manifest corruption and unregistered non-zero allocations are
    # authority failures. Missing research implementations are not: the
    # dispatcher records those as explicit BLOCKED envelopes below.
    from core.sleeve_control_plane import (
        load_sleeve_control_registry,
        write_all_sleeve_evaluation,
    )

    sleeve_registry = load_sleeve_control_registry()
    sleeve_registry.validate_allocations_registered(daily_snapshot)
    prior_signals, prior_source = latest_prior_signals(
        root=PRECOMPUTE_ROOT,
        trade_date=trade_date,
    )
    target_metrics = build_target_book_metrics(
        current_payload=signals_payload,
        current_source=f"outputs/precompute/{trade_date}/signals.json",
        previous_payload=prior_signals,
        previous_source=prior_source,
    )
    daily_snapshot["strategy_identity"] = identity
    signals_payload["strategy_identity"] = identity
    execution_payload["strategy_identity"] = identity
    daily_snapshot["target_book_metrics"] = target_metrics
    signals_payload["target_book_metrics"] = target_metrics
    execution_payload["target_book_metrics"] = target_metrics
    execution_payload["desired_one_way_turnover_pct"] = target_metrics.get(
        "desired_one_way_turnover_pct"
    )
    for key, value in identity.items():
        execution_payload.setdefault(key, value)
    daily_snapshot = _json_safe(daily_snapshot)
    signals_payload = _json_safe(signals_payload)
    execution_payload = _json_safe(execution_payload)

    snapshot_path = bundle_dir / "daily_snapshot.json"
    signals_path = bundle_dir / "signals.json"
    payload_path = bundle_dir / "planned_execution_payload.json"
    sleeve_evaluations_path = bundle_dir / "sleeve_evaluations.json"
    contract_path = bundle_dir / "contract.json"

    safe_write_text(
        snapshot_path,
        _strict_json(daily_snapshot),
        allow_overwrite=allow_overwrite,
    )
    safe_write_text(
        signals_path,
        _strict_json(signals_payload),
        allow_overwrite=allow_overwrite,
    )
    safe_write_text(
        payload_path,
        _strict_json(execution_payload),
        allow_overwrite=allow_overwrite,
    )

    # The contract is the completion marker, so publish the all-sleeve
    # evaluation before it.  A partial write can then never advertise a
    # complete five-member bundle.
    write_all_sleeve_evaluation(
        output_path=sleeve_evaluations_path,
        trade_date=trade_date,
        run_id=run_id,
        daily_snapshot=daily_snapshot,
        runtime_root=Path.cwd(),
        registry=sleeve_registry,
        allow_overwrite=allow_overwrite,
    )
    contract = build_precompute_contract(
        trade_date=trade_date,
        run_id=run_id,
        mode=mode,
        daily_snapshot=daily_snapshot,
        execution_payload=execution_payload,
    )
    safe_write_text(
        contract_path,
        _strict_json(contract),
        allow_overwrite=allow_overwrite,
    )
    return contract_path


def load_precompute_contract(trade_date: str) -> dict[str, Any] | None:
    return _read_json(precompute_contract_path(trade_date))


def validate_precompute_contract(
    contract: dict[str, Any] | None,
    *,
    expected_trade_date: str,
    expected_mode: str = "PAPER",
) -> tuple[bool, str | None]:
    if not contract:
        return False, REASON_PRECOMPUTE_MISSING
    if str(contract.get("artifact_type") or "") != PRECOMPUTE_ARTIFACT_TYPE:
        return False, REASON_PRECOMPUTE_INVALID
    if str(contract.get("trade_date") or "") != str(expected_trade_date):
        return False, REASON_PRECOMPUTE_WRONG_TRADE_DATE
    try:
        contract_mode = canonical_trading_mode_label(contract.get("mode"), field_name="contract.mode")
        expected_mode_label = canonical_trading_mode_label(expected_mode, field_name="expected_mode")
    except RuntimeError:
        return False, REASON_PRECOMPUTE_INVALID
    if contract_mode != expected_mode_label:
        return False, REASON_PRECOMPUTE_INVALID
    if str(contract.get("status") or "") != PRECOMPUTE_STATUS_COMPLETE:
        reason = str(contract.get("validation_reason") or "").strip()
        return False, reason or REASON_PRECOMPUTE_INCOMPLETE
    if not bool(contract.get("validated_for_execution")):
        reason = str(contract.get("validation_reason") or "").strip()
        return False, reason or REASON_PRECOMPUTE_VALIDATION_FAILED
    files = contract.get("files") or {}
    if not all(
        files.get(key)
        for key in (
            "daily_snapshot",
            "signals",
            "planned_execution_payload",
            "sleeve_evaluations",
        )
    ):
        return False, REASON_PRECOMPUTE_INCOMPLETE
    bundle_dir = precompute_bundle_dir(expected_trade_date)
    for rel_name in files.values():
        if not (bundle_dir / str(rel_name)).exists():
            return False, REASON_PRECOMPUTE_INCOMPLETE
    return True, None


# ---------------------------------------------------------------------------
# Bundle file discovery: shared between precompute upload and live download.
# ---------------------------------------------------------------------------

# The required bundle files (relative to the bundle dir for a given trade_date).
BUNDLE_REQUIRED_FILES = (
    "contract.json",
    "daily_snapshot.json",
    "signals.json",
    "planned_execution_payload.json",
    "sleeve_evaluations.json",
)


def discover_bundle_root(search_root: Path, report_date: str) -> Path | None:
    """Find the bundle directory containing contract.json for *report_date*.

    Handles multiple extraction layouts:
      1. ``<search_root>/outputs/precompute/<date>/contract.json``  (full prefix preserved)
      2. ``<search_root>/precompute/<date>/contract.json``          (outputs/ stripped by upload-artifact)
      3. ``<search_root>/<date>/contract.json``                     (flat extraction)
      4. ``<search_root>/<anything>/**/precompute/<date>/contract.json``  (wrapped in artifact-name dir)

    Returns the directory that directly contains contract.json, or None.
    """
    # Try explicit known layouts first (fast, no recursion).
    for candidate in (
        search_root / "outputs" / "precompute" / report_date,
        search_root / "precompute" / report_date,
        search_root / report_date,
    ):
        if (candidate / "contract.json").is_file():
            return candidate

    # Fallback: recursive glob — handles arbitrary wrapper directories.
    match = next(
        iter(search_root.glob(f"**/precompute/{report_date}/contract.json")),
        None,
    )
    if match is not None:
        return match.parent

    # Final fallback: contract.json anywhere under a date-named directory.
    match = next(
        iter(search_root.glob(f"**/{report_date}/contract.json")),
        None,
    )
    if match is not None:
        return match.parent

    return None


def check_bundle_completeness(bundle_dir: Path) -> tuple[bool, list[str], list[str]]:
    """Verify all required files exist in *bundle_dir*.

    Returns (complete, present_files, missing_files).
    """
    present: list[str] = []
    missing: list[str] = []
    for name in BUNDLE_REQUIRED_FILES:
        if (bundle_dir / name).is_file():
            present.append(name)
        else:
            missing.append(name)
    return len(missing) == 0, present, missing


def normalize_bundle_to_canonical(bundle_dir: Path, report_date: str) -> Path:
    """Copy bundle files from *bundle_dir* to the canonical location and return it.

    If *bundle_dir* is already the canonical location, this is a no-op.
    """
    import shutil

    canonical = precompute_bundle_dir(report_date)
    if bundle_dir.resolve() == canonical.resolve():
        return canonical
    canonical.mkdir(parents=True, exist_ok=True)
    for name in BUNDLE_REQUIRED_FILES:
        src = bundle_dir / name
        if src.is_file():
            shutil.copy2(str(src), str(canonical / name))
    return canonical


def load_precompute_inputs(
    *,
    trade_date: str,
    expected_mode: str = "ALPACA",
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None, str | None]:
    contract = load_precompute_contract(trade_date)
    valid, reason = validate_precompute_contract(
        contract,
        expected_trade_date=trade_date,
        expected_mode=expected_mode,
    )
    if not valid or not contract:
        return None, None, contract, reason

    files = contract.get("files") or {}
    bundle_dir = precompute_bundle_dir(trade_date)
    snapshot = _read_json(bundle_dir / str(files.get("daily_snapshot") or ""))
    signals = _read_json(bundle_dir / str(files.get("signals") or ""))
    payload = _read_json(bundle_dir / str(files.get("planned_execution_payload") or ""))
    if snapshot is None or signals is None or payload is None:
        return None, None, contract, REASON_PRECOMPUTE_INCOMPLETE
    snapshot["signals_snapshot_path"] = str(bundle_dir / str(files.get("signals")))
    return snapshot, payload, contract, None
