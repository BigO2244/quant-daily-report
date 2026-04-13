from __future__ import annotations

import datetime as dt
import json
import math
from pathlib import Path
from typing import Any

DEFAULT_POLICY: dict[str, Any] = {
    "benchmark": "SPY",
    "mode": "shadow_only",
    "north_star": "Use options as a portfolio overlay, not a primary alpha engine.",
    "premium_budget_bps": 25.0,
    "cash_ceiling_without_overlay": 0.18,
    "min_contract_utilization": 0.75,
    "activation": {
        "protective_put": {
            "composite_regimes": ["high_volatility", "breadth_washout"],
            "volatility_states": ["crisis"],
            "breadth_states": ["washed_out"],
            "macro_states": ["stress"],
        },
        "put_spread": {
            "composite_regimes": ["risk_off_defensive"],
            "volatility_states": ["elevated"],
            "breadth_states": ["deteriorating"],
            "macro_states": ["risk_off", "stress"],
        },
    },
    "strategies": {
        "protective_put": {
            "hedge_ratio": 1.0,
            "target_dte": 35,
            "long_put_moneyness": 0.98,
        },
        "put_spread": {
            "hedge_ratio": 0.50,
            "target_dte": 28,
            "long_put_moneyness": 0.98,
            "short_put_moneyness": 0.92,
        },
    },
}


def _now_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace("$", "").replace(",", "")
    try:
        return float(text)
    except Exception:
        return None


def _load_policy(path: str | Path | None) -> dict[str, Any]:
    policy = json.loads(json.dumps(DEFAULT_POLICY))
    if path is None:
        return policy
    config_path = Path(path)
    if not config_path.exists():
        return policy
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        return policy
    if not isinstance(payload, dict):
        return policy
    for key, value in payload.items():
        if key in {"activation", "strategies"} and isinstance(value, dict):
            merged = dict(policy.get(key) or {})
            for inner_key, inner_value in value.items():
                if isinstance(inner_value, dict) and isinstance(merged.get(inner_key), dict):
                    nested = dict(merged.get(inner_key) or {})
                    nested.update(inner_value)
                    merged[inner_key] = nested
                else:
                    merged[inner_key] = inner_value
            policy[key] = merged
        else:
            policy[key] = value
    return policy


def _norm_text(value: Any) -> str:
    return str(value or "").strip().lower()


def _matches_any(current: str, candidates: list[Any] | None) -> bool:
    if not current:
        return False
    return current in {_norm_text(item) for item in (candidates or []) if _norm_text(item)}


def _select_strategy(
    regime_summary: dict[str, Any] | None,
    cash_ratio: float | None,
    policy: dict[str, Any],
) -> tuple[str | None, list[str], str]:
    regime_summary = dict(regime_summary or {})
    composite = _norm_text(regime_summary.get("composite_regime"))
    volatility = _norm_text(regime_summary.get("volatility_state"))
    breadth = _norm_text(regime_summary.get("breadth_state"))
    macro = _norm_text(regime_summary.get("macro_state"))
    activation = dict(policy.get("activation") or {})
    reasons: list[str] = []

    protective = dict(activation.get("protective_put") or {})
    if (
        _matches_any(composite, protective.get("composite_regimes"))
        or _matches_any(volatility, protective.get("volatility_states"))
        or _matches_any(breadth, protective.get("breadth_states"))
        or _matches_any(macro, protective.get("macro_states"))
    ):
        if _matches_any(composite, protective.get("composite_regimes")):
            reasons.append(f"composite_regime={composite}")
        if _matches_any(volatility, protective.get("volatility_states")):
            reasons.append(f"volatility_state={volatility}")
        if _matches_any(breadth, protective.get("breadth_states")):
            reasons.append(f"breadth_state={breadth}")
        if _matches_any(macro, protective.get("macro_states")):
            reasons.append(f"macro_state={macro}")
        return "protective_put", reasons, "ACTIVE"

    spread = dict(activation.get("put_spread") or {})
    if (
        _matches_any(composite, spread.get("composite_regimes"))
        or _matches_any(volatility, spread.get("volatility_states"))
        or _matches_any(breadth, spread.get("breadth_states"))
        or _matches_any(macro, spread.get("macro_states"))
    ):
        cash_ceiling = _to_float(policy.get("cash_ceiling_without_overlay"))
        if cash_ceiling is not None and cash_ratio is not None and cash_ratio >= cash_ceiling:
            reasons.append(
                f"cash_ratio={cash_ratio:.2%} already above overlay cash ceiling {cash_ceiling:.2%}"
            )
            return None, reasons, "INACTIVE_ALREADY_DEFENSIVE"
        if _matches_any(composite, spread.get("composite_regimes")):
            reasons.append(f"composite_regime={composite}")
        if _matches_any(volatility, spread.get("volatility_states")):
            reasons.append(f"volatility_state={volatility}")
        if _matches_any(breadth, spread.get("breadth_states")):
            reasons.append(f"breadth_state={breadth}")
        if _matches_any(macro, spread.get("macro_states")):
            reasons.append(f"macro_state={macro}")
        return "put_spread", reasons, "ACTIVE"

    reasons.append("regime does not require an options hedge overlay")
    return None, reasons, "INACTIVE"


def _pick_expiry(asof_date: str | None, target_dte: int) -> str | None:
    if not asof_date:
        return None
    try:
        anchor = dt.date.fromisoformat(str(asof_date))
    except Exception:
        return None
    candidate = anchor + dt.timedelta(days=max(1, int(target_dte)))
    while candidate.weekday() != 4:
        candidate += dt.timedelta(days=1)
    return candidate.isoformat()


def _floor_strike(spot: float, moneyness: float) -> int:
    return max(1, int(math.floor(float(spot) * float(moneyness))))


def build_options_overlay_shadow(
    *,
    trade_date: str,
    asof_date: str | None,
    regime_summary: dict[str, Any] | None,
    portfolio_equity: float | None,
    portfolio_cash: float | None,
    spy_price: float | None,
    live_regime_review: dict[str, Any] | None = None,
    policy_path: str | Path | None = Path("config/options_overlay_policy.json"),
) -> dict[str, Any]:
    policy = _load_policy(policy_path)
    equity = _to_float(portfolio_equity)
    cash = _to_float(portfolio_cash)
    spot = _to_float(spy_price)
    cash_ratio = None
    invested_ratio = None
    if equity is not None and equity > 0 and cash is not None:
        cash_ratio = max(0.0, min(1.0, float(cash) / float(equity)))
        invested_ratio = max(0.0, 1.0 - cash_ratio)
    elif equity is not None and equity > 0:
        invested_ratio = 1.0

    strategy, reasons, base_status = _select_strategy(regime_summary, cash_ratio, policy)
    recommendation: dict[str, Any] = {
        "strategy": strategy,
        "feasible": False,
        "target_hedge_ratio": None,
        "target_protected_notional": None,
        "contract_notional": None,
        "contracts_float": None,
        "contracts_recommended": 0,
        "target_dte": None,
        "expiry": None,
        "premium_budget_dollars": None,
        "max_premium_per_contract": None,
        "long_put": None,
        "short_put": None,
    }

    status = base_status
    if equity is None or equity <= 0 or spot is None or spot <= 0:
        status = "DATA_UNAVAILABLE"
        reasons = ["portfolio equity or SPY price unavailable"]
    elif strategy:
        strategy_cfg = dict((policy.get("strategies") or {}).get(strategy) or {})
        hedge_ratio = max(0.0, min(1.0, float(_to_float(strategy_cfg.get("hedge_ratio")) or 0.0)))
        target_dte = int(_to_float(strategy_cfg.get("target_dte")) or 0)
        premium_budget_dollars = float(equity) * float(_to_float(policy.get("premium_budget_bps")) or 0.0) / 10000.0
        target_protected_notional = float(equity) * float(invested_ratio if invested_ratio is not None else 1.0) * hedge_ratio
        contract_notional = float(spot) * 100.0
        contracts_float = (
            target_protected_notional / contract_notional
            if contract_notional > 0
            else 0.0
        )
        min_contract_utilization = float(_to_float(policy.get("min_contract_utilization")) or 0.0)
        contracts_recommended = 0
        feasible = False
        if contracts_float >= min_contract_utilization:
            contracts_recommended = max(1, int(math.floor(contracts_float + 1e-9)))
            feasible = contracts_recommended > 0
            status = "READY_SHADOW_RECOMMENDATION" if feasible else "WATCH_ONLY_CONTRACT_TOO_LARGE"
        else:
            status = "WATCH_ONLY_CONTRACT_TOO_LARGE"
            reasons.append(
                f"target hedge covers only {contracts_float:.2f} SPY contracts at current portfolio size"
            )

        long_put = {
            "strike": _floor_strike(float(spot), float(_to_float(strategy_cfg.get("long_put_moneyness")) or 1.0)),
            "kind": "PUT",
        }
        short_put = None
        if strategy == "put_spread":
            short_strike = _floor_strike(float(spot), float(_to_float(strategy_cfg.get("short_put_moneyness")) or 1.0))
            if short_strike >= long_put["strike"]:
                short_strike = max(1, long_put["strike"] - 1)
            short_put = {"strike": short_strike, "kind": "PUT"}

        recommendation = {
            "strategy": strategy,
            "feasible": feasible,
            "target_hedge_ratio": hedge_ratio,
            "target_protected_notional": target_protected_notional,
            "contract_notional": contract_notional,
            "contracts_float": contracts_float,
            "contracts_recommended": contracts_recommended,
            "target_dte": target_dte,
            "expiry": _pick_expiry(asof_date, target_dte),
            "premium_budget_dollars": premium_budget_dollars,
            "max_premium_per_contract": (
                premium_budget_dollars / float(contracts_recommended)
                if contracts_recommended > 0
                else None
            ),
            "long_put": long_put,
            "short_put": short_put,
        }

    return {
        "generated_at": _now_utc(),
        "trade_date": trade_date,
        "asof_date": asof_date,
        "benchmark": str(policy.get("benchmark") or "SPY"),
        "mode": str(policy.get("mode") or "shadow_only"),
        "north_star": str(policy.get("north_star") or ""),
        "portfolio": {
            "equity": equity,
            "cash": cash,
            "cash_ratio": cash_ratio,
            "invested_ratio": invested_ratio,
        },
        "regime": {
            "composite_regime": (regime_summary or {}).get("composite_regime"),
            "trend_state": (regime_summary or {}).get("trend_state"),
            "volatility_state": (regime_summary or {}).get("volatility_state"),
            "breadth_state": (regime_summary or {}).get("breadth_state"),
            "macro_state": (regime_summary or {}).get("macro_state"),
        },
        "allocator_review_status": ((live_regime_review or {}).get("promotion_gate") or {}).get("overall_status"),
        "policy": {
            "premium_budget_bps": _to_float(policy.get("premium_budget_bps")),
            "cash_ceiling_without_overlay": _to_float(policy.get("cash_ceiling_without_overlay")),
            "min_contract_utilization": _to_float(policy.get("min_contract_utilization")),
        },
        "trigger": {
            "active": bool(strategy),
            "status": status,
            "reasons": reasons,
        },
        "recommendation": recommendation,
    }


def _format_pct(value: float | None, *, signed: bool = False) -> str:
    if value is None:
        return "N/A"
    return f"{value:+.2%}" if signed else f"{value:.2%}"


def _format_money(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"${value:,.2f}"


def build_options_overlay_shadow_markdown(payload: dict[str, Any]) -> str:
    recommendation = dict(payload.get("recommendation") or {})
    trigger = dict(payload.get("trigger") or {})
    regime = dict(payload.get("regime") or {})
    portfolio = dict(payload.get("portfolio") or {})
    lines = [
        "# Options Overlay Shadow Review",
        "",
        f"- Trade date: {payload.get('trade_date') or 'N/A'}",
        f"- As of: {payload.get('asof_date') or 'N/A'}",
        f"- Benchmark: {payload.get('benchmark') or 'SPY'}",
        f"- Mode: {str(payload.get('mode') or 'shadow_only').upper()}",
        f"- Status: {str(trigger.get('status') or '').upper()}",
        "",
        "## Portfolio",
        "",
        f"- Equity: {_format_money(portfolio.get('equity'))}",
        f"- Cash: {_format_money(portfolio.get('cash'))}",
        f"- Cash ratio: {_format_pct(portfolio.get('cash_ratio'))}",
        f"- Invested ratio: {_format_pct(portfolio.get('invested_ratio'))}",
        "",
        "## Regime",
        "",
        f"- Composite: {regime.get('composite_regime') or 'unknown'}",
        f"- Trend / Vol / Breadth / Macro: "
        f"{regime.get('trend_state') or 'unknown'} / "
        f"{regime.get('volatility_state') or 'unknown'} / "
        f"{regime.get('breadth_state') or 'unknown'} / "
        f"{regime.get('macro_state') or 'unknown'}",
        "",
        "## Recommendation",
        "",
        f"- Strategy: {recommendation.get('strategy') or 'none'}",
        f"- Feasible: {'YES' if recommendation.get('feasible') else 'NO'}",
        f"- Target hedge ratio: {_format_pct(recommendation.get('target_hedge_ratio'))}",
        f"- Target protected notional: {_format_money(recommendation.get('target_protected_notional'))}",
        f"- Contract notional: {_format_money(recommendation.get('contract_notional'))}",
        f"- Contracts: {recommendation.get('contracts_recommended') or 0} "
        f"(float={recommendation.get('contracts_float') if recommendation.get('contracts_float') is not None else 'N/A'})",
        f"- Expiry: {recommendation.get('expiry') or 'N/A'}",
        f"- Premium budget: {_format_money(recommendation.get('premium_budget_dollars'))}",
        "",
        "## Trigger Reasons",
        "",
    ]
    for reason in trigger.get("reasons") or ["none"]:
        lines.append(f"- {reason}")
    return "\n".join(lines) + "\n"


def write_options_overlay_shadow(
    *,
    run_root: str | Path,
    output_dir: str | Path,
    trade_date: str,
    asof_date: str | None,
    regime_summary: dict[str, Any] | None,
    portfolio_equity: float | None,
    portfolio_cash: float | None,
    spy_price: float | None,
    live_regime_review: dict[str, Any] | None = None,
    policy_path: str | Path | None = Path("config/options_overlay_policy.json"),
) -> dict[str, Any]:
    payload = build_options_overlay_shadow(
        trade_date=trade_date,
        asof_date=asof_date,
        regime_summary=regime_summary,
        portfolio_equity=portfolio_equity,
        portfolio_cash=portfolio_cash,
        spy_price=spy_price,
        live_regime_review=live_regime_review,
        policy_path=policy_path,
    )
    markdown = build_options_overlay_shadow_markdown(payload)

    run_root_path = Path(run_root)
    out_dir = Path(output_dir)
    run_root_path.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    run_json_path = run_root_path / "options_overlay_shadow.json"
    dated_json_path = out_dir / f"options_overlay_shadow_{trade_date}.json"
    dated_md_path = out_dir / f"options_overlay_shadow_{trade_date}.md"
    latest_json_path = out_dir / "options_overlay_shadow_latest.json"

    run_json_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    dated_json_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    dated_md_path.write_text(markdown, encoding="utf-8")
    latest_json_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    payload["artifact_paths"] = {
        "run_json": str(run_json_path),
        "dated_json": str(dated_json_path),
        "dated_markdown": str(dated_md_path),
        "latest_json": str(latest_json_path),
    }
    return payload
