from __future__ import annotations

import datetime as dt
import json
import math
from pathlib import Path
from typing import Any

from core.options_overlay_shadow import build_options_overlay_shadow

DEFAULT_PAPER_POLICY: dict[str, Any] = {
    "benchmark": "SPY",
    "mode": "paper_review",
    "north_star": "Promote options from shadow to paper only after the overlay is feasible and the review gate is clear.",
    "roll_before_dte": 14,
    "max_holding_dte": 45,
    "min_contract_utilization": 0.75,
}


def _now_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def _load_policy(path: str | Path | None) -> dict[str, Any]:
    policy = json.loads(json.dumps(DEFAULT_PAPER_POLICY))
    if path is None:
        return policy
    config_path = Path(path)
    if not config_path.exists():
        return policy
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        return policy
    if isinstance(payload, dict):
        policy.update(payload)
    return policy


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


def _paper_review_status(
    *,
    shadow_payload: dict[str, Any],
    live_regime_review: dict[str, Any] | None,
) -> tuple[str, bool, list[str]]:
    trigger = dict(shadow_payload.get("trigger") or {})
    recommendation = dict(shadow_payload.get("recommendation") or {})
    review_gate = str(((live_regime_review or {}).get("promotion_gate") or {}).get("overall_status") or "").strip().lower()

    reasons = list(trigger.get("reasons") or [])
    if not trigger.get("active"):
        return "INACTIVE", False, reasons or ["shadow overlay inactive"]
    if review_gate == "not_ready":
        reasons.append("allocator review gate not ready")
        return "WATCH_ALLOCATOR_BLOCKED", False, reasons
    if not bool(recommendation.get("feasible")) or int(recommendation.get("contracts_recommended") or 0) <= 0:
        reasons.append("shadow recommendation is not contract-feasible")
        return "WATCH_ONLY_CONTRACT_TOO_LARGE", False, reasons
    if trigger.get("status") != "READY_SHADOW_RECOMMENDATION":
        reasons.append(f"shadow status={trigger.get('status')}")
        return "WATCH_ONLY_SHADOW_NOT_READY", False, reasons
    if review_gate in {"", "watch"}:
        reasons.append(f"allocator review gate={review_gate or 'unknown'}")
    return "READY_FOR_PAPER_REVIEW", True, reasons


def _paper_plan_from_shadow(
    *,
    shadow_payload: dict[str, Any],
    policy: dict[str, Any],
) -> dict[str, Any]:
    recommendation = dict(shadow_payload.get("recommendation") or {})
    target_dte = int(_to_float(recommendation.get("target_dte")) or 0)
    roll_before_dte = max(7, min(int(_to_float(policy.get("roll_before_dte")) or 14), max(7, target_dte - 7)))
    contract_count = int(recommendation.get("contracts_recommended") or 0)
    max_holding_dte = int(_to_float(policy.get("max_holding_dte")) or max(30, target_dte))
    if contract_count <= 0:
        contract_count = 0
    return {
        "strategy": recommendation.get("strategy"),
        "contracts_recommended": contract_count,
        "expiry": recommendation.get("expiry"),
        "target_dte": target_dte,
        "max_holding_dte": max_holding_dte,
        "roll_before_dte": roll_before_dte,
        "target_hedge_ratio": recommendation.get("target_hedge_ratio"),
        "target_protected_notional": recommendation.get("target_protected_notional"),
        "premium_budget_dollars": recommendation.get("premium_budget_dollars"),
        "max_premium_per_contract": recommendation.get("max_premium_per_contract"),
        "long_put": recommendation.get("long_put"),
        "short_put": recommendation.get("short_put"),
        "legs": recommendation.get("legs") or [],
        "role": recommendation.get("role"),
        "paper_trade_type": "review_only",
    }


def build_options_overlay_paper_review(
    *,
    trade_date: str,
    asof_date: str | None,
    regime_summary: dict[str, Any] | None,
    portfolio_equity: float | None,
    portfolio_cash: float | None,
    spy_price: float | None,
    live_regime_review: dict[str, Any] | None = None,
    shadow_payload: dict[str, Any] | None = None,
    policy_path: str | Path | None = Path("config/options_overlay_paper_policy.json"),
) -> dict[str, Any]:
    policy = _load_policy(policy_path)
    shadow_payload = dict(
        shadow_payload
        or build_options_overlay_shadow(
            trade_date=trade_date,
            asof_date=asof_date,
            regime_summary=regime_summary,
            portfolio_equity=portfolio_equity,
            portfolio_cash=portfolio_cash,
            spy_price=spy_price,
            live_regime_review=live_regime_review,
        )
    )
    paper_status, paper_ready, reasons = _paper_review_status(
        shadow_payload=shadow_payload,
        live_regime_review=live_regime_review,
    )
    review = {
        "generated_at": _now_utc(),
        "trade_date": trade_date,
        "asof_date": asof_date,
        "benchmark": str(policy.get("benchmark") or shadow_payload.get("benchmark") or "SPY"),
        "mode": str(policy.get("mode") or "paper_review"),
        "north_star": str(policy.get("north_star") or ""),
        "shadow_status": (shadow_payload.get("trigger") or {}).get("status"),
        "allocator_review_status": ((live_regime_review or {}).get("promotion_gate") or {}).get("overall_status"),
        "paper_review_status": paper_status,
        "paper_ready": paper_ready,
        "paper_reasons": reasons,
        "paper_policy": {
            "roll_before_dte": int(_to_float(policy.get("roll_before_dte")) or 14),
            "max_holding_dte": int(_to_float(policy.get("max_holding_dte")) or 45),
            "min_contract_utilization": _to_float(policy.get("min_contract_utilization")),
        },
        "shadow": shadow_payload,
        "paper_plan": _paper_plan_from_shadow(shadow_payload=shadow_payload, policy=policy),
        "candidate_strategies": shadow_payload.get("candidate_strategies") or [],
    }
    return review


def build_options_overlay_paper_markdown(payload: dict[str, Any]) -> str:
    paper_plan = dict(payload.get("paper_plan") or {})
    shadow = dict(payload.get("shadow") or {})
    recommendation = dict(shadow.get("recommendation") or {})
    lines = [
        "# Options Overlay Paper Review",
        "",
        f"- Trade date: {payload.get('trade_date') or 'N/A'}",
        f"- As of: {payload.get('asof_date') or 'N/A'}",
        f"- Mode: {str(payload.get('mode') or 'paper_review').upper()}",
        f"- Paper review status: {str(payload.get('paper_review_status') or 'unknown').upper()}",
        f"- Shadow status: {shadow.get('trigger', {}).get('status') or 'unknown'}",
        "",
        "## Paper Plan",
        "",
        f"- Strategy: {paper_plan.get('strategy') or 'none'}",
        f"- Contracts: {paper_plan.get('contracts_recommended') or 0}",
        f"- Expiry: {paper_plan.get('expiry') or 'N/A'}",
        f"- Target DTE: {paper_plan.get('target_dte') or 'N/A'}",
        f"- Roll before DTE: {paper_plan.get('roll_before_dte') or 'N/A'}",
        f"- Premium budget: {paper_plan.get('premium_budget_dollars') or 'N/A'}",
        f"- Role: {paper_plan.get('role') or 'N/A'}",
        "",
        "## Shadow Source",
        "",
        f"- Feasible: {'YES' if recommendation.get('feasible') else 'NO'}",
        f"- Target hedge ratio: {recommendation.get('target_hedge_ratio') if recommendation.get('target_hedge_ratio') is not None else 'N/A'}",
        f"- Target protected notional: {recommendation.get('target_protected_notional') if recommendation.get('target_protected_notional') is not None else 'N/A'}",
        "",
        "## Reasons",
        "",
    ]
    for reason in payload.get("paper_reasons") or ["none"]:
        lines.append(f"- {reason}")
    return "\n".join(lines) + "\n"


def write_options_overlay_paper_review(
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
    shadow_payload: dict[str, Any] | None = None,
    policy_path: str | Path | None = Path("config/options_overlay_paper_policy.json"),
) -> dict[str, Any]:
    payload = build_options_overlay_paper_review(
        trade_date=trade_date,
        asof_date=asof_date,
        regime_summary=regime_summary,
        portfolio_equity=portfolio_equity,
        portfolio_cash=portfolio_cash,
        spy_price=spy_price,
        live_regime_review=live_regime_review,
        shadow_payload=shadow_payload,
        policy_path=policy_path,
    )
    markdown = build_options_overlay_paper_markdown(payload)

    run_root_path = Path(run_root)
    out_dir = Path(output_dir)
    run_root_path.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    run_json_path = run_root_path / "options_overlay_paper_review.json"
    dated_json_path = out_dir / f"options_overlay_paper_review_{trade_date}.json"
    dated_md_path = out_dir / f"options_overlay_paper_review_{trade_date}.md"
    latest_json_path = out_dir / "options_overlay_paper_review_latest.json"

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
