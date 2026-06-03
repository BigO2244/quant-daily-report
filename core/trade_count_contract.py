from __future__ import annotations

from typing import Any, Mapping


_FILLED_ORDER_STATUSES = {"FILLED", "FILLED_ESTIMATE"}


def _as_dict(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def compute_trade_count_contract(
    *,
    daily_snapshot: Mapping[str, Any] | None = None,
    paper_summary: Mapping[str, Any] | None = None,
    execution_payload: Mapping[str, Any] | None = None,
    execution_results: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Normalize trade-count semantics across reporting layers.

    Returns explicit count fields plus a best-effort source map so downstream
    artifacts can use one vocabulary without recomputing from different objects.
    """
    daily_snapshot = _as_dict(daily_snapshot)
    paper_summary = _as_dict(paper_summary)
    execution_payload = _as_dict(execution_payload)
    execution_results = _as_dict(execution_results)

    model_proposed = len(_as_list(daily_snapshot.get("proposed_trades")))
    planner_intended = 0
    planner_source = "paper_summary.trade_plan"
    trade_plan = paper_summary.get("trade_plan")
    if isinstance(trade_plan, list):
        planner_intended = len(trade_plan)
    else:
        for key in (
            "planner_intended_trades_count",
            "proposed_trades_intent_count",
            "proposed_trades_intent",
        ):
            if execution_payload.get(key) is not None:
                planner_intended = _to_int(execution_payload.get(key))
                planner_source = f"execution_payload.{key}"
                break
        else:
            planner_intended = len(_as_list(execution_payload.get("trades")))
            planner_source = "execution_payload.trades"

    eligible_source = "execution_payload.executable_trades_count"
    if execution_payload.get("execution_eligible_trades_count") is not None:
        execution_eligible = _to_int(execution_payload.get("execution_eligible_trades_count"))
        eligible_source = "execution_payload.execution_eligible_trades_count"
    elif execution_payload.get("executable_trades_count") is not None:
        execution_eligible = _to_int(execution_payload.get("executable_trades_count"))
    else:
        trades = execution_payload.get("trades")
        if isinstance(trades, list):
            execution_eligible = len(trades)
            eligible_source = "execution_payload.trades"
        else:
            execution_filter = _as_dict(paper_summary.get("execution_filter"))
            execution_eligible = _to_int(execution_filter.get("kept"))
            eligible_source = "paper_summary.execution_filter.kept"

    if execution_results.get("orders_submitted_count") is not None:
        orders_submitted = _to_int(execution_results.get("orders_submitted_count"))
        submitted_source = "execution_results.orders_submitted_count"
    elif execution_results.get("submitted_count") is not None:
        orders_submitted = _to_int(execution_results.get("submitted_count"))
        submitted_source = "execution_results.submitted_count"
    else:
        alpaca_submission_summary = _as_dict(paper_summary.get("alpaca_submission_summary"))
        orders_submitted = _to_int(alpaca_submission_summary.get("submit_success"))
        submitted_source = "paper_summary.alpaca_submission_summary.submit_success"

    if execution_results.get("orders_filled_count") is not None:
        orders_filled = _to_int(execution_results.get("orders_filled_count"))
        filled_source = "execution_results.orders_filled_count"
    elif execution_results.get("filled_count") is not None:
        orders_filled = _to_int(execution_results.get("filled_count"))
        filled_source = "execution_results.filled_count"
    elif execution_results.get("orders_filled") is not None:
        orders_filled = _to_int(execution_results.get("orders_filled"))
        filled_source = "execution_results.orders_filled"
    elif paper_summary.get("orders_filled_count") is not None:
        # Final observed fills from the post-trade re-poll (re-polls broker order
        # status after submission), not the submit-time broker_responses snapshot
        # which is captured while orders are still ACCEPTED/pending.
        orders_filled = _to_int(paper_summary.get("orders_filled_count"))
        filled_source = "paper_summary.orders_filled_count"
    else:
        responses = _as_list(execution_results.get("broker_responses"))
        orders_filled = sum(
            1
            for response in responses
            if str(_as_dict(response).get("status") or "").upper() in _FILLED_ORDER_STATUSES
        )
        filled_source = "execution_results.broker_responses[].status"

    return {
        "model_proposed_trades_count": int(model_proposed),
        "planner_intended_trades_count": int(planner_intended),
        "execution_eligible_trades_count": int(execution_eligible),
        "orders_submitted_count": int(orders_submitted),
        "orders_filled_count": int(orders_filled),
        "sources": {
            "model_proposed_trades_count": "daily_snapshot.proposed_trades",
            "planner_intended_trades_count": planner_source,
            "execution_eligible_trades_count": eligible_source,
            "orders_submitted_count": submitted_source,
            "orders_filled_count": filled_source,
        },
    }
