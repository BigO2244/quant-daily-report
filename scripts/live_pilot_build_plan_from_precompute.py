from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.live_pilot_guardrails import validate_live_pilot_plan
from paper.run_manager import safe_write_text


DEFAULT_CAPITAL_CAP = 100.0
DEFAULT_MAX_ORDERS = 1
DEFAULT_OUTPUT_DIR = Path("outputs/live_pilot/plans")
DEFAULT_PRECOMPUTE_ROOT = Path("outputs/precompute")


def _now_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_text(path: Path, text: str) -> None:
    safe_write_text(path, text, allow_overwrite=True)


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    _write_text(path, json.dumps(dict(payload), indent=2, sort_keys=True, default=str) + "\n")


def _safe_float(value: object) -> float | None:
    try:
        numeric = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return numeric


def _safe_positive_float(value: object) -> float | None:
    numeric = _safe_float(value)
    return numeric if numeric is not None and numeric > 0 else None


def _clean_symbol(value: object) -> str:
    return str(value or "").strip().upper()


def _first_nonempty(row: Mapping[str, Any], keys: Iterable[str]) -> Any:
    for key in keys:
        value = row.get(key)
        if value is not None and str(value).strip() != "":
            return value
    return None


def _extract_trades(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    trades = payload.get("trades") or payload.get("orders") or payload.get("planned_trades") or []
    if not isinstance(trades, list):
        raise ValueError("planned execution payload trades/orders must be a list")
    return [trade for trade in trades if isinstance(trade, Mapping)]


def _trade_sleeve(trade: Mapping[str, Any], payload: Mapping[str, Any]) -> str:
    value = _first_nonempty(
        trade,
        ("sleeve", "sleeve_id", "approved_sleeve", "strategy", "strategy_id", "strategy_name"),
    )
    if value is None:
        value = _first_nonempty(
            payload,
            ("sleeve", "sleeve_id", "approved_sleeve", "strategy", "strategy_id", "strategy_name"),
        )
    return str(value or "").strip()


def _limit_price(trade: Mapping[str, Any]) -> tuple[float | None, str | None]:
    for key in ("limit_price", "price", "entry_price"):
        value = _safe_positive_float(trade.get(key))
        if value is not None:
            return value, key
    return None, None


def _shares(trade: Mapping[str, Any]) -> float | None:
    return _safe_positive_float(_first_nonempty(trade, ("shares", "qty", "quantity")))


def _asset_class(trade: Mapping[str, Any]) -> str:
    return str(trade.get("asset_class") or trade.get("class") or "us_equity").strip().lower()


def _reject(
    trade: Mapping[str, Any],
    *,
    index: int,
    reasons: list[str],
) -> dict[str, Any]:
    return {
        "index": index,
        "ticker": _clean_symbol(trade.get("ticker") or trade.get("symbol")),
        "side": str(trade.get("side") or "").strip().upper(),
        "sleeve": str(trade.get("sleeve") or trade.get("sleeve_id") or trade.get("strategy") or "").strip(),
        "notional": _safe_float(trade.get("notional")),
        "reasons": list(reasons),
        "source_trade": dict(trade),
    }


def _candidate_order(
    trade: Mapping[str, Any],
    *,
    index: int,
    payload: Mapping[str, Any],
    approved_sleeve: str,
    capital_cap: float,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    reasons: list[str] = []
    ticker = _clean_symbol(trade.get("ticker") or trade.get("symbol"))
    side = str(trade.get("side") or "").strip().upper()
    sleeve = _trade_sleeve(trade, payload)
    shares = _shares(trade)
    limit_price, limit_price_source = _limit_price(trade)
    asset_class = _asset_class(trade)

    if not ticker:
        reasons.append("missing_ticker")
    elif not ticker.replace(".", "").isalpha():
        reasons.append("unsupported_symbol_format")
    elif ticker.endswith("USD") and len(ticker) > 4:
        reasons.append("unsupported_crypto_symbol")
    if not sleeve:
        reasons.append("missing_sleeve")
    elif sleeve != approved_sleeve:
        reasons.append(f"sleeve_mismatch:{sleeve}")
    if side != "BUY":
        reasons.append(f"unsupported_side:{side or 'missing'}")
    if asset_class not in {"us_equity", "equity", "assetclass.us_equity"}:
        reasons.append(f"unsupported_asset_class:{asset_class or 'missing'}")
    if shares is None:
        reasons.append("missing_positive_shares")
    if limit_price is None:
        reasons.append("missing_limit_price")

    notional = None
    if shares is not None and limit_price is not None:
        notional = round(float(shares) * float(limit_price), 6)
        if notional > float(capital_cap):
            reasons.append("notional_exceeds_cap")

    if reasons:
        return None, _reject(trade, index=index, reasons=reasons)

    order = {
        "ticker": ticker,
        "symbol": ticker,
        "side": "BUY",
        "shares": float(shares or 0.0),
        "qty": float(shares or 0.0),
        "limit_price": float(limit_price or 0.0),
        "notional": float(notional or 0.0),
        "order_type": "limit",
        "sleeve": sleeve,
        "source_precompute_index": index,
        "source_reason": trade.get("reason"),
        "limit_price_source": limit_price_source,
    }
    return order, None


def latest_precompute_payload_path(precompute_root: Path = DEFAULT_PRECOMPUTE_ROOT) -> Path:
    candidates = sorted(precompute_root.glob("*/planned_execution_payload.json"))
    if not candidates:
        raise FileNotFoundError(f"No planned_execution_payload.json found under {precompute_root}")
    return candidates[-1]


def precompute_payload_path_for_date(trade_date: str, precompute_root: Path = DEFAULT_PRECOMPUTE_ROOT) -> Path:
    path = precompute_root / str(trade_date) / "planned_execution_payload.json"
    if not path.exists():
        raise FileNotFoundError(f"Missing planned execution payload: {path}")
    return path


def build_live_pilot_plan(
    *,
    payload_path: Path,
    approved_sleeve: str,
    capital_cap: float = DEFAULT_CAPITAL_CAP,
    max_orders: int = DEFAULT_MAX_ORDERS,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
) -> dict[str, Any]:
    if not str(approved_sleeve or "").strip():
        raise ValueError("approved_sleeve is required")
    if float(capital_cap) <= 0 or float(capital_cap) > DEFAULT_CAPITAL_CAP:
        raise ValueError("capital_cap must be > 0 and <= 100")
    if int(max_orders) != 1:
        raise ValueError("FR-104 Phase 1 requires max_orders=1")

    payload = _read_json(payload_path)
    if not isinstance(payload, Mapping):
        raise ValueError("planned execution payload must be a JSON object")
    trade_date = str(payload.get("trade_date") or payload_path.parent.name)
    trades = _extract_trades(payload)

    candidates: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for index, trade in enumerate(trades):
        candidate, rejection = _candidate_order(
            trade,
            index=index,
            payload=payload,
            approved_sleeve=approved_sleeve,
            capital_cap=float(capital_cap),
        )
        if candidate is not None:
            candidates.append(candidate)
        if rejection is not None:
            rejected.append(rejection)

    candidates = sorted(candidates, key=lambda row: (float(row.get("notional") or 0.0), str(row.get("ticker") or "")))
    selected = candidates[:1]
    for extra in candidates[1:]:
        rejected.append(
            {
                "index": extra.get("source_precompute_index"),
                "ticker": extra.get("ticker"),
                "side": extra.get("side"),
                "sleeve": extra.get("sleeve"),
                "notional": extra.get("notional"),
                "reasons": ["max_one_order_selected"],
                "source_trade": extra,
            }
        )

    status = "READY_FOR_MANUAL_APPROVAL" if selected else "BLOCKED_NO_QUALIFYING_ORDER"
    reason_code = "selected_one_order" if selected else "no_qualifying_live_pilot_order"
    plan_path = output_dir / f"live_pilot_plan_{trade_date}.json"
    md_path = output_dir / f"live_pilot_plan_{trade_date}.md"
    dry_run_command = (
        "TRADING_MODE=live_pilot ALPACA_PAPER=0 ALPACA_BASE_URL=https://api.alpaca.markets "
        f"CAERUS_LIVE_PILOT_APPROVED=1 CAERUS_LIVE_PILOT_CAPITAL_CAP={float(capital_cap):g} "
        f"CAERUS_LIVE_PILOT_SLEEVE_ID={approved_sleeve} "
        "CAERUS_LIVE_PILOT_ACCOUNT_ID_HASH=<SHA256_ACCOUNT_ID> "
        f"CAERUS_LIVE_PILOT_MAX_ORDERS={int(max_orders)} CAERUS_LIVE_PILOT_DRY_RUN=1 "
        f".venv/bin/python3 scripts/live_pilot_execute.py --plan {plan_path.as_posix()}"
    )
    live_command = dry_run_command.replace("CAERUS_LIVE_PILOT_DRY_RUN=1", "CAERUS_LIVE_PILOT_DRY_RUN=0")

    plan = {
        "schema_version": "live_pilot_plan_from_precompute.v1",
        "generated_at": _now_utc(),
        "status": status,
        "reason_code": reason_code,
        "source_precompute_payload": str(payload_path),
        "trade_date": trade_date,
        "approved_sleeve": approved_sleeve,
        "capital_cap": float(capital_cap),
        "max_orders": int(max_orders),
        "selected_order": selected[0] if selected else None,
        "rejected_orders_with_reasons": rejected,
        "required_dry_run_command": dry_run_command,
        "required_live_command": live_command,
        "operator_confirmation": {
            "approved_sleeve": approved_sleeve,
            "capital_cap": float(capital_cap),
            "selected_order": selected[0] if selected else None,
            "required_manual_review": True,
            "orders_submitted": 0,
        },
        "trades": selected,
    }

    # Prove the emitted plan is compatible with scripts/live_pilot_execute.py's
    # live-pilot validation schema before writing it.
    if selected:
        validation = validate_live_pilot_plan(
            selected,
            capital_cap_usd=float(capital_cap),
            max_orders=int(max_orders),
            run_id=f"plan-{trade_date}",
        )
        if validation.status != "PASS":
            raise RuntimeError(f"emitted_live_pilot_plan_failed_validation:{validation.reason_codes}")

    output_dir.mkdir(parents=True, exist_ok=True)
    _write_json(plan_path, plan)
    _write_text(md_path, render_markdown(plan, json_path=plan_path))
    plan["json_path"] = str(plan_path)
    plan["markdown_path"] = str(md_path)
    return plan


def render_markdown(plan: Mapping[str, Any], *, json_path: Path) -> str:
    selected = plan.get("selected_order")
    rejected = plan.get("rejected_orders_with_reasons") or []
    lines = [
        "# LIVE_PILOT Plan From Precompute",
        "",
        f"Status: `{plan.get('status')}`",
        f"Trade Date: `{plan.get('trade_date')}`",
        f"Approved Sleeve: `{plan.get('approved_sleeve')}`",
        f"Capital Cap: `${float(plan.get('capital_cap') or 0.0):.2f}`",
        f"JSON Plan: `{json_path.as_posix()}`",
        "",
        "## Selected Order",
        "",
    ]
    if selected:
        lines.extend(
            [
                f"- Ticker: `{selected.get('ticker')}`",
                f"- Side: `{selected.get('side')}`",
                f"- Shares: `{selected.get('shares')}`",
                f"- Limit Price: `{selected.get('limit_price')}`",
                f"- Notional: `${float(selected.get('notional') or 0.0):.2f}`",
            ]
        )
    else:
        lines.append("No qualifying order selected.")
    lines.extend(
        [
            "",
            "## Rejected Orders",
            "",
        ]
    )
    if rejected:
        for row in rejected:
            reasons = ", ".join(str(reason) for reason in row.get("reasons") or [])
            lines.append(f"- `{row.get('ticker') or 'UNKNOWN'}`: {reasons}")
    else:
        lines.append("None.")
    lines.extend(
        [
            "",
            "## Required Dry-Run Command",
            "",
            "```bash",
            str(plan.get("required_dry_run_command") or ""),
            "```",
            "",
            "## Required Live Command - Not Executed",
            "",
            "```bash",
            str(plan.get("required_live_command") or ""),
            "```",
            "",
            "## Operator Confirmation",
            "",
            "- Confirm the sleeve, cap, account hash, selected order, and dry-run artifact before any live attempt.",
            "- This builder does not submit orders.",
        ]
    )
    return "\n".join(lines) + "\n"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a one-order LIVE_PILOT plan from precompute output")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--payload-path", default=None, help="Explicit planned_execution_payload.json path")
    source.add_argument("--trade-date", default=None, help="Trade date under outputs/precompute/<DATE>")
    parser.add_argument("--precompute-root", default=str(DEFAULT_PRECOMPUTE_ROOT))
    parser.add_argument("--approved-sleeve", required=True)
    parser.add_argument("--capital-cap", type=float, default=DEFAULT_CAPITAL_CAP)
    parser.add_argument("--max-orders", type=int, default=DEFAULT_MAX_ORDERS)
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    precompute_root = Path(args.precompute_root)
    if args.payload_path:
        payload_path = Path(args.payload_path)
    elif args.trade_date:
        payload_path = precompute_payload_path_for_date(args.trade_date, precompute_root)
    else:
        payload_path = latest_precompute_payload_path(precompute_root)

    plan = build_live_pilot_plan(
        payload_path=payload_path,
        approved_sleeve=str(args.approved_sleeve),
        capital_cap=float(args.capital_cap),
        max_orders=int(args.max_orders),
        output_dir=Path(args.output_dir),
    )
    print(json.dumps({"status": plan.get("status"), "json_path": plan.get("json_path"), "markdown_path": plan.get("markdown_path")}, indent=2, sort_keys=True))
    return 0 if plan.get("selected_order") else 1


if __name__ == "__main__":
    raise SystemExit(main())
