from __future__ import annotations

import datetime as dt
import json
import math
from pathlib import Path
from typing import Any

DEFAULT_POLICY: dict[str, Any] = {
    "benchmark": "SPY",
    "mode": "shadow_only",
    "north_star": "Use options as a capital-efficient overlay sleeve: harvest premium, define risk, and add convex exposure without bypassing promotion gates.",
    "premium_budget_bps": 75.0,
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
        "covered_call": {
            "composite_regimes": ["risk_on_trending", "neutral_mixed"],
            "volatility_states": ["elevated", "normal"],
            "breadth_states": ["mixed", "healthy"],
            "macro_states": ["neutral", "risk_on"],
        },
        "long_straddle": {
            "composite_regimes": ["high_volatility", "breadth_washout"],
            "volatility_states": ["crisis", "elevated"],
            "breadth_states": ["washed_out"],
            "macro_states": ["stress"],
        },
        "call_butterfly": {
            "composite_regimes": ["neutral_mixed"],
            "volatility_states": ["elevated"],
            "breadth_states": ["mixed", "deteriorating"],
            "macro_states": ["neutral"],
        },
        "leap_call": {
            "composite_regimes": ["risk_on_trending", "neutral_mixed"],
            "volatility_states": ["normal", "elevated"],
            "breadth_states": ["healthy", "mixed"],
            "macro_states": ["risk_on", "neutral"],
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
        "covered_call": {
            "target_dte": 35,
            "short_call_moneyness": 1.05,
            "premium_budget_bps": 0.0,
            "requires_covered_inventory": True,
        },
        "long_straddle": {
            "target_dte": 35,
            "long_call_moneyness": 1.0,
            "long_put_moneyness": 1.0,
            "premium_budget_bps": 50.0,
        },
        "call_butterfly": {
            "target_dte": 28,
            "lower_call_moneyness": 0.98,
            "center_call_moneyness": 1.0,
            "upper_call_moneyness": 1.02,
            "premium_budget_bps": 35.0,
        },
        "leap_call": {
            "target_dte": 390,
            "long_call_moneyness": 0.85,
            "premium_budget_bps": 125.0,
            "role": "cash_replacement_convexity",
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


def _ceil_strike(spot: float, moneyness: float) -> int:
    return max(1, int(math.ceil(float(spot) * float(moneyness))))


def _candidate_match_reasons(
    strategy: str,
    regime_summary: dict[str, Any] | None,
    policy: dict[str, Any],
) -> list[str]:
    regime_summary = dict(regime_summary or {})
    activation = dict(policy.get("activation") or {}).get(strategy) or {}
    checks = [
        ("composite_regime", "composite_regimes"),
        ("volatility_state", "volatility_states"),
        ("breadth_state", "breadth_states"),
        ("macro_state", "macro_states"),
    ]
    reasons: list[str] = []
    for regime_key, activation_key in checks:
        current = _norm_text(regime_summary.get(regime_key))
        if _matches_any(current, activation.get(activation_key)):
            reasons.append(f"{regime_key}={current}")
    return reasons


def _strategy_legs(strategy: str, spot: float, cfg: dict[str, Any]) -> list[dict[str, Any]]:
    if strategy == "protective_put":
        return [
            {
                "side": "BUY",
                "kind": "PUT",
                "strike": _floor_strike(spot, float(_to_float(cfg.get("long_put_moneyness")) or 1.0)),
            }
        ]
    if strategy == "put_spread":
        long_strike = _floor_strike(spot, float(_to_float(cfg.get("long_put_moneyness")) or 1.0))
        short_strike = _floor_strike(spot, float(_to_float(cfg.get("short_put_moneyness")) or 0.92))
        if short_strike >= long_strike:
            short_strike = max(1, long_strike - 1)
        return [
            {"side": "BUY", "kind": "PUT", "strike": long_strike},
            {"side": "SELL", "kind": "PUT", "strike": short_strike},
        ]
    if strategy == "covered_call":
        return [
            {
                "side": "SELL",
                "kind": "CALL",
                "strike": _ceil_strike(spot, float(_to_float(cfg.get("short_call_moneyness")) or 1.05)),
            }
        ]
    if strategy == "long_straddle":
        strike = max(1, int(round(spot)))
        return [
            {"side": "BUY", "kind": "CALL", "strike": strike},
            {"side": "BUY", "kind": "PUT", "strike": strike},
        ]
    if strategy == "call_butterfly":
        lower = _floor_strike(spot, float(_to_float(cfg.get("lower_call_moneyness")) or 0.98))
        center = max(lower + 1, int(round(spot * float(_to_float(cfg.get("center_call_moneyness")) or 1.0))))
        upper = max(center + 1, _ceil_strike(spot, float(_to_float(cfg.get("upper_call_moneyness")) or 1.02)))
        return [
            {"side": "BUY", "kind": "CALL", "strike": lower, "ratio": 1},
            {"side": "SELL", "kind": "CALL", "strike": center, "ratio": 2},
            {"side": "BUY", "kind": "CALL", "strike": upper, "ratio": 1},
        ]
    if strategy == "leap_call":
        return [
            {
                "side": "BUY",
                "kind": "CALL",
                "strike": _floor_strike(spot, float(_to_float(cfg.get("long_call_moneyness")) or 0.85)),
            }
        ]
    return []


def _build_strategy_candidate(
    *,
    strategy: str,
    reasons: list[str],
    policy: dict[str, Any],
    equity: float | None,
    cash: float | None,
    invested_ratio: float | None,
    spot: float | None,
    asof_date: str | None,
) -> dict[str, Any]:
    cfg = dict((policy.get("strategies") or {}).get(strategy) or {})
    target_dte = int(_to_float(cfg.get("target_dte")) or 0)
    expiry = _pick_expiry(asof_date, target_dte)
    premium_bps = _to_float(cfg.get("premium_budget_bps"))
    if premium_bps is None:
        premium_bps = _to_float(policy.get("premium_budget_bps")) or 0.0
    premium_budget = (float(equity) * float(premium_bps) / 10000.0) if equity is not None else None
    contract_notional = (float(spot) * 100.0) if spot is not None and spot > 0 else None
    notional_basis = float(equity or 0.0) * float(invested_ratio if invested_ratio is not None else 1.0)
    if strategy == "covered_call":
        notional_basis = max(0.0, notional_basis)
    elif strategy == "leap_call":
        notional_basis = max(0.0, float(cash or 0.0) + (float(equity or 0.0) * 0.05))

    contracts_float = (
        notional_basis / contract_notional
        if contract_notional and contract_notional > 0 and notional_basis > 0
        else 0.0
    )
    min_contract_premium = float(_to_float(policy.get("min_contract_premium")) or 50.0)
    feasible = bool(
        equity and equity > 0
        and spot and spot > 0
        and premium_budget is not None
        and premium_budget >= min_contract_premium
    )
    if strategy == "covered_call" and bool(cfg.get("requires_covered_inventory")):
        # We do not yet pass per-underlying inventory into this overlay; keep it
        # as a paper-review candidate until covered-lot validation is wired.
        feasible = False
        reasons = [*reasons, "covered inventory validation not wired"]
    contracts_recommended = max(1, int(math.floor(contracts_float + 1e-9))) if feasible else 0
    return {
        "strategy": strategy,
        "status": "CANDIDATE" if reasons else "NO_REGIME_MATCH",
        "reasons": reasons,
        "feasible": feasible,
        "target_dte": target_dte,
        "expiry": expiry,
        "premium_budget_dollars": premium_budget,
        "contract_notional": contract_notional,
        "contracts_float": contracts_float,
        "contracts_recommended": contracts_recommended,
        "role": cfg.get("role") or ("income_overlay" if strategy == "covered_call" else "volatility_overlay"),
        "legs": _strategy_legs(strategy, float(spot), cfg) if spot is not None and spot > 0 else [],
    }


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
    candidate_strategies: list[dict[str, Any]] = []
    for candidate_name in [
        "protective_put",
        "put_spread",
        "covered_call",
        "long_straddle",
        "call_butterfly",
        "leap_call",
    ]:
        candidate_reasons = _candidate_match_reasons(candidate_name, regime_summary, policy)
        if candidate_reasons:
            candidate_strategies.append(
                _build_strategy_candidate(
                    strategy=candidate_name,
                    reasons=candidate_reasons,
                    policy=policy,
                    equity=equity,
                    cash=cash,
                    invested_ratio=invested_ratio,
                    spot=spot,
                    asof_date=asof_date,
                )
            )
    if strategy is None and base_status == "INACTIVE" and candidate_strategies:
        strategy = str(candidate_strategies[0].get("strategy") or "")
        reasons = list(candidate_strategies[0].get("reasons") or reasons)
        base_status = "ACTIVE"
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
    elif strategy in {"protective_put", "put_spread"}:
        strategy_cfg = dict((policy.get("strategies") or {}).get(strategy) or {})
        hedge_ratio = max(0.0, min(1.0, float(_to_float(strategy_cfg.get("hedge_ratio")) or 0.0)))
        target_dte = int(_to_float(strategy_cfg.get("target_dte")) or 0)
        strategy_premium_bps = _to_float(strategy_cfg.get("premium_budget_bps"))
        if strategy_premium_bps is None:
            strategy_premium_bps = _to_float(policy.get("premium_budget_bps")) or 0.0
        premium_budget_dollars = float(equity) * float(strategy_premium_bps) / 10000.0
        target_protected_notional = float(equity) * float(invested_ratio if invested_ratio is not None else 1.0) * hedge_ratio
        contract_notional = float(spot) * 100.0
        contracts_float = (
            target_protected_notional / contract_notional
            if contract_notional > 0
            else 0.0
        )
        min_contract_premium = float(_to_float(policy.get("min_contract_premium")) or 50.0)
        contracts_recommended = 0
        feasible = False
        if premium_budget_dollars >= min_contract_premium:
            contracts_recommended = max(1, int(math.floor(contracts_float + 1e-9)))
            feasible = True
            status = "READY_SHADOW_RECOMMENDATION"
        else:
            status = "WATCH_ONLY_CONTRACT_TOO_LARGE"
            reasons.append(
                f"premium budget ${premium_budget_dollars:.2f} below minimum ${min_contract_premium:.2f} per contract"
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
    elif strategy:
        selected_candidate = next(
            (dict(item) for item in candidate_strategies if item.get("strategy") == strategy),
            {},
        )
        if selected_candidate:
            feasible = bool(selected_candidate.get("feasible"))
            contracts_recommended = int(selected_candidate.get("contracts_recommended") or 0)
            status = "READY_SHADOW_RECOMMENDATION" if feasible else "WATCH_ONLY_REVIEW_CANDIDATE"
            if not feasible:
                reasons = list(selected_candidate.get("reasons") or reasons)
            recommendation = {
                "strategy": strategy,
                "feasible": feasible,
                "target_hedge_ratio": None,
                "target_protected_notional": None,
                "contract_notional": selected_candidate.get("contract_notional"),
                "contracts_float": selected_candidate.get("contracts_float"),
                "contracts_recommended": contracts_recommended,
                "target_dte": selected_candidate.get("target_dte"),
                "expiry": selected_candidate.get("expiry"),
                "premium_budget_dollars": selected_candidate.get("premium_budget_dollars"),
                "max_premium_per_contract": (
                    float(selected_candidate.get("premium_budget_dollars") or 0.0) / float(contracts_recommended)
                    if contracts_recommended > 0
                    else None
                ),
                "long_put": None,
                "short_put": None,
                "legs": selected_candidate.get("legs") or [],
                "role": selected_candidate.get("role"),
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
        "candidate_strategies": candidate_strategies,
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
        "## Strategy Candidates",
        "",
    ]
    candidates = list(payload.get("candidate_strategies") or [])
    if candidates:
        for candidate in candidates:
            lines.append(
                f"- {candidate.get('strategy')}: feasible={'YES' if candidate.get('feasible') else 'NO'}, "
                f"contracts={candidate.get('contracts_recommended') or 0}, "
                f"expiry={candidate.get('expiry') or 'N/A'}, role={candidate.get('role') or 'N/A'}"
            )
    else:
        lines.append("- none")
    lines.extend([
        "",
        "## Trigger Reasons",
        "",
    ])
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
