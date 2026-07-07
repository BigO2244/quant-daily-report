from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.live_trade_ledger import live_order_record, live_trade_ledger_path


TERMINAL_REJECTED = {"rejected", "canceled", "cancelled", "failed"}


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def _orders_from(path: Path) -> list[dict[str, Any]]:
    payload = _load_json(path)
    rows = payload.get("orders") if isinstance(payload, Mapping) else payload
    if not isinstance(rows, list):
        return []
    return [dict(row) for row in rows if isinstance(row, Mapping)]


def _status_norm(status: Any) -> str:
    value = str(status or "").strip().lower()
    if "." in value:
        value = value.rsplit(".", 1)[-1]
    return value


def _outcome_event(status: Any) -> str | None:
    value = _status_norm(status)
    if value == "expired":
        return "expired"
    if value == "filled":
        return "filled"
    if value in TERMINAL_REJECTED:
        return "rejected"
    return None


def _first_value(*values: Any) -> Any:
    for value in values:
        if value not in (None, ""):
            return value
    return None


def _order_payload(row: Mapping[str, Any]) -> Mapping[str, Any]:
    order = row.get("order")
    return order if isinstance(order, Mapping) else {}


def _row_status(row: Mapping[str, Any]) -> Any:
    order = _order_payload(row)
    return _first_value(row.get("status"), order.get("status"))


def _row_client_order_id(row: Mapping[str, Any]) -> Any:
    order = _order_payload(row)
    return _first_value(order.get("client_order_id"), row.get("client_order_id"))


def _row_broker_order_id(row: Mapping[str, Any]) -> Any:
    order = _order_payload(row)
    return _first_value(order.get("id"), row.get("broker_order_id"), row.get("order_id"))


def _row_filled_qty(row: Mapping[str, Any], *, event: str) -> Any:
    order = _order_payload(row)
    filled_qty = _first_value(
        row.get("filled_qty"),
        row.get("filled_quantity"),
        order.get("filled_qty"),
        order.get("filled_quantity"),
    )
    if filled_qty not in (None, ""):
        return filled_qty
    return _row_qty(row) if event == "filled" else None


def _row_qty(row: Mapping[str, Any]) -> Any:
    order = _order_payload(row)
    return _first_value(row.get("qty"), row.get("shares"), order.get("qty"))


def _row_notional(row: Mapping[str, Any]) -> Any:
    notional = _first_value(row.get("notional"), row.get("submitted_notional"))
    if notional not in (None, ""):
        return notional
    qty = _to_float(_row_qty(row))
    price = _to_float(_row_limit_price(row))
    if qty is None or price is None:
        return None
    return round(qty * price, 6)


def _row_limit_price(row: Mapping[str, Any]) -> Any:
    return _first_value(row.get("submitted_price"), row.get("limit_price"), row.get("price"))


def _to_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _event_ts(row: Mapping[str, Any], *, run_root: Path, event: str) -> str | None:
    order = _order_payload(row)
    if event == "filled":
        ts = _first_value(row.get("filled_at"), order.get("filled_at"))
        if ts:
            return str(ts)
    if event == "submitted":
        ts = _first_value(row.get("submitted_at"), order.get("submitted_at"))
        if ts:
            return str(ts)
    summary = _load_json(run_root / "live_pilot_operator_summary.json")
    if isinstance(summary, Mapping):
        ts = summary.get("generated_at")
        if ts:
            return str(ts)
    preflight = _load_json(run_root / "live_pilot_preflight.json")
    if isinstance(preflight, Mapping):
        ts = preflight.get("generated_at")
        if ts:
            return str(ts)
    return None


def _event_reason(row: Mapping[str, Any], *, event: str) -> Any:
    if event not in {"rejected", "expired"}:
        return None
    return _first_value(row.get("error"), row.get("reason"), _row_status(row))


def _record_from_row(*, run_root: Path, row: Mapping[str, Any], event: str) -> dict[str, Any]:
    order = _order_payload(row)
    return live_order_record(
        event=event,
        symbol=_first_value(row.get("symbol"), row.get("ticker"), order.get("symbol")),
        side=_first_value(row.get("side"), order.get("side")),
        qty=_row_qty(row),
        filled_qty=_row_filled_qty(row, event=event),
        limit_price=_row_limit_price(row),
        notional=_row_notional(row),
        client_order_id=_row_client_order_id(row),
        broker_order_id=_row_broker_order_id(row),
        run_root=run_root,
        status=_row_status(row),
        reason=_event_reason(row, event=event),
        ts_utc=_event_ts(row, run_root=run_root, event=event),
    )


def _blocked_record(run_root: Path) -> dict[str, Any] | None:
    submitted = _orders_from(run_root / "live_pilot_orders_submitted.json")
    if submitted:
        return None
    preflight = _load_json(run_root / "live_pilot_preflight.json")
    if isinstance(preflight, Mapping) and preflight.get("dry_run") is True:
        return None
    summary = _load_json(run_root / "live_pilot_operator_summary.json")
    if not isinstance(summary, Mapping) or str(summary.get("terminal_status") or "").upper() != "BLOCKED":
        return None
    reconciliation = _load_json(run_root / "live_pilot_reconciliation.json")
    capital_gate = _load_json(run_root / "live_pilot_capital_gate.json")
    transition = _load_json(run_root / "live_pilot_transition_plan.json")
    intended = _orders_from(run_root / "live_pilot_orders_intended.json")
    diagnostics = transition.get("diagnostics") if isinstance(transition, Mapping) else {}
    diagnostics = diagnostics if isinstance(diagnostics, Mapping) else {}
    row: dict[str, Any] = dict(intended[0]) if intended else {}
    row.setdefault("symbol", diagnostics.get("over_cap_symbol"))
    row.setdefault("side", "BUY")
    row.setdefault("qty", diagnostics.get("incremental_need_shares") or diagnostics.get("planned_buy_shares"))
    row.setdefault("notional", diagnostics.get("planned_buy_notional"))
    reason = _first_value(
        summary.get("reason_code"),
        reconciliation.get("buy_block_reason") if isinstance(reconciliation, Mapping) else None,
        reconciliation.get("block_reason") if isinstance(reconciliation, Mapping) else None,
        capital_gate.get("buy_block_reason") if isinstance(capital_gate, Mapping) else None,
        capital_gate.get("block_reason") if isinstance(capital_gate, Mapping) else None,
    )
    if not reason:
        return None
    row["status"] = "REJECTED"
    row["reason"] = reason
    return live_order_record(
        event="rejected",
        symbol=_first_value(row.get("symbol"), row.get("ticker")),
        side=row.get("side"),
        qty=row.get("qty"),
        filled_qty=None,
        limit_price=_row_limit_price(row),
        notional=_row_notional(row),
        client_order_id=row.get("client_order_id"),
        broker_order_id=None,
        run_root=run_root,
        status="REJECTED",
        reason=reason,
        ts_utc=_event_ts(row, run_root=run_root, event="rejected"),
    )


def reconstruct_records(output_root: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    runs_root = output_root / "runs"
    for run_root in sorted(path for path in runs_root.glob("*") if path.is_dir()):
        for row in _orders_from(run_root / "live_pilot_orders_submitted.json"):
            if _status_norm(_row_status(row)) == "dry_run_not_submitted":
                continue
            records.append(_record_from_row(run_root=run_root, row=row, event="submitted"))
            event = _outcome_event(_row_status(row))
            if event is not None:
                records.append(_record_from_row(run_root=run_root, row=row, event=event))
        blocked = _blocked_record(run_root)
        if blocked is not None:
            records.append(blocked)
    return sorted(records, key=lambda item: (str(item.get("ts_utc") or ""), str(item.get("run_root") or ""), str(item.get("event") or "")))


def _write_jsonl(path: Path, records: Sequence[Mapping[str, Any]], *, append_existing: bool) -> None:
    if path.exists() and path.stat().st_size > 0 and not append_existing:
        raise FileExistsError(f"ledger already exists and is non-empty: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(dict(record), sort_keys=True, separators=(",", ":")) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def _summary(records: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    rows = list(records)
    client_ids = sorted(
        {
            str(row.get("client_order_id"))
            for row in rows
            if str(row.get("client_order_id") or "").strip()
        }
    )
    broker_client_ids = sorted(
        {
            str(row.get("client_order_id"))
            for row in rows
            if str(row.get("client_order_id") or "").strip() and str(row.get("broker_order_id") or "").strip()
        }
    )
    return {
        "record_count": len(rows),
        "event_counts": dict(Counter(str(row.get("event") or "") for row in rows)),
        "client_order_ids": client_ids,
        "broker_order_client_order_ids": broker_client_ids,
        "broker_order_client_order_id_count": len(broker_client_ids),
    }


def _alpaca_client_order_ids(path: Path) -> list[str]:
    payload = _load_json(path)
    if isinstance(payload, Mapping):
        rows = payload.get("orders") or payload.get("data") or []
    else:
        rows = payload
    if not isinstance(rows, list):
        raise ValueError(f"Alpaca orders export must be a JSON list or object with orders/data: {path}")
    client_ids = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        client_id = str(row.get("client_order_id") or "").strip()
        if client_id:
            client_ids.append(client_id)
    return sorted(set(client_ids))


def _alpaca_cross_check(records: Sequence[Mapping[str, Any]], alpaca_orders_json: Path) -> dict[str, Any]:
    alpaca_client_ids = _alpaca_client_order_ids(alpaca_orders_json)
    ledger_client_ids = sorted(
        {
            str(row.get("client_order_id"))
            for row in records
            if str(row.get("client_order_id") or "").strip() and str(row.get("broker_order_id") or "").strip()
        }
    )
    return {
        "alpaca_order_count": len(alpaca_client_ids),
        "ledger_broker_order_count": len(ledger_client_ids),
        "client_order_ids_match": alpaca_client_ids == ledger_client_ids,
        "missing_from_ledger": sorted(set(alpaca_client_ids) - set(ledger_client_ids)),
        "extra_in_ledger": sorted(set(ledger_client_ids) - set(alpaca_client_ids)),
    }


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill the append-only live-pilot trade ledger from run artifacts.")
    parser.add_argument("--output-root", default=None, help="Live-pilot output root; defaults to CAERUS_LIVE_PILOT_OUTPUT_ROOT or outputs/live_pilot.")
    parser.add_argument("--ledger-path", default=None, help="Explicit ledger JSONL path.")
    parser.add_argument("--alpaca-orders-json", default=None, help="Optional Alpaca order-history JSON export for client_order_id cross-check.")
    parser.add_argument("--dry-run", action="store_true", help="Print reconstructed records and summary without writing.")
    parser.add_argument("--append-existing", action="store_true", help="Append even if the target ledger already contains records.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    output_root = Path(args.output_root) if args.output_root else live_trade_ledger_path().parent
    ledger = Path(args.ledger_path) if args.ledger_path else live_trade_ledger_path(output_root=output_root, env={})
    records = reconstruct_records(output_root)
    summary = _summary(records)
    payload = {"ledger_path": str(ledger), "output_root": str(output_root), **summary}
    if args.alpaca_orders_json:
        payload["alpaca_cross_check"] = _alpaca_cross_check(records, Path(args.alpaca_orders_json))
    if args.dry_run:
        print(json.dumps({"summary": payload, "records": records}, indent=2, sort_keys=True))
        return 0
    _write_jsonl(ledger, records, append_existing=bool(args.append_existing))
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
