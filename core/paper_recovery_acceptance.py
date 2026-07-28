"""Acceptance gate for the temporary paper recovery observation policy."""

from __future__ import annotations

from typing import Any, Mapping


def _metric_map(replay: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {
        str(row.get("policy_id")): row
        for row in replay.get("metrics") or []
        if isinstance(row, Mapping) and row.get("policy_id")
    }


def evaluate_paper_recovery_acceptance(
    *,
    replay: Mapping[str, Any],
    config: Mapping[str, Any],
    live_control: Mapping[str, Any],
) -> dict[str, Any]:
    gates = config.get("acceptance_gates") or {}
    metrics = _metric_map(replay)
    baseline = metrics.get("observed_daily_targets") or {}
    candidate_id = str(config.get("policy_id") or "")
    replay_candidate_id = (
        "observed_weekly_rotation_guard"
        if candidate_id == "weekly_rotation_guard_v1"
        else candidate_id
    )
    candidate = metrics.get(replay_candidate_id) or {}

    checks: list[dict[str, Any]] = []

    def check(name: str, passed: bool, actual: Any, required: Any) -> None:
        checks.append(
            {
                "name": name,
                "passed": bool(passed),
                "actual": actual,
                "required": required,
            }
        )

    min_obs = int(gates.get("minimum_replay_observations") or 0)
    obs = int(candidate.get("observation_count") or 0)
    check("minimum_replay_observations", obs >= min_obs, obs, f">={min_obs}")

    max_turnover = float(gates.get("maximum_average_one_way_turnover") or 0.0)
    turnover = candidate.get("average_one_way_turnover")
    check(
        "maximum_average_one_way_turnover",
        turnover is not None and float(turnover) <= max_turnover,
        turnover,
        f"<={max_turnover}",
    )

    baseline_return = baseline.get("total_return")
    candidate_return = candidate.get("total_return")
    return_improvement = (
        float(candidate_return) - float(baseline_return)
        if candidate_return is not None and baseline_return is not None
        else None
    )
    min_return_improvement = float(
        gates.get("minimum_return_improvement_vs_daily") or 0.0
    )
    check(
        "minimum_return_improvement_vs_daily",
        return_improvement is not None
        and return_improvement >= min_return_improvement,
        return_improvement,
        f">={min_return_improvement}",
    )

    baseline_drawdown = baseline.get("max_drawdown")
    candidate_drawdown = candidate.get("max_drawdown")
    drawdown_improvement = (
        float(candidate_drawdown) - float(baseline_drawdown)
        if candidate_drawdown is not None and baseline_drawdown is not None
        else None
    )
    min_drawdown_improvement = float(
        gates.get("minimum_drawdown_improvement_vs_daily") or 0.0
    )
    check(
        "minimum_drawdown_improvement_vs_daily",
        drawdown_improvement is not None
        and drawdown_improvement >= min_drawdown_improvement,
        drawdown_improvement,
        f">={min_drawdown_improvement}",
    )

    max_missing = float(gates.get("maximum_missing_price_weight") or 0.0)
    missing = candidate.get("max_missing_price_weight")
    check(
        "maximum_missing_price_weight",
        missing is not None and float(missing) <= max_missing,
        missing,
        f"<={max_missing}",
    )

    check(
        "explicit_paper_observation_approval",
        config.get("enabled") is True
        and config.get("paper_only") is True
        and config.get("live_eligible") is False
        and config.get("approval_status")
        == "APPROVED_FOR_PAPER_OBSERVATION",
        {
            "enabled": config.get("enabled"),
            "paper_only": config.get("paper_only"),
            "live_eligible": config.get("live_eligible"),
            "approval_status": config.get("approval_status"),
        },
        "enabled paper-only approval; live_eligible=false",
    )

    check(
        "live_kill_switch_engaged",
        live_control.get("kill_switch_engaged") is True,
        live_control.get("kill_switch_engaged"),
        True,
    )
    check(
        "live_account_flat",
        int(
            live_control.get("positions_count")
            if live_control.get("positions_count") is not None
            else -1
        )
        == 0
        and int(
            live_control.get("open_orders_count")
            if live_control.get("open_orders_count") is not None
            else -1
        )
        == 0
        and float(live_control.get("long_market_value") or 0.0) == 0.0
        and float(live_control.get("short_market_value") or 0.0) == 0.0,
        {
            "positions_count": live_control.get("positions_count"),
            "open_orders_count": live_control.get("open_orders_count"),
            "long_market_value": live_control.get("long_market_value"),
            "short_market_value": live_control.get("short_market_value"),
        },
        "all zero",
    )

    passed = all(item["passed"] for item in checks)
    return {
        "schema_version": "caerus_paper_recovery_acceptance_v1",
        "status": (
            "APPROVED_FOR_PAPER_OBSERVATION"
            if passed
            else "BLOCKED"
        ),
        "policy_id": candidate_id,
        "replay_policy_id": replay_candidate_id,
        "checks": checks,
        "paper_enablement_allowed": passed,
        "live_review_eligible": False,
        "live_rearm_allowed": False,
        "forward_observation_requirements": {
            "paper_sessions": int(
                gates.get("required_forward_paper_sessions_before_live_review")
                or 0
            ),
            "clean_target_attainment_sessions": int(
                gates.get("required_clean_target_attainment_sessions") or 0
            ),
        },
    }
