from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from decimal import Decimal, ROUND_DOWN
from pathlib import Path
from typing import Any, Iterable, Mapping

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.live_pilot_guardrails import normalize_live_pilot_limit_price, validate_live_pilot_plan
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


def _payload_strategy_identity(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    value = payload.get("strategy_identity")
    return value if isinstance(value, Mapping) else {}


def _trade_sleeve_provenance(
    trade: Mapping[str, Any],
    payload: Mapping[str, Any],
    source_signal: Mapping[str, Any] | None,
) -> tuple[str, dict[str, Any]]:
    value = _first_nonempty(
        trade,
        ("sleeve", "sleeve_id", "approved_sleeve", "strategy", "strategy_id", "strategy_name"),
    )
    source = "source_trade"
    if value is None:
        value = _first_nonempty(
            payload,
            ("sleeve", "sleeve_id", "approved_sleeve", "strategy", "strategy_id", "strategy_name"),
        )
        source = "source_payload"
    strategy_identity = _payload_strategy_identity(payload)
    if value is None:
        value = _first_nonempty(
            payload,
            ("live_strategy_id",),
        )
        source = "precompute_live_strategy_id"
    if value is None:
        value = _first_nonempty(
            strategy_identity,
            ("live_strategy_id", "strategy_id", "strategy", "strategy_name"),
        )
        source = "precompute_strategy_identity"
    if value is None and source_signal is not None:
        value = _first_nonempty(
            source_signal,
            ("sleeve", "sleeve_id", "strategy", "strategy_id", "strategy_name"),
        )
        source = "precompute_signal_ticker_match"

    signal_sleeve = None
    signal_target_weight = None
    signal_raw_score = None
    if source_signal is not None:
        signal_sleeve = _first_nonempty(
            source_signal,
            ("sleeve", "sleeve_id", "strategy", "strategy_id", "strategy_name"),
        )
        signal_target_weight = source_signal.get("target_weight")
        signal_raw_score = source_signal.get("raw_score")

    sleeve = str(value or "").strip()
    return sleeve, {
        "sleeve_source": source if sleeve else None,
        "source_strategy_id": str(
            payload.get("live_strategy_id")
            or strategy_identity.get("live_strategy_id")
            or strategy_identity.get("strategy_id")
            or ""
        ).strip() or None,
        "source_strategy_identity": dict(strategy_identity) if strategy_identity else None,
        "source_signal_sleeve": str(signal_sleeve or "").strip() or None,
        "source_signal_target_weight": signal_target_weight,
        "source_signal_raw_score": signal_raw_score,
        "missing_source_trade_sleeve_recovered": value is not None
        and _first_nonempty(
            trade,
            ("sleeve", "sleeve_id", "approved_sleeve", "strategy", "strategy_id", "strategy_name"),
        )
        is None,
    }


def _resolve_payload_relative_path(payload_path: Path, raw_path: object) -> Path | None:
    raw = str(raw_path or "").strip()
    if not raw:
        return None
    candidate = Path(raw)
    if candidate.is_absolute():
        return candidate
    candidates = [candidate]
    parts = list(payload_path.parts)
    try:
        outputs_index = parts.index("outputs")
    except ValueError:
        outputs_index = -1
    if outputs_index > 0:
        repo_root = Path(*parts[:outputs_index])
        candidates.append(repo_root / candidate)
    candidates.append(payload_path.parent / candidate.name)
    for path in candidates:
        if path.exists():
            return path
    return candidates[0]


def _source_signal_lookup(payload_path: Path, payload: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    path = _resolve_payload_relative_path(payload_path, payload.get("execution_target_source"))
    if path is None or not path.exists():
        return {}
    try:
        source_payload = _read_json(path)
    except Exception:
        return {}
    if not isinstance(source_payload, Mapping):
        return {}
    signals = source_payload.get("signals")
    if not isinstance(signals, list):
        return {}
    out: dict[str, Mapping[str, Any]] = {}
    for raw in signals:
        if not isinstance(raw, Mapping):
            continue
        ticker = _clean_symbol(raw.get("ticker") or raw.get("symbol"))
        if ticker and ticker not in out:
            out[ticker] = raw
    return out


def _limit_price(trade: Mapping[str, Any]) -> tuple[float | None, str | None]:
    for key in ("limit_price", "price", "entry_price"):
        value = _safe_positive_float(trade.get(key))
        if value is not None:
            return value, key
    return None, None


def _shares(trade: Mapping[str, Any]) -> float | None:
    return _safe_positive_float(_first_nonempty(trade, ("shares", "qty", "quantity")))


def _floor_qty_for_cap(qty: float, *, cap: float, limit_price: float) -> float:
    candidate = min(Decimal(str(qty)), Decimal(str(cap)) / Decimal(str(limit_price)))
    return float(candidate.quantize(Decimal("0.000000001"), rounding=ROUND_DOWN))


def _asset_class(trade: Mapping[str, Any]) -> str:
    return str(trade.get("asset_class") or trade.get("class") or "us_equity").strip().lower()


def _reject(
    trade: Mapping[str, Any],
    *,
    index: int,
    reasons: list[str],
    sleeve: str | None = None,
    sleeve_provenance: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "index": index,
        "ticker": _clean_symbol(trade.get("ticker") or trade.get("symbol")),
        "side": str(trade.get("side") or "").strip().upper(),
        "sleeve": str(
            sleeve
            if sleeve is not None
            else trade.get("sleeve")
            or trade.get("sleeve_id")
            or trade.get("strategy")
            or ""
        ).strip(),
        "sleeve_provenance": dict(sleeve_provenance or {}),
        "notional": _safe_float(trade.get("notional")),
        "reasons": list(reasons),
        "source_trade": dict(trade),
    }


def _candidate_order(
    trade: Mapping[str, Any],
    *,
    index: int,
    payload: Mapping[str, Any],
    source_signal: Mapping[str, Any] | None,
    approved_sleeve: str,
    capital_cap: float,
    allow_missing_sleeve: bool,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    reasons: list[str] = []
    ticker = _clean_symbol(trade.get("ticker") or trade.get("symbol"))
    side = str(trade.get("side") or "").strip().upper()
    sleeve, sleeve_provenance = _trade_sleeve_provenance(trade, payload, source_signal)
    shares = _shares(trade)
    limit_price, limit_price_source = _limit_price(trade)
    asset_class = _asset_class(trade)
    missing_sleeve_overridden = False

    if not ticker:
        reasons.append("missing_ticker")
    elif not ticker.replace(".", "").isalpha():
        reasons.append("unsupported_symbol_format")
    elif ticker.endswith("USD") and len(ticker) > 4:
        reasons.append("unsupported_crypto_symbol")
    if not sleeve:
        if allow_missing_sleeve and side == "BUY":
            sleeve = approved_sleeve
            missing_sleeve_overridden = True
            sleeve_provenance["sleeve_source"] = "missing_in_source_overridden_for_live_pilot"
            sleeve_provenance["approved_sleeve_override"] = approved_sleeve
        else:
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

    source_notional = None
    original_limit_price = limit_price
    normalized_limit_price = None
    pre_normalization_qty = shares
    final_qty = shares
    scaled_to_pilot_cap = False
    if shares is not None and limit_price is not None:
        normalized_limit_price = normalize_live_pilot_limit_price(float(limit_price))
        source_notional = round(float(shares) * float(limit_price), 6)
        pre_normalization_qty = _floor_qty_for_cap(float(shares), cap=float(capital_cap), limit_price=float(limit_price))
        final_qty = _floor_qty_for_cap(
            float(pre_normalization_qty),
            cap=float(capital_cap),
            limit_price=float(normalized_limit_price),
        )
        scaled_to_pilot_cap = abs(float(final_qty) - float(shares)) > 1e-12

    if reasons:
        return None, _reject(
            trade,
            index=index,
            reasons=reasons,
            sleeve=sleeve,
            sleeve_provenance=sleeve_provenance,
        )

    pilot_notional = float(Decimal(str(final_qty or 0.0)) * Decimal(str(normalized_limit_price or 0.0)))
    order = {
        "ticker": ticker,
        "symbol": ticker,
        "side": "BUY",
        "shares": float(final_qty or 0.0),
        "qty": float(final_qty or 0.0),
        "limit_price": float(normalized_limit_price or 0.0),
        "expected_price": float(normalized_limit_price or 0.0),
        "cap_enforcement_price": float(normalized_limit_price or 0.0),
        "original_limit_price": float(original_limit_price or 0.0),
        "normalized_limit_price": float(normalized_limit_price or 0.0),
        "notional": float(pilot_notional),
        "order_type": "market",
        "time_in_force": "day",
        "order_policy": "fr104_live_pilot_market_order_normal_hours_only",
        "sleeve": sleeve,
        "source_precompute_index": index,
        "source_reason": trade.get("reason"),
        "limit_price_source": limit_price_source,
        "scaled_to_pilot_cap": bool(scaled_to_pilot_cap),
        "source_notional": float(source_notional or 0.0),
        "pilot_notional_cap": float(capital_cap),
        "source_order_qty": float(shares or 0.0),
        "original_qty": float(shares or 0.0),
        "pre_normalization_qty": float(pre_normalization_qty or 0.0),
        "pilot_qty": float(final_qty or 0.0),
        "final_qty": float(final_qty or 0.0),
        "scale_reason": "live_pilot_cap" if scaled_to_pilot_cap else None,
        "sleeve_source": sleeve_provenance.get("sleeve_source") or "source",
        "sleeve_provenance": sleeve_provenance,
        "source_strategy_id": sleeve_provenance.get("source_strategy_id"),
        "source_signal_sleeve": sleeve_provenance.get("source_signal_sleeve"),
        "source_signal_target_weight": sleeve_provenance.get("source_signal_target_weight"),
        "source_signal_raw_score": sleeve_provenance.get("source_signal_raw_score"),
        "approved_sleeve_override": approved_sleeve if missing_sleeve_overridden else None,
    }
    for key in ("stop_loss", "take_profit"):
        if key in trade:
            order[key] = trade.get(key)
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
    allow_missing_sleeve: bool = False,
    allow_fractional: bool = False,
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
    signal_lookup = _source_signal_lookup(payload_path, payload)

    candidates: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for index, trade in enumerate(trades):
        candidate, rejection = _candidate_order(
            trade,
            index=index,
            payload=payload,
            source_signal=signal_lookup.get(_clean_symbol(trade.get("ticker") or trade.get("symbol"))),
            approved_sleeve=approved_sleeve,
            capital_cap=float(capital_cap),
            allow_missing_sleeve=bool(allow_missing_sleeve),
        )
        if candidate is not None:
            candidates.append(candidate)
        if rejection is not None:
            rejected.append(rejection)

    candidates = sorted(
        candidates,
        key=lambda row: (
            1 if row.get("sleeve_source") == "missing_in_source_overridden_for_live_pilot" else 0,
            int(row.get("source_precompute_index") or 0),
        ),
    )
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
        f"CAERUS_LIVE_PILOT_MAX_ORDERS={int(max_orders)} "
        f"CAERUS_LIVE_PILOT_ALLOW_MISSING_SLEEVE={1 if allow_missing_sleeve else 0} "
        f"CAERUS_LIVE_PILOT_ALLOW_FRACTIONAL={1 if allow_fractional else 0} CAERUS_LIVE_PILOT_DRY_RUN=1 "
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
        "allow_missing_sleeve": bool(allow_missing_sleeve),
        "allow_fractional": bool(allow_fractional),
        "order_policy": {
            "scope": "FR-104 LIVE_PILOT only",
            "order_type": "market",
            "time_in_force": "day",
            "normal_market_hours_only": True,
            "cap_enforced_before_submission": True,
            "cap_enforcement_price": "normalized expected price from source precompute",
            "duplicate_open_order_policy": "skip_if_open_live_pilot_order_detected",
            "paper_or_production_impact": "none",
        },
        "selected_order": selected[0] if selected else None,
        "rejected_orders_with_reasons": rejected,
        "required_dry_run_command": dry_run_command,
        "required_live_command": live_command,
        "operator_confirmation": {
            "approved_sleeve": approved_sleeve,
            "capital_cap": float(capital_cap),
            "selected_order": selected[0] if selected else None,
            "allow_missing_sleeve": bool(allow_missing_sleeve),
            "allow_fractional": bool(allow_fractional),
            "required_manual_review": True,
            "orders_submitted": 0,
        },
        "trades": selected,
    }

    # Prove the emitted plan is compatible with scripts/live_pilot_execute.py's
    # live-pilot validation schema before writing it.
    if selected:
        validation_env = dict(os.environ)
        if allow_fractional:
            validation_env["CAERUS_LIVE_PILOT_ALLOW_FRACTIONAL"] = "1"
        validation = validate_live_pilot_plan(
            selected,
            env=validation_env,
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
                f"- Order Type: `{selected.get('order_type')}`",
                f"- Expected/Cap Price: `{selected.get('expected_price') or selected.get('limit_price')}`",
                f"- Notional: `${float(selected.get('notional') or 0.0):.2f}`",
                f"- Sleeve: `{selected.get('sleeve')}`",
                f"- Sleeve Source: `{selected.get('sleeve_source')}`",
                f"- Source Signal Sleeve: `{selected.get('source_signal_sleeve') or 'n/a'}`",
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
    parser.add_argument(
        "--allow-missing-sleeve",
        action="store_true",
        default=os.getenv("CAERUS_LIVE_PILOT_ALLOW_MISSING_SLEEVE", "").strip().lower() in {"1", "true", "yes", "y", "on"},
        help="Allow missing source sleeve metadata only for isolated live pilot plan selection.",
    )
    parser.add_argument(
        "--allow-fractional",
        action="store_true",
        default=os.getenv("CAERUS_LIVE_PILOT_ALLOW_FRACTIONAL", "").strip().lower() in {"1", "true", "yes", "y", "on"},
        help="Allow fractional quantity only for isolated live pilot plan validation.",
    )
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
        allow_missing_sleeve=bool(args.allow_missing_sleeve),
        allow_fractional=bool(args.allow_fractional),
    )
    print(json.dumps({"status": plan.get("status"), "json_path": plan.get("json_path"), "markdown_path": plan.get("markdown_path")}, indent=2, sort_keys=True))
    return 0 if plan.get("selected_order") else 1


if __name__ == "__main__":
    raise SystemExit(main())
