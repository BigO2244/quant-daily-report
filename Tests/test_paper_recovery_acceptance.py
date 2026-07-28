import json
from pathlib import Path

from core.paper_recovery_acceptance import evaluate_paper_recovery_acceptance
from scripts.research.certify_paper_recovery_candidate import _live_control


def test_acceptance_passes_replay_and_live_safety_gates() -> None:
    replay = {
        "metrics": [
            {
                "policy_id": "observed_daily_targets",
                "observation_count": 51,
                "total_return": -0.075,
                "max_drawdown": -0.116,
            },
            {
                "policy_id": "observed_weekly_rotation_guard",
                "observation_count": 51,
                "total_return": 0.024,
                "max_drawdown": -0.042,
                "average_one_way_turnover": 0.095,
                "max_missing_price_weight": 0.0,
            },
        ]
    }
    config = {
        "enabled": True,
        "policy_id": "weekly_rotation_guard_v1",
        "approval_status": "APPROVED_FOR_PAPER_OBSERVATION",
        "paper_only": True,
        "live_eligible": False,
        "acceptance_gates": {
            "minimum_replay_observations": 50,
            "maximum_average_one_way_turnover": 0.15,
            "minimum_return_improvement_vs_daily": 0.05,
            "minimum_drawdown_improvement_vs_daily": 0.05,
            "maximum_missing_price_weight": 0.01,
            "required_forward_paper_sessions_before_live_review": 20,
            "required_clean_target_attainment_sessions": 10,
        },
    }
    live_control = {
        "kill_switch_engaged": True,
        "positions_count": 0,
        "open_orders_count": 0,
        "long_market_value": 0,
        "short_market_value": 0,
    }

    result = evaluate_paper_recovery_acceptance(
        replay=replay,
        config=config,
        live_control=live_control,
    )

    assert result["status"] == "APPROVED_FOR_PAPER_OBSERVATION"
    assert result["paper_enablement_allowed"] is True
    assert result["live_rearm_allowed"] is False


def test_acceptance_blocks_if_live_is_not_flat() -> None:
    result = evaluate_paper_recovery_acceptance(
        replay={"metrics": []},
        config={"acceptance_gates": {}},
        live_control={
            "kill_switch_engaged": True,
            "positions_count": 1,
            "open_orders_count": 0,
        },
    )
    assert result["status"] == "BLOCKED"
    flat_check = next(
        item for item in result["checks"] if item["name"] == "live_account_flat"
    )
    assert flat_check["passed"] is False


def test_live_control_derives_open_orders_from_ledger(tmp_path: Path) -> None:
    kill_path = tmp_path / "kill.json"
    kill_path.write_text(json.dumps({"engaged": True}), encoding="utf-8")
    ledger = tmp_path / "live"
    (ledger / "positions").mkdir(parents=True)
    (ledger / "account_snapshots.jsonl").write_text(
        json.dumps(
            {
                "cash": "460.90",
                "equity": "460.90",
                "long_market_value": "0",
                "short_market_value": "0",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (ledger / "positions" / "positions_latest.json").write_text(
        json.dumps({"positions": []}),
        encoding="utf-8",
    )
    (ledger / "orders.jsonl").write_text(
        "\n".join(
            [
                json.dumps({"id": "closed", "status": "filled"}),
                json.dumps({"id": "working", "status": "new"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    control = _live_control(
        kill_state_path=kill_path,
        live_ledger_root=ledger,
    )

    assert control["positions_count"] == 0
    assert control["open_orders_count"] == 1
    assert control["open_order_ids"] == ["working"]
