from __future__ import annotations

import datetime as dt
import json
import math
from pathlib import Path
from typing import Any

from brokers.alpaca_broker import AlpacaBroker
from core.execution_authority_policy import (
    OPTIONS_CAPITAL_EXECUTION_AUTHORITY,
    OPTIONS_MUTATION_REASON,
)

DEFAULT_EXECUTION_POLICY: dict[str, Any] = {
    "benchmark": "SPY",
    "mode": "live_execution_review",
    "north_star": "Promote options to live only after the paper-review lane is ready and the account is explicitly enabled.",
    "allowed_strategies": ["protective_put"],
    "default_order_type": "market",
    "allow_live_submission": False,
    "max_contracts": 1,
    "require_paper_ready": True,
    "require_allocator_ready": True,
}


def _now_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def _load_policy(path: str | Path | None) -> dict[str, Any]:
    policy = json.loads(json.dumps(DEFAULT_EXECUTION_POLICY))
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


def _occ_strike_code(strike: float) -> str:
    return f"{int(round(float(strike) * 1000.0)):08d}"


def build_option_symbol(
    *,
    underlying: str,
    expiry: str,
    option_type: str,
    strike: float,
) -> str:
    root = str(underlying or "").strip().upper()[:6]
    expiry_text = str(expiry or "").strip()
    expiry_norm = expiry_text.replace("-", "")
    if len(expiry_norm) == 8:
        try:
            expiry_norm = dt.date.fromisoformat(expiry_text).strftime("%y%m%d")
        except Exception as exc:
            raise ValueError(f"invalid option expiry: {expiry}") from exc
    elif len(expiry_norm) != 6:
        raise ValueError(f"invalid option expiry: {expiry}")
    cp = str(option_type or "").strip().upper()
    if cp not in {"C", "P", "CALL", "PUT"}:
        raise ValueError(f"invalid option type: {option_type}")
    cp_code = "C" if cp in {"C", "CALL"} else "P"
    return f"{root}{expiry_norm}{cp_code}{_occ_strike_code(strike)}"


def _paper_ready(review: dict[str, Any] | None) -> bool:
    review = dict(review or {})
    return bool(review.get("paper_ready"))


def _allocator_ready(review: dict[str, Any] | None) -> bool:
    review = dict(review or {})
    return str(review.get("allocator_review_status") or "").strip().lower() == "ready"


def _allowed_strategies(policy: dict[str, Any]) -> set[str]:
    return {
        str(strategy).strip().lower()
        for strategy in list(policy.get("allowed_strategies") or [])
        if str(strategy).strip()
    }


def _live_plan_from_paper_review(review: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    paper_plan = dict(review.get("paper_plan") or {})
    underlying = str(policy.get("benchmark") or review.get("benchmark") or "SPY").upper()
    strategy = str(paper_plan.get("strategy") or "").strip().lower()
    allowed = _allowed_strategies(policy)
    if not allowed:
        raise PermissionError("no options strategies are allowed for live submission")
    if strategy not in allowed:
        raise PermissionError(f"strategy {strategy or 'unknown'} is not allowed for live submission")
    expiry = str(paper_plan.get("expiry") or "").strip()
    target_dte = int(_to_float(paper_plan.get("target_dte")) or 0)
    contracts = max(0, int(_to_float(paper_plan.get("contracts_recommended")) or 0))
    contracts = min(contracts, int(_to_float(policy.get("max_contracts")) or 1))
    if contracts <= 0:
        raise ValueError("non-positive contract count in paper plan")
    if strategy == "protective_put":
        option_type = "PUT"
        strike = _to_float((paper_plan.get("long_put") or {}).get("strike"))
        if strike is None:
            raise ValueError("missing long_put strike in paper plan")
        option_symbol = build_option_symbol(
            underlying=underlying,
            expiry=expiry,
            option_type=option_type,
            strike=strike,
        )
        return {
            "strategy": strategy,
            "underlying": underlying,
            "option_symbol": option_symbol,
            "option_type": option_type,
            "side": "BUY",
            "contracts": contracts,
            "expiry": expiry,
            "target_dte": target_dte,
            "strike": strike,
            "paper_trade_type": "review_only",
            "order_type": str(policy.get("default_order_type") or "market").lower(),
        }
    if strategy == "put_spread":
        raise NotImplementedError(
            "live execution for put spreads is not enabled yet; promote protective_put first"
        )
    raise ValueError(f"unsupported paper review strategy: {strategy}")


def build_options_execution_review(
    *,
    trade_date: str,
    asof_date: str | None,
    paper_review: dict[str, Any] | None,
    policy_path: str | Path | None = Path("config/options_execution_policy.json"),
    allow_live_submission: bool = False,
    broker: AlpacaBroker | None = None,
) -> dict[str, Any]:
    policy = _load_policy(policy_path)
    paper_review = dict(paper_review or {})
    paper_status = str(paper_review.get("paper_review_status") or "").strip().lower()
    configured_live_submission = bool(
        allow_live_submission and policy.get("allow_live_submission")
    )
    # Choice 2: options remain research/review-only.  This is deliberately not
    # configurable by policy JSON or environment state.
    live_submission_allowed = False
    allocator_ready = _allocator_ready(paper_review)
    paper_ready = _paper_ready(paper_review)
    require_allocator_ready = bool(policy.get("require_allocator_ready", True))
    require_paper_ready = bool(policy.get("require_paper_ready", True))
    reasons: list[str] = []

    execution_status = "DISABLED"
    ready_for_submission = False
    if not paper_review:
        reasons.append("missing paper review payload")
    elif str(paper_review.get("mode") or "").strip().lower() not in {"", "paper_review"}:
        reasons.append("paper review payload has unexpected mode")
    elif require_allocator_ready and not allocator_ready:
        reasons.append("allocator review gate not ready")
        execution_status = "WATCH_ALLOCATOR_BLOCKED"
    elif require_paper_ready and not paper_ready:
        reasons.append(f"paper review not ready ({paper_status or 'unknown'})")
        execution_status = "WATCH_PAPER_REVIEW_BLOCKED"
    else:
        try:
            live_plan = _live_plan_from_paper_review(paper_review, policy)
        except NotImplementedError as exc:
            reasons.append(str(exc))
            execution_status = "WATCH_SPREAD_LIVE_NOT_READY"
            live_plan = {}
        except PermissionError as exc:
            reasons.append(str(exc))
            execution_status = "WATCH_STRATEGY_NOT_ALLOWED"
            live_plan = {}
        except Exception as exc:
            reasons.append(str(exc))
            execution_status = "WATCH_PLAN_UNAVAILABLE"
            live_plan = {}
        else:
            ready_for_submission = True
            execution_status = "READY_FOR_LIVE_REVIEW"
    plan_ready_for_review = ready_for_submission
    # Keep the legacy field honest: nothing in this module is ready for capital
    # submission while Choice 2 is active.  The candidate itself remains
    # available under ``live_plan`` for review.
    ready_for_submission = False
    if configured_live_submission:
        reasons.append(OPTIONS_MUTATION_REASON)
        if plan_ready_for_review:
            execution_status = "BLOCKED_OWNER_POLICY"
    submission: dict[str, Any] = {
        "attempted": False,
        "submitted": False,
        "alpaca_order_id": None,
        "alpaca_status": None,
        "error": None,
    }
    if ready_for_submission and live_submission_allowed and broker is not None:
        live_plan = _live_plan_from_paper_review(paper_review, policy)
        qty = int(_to_float(live_plan.get("contracts")) or 0)
        if qty <= 0:
            raise ValueError("refusing to submit non-positive option contract quantity")
        client_order_id = f"opt:{trade_date}:{live_plan['strategy']}:{live_plan['option_symbol']}"
        submission["attempted"] = True
        if live_plan["order_type"] == "limit":
            raise NotImplementedError("limit orders are not wired for live options yet")
        submitted = broker.submit_option_market_order(
            symbol=str(live_plan["option_symbol"]),
            qty=float(qty),
            side=str(live_plan["side"]),
            client_order_id=client_order_id,
        )
        submission.update(
            {
                "submitted": True,
                "alpaca_order_id": str(submitted.get("id") or ""),
                "alpaca_status": str(submitted.get("status") or ""),
                "client_order_id": client_order_id,
            }
        )
        execution_status = "SUBMITTED"
    review = {
        "generated_at": _now_utc(),
        "trade_date": trade_date,
        "asof_date": asof_date,
        "benchmark": str(policy.get("benchmark") or paper_review.get("benchmark") or "SPY"),
        "mode": str(policy.get("mode") or "live_execution_review"),
        "north_star": str(policy.get("north_star") or ""),
        "policy": {
            "allow_live_submission": live_submission_allowed,
            "configured_allow_live_submission": configured_live_submission,
            "execution_authority": OPTIONS_CAPITAL_EXECUTION_AUTHORITY,
            "max_contracts": int(_to_float(policy.get("max_contracts")) or 1),
            "allowed_strategies": list(policy.get("allowed_strategies") or []),
        },
        "paper_review_status": paper_review.get("paper_review_status"),
        "paper_ready": paper_ready,
        "allocator_review_status": paper_review.get("allocator_review_status"),
        "ready_for_review": plan_ready_for_review,
        "ready_for_submission": ready_for_submission,
        "execution_status": execution_status,
        "execution_reasons": reasons,
        "live_plan": live_plan if plan_ready_for_review else {},
        "submission": submission,
        "paper_review": paper_review,
    }
    return review


def build_options_execution_markdown(payload: dict[str, Any]) -> str:
    live_plan = dict(payload.get("live_plan") or {})
    submission = dict(payload.get("submission") or {})
    lines = [
        "# Options Overlay Execution Review",
        "",
        f"- Trade date: {payload.get('trade_date') or 'N/A'}",
        f"- As of: {payload.get('asof_date') or 'N/A'}",
        f"- Execution status: {str(payload.get('execution_status') or 'unknown').upper()}",
        f"- Live submission allowed: {bool((payload.get('policy') or {}).get('allow_live_submission'))}",
        "",
        "## Live Plan",
        "",
        f"- Strategy: {live_plan.get('strategy') or 'none'}",
        f"- Option symbol: {live_plan.get('option_symbol') or 'N/A'}",
        f"- Contracts: {live_plan.get('contracts') or 0}",
        f"- Side: {live_plan.get('side') or 'N/A'}",
        f"- Expiry: {live_plan.get('expiry') or 'N/A'}",
        f"- Strike: {live_plan.get('strike') if live_plan.get('strike') is not None else 'N/A'}",
        "",
        "## Submission",
        "",
        f"- Attempted: {bool(submission.get('attempted'))}",
        f"- Submitted: {bool(submission.get('submitted'))}",
        f"- Alpaca order id: {submission.get('alpaca_order_id') or 'N/A'}",
        f"- Alpaca status: {submission.get('alpaca_status') or 'N/A'}",
    ]
    for reason in payload.get("execution_reasons") or ["none"]:
        lines.append(f"- Reason: {reason}")
    return "\n".join(lines) + "\n"


def write_options_execution_review(
    *,
    run_root: str | Path,
    output_dir: str | Path,
    trade_date: str,
    asof_date: str | None,
    paper_review: dict[str, Any] | None,
    policy_path: str | Path | None = Path("config/options_execution_policy.json"),
    allow_live_submission: bool = False,
    broker: AlpacaBroker | None = None,
) -> dict[str, Any]:
    payload = build_options_execution_review(
        trade_date=trade_date,
        asof_date=asof_date,
        paper_review=paper_review,
        policy_path=policy_path,
        allow_live_submission=allow_live_submission,
        broker=broker,
    )
    markdown = build_options_execution_markdown(payload)

    run_root_path = Path(run_root)
    out_dir = Path(output_dir)
    run_root_path.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    run_json_path = run_root_path / "options_execution_review.json"
    dated_json_path = out_dir / f"options_execution_review_{trade_date}.json"
    dated_md_path = out_dir / f"options_execution_review_{trade_date}.md"
    latest_json_path = out_dir / "options_execution_review_latest.json"

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
