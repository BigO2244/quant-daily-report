from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import sys
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.live_pilot_guardrails import (
    LIVE_PILOT_APPROVED_MAX_CAP_USD,
    normalize_live_pilot_limit_price,
)
from core.risk_controls import (
    RiskControls,
    load_sector_map,
    peak_equity_path,
    update_peak_equity_state,
)
from paper.paper_broker import fetch_open_prices_yfinance, load_targets
from paper.run_manager import safe_write_text

# The live-pilot capital cap tracks the account's portfolio value (resolved
# dynamically upstream in the cron lane / execution path). This constant is only a
# non-zero placeholder default for ad-hoc CLI invocations; the cron always passes an
# explicit ``--capital-cap`` equal to the resolved portfolio value.
DEFAULT_CAPITAL_CAP = LIVE_PILOT_APPROVED_MAX_CAP_USD
# Full rebalance: the buy blast-radius ceiling. The executor enforces the buy count
# from CAERUS_LIVE_PILOT_MAX_ORDERS; this is the plan-side default/echo.
DEFAULT_MAX_ORDERS = 50
DEFAULT_OUTPUT_DIR = Path("outputs/live_pilot/plans")
DEFAULT_PRECOMPUTE_ROOT = Path("outputs/precompute")

# Live-pilot peak-equity state is isolated from paper's state dir. Reusing paper's
# peak (built from paper's ~$10k equity) against the live account's small portfolio
# value would compute a spurious drawdown and trip the circuit breaker every run.
LIVE_PILOT_STATE_DIR = Path("outputs/live_pilot/state")

TARGET_PORTFOLIO_SCHEMA = "caerus.transition_target.v2"
PLAN_SCHEMA_VERSION = "live_pilot_plan_from_precompute.v2"

PriceFetcher = Callable[[Sequence[str], str], "pd.DataFrame"]


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
    return numeric if math.isfinite(numeric) else None


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


def _is_supported_equity_symbol(symbol: str) -> bool:
    """Reject anything that is not a plain us-equity ticker (fail-closed).

    ``load_targets`` already rejects banned leveraged/inverse ETFs, so target names
    are strategy equities. This is a defensive belt-and-suspenders check so a crypto
    or malformed symbol can never slip into a live target row.
    """
    if not symbol or symbol == "CASH":
        return False
    if not symbol.replace(".", "").isalpha():
        return False
    if symbol.endswith("USD") and len(symbol) > 4:
        return False
    return True


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


def _resolve_signals_path(payload_path: Path, payload: Mapping[str, Any]) -> Path:
    """Locate the strategy target signals file for the LIVE lane.

    Preference (live-only; paper uses its own loader and always reads signals.json):
      1. ``signals.json`` beside the payload in the precompute bundle dir.
      2. an explicit ``execution_target_source`` pointer in the payload.
    """
    sibling = payload_path.parent / "signals.json"
    if sibling.exists():
        return sibling
    raw = str(payload.get("execution_target_source") or "").strip()
    if raw:
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = payload_path.parent / candidate.name
        if candidate.exists():
            return candidate
    # Return the (missing) sibling so the caller fails closed with a clear path.
    return sibling


def _entry_prices_from_payload(payload: Mapping[str, Any]) -> dict[str, float]:
    """Reference prices for names that CHANGED in the precompute (delta names).

    The payload ``trades[]`` carry ``entry_price`` (the precompute's reference price)
    only for tickers that changed. Full-target names not present here are priced via
    the yfinance open fetch (the same source paper uses).
    """
    out: dict[str, float] = {}
    trades = payload.get("trades") or payload.get("orders") or payload.get("planned_trades") or []
    if not isinstance(trades, list):
        return out
    for trade in trades:
        if not isinstance(trade, Mapping):
            continue
        symbol = _clean_symbol(trade.get("ticker") or trade.get("symbol"))
        if not symbol:
            continue
        price = _safe_positive_float(
            _first_nonempty(trade, ("entry_price", "price", "limit_price", "normalized_limit_price"))
        )
        if price is not None and symbol not in out:
            out[symbol] = float(price)
    return out


def _yfinance_prices(
    symbols: Sequence[str],
    run_date: str,
    *,
    price_fetcher: PriceFetcher,
) -> dict[str, float]:
    if not symbols:
        return {}
    try:
        frame = price_fetcher(list(symbols), run_date)
    except Exception:
        # Fail closed: any fetch error leaves these names unpriced, which blocks the
        # plan below rather than silently dropping them from the rebalance.
        return {}
    out: dict[str, float] = {}
    if frame is None or getattr(frame, "empty", True):
        return out
    for _, row in frame.iterrows():
        symbol = _clean_symbol(row.get("ticker"))
        price = _safe_positive_float(row.get("open") or row.get("price"))
        if symbol and price is not None:
            out[symbol] = float(price)
    return out


def _hydrate_prices(
    symbols: Sequence[str],
    *,
    payload: Mapping[str, Any],
    run_date: str,
    price_fetcher: PriceFetcher,
) -> tuple[dict[str, float], dict[str, str], list[str]]:
    """Return (price_by_symbol, source_by_symbol, unpriced_symbols), fail-closed.

    Per name: payload ``entry_price`` where present, else the yfinance open for the
    remainder. Any symbol that still lacks a positive price is returned in
    ``unpriced_symbols`` so the caller blocks the plan (mirrors the
    ``live_pilot_capital_cap_unresolved`` fail-closed pattern) and the executor never
    silently drops it.
    """
    entry_prices = _entry_prices_from_payload(payload)
    price_by_symbol: dict[str, float] = {}
    source_by_symbol: dict[str, str] = {}
    need_fetch: list[str] = []
    for symbol in symbols:
        price = entry_prices.get(symbol)
        if price is not None and price > 0:
            price_by_symbol[symbol] = float(price)
            source_by_symbol[symbol] = "payload_entry_price"
        else:
            need_fetch.append(symbol)

    fetched = _yfinance_prices(need_fetch, run_date, price_fetcher=price_fetcher)
    for symbol in need_fetch:
        price = fetched.get(symbol)
        if price is not None and price > 0:
            price_by_symbol[symbol] = float(price)
            source_by_symbol[symbol] = "yfinance_open"

    unpriced = [s for s in symbols if s not in price_by_symbol]
    return price_by_symbol, source_by_symbol, unpriced


def _target_rows_from_weights(
    weights: pd.DataFrame,
    *,
    approved_sleeve: str,
    price_by_symbol: Mapping[str, float],
    source_by_symbol: Mapping[str, str],
    signal_sleeve_by_symbol: Mapping[str, str],
) -> list[dict[str, Any]]:
    """Full target portfolio rows (schema caerus.transition_target.v2).

    One row per risk-adjusted target name, each carrying a GUARANTEED positive price
    and the approved sleeve (with source-signal sleeve preserved as provenance). The
    executor's Transition Engine consumes these to rebalance the whole live account
    to target weights, sizing against the live snapshot and enforcing caps/max_orders.
    Ranked by target weight desc, then symbol, for stable priority.
    """
    rows: list[dict[str, Any]] = []
    for _, w in weights.iterrows():
        symbol = _clean_symbol(w.get("ticker"))
        price = _safe_positive_float(price_by_symbol.get(symbol))
        target_weight = _safe_float(w.get("target_weight"))
        if not symbol or price is None or target_weight is None:
            continue
        normalized_price = normalize_live_pilot_limit_price(float(price))
        rows.append(
            {
                "symbol": symbol,
                "ticker": symbol,
                "target_weight": float(target_weight),
                "price": float(normalized_price),
                "reference_price": float(price),
                "price_source": source_by_symbol.get(symbol),
                "order_type": "market",
                "sleeve": approved_sleeve,
                "source_signal_sleeve": signal_sleeve_by_symbol.get(symbol) or None,
                "source_signal_target_weight": float(target_weight),
            }
        )
    rows.sort(key=lambda r: (-(float(r["target_weight"])), str(r.get("symbol") or "")))
    return rows


def build_live_pilot_plan(
    *,
    payload_path: Path,
    approved_sleeve: str,
    capital_cap: float = DEFAULT_CAPITAL_CAP,
    max_orders: int = DEFAULT_MAX_ORDERS,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    allow_missing_sleeve: bool = False,
    allow_fractional: bool = False,
    price_fetcher: PriceFetcher | None = None,
    sector_map: Mapping[str, str] | None = None,
    state_dir: Path | None = None,
    lane: str | None = None,
    recovery_policy: str | None = None,
    recovery_policy_config: Path = Path("config/paper_recovery_policy.json"),
    factor_history_fetcher: Callable[[str, str], pd.DataFrame] | None = None,
) -> dict[str, Any]:
    """Build a FULL rebalance target plan for the live pilot.

    The plan is the strategy's risk-adjusted target weights over the ENTIRE target
    universe (from ``signals.json``), sized to the live portfolio value. It uses the
    SAME pipeline paper uses -- ``paper.paper_broker.load_targets`` then
    ``core.risk_controls.RiskControls.apply_to_targets`` fed the LIVE equity -- so
    there is one implementation and no drift. The executor rebalances the live
    holdings to this target through the shared core, selling over-weight / removed
    names and buying under-weight ones, honouring the safety gates.
    """
    if not str(approved_sleeve or "").strip():
        raise ValueError("approved_sleeve is required")
    if float(capital_cap) <= 0:
        # No fixed program ceiling: the cap tracks the account's portfolio value
        # (resolved upstream in the cron lane / execution path). Must still be positive.
        raise ValueError("capital_cap must be > 0")
    if int(max_orders) <= 0:
        raise ValueError("max_orders must be > 0")

    price_fetcher = price_fetcher or fetch_open_prices_yfinance

    payload = _read_json(payload_path)
    if not isinstance(payload, Mapping):
        raise ValueError("planned execution payload must be a JSON object")
    trade_date = str(payload.get("trade_date") or payload_path.parent.name)

    signals_path = _resolve_signals_path(payload_path, payload)
    if not signals_path.exists():
        return _emit_blocked_plan(
            output_dir=output_dir,
            trade_date=trade_date,
            approved_sleeve=approved_sleeve,
            capital_cap=float(capital_cap),
            max_orders=int(max_orders),
            allow_fractional=bool(allow_fractional),
            reason_code="live_pilot_signals_source_missing",
            diagnostics={"signals_path": str(signals_path)},
        )

    recovery_policy_meta: dict[str, Any] | None = None
    if recovery_policy:
        if str(lane or "").strip().lower() != "paper":
            return _emit_blocked_plan(
                output_dir=output_dir,
                trade_date=trade_date,
                approved_sleeve=approved_sleeve,
                capital_cap=float(capital_cap),
                max_orders=int(max_orders),
                allow_fractional=bool(allow_fractional),
                reason_code="paper_recovery_policy_wrong_lane",
                diagnostics={
                    "lane": lane,
                    "recovery_policy": recovery_policy,
                },
            )
        try:
            from core.paper_recovery_policy import (
                derive_weekly_rotation_guard_payload,
                fetch_factor_closes_yfinance,
                validate_recovery_config,
            )

            config = _read_json(recovery_policy_config)
            config_validation = validate_recovery_config(
                config,
                requested_policy=recovery_policy,
            )
            if config_validation.get("status") != "PASS":
                raise ValueError(
                    "recovery config blocked: "
                    + ",".join(config_validation.get("reason_codes") or [])
                )
            derived_payload, recovery_policy_meta = (
                derive_weekly_rotation_guard_payload(
                    precompute_root=payload_path.parent.parent,
                    trade_date=trade_date,
                    factor_fetcher=(
                        factor_history_fetcher
                        or fetch_factor_closes_yfinance
                    ),
                )
            )
            recovery_policy_meta["config_path"] = str(recovery_policy_config)
            recovery_policy_meta["config_validation"] = config_validation
            recovery_dir = output_dir.parent / "recovery_targets"
            recovery_dir.mkdir(parents=True, exist_ok=True)
            derived_path = (
                recovery_dir / f"paper_recovery_targets_{trade_date}.json"
            )
            _write_json(derived_path, derived_payload)
            signals_path = derived_path
        except Exception as exc:
            return _emit_blocked_plan(
                output_dir=output_dir,
                trade_date=trade_date,
                approved_sleeve=approved_sleeve,
                capital_cap=float(capital_cap),
                max_orders=int(max_orders),
                allow_fractional=bool(allow_fractional),
                reason_code="paper_recovery_policy_build_failed",
                diagnostics={
                    "recovery_policy": recovery_policy,
                    "config_path": str(recovery_policy_config),
                    "error": str(exc),
                },
            )

    identity_validation: dict[str, Any] | None = None
    if lane is not None:
        from core.strategy_identity import validate_lane_strategy_identity

        signals_payload = _read_json(signals_path)
        identity = (
            signals_payload.get("strategy_identity")
            if isinstance(signals_payload, Mapping)
            and isinstance(signals_payload.get("strategy_identity"), Mapping)
            else {}
        )
        identity_validation = validate_lane_strategy_identity(
            identity=identity,
            approved_strategy=approved_sleeve,
            lane=lane,
        )
        if identity_validation.get("status") != "PASS":
            return _emit_blocked_plan(
                output_dir=output_dir,
                trade_date=trade_date,
                approved_sleeve=approved_sleeve,
                capital_cap=float(capital_cap),
                max_orders=int(max_orders),
                allow_fractional=bool(allow_fractional),
                reason_code="strategy_identity_mismatch",
                diagnostics={"strategy_identity_validation": identity_validation},
            )

    # 1) Strategy target for the FULL universe (same loader paper uses).
    payload_cash_default = _safe_float(payload.get("cash_target_weight")) or 0.0
    targets, cash_target_weight, _snapshot_date, _asof = load_targets(
        str(signals_path),
        cash_target_weight_default=float(payload_cash_default),
    )
    signal_sleeve_by_symbol = {
        _clean_symbol(row.get("ticker")): str(row.get("sleeve") or "").strip()
        for _, row in targets.iterrows()
    }

    # Live remains fail-closed when any target's layer cannot be resolved. This
    # is classification-only; paper/live continue to share the same target and
    # risk-control path, and no per-layer sector-cap behavior is introduced.
    from core.sleeve_layers import unresolved_sleeve_labels

    unresolved_layers: dict[str, list[str]] = {}
    for symbol, label in signal_sleeve_by_symbol.items():
        unresolved = unresolved_sleeve_labels(label)
        if unresolved:
            unresolved_layers[symbol] = unresolved
    if unresolved_layers:
        return _emit_blocked_plan(
            output_dir=output_dir,
            trade_date=trade_date,
            approved_sleeve=approved_sleeve,
            capital_cap=float(capital_cap),
            max_orders=int(max_orders),
            allow_fractional=bool(allow_fractional),
            reason_code="live_pilot_layer_unresolved",
            diagnostics={"unresolved_layer_labels": unresolved_layers},
        )

    # 2) Risk controls at the LIVE portfolio size. current_equity is the resolved
    #    dynamic cap (== the live portfolio value; an optional operator CAPITAL_CAP
    #    only tightens it -- that IS "the portfolio size for Live"). Peak-equity is
    #    tracked in a live-scoped state dir, isolated from paper's.
    live_equity = float(capital_cap)
    peak_path = peak_equity_path(state_dir=Path(state_dir) if state_dir else LIVE_PILOT_STATE_DIR)
    peak_state = update_peak_equity_state(
        current_equity=live_equity,
        trade_date=trade_date,
        source="live_pilot_dynamic_cap",
        path=peak_path,
    )
    resolved_sector_map = dict(sector_map) if sector_map is not None else load_sector_map()
    controls = RiskControls()
    result = controls.apply_to_targets(
        targets,
        sector_map=resolved_sector_map,
        cash_target_weight=cash_target_weight,
        current_equity=live_equity,
        peak_equity=_safe_float(peak_state.get("peak_equity")),
    )
    adjusted_weights = result.weights

    # 3) Sanity: every target name must be a plain equity (fail-closed).
    bad_symbols = [
        _clean_symbol(w.get("ticker"))
        for _, w in adjusted_weights.iterrows()
        if not _is_supported_equity_symbol(_clean_symbol(w.get("ticker")))
    ]
    if bad_symbols:
        return _emit_blocked_plan(
            output_dir=output_dir,
            trade_date=trade_date,
            approved_sleeve=approved_sleeve,
            capital_cap=float(capital_cap),
            max_orders=int(max_orders),
            allow_fractional=bool(allow_fractional),
            reason_code="live_pilot_target_unsupported_symbol",
            diagnostics={"unsupported_symbols": sorted(set(bad_symbols))},
            risk_controls=result.to_artifact(),
        )

    target_symbols = [_clean_symbol(w.get("ticker")) for _, w in adjusted_weights.iterrows()]

    # 4) Price EVERY target name; fail closed if any name ends up unpriced.
    price_by_symbol, source_by_symbol, unpriced = _hydrate_prices(
        target_symbols,
        payload=payload,
        run_date=trade_date,
        price_fetcher=price_fetcher,
    )
    if unpriced:
        return _emit_blocked_plan(
            output_dir=output_dir,
            trade_date=trade_date,
            approved_sleeve=approved_sleeve,
            capital_cap=float(capital_cap),
            max_orders=int(max_orders),
            allow_fractional=bool(allow_fractional),
            reason_code="live_pilot_target_unpriced",
            diagnostics={"unpriced_targets": sorted(set(unpriced))},
            risk_controls=result.to_artifact(),
        )

    target_portfolio = _target_rows_from_weights(
        adjusted_weights,
        approved_sleeve=approved_sleeve,
        price_by_symbol=price_by_symbol,
        source_by_symbol=source_by_symbol,
        signal_sleeve_by_symbol=signal_sleeve_by_symbol,
    )
    if not target_portfolio:
        return _emit_blocked_plan(
            output_dir=output_dir,
            trade_date=trade_date,
            approved_sleeve=approved_sleeve,
            capital_cap=float(capital_cap),
            max_orders=int(max_orders),
            allow_fractional=bool(allow_fractional),
            reason_code="live_pilot_no_target_names",
            diagnostics={"cash_target_weight": float(result.cash_target_weight)},
            risk_controls=result.to_artifact(),
        )

    # Canonical authority chain. Alpha/precompute supplies evidence; Decision
    # owns the original target; Risk may only reduce it; Trader receives the
    # hash-verified risk-approved target package.
    from authority.contracts import (
        build_decision_package,
        build_evidence_package,
        build_risk_package,
    )
    from authority.pipeline import execution_package_from_risk

    lane_id = str(lane or "manual")
    authority_stem = f"{trade_date}:{lane_id}:{approved_sleeve}"
    decision_target_rows = [
        {
            "symbol": _clean_symbol(row.get("ticker")),
            "sleeve": str(row.get("sleeve") or ""),
            "target_weight": float(row.get("target_weight") or 0.0),
        }
        for _, row in targets.iterrows()
    ]
    evidence = build_evidence_package(
        package_id=f"evidence:{authority_stem}",
        trade_date=trade_date,
        source_refs=(str(signals_path), str(payload_path)),
        observations=decision_target_rows,
    )
    decision = build_decision_package(
        package_id=f"decision:{authority_stem}",
        trade_date=trade_date,
        evidence=evidence,
        target_rows=decision_target_rows,
        target_cash_weight=float(cash_target_weight),
        source_refs=(str(signals_path), str(payload_path)),
    )
    risk = build_risk_package(
        package_id=f"risk:{authority_stem}",
        decision=decision,
        approved_target_rows=target_portfolio,
        approved_cash_weight=float(result.cash_target_weight),
        constraints=result.to_artifact(),
        source_refs=(f"decision:{decision.package_id}",),
    )
    execution = execution_package_from_risk(risk)
    authority_dir = output_dir / "authority" / trade_date
    authority_paths = {
        "evidence": authority_dir / "evidence_package.json",
        "decision": authority_dir / "decision_package.json",
        "risk": authority_dir / "risk_package.json",
        "execution": authority_dir / "execution_package.json",
    }
    authority_payloads = {
        "evidence": evidence.to_dict(),
        "decision": decision.to_dict(),
        "risk": risk.to_dict(),
        "execution": execution.to_dict(),
    }
    authority_dir.mkdir(parents=True, exist_ok=True)
    for name, path in authority_paths.items():
        _write_json(path, authority_payloads[name])

    plan = _plan_scaffold(
        trade_date=trade_date,
        approved_sleeve=approved_sleeve,
        capital_cap=float(capital_cap),
        max_orders=int(max_orders),
        allow_missing_sleeve=bool(allow_missing_sleeve),
        allow_fractional=bool(allow_fractional),
        output_dir=output_dir,
        payload_path=payload_path,
        signals_path=signals_path,
    )
    plan.update(
        {
            "status": "READY_FOR_MANUAL_APPROVAL",
            "reason_code": "full_rebalance_target_ready",
            "cash_target_weight": float(result.cash_target_weight),
            "target_portfolio_schema": TARGET_PORTFOLIO_SCHEMA,
            "target_portfolio": target_portfolio,
            "target_name_count": len(target_portfolio),
            "risk_controls": result.to_artifact(),
            "price_sources": {row["symbol"]: row["price_source"] for row in target_portfolio},
            "strategy_identity_validation": identity_validation
            or {
                "status": "UNVERIFIED",
                "reason_code": "lane_not_supplied_to_builder",
            },
            "paper_recovery_policy": recovery_policy_meta,
            "approved_execution_package": execution.to_dict(),
            "authority_package_paths": {
                name: str(path) for name, path in authority_paths.items()
            },
        }
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    plan_path = output_dir / f"live_pilot_plan_{trade_date}.json"
    md_path = output_dir / f"live_pilot_plan_{trade_date}.md"
    _write_json(plan_path, plan)
    _write_text(md_path, render_markdown(plan, json_path=plan_path))
    plan["json_path"] = str(plan_path)
    plan["markdown_path"] = str(md_path)
    return plan


def _plan_scaffold(
    *,
    trade_date: str,
    approved_sleeve: str,
    capital_cap: float,
    max_orders: int,
    allow_missing_sleeve: bool,
    allow_fractional: bool,
    output_dir: Path,
    payload_path: Path,
    signals_path: Path,
) -> dict[str, Any]:
    plan_path = output_dir / f"live_pilot_plan_{trade_date}.json"
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
    return {
        "schema_version": PLAN_SCHEMA_VERSION,
        "generated_at": _now_utc(),
        "source_precompute_payload": str(payload_path),
        "source_signals": str(signals_path),
        "trade_date": trade_date,
        "approved_sleeve": approved_sleeve,
        "capital_cap": float(capital_cap),
        "max_orders": int(max_orders),
        "allow_missing_sleeve": bool(allow_missing_sleeve),
        "allow_fractional": bool(allow_fractional),
        "order_policy": {
            "scope": "FR-104 LIVE_PILOT full rebalance",
            "model": "rebalance_to_risk_adjusted_target_weight",
            "order_type": "market",
            "time_in_force": "day",
            "normal_market_hours_only": True,
            "buys_capped_by": "CAERUS_LIVE_PILOT_MAX_ORDERS",
            "sells_gated_by": "CAERUS_LIVE_PILOT_SELLS_ENABLED + sell whitelist/wildcard",
            "sizing": "live portfolio value via execution core (same engine as paper)",
            "paper_or_production_impact": "none",
        },
        "required_dry_run_command": dry_run_command,
        "required_live_command": live_command,
        "operator_confirmation": {
            "approved_sleeve": approved_sleeve,
            "capital_cap": float(capital_cap),
            "max_orders": int(max_orders),
            "allow_missing_sleeve": bool(allow_missing_sleeve),
            "allow_fractional": bool(allow_fractional),
            "required_manual_review": True,
            "orders_submitted": 0,
        },
    }


def _emit_blocked_plan(
    *,
    output_dir: Path,
    trade_date: str,
    approved_sleeve: str,
    capital_cap: float,
    max_orders: int,
    allow_fractional: bool,
    reason_code: str,
    diagnostics: Mapping[str, Any],
    risk_controls: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    plan = _plan_scaffold(
        trade_date=trade_date,
        approved_sleeve=approved_sleeve,
        capital_cap=float(capital_cap),
        max_orders=int(max_orders),
        allow_missing_sleeve=False,
        allow_fractional=bool(allow_fractional),
        output_dir=output_dir,
        payload_path=Path(""),
        signals_path=Path(""),
    )
    plan.update(
        {
            "status": "BLOCKED",
            "reason_code": reason_code,
            "cash_target_weight": 0.0,
            "target_portfolio_schema": TARGET_PORTFOLIO_SCHEMA,
            "target_portfolio": [],
            "target_name_count": 0,
            "block_diagnostics": dict(diagnostics),
            "risk_controls": dict(risk_controls) if risk_controls is not None else None,
        }
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    plan_path = output_dir / f"live_pilot_plan_{trade_date}.json"
    md_path = output_dir / f"live_pilot_plan_{trade_date}.md"
    _write_json(plan_path, plan)
    _write_text(md_path, render_markdown(plan, json_path=plan_path))
    plan["json_path"] = str(plan_path)
    plan["markdown_path"] = str(md_path)
    return plan


def render_markdown(plan: Mapping[str, Any], *, json_path: Path) -> str:
    target_portfolio = plan.get("target_portfolio") or []
    lines = [
        "# LIVE_PILOT Full Rebalance Plan From Precompute",
        "",
        f"Status: `{plan.get('status')}`",
        f"Reason: `{plan.get('reason_code')}`",
        f"Trade Date: `{plan.get('trade_date')}`",
        f"Approved Sleeve: `{plan.get('approved_sleeve')}`",
        f"Capital Cap (portfolio value): `${float(plan.get('capital_cap') or 0.0):.2f}`",
        f"Cash Target Weight: `{float(plan.get('cash_target_weight') or 0.0):.4f}`",
        f"Max Buy Orders: `{plan.get('max_orders')}`",
        f"Target Names: `{plan.get('target_name_count')}`",
        f"JSON Plan: `{json_path.as_posix()}`",
        "",
        "## Target Portfolio (risk-adjusted weights)",
        "",
    ]
    if target_portfolio:
        lines.append("| Symbol | Target Weight | Ref Price | Price Source |")
        lines.append("| --- | ---: | ---: | --- |")
        for row in target_portfolio:
            lines.append(
                f"| `{row.get('symbol')}` | {float(row.get('target_weight') or 0.0):.4f} | "
                f"{float(row.get('price') or 0.0):.2f} | {row.get('price_source')} |"
            )
    else:
        lines.append("No target names (plan blocked).")
        diagnostics = plan.get("block_diagnostics") or {}
        if diagnostics:
            lines.extend(["", "## Block Diagnostics", "", "```json", json.dumps(dict(diagnostics), indent=2, sort_keys=True), "```"])
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
            "- Confirm the sleeve, cap (portfolio value), target weights, and dry-run artifact before any live attempt.",
            "- Sells are gated by the fail-closed sells master flag + whitelist/wildcard; buys are capped by max_orders.",
            "- This builder does not submit orders.",
        ]
    )
    return "\n".join(lines) + "\n"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a full-rebalance LIVE_PILOT plan from precompute output")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--payload-path", default=None, help="Explicit planned_execution_payload.json path")
    source.add_argument("--trade-date", default=None, help="Trade date under outputs/precompute/<DATE>")
    parser.add_argument("--precompute-root", default=str(DEFAULT_PRECOMPUTE_ROOT))
    parser.add_argument("--approved-sleeve", required=True)
    parser.add_argument(
        "--lane",
        choices=("paper", "live_pilot"),
        default=None,
        help="Execution lane; production callers must set this for fail-closed strategy identity validation.",
    )
    parser.add_argument(
        "--recovery-policy",
        default=None,
        help="Governed paper-only recovery policy id; rejected on non-paper lanes.",
    )
    parser.add_argument(
        "--recovery-policy-config",
        default="config/paper_recovery_policy.json",
    )
    parser.add_argument("--capital-cap", type=float, default=DEFAULT_CAPITAL_CAP)
    parser.add_argument("--max-orders", type=int, default=DEFAULT_MAX_ORDERS)
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument(
        "--state-dir",
        default=None,
        help=(
            "Peak-equity state dir for this lane. Default (unset) keeps the live-scoped "
            f"{LIVE_PILOT_STATE_DIR} — live behavior unchanged. The unified PAPER lane "
            "passes its own isolated dir (e.g. outputs/paper_lane/state) so paper and "
            "live peak-equity/drawdown state can never cross-contaminate."
        ),
    )
    parser.add_argument(
        "--allow-missing-sleeve",
        action="store_true",
        default=os.getenv("CAERUS_LIVE_PILOT_ALLOW_MISSING_SLEEVE", "").strip().lower() in {"1", "true", "yes", "y", "on"},
        help="Accepted for CLI compatibility; the full-rebalance builder stamps the approved sleeve on all target rows.",
    )
    parser.add_argument(
        "--allow-fractional",
        action="store_true",
        default=os.getenv("CAERUS_LIVE_PILOT_ALLOW_FRACTIONAL", "").strip().lower() in {"1", "true", "yes", "y", "on"},
        help="Echoed into the plan's dry-run/live commands; the executor enforces fractional policy.",
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
        state_dir=Path(args.state_dir) if args.state_dir else None,
        lane=args.lane,
        recovery_policy=args.recovery_policy,
        recovery_policy_config=Path(args.recovery_policy_config),
    )
    print(
        json.dumps(
            {
                "status": plan.get("status"),
                "reason_code": plan.get("reason_code"),
                "target_name_count": plan.get("target_name_count"),
                "json_path": plan.get("json_path"),
                "markdown_path": plan.get("markdown_path"),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if plan.get("status") == "READY_FOR_MANUAL_APPROVAL" else 1


if __name__ == "__main__":
    raise SystemExit(main())
