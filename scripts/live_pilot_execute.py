from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from brokers.alpaca_broker import AlpacaBroker
from core.live_pilot_guardrails import (
    LIVE_PILOT_MODE,
    account_id_hash,
    build_live_pilot_gate_result,
    expected_account_matches,
    validate_live_pilot_asset,
    validate_live_pilot_plan,
)
from paper.run_manager import generate_run_id, safe_write_text


def _now_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return str(value)


def _write_json(path: Path, payload: Mapping[str, Any]) -> Path:
    safe_write_text(
        path,
        json.dumps(_json_safe(dict(payload)), indent=2, sort_keys=True) + "\n",
        allow_overwrite=True,
    )
    return path


def _load_plan(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return {"trades": payload}
    if not isinstance(payload, dict):
        raise ValueError("live pilot plan must be a JSON object or list")
    return payload


def _trades_from_plan(plan: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    trades = plan.get("trades") or plan.get("orders") or []
    if not isinstance(trades, list):
        raise ValueError("live pilot plan trades/orders must be a list")
    return [trade for trade in trades if isinstance(trade, Mapping)]


def _public_account(account: Mapping[str, Any] | None) -> dict[str, Any]:
    account = dict(account or {})
    account_id = str(account.get("id") or "").strip()
    return {
        "account_id_hash": account_id_hash(account_id) if account_id else None,
        "status": account.get("status"),
        "cash": account.get("cash"),
        "equity": account.get("equity"),
        "buying_power": account.get("buying_power"),
        "portfolio_value": account.get("portfolio_value"),
    }


def _broker_snapshot(broker: Any) -> dict[str, Any]:
    account = broker.get_account() if hasattr(broker, "get_account") else {}
    positions = broker.get_positions() if hasattr(broker, "get_positions") else []
    return {
        "captured_at": _now_utc(),
        "account": _public_account(account),
        "positions": _json_safe(positions or []),
    }


def _status_norm(status: object) -> str:
    value = str(status or "").strip().lower()
    if "." in value:
        value = value.rsplit(".", 1)[-1]
    return value


def _status_bucket(status: object) -> str:
    value = _status_norm(status)
    if value in {"filled"}:
        return "filled"
    if value in {"partially_filled"}:
        return "partial"
    if value in {"rejected", "canceled", "cancelled", "expired", "failed"}:
        return "rejected"
    if value in {"accepted", "pending_new", "new", "done_for_day", "pending_replace", "pending_cancel"}:
        return "accepted_open"
    return "unresolved"


def _reconcile(
    *,
    dry_run: bool,
    intended: list[dict[str, Any]],
    submitted: list[dict[str, Any]],
    errors: list[str],
) -> dict[str, Any]:
    accepted = 0
    filled = 0
    rejected = 0
    unresolved = 0
    partial = 0
    open_count = 0
    for row in submitted:
        status_value = row.get("status") or (row.get("order") or {}).get("status")
        bucket = _status_bucket(status_value)
        if bucket in {"accepted_open", "partial", "filled"}:
            accepted += 1
        if bucket == "accepted_open":
            open_count += 1
        if bucket == "partial":
            partial += 1
        if bucket == "filled":
            filled += 1
        elif bucket == "rejected":
            rejected += 1
        elif bucket == "unresolved":
            unresolved += 1

    if dry_run:
        state = "DRY_RUN"
        status = "DRY_RUN_NO_SUBMISSION"
        action = "Review artifacts and keep dry run enabled until human approval is recorded."
    elif errors or rejected:
        state = "REJECTED"
        status = "FAILED_RECONCILIATION"
        action = "Do not continue live pilot; inspect broker state and resolve rejected/unresolved orders manually."
    elif partial:
        state = "PARTIAL"
        status = "FAILED_RECONCILIATION"
        action = "Do not continue live pilot; wait for broker terminal truth or manually review partial fill state."
    elif unresolved or len(submitted) != len(intended):
        state = "UNRESOLVED"
        status = "FAILED_RECONCILIATION"
        action = "Do not continue live pilot; inspect broker state and resolve rejected/unresolved orders manually."
    else:
        state = "CLEAN"
        status = "CLEAN"
        action = "Monitor broker terminal states and preserve all live pilot artifacts."

    return {
        "schema_version": "live_pilot_reconciliation.v1",
        "generated_at": _now_utc(),
        "status": status,
        "state": state,
        "intended_count": len(intended),
        "submitted_count": len(submitted),
        "accepted_count": accepted,
        "filled_count": filled,
        "partial_count": partial,
        "open_count": open_count,
        "rejected_count": rejected + len(errors),
        "unresolved_count": unresolved,
        "errors": list(errors),
        "operator_action": action,
        "rollback_recommendation": (
            "No auto-liquidation. Cancel/flatten only under a separately approved live incident runbook."
            if status != "CLEAN"
            else "No rollback action required unless broker state later diverges."
        ),
    }


def _write_blocked_artifacts(
    *,
    run_root: Path,
    run_id: str,
    reason_code: str,
    operator_action: str,
    preflight: Mapping[str, Any],
    intended: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    intended = intended or []
    submitted: list[dict[str, Any]] = []
    reconciliation = _reconcile(
        dry_run=False,
        intended=intended,
        submitted=submitted,
        errors=[reason_code],
    )
    summary = {
        "schema_version": "live_pilot_operator_summary.v1",
        "generated_at": _now_utc(),
        "run_id": run_id,
        "mode": LIVE_PILOT_MODE.upper(),
        "terminal_status": "BLOCKED",
        "reason_code": reason_code,
        "live_orders_allowed": False,
        "submitted_count": 0,
        "operator_action": operator_action,
    }
    _write_json(run_root / "live_pilot_orders_intended.json", {"orders": intended})
    _write_json(run_root / "live_pilot_orders_submitted.json", {"orders": submitted})
    blocked_snapshot = {
        "captured_at": _now_utc(),
        "status": "NOT_CAPTURED_BLOCKED_BEFORE_BROKER_SNAPSHOT",
        "account": {},
        "positions": [],
    }
    for snapshot_name in (
        "live_pilot_broker_snapshot_pre.json",
        "live_pilot_broker_snapshot_post.json",
    ):
        snapshot_path = run_root / snapshot_name
        if not snapshot_path.exists():
            _write_json(snapshot_path, blocked_snapshot)
    _write_json(run_root / "live_pilot_reconciliation.json", reconciliation)
    _write_json(run_root / "live_pilot_capital_usage.json", {"capital_used_usd": 0.0})
    _write_json(run_root / "live_pilot_operator_summary.json", summary)
    _write_json(run_root / "live_pilot_preflight.json", dict(preflight))
    return summary


def run_live_pilot(
    *,
    plan: Mapping[str, Any],
    plan_path: str | None = None,
    broker: Any | None = None,
    env: Mapping[str, str] | None = None,
    run_id: str | None = None,
    output_root: Path | str = Path("outputs/live_pilot"),
) -> dict[str, Any]:
    environ = env if env is not None else os.environ
    run_id = str(run_id or environ.get("RUN_ID") or generate_run_id())
    run_root = Path(output_root) / "runs" / run_id
    run_root.mkdir(parents=True, exist_ok=True)

    broker = broker or AlpacaBroker.from_env()
    broker_paper = bool(getattr(broker, "paper", True))
    base_url = str(getattr(broker, "base_url", "") or "")
    gate = build_live_pilot_gate_result(
        broker_paper=broker_paper,
        base_url=base_url,
        env=environ,
        submission_intent=False,
    )
    preflight = gate.to_dict()
    preflight["schema_version"] = "live_pilot_preflight.v1"
    preflight["run_id"] = run_id
    preflight["generated_at"] = _now_utc()
    preflight["orders_submitted"] = 0
    _write_json(run_root / "live_pilot_preflight.json", preflight)

    payload = {
        "schema_version": "live_pilot_execution_payload.v1",
        "generated_at": _now_utc(),
        "run_id": run_id,
        "mode": LIVE_PILOT_MODE,
        "plan_path": plan_path,
        "dry_run": bool(gate.dry_run),
        "paper_paths_touched": False,
    }
    _write_json(run_root / "live_pilot_execution_payload.json", payload)

    if gate.status != "PASS":
        return _write_blocked_artifacts(
            run_root=run_root,
            run_id=run_id,
            reason_code=gate.reason_code,
            operator_action=gate.operator_action,
            preflight=preflight,
        )

    try:
        pre_snapshot = _broker_snapshot(broker)
    except Exception as exc:
        return _write_blocked_artifacts(
            run_root=run_root,
            run_id=run_id,
            reason_code="live_pilot_pre_snapshot_failed",
            operator_action=f"Resolve read-only broker snapshot failure before live pilot: {exc}",
            preflight=preflight,
        )
    _write_json(run_root / "live_pilot_broker_snapshot_pre.json", pre_snapshot)

    account_public = pre_snapshot.get("account") or {}
    account_hash = str(account_public.get("account_id_hash") or "")
    account_match = False
    account_reason = "missing_actual_account_id"
    # Only compare raw account id when the broker exposes it through get_account.
    raw_account = broker.get_account() if hasattr(broker, "get_account") else {}
    account_match, account_reason = expected_account_matches((raw_account or {}).get("id"), environ)
    if not account_match and account_hash:
        expected_hash = str(environ.get("CAERUS_LIVE_PILOT_ACCOUNT_ID_HASH") or "").strip().lower()
        account_match = bool(expected_hash and account_hash.lower() == expected_hash)
        account_reason = "account_id_hash_match" if account_match else account_reason
    if not account_match:
        return _write_blocked_artifacts(
            run_root=run_root,
            run_id=run_id,
            reason_code=account_reason,
            operator_action="Expected live pilot account id/hash does not match broker account.",
            preflight=preflight,
        )

    plan_validation = validate_live_pilot_plan(
        _trades_from_plan(plan),
        env=environ,
        capital_cap_usd=float(gate.capital_cap_usd or 0.0),
        max_orders=int(gate.max_orders or 0),
        run_id=run_id,
    )
    intended = [order.to_dict() for order in plan_validation.orders]
    _write_json(run_root / "live_pilot_orders_intended.json", plan_validation.to_dict())
    if plan_validation.status != "PASS":
        return _write_blocked_artifacts(
            run_root=run_root,
            run_id=run_id,
            reason_code=";".join(plan_validation.reason_codes),
            operator_action=plan_validation.operator_action,
            preflight=preflight,
            intended=intended,
        )

    asset_errors: list[str] = []
    for order in plan_validation.orders:
        asset = broker.get_asset(order.symbol) if hasattr(broker, "get_asset") else None
        error = validate_live_pilot_asset(asset, order.symbol)
        if error:
            asset_errors.append(error)
    if asset_errors:
        return _write_blocked_artifacts(
            run_root=run_root,
            run_id=run_id,
            reason_code=";".join(asset_errors),
            operator_action="Unsupported or non-tradable assets blocked before submission.",
            preflight=preflight,
            intended=intended,
        )

    submitted: list[dict[str, Any]] = []
    submit_errors: list[str] = []
    if gate.dry_run:
        submitted = [
            {
                **order.to_dict(),
                "status": "DRY_RUN_NOT_SUBMITTED",
                "order": None,
            }
            for order in plan_validation.orders
        ]
    else:
        for order in plan_validation.orders:
            try:
                broker_result = broker.submit_limit_order(
                    symbol=order.symbol,
                    qty=order.qty,
                    side=order.side,
                    limit_price=order.limit_price,
                    client_order_id=order.client_order_id,
                    tif="day",
                )
                submitted.append(
                    {
                        **order.to_dict(),
                        "status": str((broker_result or {}).get("status") or "accepted"),
                        "order": broker_result,
                    }
                )
            except Exception as exc:
                submit_errors.append(f"{order.symbol}:broker_submit_failed:{exc}")
                submitted.append(
                    {
                        **order.to_dict(),
                        "status": "REJECTED",
                        "error": str(exc),
                    }
                )

    _write_json(run_root / "live_pilot_orders_submitted.json", {"orders": submitted})

    try:
        post_snapshot = _broker_snapshot(broker)
    except Exception as exc:
        post_snapshot = {
            "captured_at": _now_utc(),
            "status": "SNAPSHOT_FAILED",
            "error": str(exc),
        }
        submit_errors.append(f"post_snapshot_failed:{exc}")
    _write_json(run_root / "live_pilot_broker_snapshot_post.json", post_snapshot)

    reconciliation = _reconcile(
        dry_run=bool(gate.dry_run),
        intended=intended,
        submitted=submitted,
        errors=submit_errors,
    )
    _write_json(run_root / "live_pilot_reconciliation.json", reconciliation)
    _write_json(
        run_root / "live_pilot_capital_usage.json",
        {
            "schema_version": "live_pilot_capital_usage.v1",
            "capital_cap_usd": gate.capital_cap_usd,
            "planned_notional_usd": plan_validation.total_notional,
            "submitted_notional_usd": 0.0 if gate.dry_run else sum(float(row.get("notional") or 0.0) for row in submitted if row.get("status") != "REJECTED"),
            "dry_run": bool(gate.dry_run),
        },
    )

    terminal_status = (
        "DRY_RUN"
        if gate.dry_run
        else ("SUBMITTED" if reconciliation.get("status") == "CLEAN" else "FAILED_RECONCILIATION")
    )
    summary = {
        "schema_version": "live_pilot_operator_summary.v1",
        "generated_at": _now_utc(),
        "run_id": run_id,
        "mode": LIVE_PILOT_MODE.upper(),
        "terminal_status": terminal_status,
        "reason_code": reconciliation.get("status"),
        "live_orders_allowed": bool(gate.live_orders_allowed),
        "dry_run": bool(gate.dry_run),
        "intended_count": len(intended),
        "submitted_count": 0 if gate.dry_run else len(submitted),
        "operator_action": reconciliation.get("operator_action"),
        "run_root": str(run_root),
    }
    _write_json(run_root / "live_pilot_operator_summary.json", summary)
    return summary



def _extract_broker_order_id(row: Mapping[str, Any]) -> str:
    order = row.get("order") if isinstance(row.get("order"), Mapping) else {}
    return str((order or {}).get("id") or row.get("broker_order_id") or row.get("order_id") or "").strip()


def refresh_live_pilot_reconciliation(
    *,
    run_root: Path | str,
    broker: Any | None = None,
) -> dict[str, Any]:
    run_root = Path(run_root)
    broker = broker or AlpacaBroker.from_env()
    intended_payload = _load_plan(run_root / "live_pilot_orders_intended.json")
    submitted_payload = _load_plan(run_root / "live_pilot_orders_submitted.json")
    intended = _trades_from_plan(intended_payload)
    submitted = [dict(row) for row in _trades_from_plan(submitted_payload)]

    refresh_errors: list[str] = []
    refreshed: list[dict[str, Any]] = []
    for row in submitted:
        order_id = _extract_broker_order_id(row)
        if not order_id:
            refresh_errors.append(f"{row.get('symbol') or row.get('ticker')}:missing_broker_order_id")
            refreshed.append(row)
            continue
        try:
            broker_order = broker.get_order(order_id)
            if not broker_order:
                refresh_errors.append(f"{row.get('symbol') or row.get('ticker')}:broker_order_not_found:{order_id}")
                refreshed.append(row)
                continue
            refreshed.append({
                **row,
                "status": str((broker_order or {}).get("status") or row.get("status")),
                "order": broker_order,
                "refreshed_at": _now_utc(),
            })
        except Exception as exc:
            refresh_errors.append(f"{row.get('symbol') or row.get('ticker')}:broker_order_refresh_failed:{exc}")
            refreshed.append(row)

    _write_json(run_root / "live_pilot_orders_submitted.json", {"orders": refreshed})
    try:
        post_snapshot = _broker_snapshot(broker)
    except Exception as exc:
        post_snapshot = {"captured_at": _now_utc(), "status": "SNAPSHOT_FAILED", "error": str(exc)}
        refresh_errors.append(f"post_snapshot_failed:{exc}")
    _write_json(run_root / "live_pilot_broker_snapshot_post.json", post_snapshot)

    reconciliation = _reconcile(
        dry_run=False,
        intended=intended,
        submitted=refreshed,
        errors=refresh_errors,
    )
    reconciliation["refreshed_existing_run"] = True
    _write_json(run_root / "live_pilot_reconciliation.json", reconciliation)

    summary = {
        "schema_version": "live_pilot_operator_summary.v1",
        "generated_at": _now_utc(),
        "run_id": run_root.name,
        "mode": LIVE_PILOT_MODE.upper(),
        "terminal_status": "SUBMITTED" if reconciliation.get("status") == "CLEAN" else "FAILED_RECONCILIATION",
        "reason_code": reconciliation.get("status"),
        "live_orders_allowed": True,
        "dry_run": False,
        "intended_count": len(intended),
        "submitted_count": len(refreshed),
        "operator_action": reconciliation.get("operator_action"),
        "run_root": str(run_root),
        "refreshed_existing_run": True,
    }
    _write_json(run_root / "live_pilot_operator_summary.json", summary)
    return summary

def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the isolated Caerus LIVE_PILOT executor")
    parser.add_argument("--plan", default=None, help="Path to a live pilot JSON plan")
    parser.add_argument("--refresh-run", default=None, help="Read-only refresh for an existing live pilot run root")
    parser.add_argument("--run-id", default=None, help="Optional deterministic run id")
    parser.add_argument("--output-root", default="outputs/live_pilot", help="Isolated live pilot output root")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.refresh_run:
        result = refresh_live_pilot_reconciliation(run_root=Path(args.refresh_run))
    else:
        if not args.plan:
            raise SystemExit("--plan is required unless --refresh-run is provided")
        plan_path = Path(args.plan)
        result = run_live_pilot(
            plan=_load_plan(plan_path),
            plan_path=str(plan_path),
            run_id=args.run_id,
            output_root=Path(args.output_root),
        )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if str(result.get("terminal_status") or "").upper() in {"DRY_RUN", "SUBMITTED"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
