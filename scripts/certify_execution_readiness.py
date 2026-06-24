from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from brokers.alpaca_broker import AlpacaBroker
from core.security_master import resolve_trade_plan_symbols
from paper.paper_broker import (
    PaperConfig,
    _account_is_clean_for_buying_power,
    _apply_buy_budget,
    _budget_skipped_order_diagnostics,
    _compute_buy_budget,
    _normalize_and_filter_executable_trades,
    _normalize_precomputed_trade_plan,
    _split_orders_for_execution,
    _validate_alpaca_assets,
    load_config,
)


CERTIFICATION_FILENAME = "execution_readiness_certification.json"
READINESS_PASS = "PASS"
READINESS_WARN = "WARN"
READINESS_FAIL = "FAIL"
ALLOWED_PAYLOAD_STATUSES = {"PLANNED", "EXECUTABLE"}


def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    if isinstance(value, tuple):
        return [_json_safe(v) for v in value]
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return value
    return str(value)


def _safe_float(value: object, default: float | None = None) -> float | None:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except Exception:
        return default


def _safe_int(value: object, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(value)
    except Exception:
        try:
            return int(float(value))
        except Exception:
            return default


def _side(value: object) -> str:
    raw = str(value or "").strip().upper()
    return "SELL" if raw in {"SELL", "CLOSE", "REDUCE"} else raw


def _trade_quantity(trade: Mapping[str, object]) -> float:
    return abs(_safe_float(trade.get("shares", trade.get("quantity", trade.get("qty"))), 0.0) or 0.0)


def _trade_notional(trade: Mapping[str, object]) -> float:
    notional = _safe_float(trade.get("notional"), 0.0) or 0.0
    if notional > 0:
        return abs(float(notional))
    price = abs(_safe_float(trade.get("price", trade.get("entry_price")), 0.0) or 0.0)
    return _trade_quantity(trade) * price


def _normalize_account_status(account: Mapping[str, object]) -> str:
    raw = account.get("raw") if isinstance(account.get("raw"), Mapping) else {}
    status = account.get("status")
    if (status is None or status == "") and isinstance(raw, Mapping):
        status = raw.get("status")
    text = str(status or "").strip().upper()
    if "." in text:
        text = text.rsplit(".", 1)[-1]
    return text


def _latest_precompute_trade_date(repo_root: Path) -> str:
    root = repo_root / "outputs" / "precompute"
    candidates = sorted(
        path.name
        for path in root.iterdir()
        if path.is_dir() and (path / "planned_execution_payload.json").exists()
    ) if root.exists() else []
    if not candidates:
        raise FileNotFoundError(f"no planned_execution_payload.json found under {root}")
    return candidates[-1]


def _payload_path(repo_root: Path, trade_date: str, payload_path: str | Path | None) -> Path:
    if payload_path:
        path = Path(payload_path)
        return path if path.is_absolute() else repo_root / path
    return repo_root / "outputs" / "precompute" / str(trade_date) / "planned_execution_payload.json"


def _default_output_path(repo_root: Path, trade_date: str, output_path: str | Path | None) -> Path:
    if output_path:
        path = Path(output_path)
        return path if path.is_absolute() else repo_root / path
    return repo_root / "outputs" / "precompute" / str(trade_date) / CERTIFICATION_FILENAME


def _expected_planned_trade_count(payload: Mapping[str, object]) -> int | None:
    for key in (
        "planned_trade_count",
        "planned_trades_count",
        "planner_intended_trades_count",
        "model_proposed_trades_count",
    ):
        if payload.get(key) is not None:
            return _safe_int(payload.get(key), default=-1)
    return None


def _load_payload(path: Path) -> dict[str, object]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError("planned_execution_payload_missing_or_malformed")
    return payload


def _validate_payload(
    payload: Mapping[str, object],
    *,
    trade_date: str,
) -> tuple[list[dict[str, object]], list[str]]:
    fail_reasons: list[str] = []
    payload_trade_date = str(payload.get("trade_date") or payload.get("planned_for") or "").strip()
    if payload_trade_date != str(trade_date):
        fail_reasons.append(
            f"planned_execution_payload_trade_date_mismatch:{payload_trade_date or 'missing'}"
        )

    payload_status = str(
        payload.get("execution_status") or payload.get("status") or payload.get("status_label") or ""
    ).strip().upper()
    if payload_status not in ALLOWED_PAYLOAD_STATUSES:
        fail_reasons.append(
            f"planned_execution_payload_status_not_executable:{payload_status or 'missing'}"
        )

    trades_raw = payload.get("trades")
    if not isinstance(trades_raw, list):
        return [], [*fail_reasons, "planned_execution_payload_trades_missing_or_malformed"]

    expected_count = _expected_planned_trade_count(payload)
    if expected_count is not None and expected_count >= 0 and expected_count != len(trades_raw):
        fail_reasons.append(
            f"planned_execution_payload_trades_count_mismatch:expected={expected_count}:actual={len(trades_raw)}"
        )

    trades: list[dict[str, object]] = []
    for idx, trade in enumerate(trades_raw):
        if not isinstance(trade, dict):
            fail_reasons.append(f"planned_execution_payload_trade_malformed:index={idx}")
            continue
        ticker = str(trade.get("ticker") or trade.get("symbol") or "").strip().upper()
        side = str(trade.get("side") or "").strip().upper()
        quantity = _trade_quantity(trade)
        price = _safe_float(trade.get("price", trade.get("entry_price")), 0.0) or 0.0
        notional = _trade_notional(trade)
        if not ticker or side not in {"BUY", "SELL", "CLOSE", "REDUCE"} or quantity <= 0.0:
            fail_reasons.append(
                f"planned_execution_payload_trade_invalid:index={idx}:ticker={ticker or 'missing'}:side={side or 'missing'}:quantity={quantity}"
            )
            continue
        if price <= 0.0 and notional <= 0.0:
            fail_reasons.append(f"planned_execution_payload_trade_missing_price:index={idx}:ticker={ticker}")
            continue
        row = dict(trade)
        row["ticker"] = ticker
        row["side"] = side
        trades.append(row)
    return trades, fail_reasons


def _execution_filter_diagnostics(
    trades: list[dict[str, object]],
    cfg: PaperConfig,
) -> list[dict[str, object]]:
    diagnostics: list[dict[str, object]] = []
    for idx, trade in enumerate(trades):
        ticker = str(trade.get("ticker") or trade.get("symbol") or "").strip().upper()
        side = _side(trade.get("side"))
        shares = _trade_quantity(trade)
        price = _safe_float(trade.get("price", trade.get("entry_price")), 0.0) or 0.0
        notional = _trade_notional(trade)
        if price <= 0.0 and shares > 0 and notional > 0:
            price = notional / shares
        rounded_shares = shares if cfg.allow_fractional else float(math.floor(shares))
        row = {
            "source_index": idx,
            "ticker": ticker,
            "side": side,
            "requested_quantity": shares,
            "price": price,
            "requested_notional": notional,
            "retained": False,
            "stage": "execution_filter",
            "skip_reason": None,
        }
        if rounded_shares <= 1e-12 or (not cfg.allow_fractional and rounded_shares < 1.0):
            row["skip_reason"] = "zero_shares"
        else:
            rounded_notional = abs(rounded_shares * price)
            row["rounded_quantity"] = rounded_shares
            row["rounded_notional"] = rounded_notional
            if rounded_notional < float(cfg.min_trade_dollars):
                row["skip_reason"] = "min_notional"
            else:
                row["retained"] = True
                row["skip_reason"] = None
        diagnostics.append(row)
    return diagnostics


def _records_from_frame(frame: Any) -> list[dict[str, object]]:
    if frame is None or getattr(frame, "empty", True):
        return []
    records: list[dict[str, object]] = []
    for item in frame.to_dict("records"):
        record = dict(item)
        if "quantity" not in record and "shares" in record:
            record["quantity"] = record.get("shares")
        records.append(record)
    return records


def _trade_summary(order: Mapping[str, object], *, retained: bool, stage: str) -> dict[str, object]:
    return {
        "ticker": str(order.get("ticker") or order.get("symbol") or ""),
        "side": str(order.get("side") or ""),
        "quantity": _safe_float(order.get("quantity", order.get("qty", order.get("shares"))), None),
        "notional": _trade_notional(order),
        "retained": bool(retained),
        "stage": stage,
        "skip_reason": order.get("block_reason"),
        "buy_budget_basis": order.get("buy_budget_basis"),
        "buy_budget_at_decision": order.get("buy_budget_at_decision"),
        "cash_at_buy_decision": order.get("cash_at_buy_decision"),
        "buying_power_at_buy_decision": order.get("buying_power_at_buy_decision"),
        "account_status": order.get("account_status"),
        "account_status_clean_for_buying_power": order.get(
            "account_status_clean_for_buying_power"
        ),
    }


def _load_account_snapshot(path: str | Path) -> dict[str, object]:
    payload = _load_payload(Path(path))
    account = payload.get("account") if isinstance(payload.get("account"), Mapping) else payload
    if not isinstance(account, Mapping):
        raise ValueError("account_snapshot_missing_or_malformed")
    return dict(account)


def _build_broker() -> AlpacaBroker:
    return AlpacaBroker.from_env()


def certify_execution_readiness(
    *,
    trade_date: str | None = None,
    planned_payload_path: str | Path | None = None,
    output_path: str | Path | None = None,
    mode: str = "paper",
    no_submit: bool = True,
    write_artifact: bool = True,
    account_snapshot: Mapping[str, object] | None = None,
    account_snapshot_path: str | Path | None = None,
    broker: object | None = None,
    cfg: PaperConfig | None = None,
    repo_root: str | Path | None = None,
    now_utc: dt.datetime | None = None,
) -> dict[str, object]:
    repo = Path(repo_root or Path.cwd()).resolve()
    resolved_trade_date = str(trade_date or _latest_precompute_trade_date(repo))
    payload_path = _payload_path(repo, resolved_trade_date, planned_payload_path)
    artifact_path = _default_output_path(repo, resolved_trade_date, output_path)
    timestamp = (now_utc or _utc_now()).astimezone(dt.timezone.utc).isoformat()
    fail_reasons: list[str] = []
    warning_reasons: list[str] = []
    payload: dict[str, object] = {}
    trades: list[dict[str, object]] = []

    if not no_submit:
        fail_reasons.append("no_submit_flag_required")

    if not payload_path.exists():
        fail_reasons.append("planned_execution_payload_missing")
    else:
        try:
            payload = _load_payload(payload_path)
            trades, payload_failures = _validate_payload(payload, trade_date=resolved_trade_date)
            fail_reasons.extend(payload_failures)
        except Exception as exc:
            fail_reasons.append(f"planned_execution_payload_load_failed:{type(exc).__name__}:{exc}")

    payload_status = str(
        payload.get("execution_status") or payload.get("status") or payload.get("status_label") or ""
    ).strip().upper()
    planned_trade_count = len(trades)
    expected_count = _expected_planned_trade_count(payload)
    if expected_count is not None and expected_count >= 0:
        planned_trade_count = int(expected_count)

    runtime_cfg = cfg or load_config(str(repo / "paper" / "config_paper.json"))
    mode_normalized = str(mode or "paper").strip().lower()
    cfg_mode = "live_pilot" if mode_normalized == "pilot" else mode_normalized
    runtime_cfg = replace(runtime_cfg, trading_mode=cfg_mode)

    filter_diagnostics: list[dict[str, object]] = []
    execution_filter_stats = {
        "raw": 0,
        "rounded": 0,
        "dropped_zero_shares": 0,
        "dropped_min_notional": 0,
        "kept": 0,
    }
    resolved_orders: list[dict[str, object]] = []
    symbol_resolution_payload: dict[str, object] = {}
    asset_validation: dict[str, object] = {}
    sell_orders: list[dict[str, object]] = []
    buy_orders: list[dict[str, object]] = []
    budgeted_buy_orders: list[dict[str, object]] = []
    budget_skipped_orders: list[dict[str, object]] = []
    account: dict[str, object] = {}
    account_status = ""
    account_clean: bool | None = None
    buy_budget = 0.0
    buy_budget_basis = "not_computed"

    if trades and not fail_reasons:
        filter_diagnostics = _execution_filter_diagnostics(trades, runtime_cfg)
        raw_frame = _normalize_precomputed_trade_plan(trades)
        executable_frame, execution_filter_stats = _normalize_and_filter_executable_trades(
            raw_frame,
            runtime_cfg,
        )
        filtered_orders = _records_from_frame(executable_frame)
        try:
            resolution = resolve_trade_plan_symbols(filtered_orders, today=resolved_trade_date)
            symbol_resolution_payload = resolution.to_payload()
            if str(resolution.status).upper() == "FAIL":
                fail_reasons.append(
                    f"symbol_resolution_failure:{resolution.reason or 'unknown'}"
                )
            else:
                if str(resolution.status).upper() == "WARN":
                    warning_reasons.append(
                        f"symbol_resolution_warning:{resolution.reason or 'unknown'}"
                    )
                resolved_orders = [dict(order) for order in resolution.trades]
        except Exception as exc:
            fail_reasons.append(f"symbol_resolution_exception:{type(exc).__name__}:{exc}")

    if any(
        row.get("side") == "SELL" and row.get("skip_reason") == "min_notional"
        for row in filter_diagnostics
    ):
        warning_reasons.append("sells_dropped_by_min_notional_filter")
    if any(not bool(row.get("retained")) for row in filter_diagnostics):
        warning_reasons.append("trades_dropped_by_execution_filter")

    if resolved_orders and not fail_reasons:
        active_broker = broker
        if active_broker is None and account_snapshot is None and account_snapshot_path is None:
            try:
                active_broker = _build_broker()
            except Exception as exc:
                fail_reasons.append(f"broker_initialization_failed:{type(exc).__name__}:{exc}")

        if active_broker is not None:
            try:
                asset_validation = _validate_alpaca_assets(active_broker, resolved_orders)  # type: ignore[arg-type]
            except Exception as exc:
                fail_reasons.append(f"asset_validation_exception:{type(exc).__name__}:{exc}")
        else:
            asset_validation = {
                "asset_validation_status": "UNAVAILABLE",
                "asset_validation_reason": "broker_asset_lookup_unavailable",
                "asset_validation_reasons": ["broker_asset_lookup_unavailable"],
                "invalid_symbols": [],
                "non_tradable_symbols": [],
                "inactive_symbols": [],
                "invalid_asset_count": 0,
                "asset_validation_records": [],
            }

        asset_status = str(asset_validation.get("asset_validation_status") or "").upper()
        if asset_status != "PASS":
            fail_reasons.append(
                f"asset_validation_failure:{asset_validation.get('asset_validation_reason') or asset_status or 'unknown'}"
            )

        if account_snapshot is not None:
            account = dict(account_snapshot)
        elif account_snapshot_path:
            try:
                account = _load_account_snapshot(account_snapshot_path)
            except Exception as exc:
                fail_reasons.append(f"account_snapshot_load_failed:{type(exc).__name__}:{exc}")
        elif active_broker is not None:
            getter = getattr(active_broker, "get_account", None)
            if not callable(getter):
                fail_reasons.append("account_snapshot_unavailable:get_account_missing")
            else:
                try:
                    account = dict(getter() or {})
                except Exception as exc:
                    fail_reasons.append(f"account_snapshot_unavailable:{type(exc).__name__}:{exc}")
        else:
            fail_reasons.append("account_snapshot_unavailable:no_broker")

    if account:
        account_status = _normalize_account_status(account)
        if not account_status:
            fail_reasons.append("account_status_cannot_be_normalized")
        account_clean = _account_is_clean_for_buying_power(account)
        try:
            buy_budget, buy_budget_basis = _compute_buy_budget(account, runtime_cfg)
        except Exception as exc:
            fail_reasons.append(f"buy_budget_computation_failed:{type(exc).__name__}:{exc}")
        cash = _safe_float(account.get("cash"), None)
        buying_power = _safe_float(account.get("buying_power"), None)
        if (
            mode_normalized == "paper"
            and account_clean is True
            and account_status == "ACTIVE"
            and buying_power is not None
            and buying_power > 0.0
            and buy_budget_basis != "broker_buying_power"
        ):
            fail_reasons.append("buy_budget_cash_fallback_with_valid_buying_power")
    else:
        cash = None
        buying_power = None

    if mode_normalized != "paper":
        warning_reasons.append("live_paper_divergence_not_comparable")

    if resolved_orders and account and not fail_reasons:
        sell_orders, buy_orders = _split_orders_for_execution(resolved_orders)
        budgeted_buy_orders, budget_skipped_orders = _apply_buy_budget(
            buy_orders,
            float(buy_budget or 0.0),
            buy_budget_basis=buy_budget_basis,
            account_status_clean=account_clean,
            cash_at_decision=cash,
            buying_power_at_decision=buying_power,
            account_status=account.get("status") or account_status,
        )
        if budget_skipped_orders and (sell_orders or budgeted_buy_orders):
            warning_reasons.append("buys_skipped_by_budget_gate")
    elif resolved_orders:
        sell_orders, buy_orders = _split_orders_for_execution(resolved_orders)

    retained_filter_diagnostics = [
        row for row in filter_diagnostics if bool(row.get("retained"))
    ]
    skipped_filter_diagnostics = [
        row for row in filter_diagnostics if not bool(row.get("retained"))
    ]
    executable_trade_count = int(len(sell_orders) + len(budgeted_buy_orders))
    skipped_trade_count = int(len(skipped_filter_diagnostics) + len(budget_skipped_orders))
    expected_submissions = executable_trade_count

    if planned_trade_count > 0 and executable_trade_count == 0:
        fail_reasons.append("planned_trades_dropped_to_zero_executable_orders")
    if planned_trade_count > 0 and expected_submissions == 0:
        fail_reasons.append("planned_trades_dropped_to_zero_expected_submissions")

    readiness_status = READINESS_FAIL if fail_reasons else (
        READINESS_WARN if warning_reasons else READINESS_PASS
    )
    expected_orders = [
        _trade_summary(order, retained=True, stage="expected_submission")
        for order in [*sell_orders, *budgeted_buy_orders]
    ]
    skipped_orders = [
        *skipped_filter_diagnostics,
        *[
            _trade_summary(order, retained=False, stage="buy_budget")
            for order in budget_skipped_orders
        ],
    ]
    artifact = {
        "schema_version": "execution_readiness_certification.v1",
        "trade_date": resolved_trade_date,
        "run_timestamp": timestamp,
        "run_timestamp_utc": timestamp,
        "source_payload_path": str(payload_path),
        "payload_status": payload_status or None,
        "mode": mode_normalized,
        "no_submit": bool(no_submit),
        "planned_trade_count": int(planned_trade_count),
        "sell_count": int(len(sell_orders)),
        "buy_count": int(len(buy_orders)),
        "executable_trade_count": executable_trade_count,
        "skipped_trade_count": skipped_trade_count,
        "dropped_zero_share_count": int(execution_filter_stats.get("dropped_zero_shares") or 0),
        "dropped_min_notional_count": int(execution_filter_stats.get("dropped_min_notional") or 0),
        "buy_budget": round(float(buy_budget or 0.0), 6),
        "buy_budget_basis": buy_budget_basis,
        "cash": cash,
        "buying_power": buying_power,
        "account_status": str(account.get("status") or "") if account else None,
        "normalized_account_status": account_status or None,
        "account_status_clean_for_buying_power": account_clean,
        "expected_submissions": int(expected_submissions),
        "broker_submission_invoked": False,
        "readiness_status": readiness_status,
        "fail_reasons": list(dict.fromkeys(fail_reasons)),
        "warning_reasons": list(dict.fromkeys(warning_reasons)),
        "execution_filter_stats": dict(execution_filter_stats),
        "security_master": symbol_resolution_payload,
        "asset_validation": asset_validation,
        "per_trade_diagnostics": {
            "execution_filter": filter_diagnostics,
            "retained": [
                *retained_filter_diagnostics,
                *expected_orders,
            ],
            "skipped": skipped_orders,
            "budget_skipped_orders": _budget_skipped_order_diagnostics(
                budget_skipped_orders
            ),
        },
        "expected_submission_orders": expected_orders,
    }

    artifact = _json_safe(artifact)
    artifact["artifact_path"] = str(artifact_path)
    if write_artifact:
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_text(
            json.dumps(artifact, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return artifact


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Certify planned execution readiness without submitting broker orders."
    )
    parser.add_argument("--trade-date", default=None)
    parser.add_argument("--planned-payload-path", default=None)
    parser.add_argument("--output-path", default=None)
    parser.add_argument("--mode", choices=("paper", "live", "pilot"), default=os.getenv("TRADING_MODE", "paper"))
    parser.add_argument(
        "--no-submit",
        action="store_true",
        help="Required safety flag. The certifier never submits orders.",
    )
    parser.add_argument("--account-snapshot-path", default=None)
    parser.add_argument(
        "--no-write",
        action="store_true",
        help="Run the certification without writing the JSON artifact.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    result = certify_execution_readiness(
        trade_date=args.trade_date,
        planned_payload_path=args.planned_payload_path,
        output_path=args.output_path,
        mode=args.mode,
        no_submit=bool(args.no_submit),
        write_artifact=not bool(args.no_write),
        account_snapshot_path=args.account_snapshot_path,
        repo_root=_REPO_ROOT,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 1 if result.get("readiness_status") == READINESS_FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
