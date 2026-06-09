from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from research.operational_drag import load_price_store


SCHEMA_VERSION = "caerus_target_attainment_v1"


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _safe_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        out = float(value)
    except Exception:
        return None
    if out != out:
        return None
    return out


def _round(value: Any, places: int = 6) -> float | None:
    numeric = _safe_float(value)
    return round(numeric, places) if numeric is not None else None


def _ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or abs(denominator) <= 1e-12:
        return None
    return numerator / denominator


def _dedupe(values: list[Any]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value)
        if text and text not in seen:
            out.append(text)
            seen.add(text)
    return out


def _run_dirs(repo: Path, trade_date: str) -> list[Path]:
    root = repo / "outputs" / "runs"
    if not root.exists():
        return []
    dirs = [path for path in root.glob(f"{trade_date}*") if path.is_dir()]
    return sorted(dirs, reverse=True)


def _select_run_dir(repo: Path, trade_date: str) -> Path | None:
    candidates = _run_dirs(repo, trade_date)
    if not candidates:
        return None
    preferred = [
        path
        for path in candidates
        if (path / "broker" / f"recon_posttrade_{trade_date}.json").exists()
        and (path / "execution_payload.json").exists()
    ]
    return preferred[0] if preferred else candidates[0]


def _order_symbol(order: dict[str, Any]) -> str:
    return str(order.get("ticker") or order.get("symbol") or "").upper().strip()


def _order_notional(order: dict[str, Any]) -> float:
    notional = _safe_float(order.get("notional"))
    if notional is not None:
        return abs(notional)
    shares = _safe_float(order.get("shares") or order.get("qty") or order.get("quantity"))
    price = _safe_float(order.get("price") or order.get("entry_price") or order.get("filled_avg_price"))
    if shares is None or price is None:
        return 0.0
    return abs(shares * price)


def _orders(payload: dict[str, Any] | None, key: str) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    rows = payload.get(key)
    if not isinstance(rows, list):
        rows = payload.get("trades")
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def _notional_by_side(rows: list[dict[str, Any]]) -> dict[str, float]:
    out = {"BUY": 0.0, "SELL": 0.0, "TOTAL": 0.0}
    for row in rows:
        side = str(row.get("side") or "").upper().strip()
        notional = _order_notional(row)
        out["TOTAL"] += notional
        if side in {"BUY", "SELL"}:
            out[side] += notional
    return {key: round(value, 6) for key, value in out.items()}


def _positions_from_rows(rows: list[dict[str, Any]], *, equity: float | None) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        symbol = str(row.get("symbol") or row.get("ticker") or "").upper().strip()
        if not symbol:
            continue
        market_value = _safe_float(row.get("market_value"))
        target_weight = _safe_float(row.get("target_weight"))
        actual_weight = _safe_float(row.get("actual_weight"))
        if market_value is not None and equity:
            weight = market_value / equity
        elif target_weight is not None:
            weight = target_weight
        elif actual_weight is not None:
            weight = actual_weight
        else:
            weight = None
        out[symbol] = {
            "symbol": symbol,
            "shares": _round(row.get("shares")),
            "price": _round(row.get("price")),
            "market_value": _round(market_value),
            "weight": _round(weight),
        }
    return out


def _positions_from_recon(
    repo: Path,
    trade_date: str,
    positions: dict[str, Any],
    *,
    equity: float | None,
    reason_codes: list[str],
) -> dict[str, dict[str, Any]]:
    price_store = load_price_store(repo, trade_date=trade_date)
    out: dict[str, dict[str, Any]] = {}
    for symbol, raw_qty in sorted(positions.items()):
        normalized = str(symbol).upper().strip()
        shares = _safe_float(raw_qty)
        if not normalized or shares is None:
            continue
        price = price_store.get(normalized, trade_date)
        market_value = shares * price if price is not None else None
        if price is None:
            reason_codes.append("missing_market_data")
        out[normalized] = {
            "symbol": normalized,
            "shares": _round(shares),
            "price": _round(price),
            "market_value": _round(market_value),
            "weight": _round(_ratio(market_value, equity)),
        }
    return out


def _concentration(positions: dict[str, dict[str, Any]]) -> dict[str, Any]:
    weights = sorted(
        [abs(float(row["weight"])) for row in positions.values() if row.get("weight") is not None],
        reverse=True,
    )
    if not weights:
        return {"max_weight": None, "top5_weight": None, "hhi": None, "position_count": len(positions)}
    return {
        "max_weight": round(weights[0], 6),
        "top5_weight": round(sum(weights[:5]), 6),
        "hhi": round(sum(weight * weight for weight in weights), 6),
        "position_count": len(weights),
    }


def _score_from_gap(gap: float | None, tolerance: float) -> float:
    if gap is None:
        return 0.0
    return max(0.0, min(1.0, 1.0 - abs(gap) / tolerance))


def _classify_symbol_drift(
    *,
    target_weight: float,
    actual_weight: float,
    execution_payload: dict[str, Any],
    intended_orders: list[dict[str, Any]],
    post_sell_rebudget: dict[str, Any] | None,
) -> list[str]:
    reasons: list[str] = []
    drift = actual_weight - target_weight
    if abs(drift) <= 0.0001:
        return ["ok"]
    buy_block = str(execution_payload.get("buy_phase_block_reason") or "").lower()
    sell_status = str(execution_payload.get("sell_phase_status") or "").upper()
    if "sell" in buy_block or sell_status in {"TIMEOUT", "PARTIAL", "REJECTED"}:
        reasons.append("sell_confirmation_constraint")
    capital_budget = next((row.get("capital_budget") for row in [execution_payload] if isinstance(row.get("capital_budget"), dict)), None)
    if capital_budget and capital_budget.get("capital_constraint_triggered"):
        reasons.append("capital_budget_constraint")
    if post_sell_rebudget and str(post_sell_rebudget.get("status") or "").upper() in {"SKIPPED", "BLOCKED", "PARTIAL"}:
        reasons.append("capital_budget_constraint")
    if "buying_power" in buy_block or "insufficient" in buy_block:
        reasons.append("buying_power_constraint")
    if any("min" in str(order.get("reason") or order.get("notes") or "").lower() for order in intended_orders):
        reasons.append("min_notional_filter")
    if drift < 0:
        reasons.append("target_underattained")
    else:
        reasons.append("target_overattained")
    return _dedupe(reasons)


def _source(path: Path, payload: dict[str, Any] | None, date: str) -> dict[str, Any]:
    return {
        "path": str(path),
        "exists": path.exists(),
        "source_date": (payload or {}).get("date") or (payload or {}).get("trade_date") or (payload or {}).get("report_date"),
        "freshness_status": (
            "FRESH"
            if path.exists() and str((payload or {}).get("date") or (payload or {}).get("trade_date") or (payload or {}).get("report_date") or date) == date
            else "MISSING" if not path.exists() else "DATE_UNKNOWN_OR_STALE"
        ),
    }


def build_target_attainment(
    *,
    trade_date: str,
    repo_root: Path | str = Path("."),
    output_root: Path | str | None = None,
    write: bool = True,
) -> dict[str, Any]:
    repo = Path(repo_root)
    out_root = Path(output_root) if output_root is not None else repo / "outputs" / "target_attainment"
    out_path = out_root / trade_date / f"target_attainment_{trade_date}.json"
    operational_dir = repo / "outputs" / "operational_drag" / trade_date
    intended_path = operational_dir / "intended_nav.json"
    actual_path = operational_dir / "actual_nav.json"
    intended_nav = _read_json(intended_path) or {}
    actual_nav = _read_json(actual_path) or {}
    run_dir = _select_run_dir(repo, trade_date)
    execution_path = run_dir / "execution_payload.json" if run_dir else repo / "outputs" / "execution_payload.json"
    intended_orders_path = (
        run_dir / "broker" / f"intended_orders_{trade_date}.json"
        if run_dir
        else repo / "outputs" / "broker" / f"intended_orders_{trade_date}.json"
    )
    recon_path = (
        run_dir / "broker" / f"recon_posttrade_{trade_date}.json"
        if run_dir
        else repo / "outputs" / "broker" / f"recon_posttrade_{trade_date}.json"
    )
    rebudget_path = (
        run_dir / "broker" / f"post_sell_rebudget_{trade_date}.json"
        if run_dir
        else repo / "outputs" / "broker" / f"post_sell_rebudget_{trade_date}.json"
    )
    execution_payload = _read_json(execution_path) or {}
    intended_orders_payload = _read_json(intended_orders_path) or {}
    recon = _read_json(recon_path) or {}
    post_sell_rebudget = _read_json(rebudget_path)
    reason_codes: list[str] = []
    if not intended_nav:
        reason_codes.append("missing_target_portfolio")
    if not actual_nav and not recon:
        reason_codes.append("missing_actual_portfolio")
    target_equity = _safe_float(intended_nav.get("intended_equity_value"))
    actual_equity = (
        _safe_float(actual_nav.get("actual_equity_value"))
        or _safe_float(recon.get("broker_equity"))
        or _safe_float((execution_payload.get("portfolio_state") or {}).get("equity"))
    )
    actual_cash = _safe_float(actual_nav.get("actual_cash")) or _safe_float(recon.get("broker_cash"))
    execution_cash_target = _safe_float(execution_payload.get("cash_target_weight"))
    target_cash_weight = (
        execution_cash_target
        if execution_cash_target is not None
        else _ratio(_safe_float(intended_nav.get("intended_cash")), target_equity)
    )
    target_gross = (
        _safe_float(execution_payload.get("gross_target_weight"))
        or _safe_float(execution_payload.get("target_gross_exposure"))
        or (1.0 - target_cash_weight if target_cash_weight is not None else None)
        or _safe_float(intended_nav.get("intended_gross_exposure"))
    )
    actual_cash_weight = _ratio(actual_cash, actual_equity)
    actual_gross = _safe_float(actual_nav.get("actual_gross_exposure"))
    if actual_gross is None and actual_cash_weight is not None:
        actual_gross = max(0.0, 1.0 - actual_cash_weight)
    target_positions = _positions_from_rows(
        intended_nav.get("intended_positions") if isinstance(intended_nav.get("intended_positions"), list) else [],
        equity=target_equity,
    )
    raw_target_gross = round(sum(abs(float(row.get("weight") or 0.0)) for row in target_positions.values()), 6)
    risk_adjustment_scale = 1.0
    if target_gross is not None and raw_target_gross > 0:
        risk_adjustment_scale = target_gross / raw_target_gross
    if abs(risk_adjustment_scale - 1.0) > 0.000001:
        reason_codes.append("risk_control_adjustment")
        for row in target_positions.values():
            raw_weight = _safe_float(row.get("weight"))
            row["raw_target_weight"] = _round(raw_weight)
            if raw_weight is not None:
                scaled_weight = raw_weight * risk_adjustment_scale
                row["weight"] = _round(scaled_weight)
                row["market_value"] = _round(
                    scaled_weight * actual_equity if actual_equity is not None else _safe_float(row.get("market_value")) * risk_adjustment_scale if _safe_float(row.get("market_value")) is not None else None,
                    2,
                )
    actual_position_reasons: list[str] = []
    actual_positions = _positions_from_rows(
        actual_nav.get("actual_positions") if isinstance(actual_nav.get("actual_positions"), list) else [],
        equity=actual_equity,
    )
    if actual_positions and all(row.get("market_value") is None for row in actual_positions.values()) and isinstance(recon.get("actual_positions"), dict):
        actual_positions = _positions_from_recon(
            repo,
            trade_date,
            recon["actual_positions"],
            equity=actual_equity,
            reason_codes=actual_position_reasons,
        )
    elif not actual_positions and isinstance(recon.get("actual_positions"), dict):
        actual_positions = _positions_from_recon(
            repo,
            trade_date,
            recon["actual_positions"],
            equity=actual_equity,
            reason_codes=actual_position_reasons,
        )
    reason_codes.extend(actual_position_reasons)
    intended_orders = _orders(intended_orders_payload, "orders_intended")
    executed_orders = _orders(execution_payload, "trades")
    intended_notional = _notional_by_side(intended_orders)
    executed_notional = _notional_by_side(executed_orders)
    symbols = sorted(set(target_positions) | set(actual_positions))
    drift_rows: list[dict[str, Any]] = []
    total_abs_weight_drift = 0.0
    for symbol in symbols:
        target = target_positions.get(symbol, {})
        actual = actual_positions.get(symbol, {})
        target_weight = float(target.get("weight") or 0.0)
        actual_weight = float(actual.get("weight") or 0.0)
        drift = actual_weight - target_weight
        total_abs_weight_drift += abs(drift)
        row_reasons = _classify_symbol_drift(
            target_weight=target_weight,
            actual_weight=actual_weight,
            execution_payload=execution_payload,
            intended_orders=intended_orders,
            post_sell_rebudget=post_sell_rebudget,
        )
        drift_rows.append(
            {
                "symbol": symbol,
                "target_weight": round(target_weight, 6),
                "actual_weight": round(actual_weight, 6),
                "weight_drift": round(drift, 6),
                "abs_weight_drift": round(abs(drift), 6),
                "target_shares": target.get("shares"),
                "actual_shares": actual.get("shares"),
                "target_market_value": target.get("market_value"),
                "actual_market_value": actual.get("market_value"),
                "reason_codes": row_reasons,
            }
        )
    drift_rows = sorted(drift_rows, key=lambda row: (-float(row["abs_weight_drift"]), str(row["symbol"])))
    target_concentration = _concentration(target_positions)
    actual_concentration = _concentration(actual_positions)
    cash_gap = (
        actual_cash_weight - target_cash_weight
        if actual_cash_weight is not None and target_cash_weight is not None
        else None
    )
    exposure_gap = target_gross - actual_gross if target_gross is not None and actual_gross is not None else None
    target_cash_dollars = (target_cash_weight * actual_equity) if target_cash_weight is not None and actual_equity is not None else None
    excess_cash = (
        max(0.0, actual_cash - target_cash_dollars)
        if actual_cash is not None and target_cash_dollars is not None
        else None
    )
    undeployed_capital = (
        max(0.0, exposure_gap * actual_equity)
        if exposure_gap is not None and actual_equity is not None
        else excess_cash
    )
    if cash_gap is not None and cash_gap > 0.0001:
        reason_codes.append("cash_above_target")
    if exposure_gap is not None and exposure_gap > 0.0001:
        reason_codes.append("exposure_below_target")
    if total_abs_weight_drift > 0.0001:
        reason_codes.append("target_weight_drift")
    if str(execution_payload.get("sell_phase_status") or "").upper() in {"TIMEOUT", "PARTIAL", "REJECTED"}:
        reason_codes.append("sell_confirmation_constraint")
    if str(execution_payload.get("buy_phase_block_reason") or "").lower():
        block_reason = str(execution_payload.get("buy_phase_block_reason") or "").lower()
        if "sell" in block_reason:
            reason_codes.append("sell_confirmation_constraint")
        if "buying_power" in block_reason or "insufficient" in block_reason:
            reason_codes.append("buying_power_constraint")
    capital_budget = intended_orders_payload.get("capital_budget") if isinstance(intended_orders_payload.get("capital_budget"), dict) else {}
    if capital_budget.get("capital_constraint_triggered") or _safe_float(capital_budget.get("clipped_or_deferred_buys_count")):
        reason_codes.append("capital_budget_constraint")
    if recon and str(recon.get("drift_status") or recon.get("comparison_status") or "").upper() not in {"OK_RECONCILED", "PASS"}:
        reason_codes.append("reconciliation_mismatch")
    if _safe_float(execution_payload.get("rejected_count")) and _safe_float(execution_payload.get("rejected_count")) > 0:
        reason_codes.append("execution_failure")
    if post_sell_rebudget and str(post_sell_rebudget.get("status") or "").upper() in {"REBUILT", "APPLIED", "OK"}:
        reason_codes.append("post_sell_rebudget_applied")
    reason_codes = sorted(set(code for code in reason_codes if code and code != "ok")) or ["ok"]
    execution_attainment = _ratio(executed_notional["TOTAL"], intended_notional["TOTAL"])
    active_share = total_abs_weight_drift / 2.0
    cash_score = _score_from_gap(cash_gap, 0.20)
    exposure_score = _score_from_gap(exposure_gap, 0.20)
    weight_score = max(0.0, min(1.0, 1.0 - active_share))
    if target_gross is not None and actual_gross is not None and target_gross > 0:
        deployment_score = round(100.0 * max(0.0, min(actual_gross / target_gross, 1.0)), 2)
    else:
        deployment_score = None
    attainment_score = round(100.0 * ((0.35 * cash_score) + (0.35 * exposure_score) + (0.30 * weight_score)), 2)
    if "missing_target_portfolio" in reason_codes or "missing_actual_portfolio" in reason_codes:
        confidence = "LOW"
    elif "missing_market_data" in reason_codes:
        confidence = "MEDIUM"
    else:
        confidence = "HIGH"
    payload = {
        "schema_version": SCHEMA_VERSION,
        "trade_date": trade_date,
        "generated_at": f"{trade_date}T00:00:00Z",
        "governance_label": "OBSERVABILITY_ONLY",
        "execution_impact": "NON_EXECUTIONAL",
        "run_id": run_dir.name if run_dir else None,
        "summary": {
            "target_cash_pct": _round(target_cash_weight),
            "actual_cash_pct": _round(actual_cash_weight),
            "cash_gap_pct": _round(cash_gap),
            "target_gross_exposure_pct": _round(target_gross),
            "actual_gross_exposure_pct": _round(actual_gross),
            "exposure_gap_pct": _round(exposure_gap),
            "deployment_efficiency_pct": _round(_ratio(actual_gross, target_gross)),
            "undeployed_capital": _round(undeployed_capital, 2),
            "excess_cash": _round(excess_cash, 2),
            "attainment_score": attainment_score,
            "deployment_score": deployment_score,
            "confidence": confidence,
        },
        "portfolio_chain": {
            "target_portfolio": {
                "gross_exposure_pct": _round(raw_target_gross),
                "cash_pct": _round(_ratio(_safe_float(intended_nav.get("intended_cash")), target_equity)),
                "source_path": str(intended_path),
            },
            "risk_adjusted_portfolio": {
                "gross_exposure_pct": _round(target_gross),
                "cash_pct": _round(target_cash_weight),
                "risk_adjustment_scale": _round(risk_adjustment_scale),
                "reason_codes": ["risk_control_adjustment"] if abs(risk_adjustment_scale - 1.0) > 0.000001 else ["ok"],
            },
            "intended_orders": {
                "order_count": len(intended_orders),
                "notional": intended_notional,
                "source_path": str(intended_orders_path),
            },
            "executed_orders": {
                "order_count": len(executed_orders),
                "notional": executed_notional,
                "source_path": str(execution_path),
            },
            "broker_holdings": {
                "position_count": len(actual_positions),
                "reconciliation_status": recon.get("drift_status") or recon.get("comparison_status"),
                "source_path": str(recon_path),
            },
            "actual_portfolio": {
                "gross_exposure_pct": _round(actual_gross),
                "cash_pct": _round(actual_cash_weight),
                "source_path": str(actual_path),
            },
        },
        "cash": {
            "target_cash_dollars": _round(target_cash_dollars, 2),
            "actual_cash_dollars": _round(actual_cash, 2),
            "excess_cash": _round(excess_cash, 2),
        },
        "exposure": {
            "target_gross_exposure_dollars": _round(target_gross * actual_equity if target_gross is not None and actual_equity is not None else None, 2),
            "actual_gross_exposure_dollars": _round(actual_gross * actual_equity if actual_gross is not None and actual_equity is not None else None, 2),
            "undeployed_capital": _round(undeployed_capital, 2),
        },
        "concentration": {
            "target": target_concentration,
            "actual": actual_concentration,
            "drift": {
                "max_weight": _round(
                    (actual_concentration.get("max_weight") or 0.0) - (target_concentration.get("max_weight") or 0.0)
                ),
                "top5_weight": _round(
                    (actual_concentration.get("top5_weight") or 0.0) - (target_concentration.get("top5_weight") or 0.0)
                ),
                "hhi": _round((actual_concentration.get("hhi") or 0.0) - (target_concentration.get("hhi") or 0.0)),
            },
        },
        "deployment": {
            "deployment_efficiency_pct": _round(_ratio(actual_gross, target_gross)),
            "deployment_score": deployment_score,
            "undeployed_capital": _round(undeployed_capital, 2),
            "excess_cash": _round(excess_cash, 2),
        },
        "execution": {
            "intended_notional": intended_notional,
            "executed_notional": executed_notional,
            "execution_attainment_pct": _round(execution_attainment),
            "submitted_count": _round(execution_payload.get("submitted_count"), 0),
            "accepted_count": _round(execution_payload.get("accepted_count"), 0),
            "rejected_count": _round(execution_payload.get("rejected_count"), 0),
        },
        "weights": drift_rows,
        "top_drift_contributors": drift_rows[:10],
        "drift_attribution": {
            "classifications": sorted(
                {
                    code
                    for row in drift_rows
                    for code in row.get("reason_codes", [])
                    if code != "ok"
                }
            )
            or ["ok"],
            "total_abs_weight_drift": round(total_abs_weight_drift, 6),
            "active_share": round(active_share, 6),
        },
        "reason_codes": reason_codes,
        "confidence": confidence,
        "deployment_score": deployment_score,
        "attainment_score": attainment_score,
        "source_artifacts": {
            "target_portfolio": str(intended_path),
            "actual_portfolio": str(actual_path),
            "intended_orders": str(intended_orders_path),
            "executed_orders": str(execution_path),
            "reconciliation": str(recon_path),
            "post_sell_rebudget": str(rebudget_path) if rebudget_path.exists() else None,
        },
        "source_diagnostics": {
            "target_portfolio": _source(intended_path, intended_nav, trade_date),
            "actual_portfolio": _source(actual_path, actual_nav, trade_date),
            "intended_orders": _source(intended_orders_path, intended_orders_payload, trade_date),
            "executed_orders": _source(execution_path, execution_payload, trade_date),
            "reconciliation": _source(recon_path, recon, trade_date),
            "post_sell_rebudget": _source(rebudget_path, post_sell_rebudget, trade_date),
        },
    }
    if write:
        _write_json(out_path, payload)
        payload["artifact_path"] = str(out_path)
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build target-attainment reconciliation artifact.")
    parser.add_argument("--date", required=True, help="Trade date in YYYY-MM-DD format.")
    parser.add_argument("--repo-root", default=".", help="Repository root.")
    parser.add_argument("--output-root", default=None, help="Optional output root.")
    parser.add_argument("--no-write", action="store_true", help="Build payload without writing artifact.")
    args = parser.parse_args(argv)
    payload = build_target_attainment(
        trade_date=args.date,
        repo_root=Path(args.repo_root),
        output_root=Path(args.output_root) if args.output_root else None,
        write=not args.no_write,
    )
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
