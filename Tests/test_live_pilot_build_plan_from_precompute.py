from __future__ import annotations

import json
from pathlib import Path

from core.live_pilot_guardrails import validate_live_pilot_plan
from scripts.live_pilot_build_plan_from_precompute import build_live_pilot_plan


def _payload_path(tmp_path: Path, trades: list[dict[str, object]], *, trade_date: str = "2026-06-22") -> Path:
    path = tmp_path / "outputs" / "precompute" / trade_date / "planned_execution_payload.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "trade_date": trade_date,
                "mode": "PAPER",
                "execution_status": "PLANNED",
                "trades": trades,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def _build(
    tmp_path: Path,
    trades: list[dict[str, object]],
    *,
    allow_missing_sleeve: bool = False,
) -> dict[str, object]:
    return build_live_pilot_plan(
        payload_path=_payload_path(tmp_path, trades),
        approved_sleeve="polaris",
        capital_cap=100,
        max_orders=1,
        output_dir=tmp_path / "outputs" / "live_pilot" / "plans",
        allow_missing_sleeve=allow_missing_sleeve,
        allow_fractional=True,
    )


def test_over_cap_buy_is_scaled_to_pilot_cap(tmp_path: Path) -> None:
    plan = _build(
        tmp_path,
        [
            {
                "ticker": "AAPL",
                "side": "BUY",
                "shares": 2,
                "limit_price": 60,
                "sleeve": "polaris",
            }
        ],
    )

    assert plan["status"] == "READY_FOR_MANUAL_APPROVAL"
    selected = plan["selected_order"]
    assert selected["ticker"] == "AAPL"
    assert selected["scaled_to_pilot_cap"] is True
    assert selected["source_order_qty"] == 2
    assert selected["source_notional"] == 120
    assert selected["pilot_notional_cap"] == 100
    assert selected["original_qty"] == 2
    assert selected["pre_normalization_qty"] < 100 / 60
    assert selected["final_qty"] == selected["pilot_qty"]
    assert selected["final_qty"] * selected["normalized_limit_price"] <= 100
    assert selected["notional"] <= 100
    assert plan["trades"] == [selected]


def test_scaled_buy_uses_normalized_limit_price_for_final_qty(tmp_path: Path) -> None:
    plan = _build(
        tmp_path,
        [
            {
                "ticker": "JNJ",
                "side": "BUY",
                "shares": 3,
                "limit_price": 228.38999938964844,
                "sleeve": "polaris",
            }
        ],
    )

    selected = plan["selected_order"]
    assert selected["ticker"] == "JNJ"
    assert selected["original_limit_price"] == 228.38999938964844
    assert selected["normalized_limit_price"] == 228.39
    assert selected["limit_price"] == 228.39
    assert selected["original_qty"] == 3
    assert selected["pre_normalization_qty"] == 0.437847542
    assert selected["final_qty"] == 0.437847541
    assert selected["final_qty"] < 0.4378475426561624
    assert selected["final_qty"] * selected["normalized_limit_price"] <= 100
    assert selected["notional"] <= 100


def test_no_sells_selected(tmp_path: Path) -> None:
    plan = _build(
        tmp_path,
        [
            {
                "ticker": "AAPL",
                "side": "SELL",
                "shares": 1,
                "limit_price": 50,
                "sleeve": "polaris",
            }
        ],
    )

    assert plan["selected_order"] is None
    assert "unsupported_side:SELL" in plan["rejected_orders_with_reasons"][0]["reasons"]


def test_unsupported_assets_not_selected(tmp_path: Path) -> None:
    plan = _build(
        tmp_path,
        [
            {
                "ticker": "BTCUSD",
                "side": "BUY",
                "shares": 1,
                "limit_price": 50,
                "sleeve": "polaris",
                "asset_class": "crypto",
            }
        ],
    )

    assert plan["selected_order"] is None
    reasons = plan["rejected_orders_with_reasons"][0]["reasons"]
    assert "unsupported_crypto_symbol" in reasons
    assert "unsupported_asset_class:crypto" in reasons


def test_max_one_order_selected(tmp_path: Path) -> None:
    plan = _build(
        tmp_path,
        [
            {
                "ticker": "MSFT",
                "side": "BUY",
                "shares": 1,
                "limit_price": 80,
                "sleeve": "polaris",
            },
            {
                "ticker": "AAPL",
                "side": "BUY",
                "shares": 1,
                "limit_price": 50,
                "sleeve": "polaris",
            },
        ],
    )

    assert plan["status"] == "READY_FOR_MANUAL_APPROVAL"
    assert plan["selected_order"]["ticker"] == "MSFT"
    assert len(plan["trades"]) == 1
    assert any(
        "max_one_order_selected" in row["reasons"]
        for row in plan["rejected_orders_with_reasons"]
    )


def test_missing_sleeve_override_selects_first_buy_in_source_order(tmp_path: Path) -> None:
    plan = _build(
        tmp_path,
        [
            {
                "ticker": "MSFT",
                "side": "BUY",
                "shares": 4,
                "limit_price": 50,
            },
            {
                "ticker": "AAPL",
                "side": "BUY",
                "shares": 1,
                "limit_price": 20,
            },
        ],
        allow_missing_sleeve=True,
    )

    selected = plan["selected_order"]
    assert plan["status"] == "READY_FOR_MANUAL_APPROVAL"
    assert selected["ticker"] == "MSFT"
    assert selected["sleeve"] == "polaris"
    assert selected["sleeve_source"] == "missing_in_source_overridden_for_live_pilot"
    assert selected["approved_sleeve_override"] == "polaris"
    assert selected["scaled_to_pilot_cap"] is True
    assert selected["source_notional"] == 200
    assert selected["pilot_qty"] == 2
    assert selected["final_qty"] == 2
    assert selected["notional"] == 100


def test_missing_sleeve_override_does_not_allow_sells(tmp_path: Path) -> None:
    plan = _build(
        tmp_path,
        [
            {
                "ticker": "AAPL",
                "side": "SELL",
                "shares": 1,
                "limit_price": 50,
            }
        ],
        allow_missing_sleeve=True,
    )

    assert plan["selected_order"] is None
    assert "unsupported_side:SELL" in plan["rejected_orders_with_reasons"][0]["reasons"]


def test_missing_limit_price_blocks(tmp_path: Path) -> None:
    plan = _build(
        tmp_path,
        [
            {
                "ticker": "AAPL",
                "side": "BUY",
                "shares": 1,
                "sleeve": "polaris",
            }
        ],
    )

    assert plan["selected_order"] is None
    assert "missing_limit_price" in plan["rejected_orders_with_reasons"][0]["reasons"]


def test_output_plan_matches_live_pilot_execute_schema(tmp_path: Path) -> None:
    plan = _build(
        tmp_path,
        [
            {
                "ticker": "AAPL",
                "side": "BUY",
                "shares": 1,
                "limit_price": 50,
                "sleeve": "polaris",
            }
        ],
    )
    json_path = Path(str(plan["json_path"]))
    md_path = Path(str(plan["markdown_path"]))

    assert json_path.exists()
    assert md_path.exists()
    written = json.loads(json_path.read_text(encoding="utf-8"))
    assert written["trades"] == plan["trades"]
    assert written["approved_sleeve"] == "polaris"
    assert written["capital_cap"] == 100
    assert written["selected_order"]["limit_price"] == 50
    assert "scripts/live_pilot_execute.py --plan" in written["required_dry_run_command"]
    assert "CAERUS_LIVE_PILOT_DRY_RUN=0" in written["required_live_command"]

    validation = validate_live_pilot_plan(
        written["trades"],
        capital_cap_usd=100,
        max_orders=1,
        run_id="schema-test",
    )
    assert validation.status == "PASS"
