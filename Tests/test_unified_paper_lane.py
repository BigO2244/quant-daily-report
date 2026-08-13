"""Unified paper lane ("one engine, two endpoints") — Phase 1 coverage.

The 9:35 ET paper cron (scripts/cron_execute.sh) runs the SAME engine as the
live-pilot lane (plan builder + scripts/live_pilot_execute.py) with MODE=paper
against the Alpaca paper endpoint. These tests pin the safety and isolation
contract:

a) gate stack: paper mode + paper host PASSES with real paper submission
   permitted; paper mode + live host BLOCKS (endpoint fail-closed);
b) peak-equity state-dir isolation between the paper and live lanes;
c) both cron lane scripts source scripts/lane_params.sh AFTER their env-file
   loads (single source of shared behavior params);
d) run-root separation: paper runs land under outputs/paper_lane/runs with the
   same artifact schema, never under outputs/live_pilot;
plus the confirm-flow pointer contract (outputs/workflow/<date>/execution.json).

All tests are hermetic: fake broker, no network, tmp_path-scoped outputs.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from authority.contracts import (
    build_decision_package,
    build_evidence_package,
    build_risk_package,
)
from authority.pipeline import execution_package_from_risk
from core.live_pilot_guardrails import build_live_pilot_gate_result
from core.live_pilot_preflight import validate_alpaca_submission_guardrails
from core.run_pointer import read_trade_stage_pointer
from scripts.live_pilot_build_plan_from_precompute import build_live_pilot_plan
from scripts.live_pilot_execute import run_live_pilot
from scripts.paper_lane_write_execution_pointer import (
    map_terminal_status,
    write_paper_lane_pointers,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
ET = ZoneInfo("America/New_York")

PAPER_HOST = "https://paper-api.alpaca.markets"
LIVE_HOST = "https://api.alpaca.markets"


def _market_open_now() -> dt.datetime:
    return dt.datetime(2026, 3, 17, 9, 35, tzinfo=ET)


def _paper_env(*, dry_run: str = "0", base_url: str = PAPER_HOST) -> dict[str, str]:
    """Env as exported by the rewritten cron_execute.sh (paper lane).

    Deliberately does NOT set CAERUS_LIVE_PILOT_KILL_SWITCH: the cron script no
    longer exports it (the paper gate stack short-circuits before consulting it,
    and pre-disarming a live safety gate via inherited env is a latent hazard).
    These tests passing without it prove the paper lane needs no kill-switch
    suppression.
    """
    return {
        "MODE": "paper",
        "TRADING_MODE": "paper",
        "ALPACA_PAPER": "1",
        "ALPACA_BASE_URL": base_url,
        "CAERUS_LIVE_PILOT_CAPITAL_CAP": "10000",
        "CAERUS_LIVE_PILOT_PLANNING_EQUITY_CAP": "10000",
        "CAERUS_LIVE_PILOT_SLEEVE_ID": "orion",
        "CAERUS_LIVE_PILOT_MAX_ORDERS": "50",
        "CAERUS_LIVE_PILOT_MIN_TRADE_USD": "10",
        "CAERUS_LIVE_PILOT_DRY_RUN": dry_run,
        "CAERUS_LIVE_PILOT_SELLS_ENABLED": "1",
        "CAERUS_LIVE_PILOT_SELL_WHITELIST": "*",
        "CAERUS_LIVE_PILOT_SETTLEMENT_TIMEOUT_SECONDS": "120",
        "CAERUS_LIVE_PILOT_SETTLEMENT_MAX_ATTEMPTS": "61",
        "CAERUS_LIVE_PILOT_SETTLEMENT_BASE_DELAY_SECONDS": "0",
        "CAERUS_LIVE_PILOT_SETTLEMENT_MAX_DELAY_SECONDS": "2",
        "CAERUS_PAPER_REUSE_CONFIRMED_SELL_PROCEEDS": "1",
        "CAERUS_LIVE_PILOT_APPROVED": "1",
        "CAERUS_LIVE_PILOT_CRON_APPROVED": "1",
        "CAERUS_LIVE_PILOT_SUBMIT_APPROVED": "1",
        "CAERUS_TEST_ONLY_ALLOW_LEGACY_FAKE_EXECUTION": "1",
    }


class FakePaperBroker:
    """Hermetic paper broker double (no network)."""

    paper = True
    base_url = PAPER_HOST

    def __init__(self, *, base_url: str = PAPER_HOST, paper: bool = True) -> None:
        self.base_url = base_url
        self.paper = paper
        self.submit_calls = 0

    def get_account(self):
        return {
            "id": "paper-acct-1",
            "status": "ACTIVE",
            "cash": "10000",
            "equity": "10000",
            "buying_power": "10000",
            "portfolio_value": "10000",
        }

    def get_positions(self):
        return []

    def get_asset(self, symbol):
        return {
            "symbol": symbol,
            "status": "active",
            "asset_class": "us_equity",
            "tradable": True,
        }

    def list_orders(self, status="open", limit=100):
        return []

    def get_order(self, order_id):
        return {"id": order_id, "status": "filled"}

    def submit_market_order(self, **kwargs):
        self.submit_calls += 1
        return {
            "id": f"paper-order-{self.submit_calls}",
            "status": "filled",
            "symbol": kwargs.get("symbol"),
            "client_order_id": kwargs.get("client_order_id"),
            "filled_avg_price": "150.00",
            "submitted_at": "2026-03-17T13:35:00+00:00",
            "filled_at": "2026-03-17T13:35:02+00:00",
        }

    def submit_limit_order(self, **kwargs):
        return self.submit_market_order(**kwargs)


class ConfirmedProceedsPaperBroker(FakePaperBroker):
    """Paper broker that reflects a filled sell in positions, cash, and history."""

    def __init__(self) -> None:
        super().__init__()
        self.cash = 150.0
        self.buying_power = 150.0
        self.equity = 500.0
        self.positions = [
            {"symbol": "OLD", "qty": "1", "market_value": "100", "cost_basis": "100"}
        ]
        self.orders_by_id: dict[str, dict[str, object]] = {}
        self.submitted_sides: list[str] = []

    def get_account(self):
        return {
            "id": "paper-acct-1",
            "status": "ACTIVE",
            "cash": str(self.cash),
            "equity": str(self.equity),
            "buying_power": str(self.buying_power),
            "portfolio_value": str(self.equity),
        }

    def get_positions(self):
        return list(self.positions)

    def list_orders(self, status="open", limit=100, after=None):
        del limit, after
        orders = list(self.orders_by_id.values())
        if status == "open":
            return [row for row in orders if row.get("status") not in {"filled", "rejected"}]
        return orders

    def get_order(self, order_id):
        return dict(self.orders_by_id.get(order_id, {}))

    def submit_market_order(self, **kwargs):
        self.submit_calls += 1
        side = str(kwargs.get("side") or "").upper()
        symbol = str(kwargs.get("symbol") or "").upper()
        qty = float(kwargs.get("qty") or 0.0)
        notional = float(kwargs.get("estimated_notional") or 0.0)
        order_id = f"paper-order-{self.submit_calls}"
        order = {
            "id": order_id,
            "status": "filled",
            "symbol": symbol,
            "side": side,
            "qty": str(qty),
            "filled_qty": str(qty),
            "filled_avg_price": str(notional / qty if qty else 0.0),
            "client_order_id": kwargs.get("client_order_id"),
            "submitted_at": "2026-03-17T13:35:00+00:00",
            "filled_at": "2026-03-17T13:35:02+00:00",
        }
        self.orders_by_id[order_id] = order
        self.submitted_sides.append(side)
        if side == "SELL":
            self.cash += notional
            self.buying_power += notional
            self.positions = [row for row in self.positions if row.get("symbol") != symbol]
        elif side == "BUY":
            self.cash -= notional
            self.buying_power -= notional
            self.positions.append(
                {"symbol": symbol, "qty": str(qty), "market_value": str(notional)}
            )
        return order


class PositionTrackingPaperBroker(FakePaperBroker):
    """Paper broker double whose post-submit snapshot reflects filled buys."""

    def __init__(self) -> None:
        super().__init__()
        self.cash = 10_000.0
        self.positions: list[dict[str, object]] = []

    def get_account(self):
        return {
            "id": "paper-acct-1",
            "status": "ACTIVE",
            "cash": str(self.cash),
            "equity": "10000",
            "buying_power": str(self.cash),
            "portfolio_value": "10000",
        }

    def get_positions(self):
        return list(self.positions)

    def submit_market_order(self, **kwargs):
        order = super().submit_market_order(**kwargs)
        qty = float(kwargs.get("qty") or 0.0)
        notional = float(kwargs.get("estimated_notional") or 0.0)
        self.cash -= notional
        self.positions.append(
            {
                "symbol": kwargs.get("symbol"),
                "qty": str(qty),
                "market_value": str(notional),
                "current_price": str(notional / qty if qty else 0.0),
            }
        )
        return order


class FailingSnapshotPaperBroker(FakePaperBroker):
    def __init__(self, error: Exception, *, stage: str = "account") -> None:
        super().__init__()
        self.error = error
        self.stage = stage
        self.account_calls = 0

    def get_account(self):
        self.account_calls += 1
        if self.stage == "account":
            raise self.error
        return super().get_account()

    def get_positions(self):
        if self.stage == "positions":
            raise self.error
        return super().get_positions()

    def list_orders(self, status="open", limit=100):
        if self.stage == "open_orders":
            raise self.error
        return super().list_orders(status=status, limit=limit)


def _plan() -> dict[str, object]:
    return {
        "trades": [
            {
                "ticker": "AAPL",
                "side": "BUY",
                "shares": 1,
                "limit_price": 150,
                "order_type": "market",
                "sleeve": "orion",
            }
        ]
    }


def _whole_share_authority_plan() -> dict[str, object]:
    policy = {
        "schema_version": "caerus.target_attainment_policy.v1",
        "account_scope": "PAPER",
        "share_mode": "WHOLE_SHARES",
        "target_cash_weight": 0.05,
        "minimum_cash_weight": 0.025,
        "fixed_drift_tolerance": 0.02,
        "nearest_feasible_required": True,
        "comparison_epoch_policy": "FIRST_CLEAN_POST_FIX_PAPER_RUN",
        "strict_green_propagation": True,
        "owner_approved_at": "2026-08-11",
    }
    rows = [{"symbol": "AAPL", "target_weight": 0.95, "price": 150.0}]
    evidence = build_evidence_package(
        package_id="evidence:paper-regression",
        trade_date="2026-03-17",
        source_refs=["caerus_orion.json"],
        observations=rows,
    )
    decision = build_decision_package(
        package_id="decision:paper-regression",
        trade_date="2026-03-17",
        evidence=evidence,
        target_rows=rows,
        target_cash_weight=0.05,
        source_refs=["caerus_orion.json"],
    )
    risk = build_risk_package(
        package_id="risk:paper-regression",
        decision=decision,
        approved_target_rows=rows,
        approved_cash_weight=0.05,
        constraints={"target_attainment_policy": policy},
        source_refs=["decision:paper-regression"],
    )
    return {
        "cash_target_weight": 0.05,
        "target_attainment_policy": policy,
        "target_portfolio": rows,
        "approved_execution_package": execution_package_from_risk(risk).to_dict(),
    }


# ---------------------------------------------------------------------------
# (a) Gate stack: paper + paper host passes; paper + live host blocks
# ---------------------------------------------------------------------------


def test_gate_passes_paper_mode_on_paper_host() -> None:
    gate = build_live_pilot_gate_result(
        broker_paper=True,
        base_url=PAPER_HOST,
        env=_paper_env(),
    )
    assert gate.status == "PASS"
    assert gate.reason_code == "paper_submission_endpoint_confirmed"
    assert gate.live_orders_allowed is False  # paper never arms live orders

    # Broker-layer submission guard: paper submission on the paper host is
    # permitted (returns instead of raising).
    result = validate_alpaca_submission_guardrails(
        broker_paper=True,
        base_url=PAPER_HOST,
        env=_paper_env(),
        order_notional=100.0,
    )
    assert result.status == "PASS"


def test_gate_blocks_paper_mode_on_live_host() -> None:
    gate = build_live_pilot_gate_result(
        broker_paper=True,
        base_url=LIVE_HOST,
        env=_paper_env(base_url=LIVE_HOST),
    )
    assert gate.status == "BLOCKED"
    assert gate.reason_code == "paper_broker_non_paper_endpoint"

    with pytest.raises(RuntimeError, match="paper_broker_non_paper_endpoint"):
        validate_alpaca_submission_guardrails(
            broker_paper=True,
            base_url=LIVE_HOST,
            env=_paper_env(base_url=LIVE_HOST),
            order_notional=100.0,
        )


def test_paper_lane_submits_real_paper_orders_through_shared_engine(tmp_path: Path) -> None:
    """MODE=paper + paper host + DRY_RUN=0 must SUBMIT (not dry) paper orders."""
    broker = FakePaperBroker()
    result = run_live_pilot(
        plan=_plan(),
        broker=broker,
        env=_paper_env(dry_run="0"),
        run_id="paper-submit",
        output_root=tmp_path / "outputs" / "paper_lane",
        now_et=_market_open_now(),
    )
    assert result["terminal_status"] == "SUBMITTED"
    assert broker.submit_calls == 1
    assert result["mode"] == "PAPER"

    run_root = tmp_path / "outputs" / "paper_lane" / "runs" / "paper-submit"
    submitted = json.loads((run_root / "live_pilot_orders_submitted.json").read_text())
    assert submitted["orders"][0]["symbol"] == "AAPL"
    assert submitted["orders"][0]["status"] == "filled"


def test_paper_lane_executes_governed_whole_share_authority_package_end_to_end(
    tmp_path: Path,
) -> None:
    """Regression: a PAPER-only authority policy must reach submit and recon."""
    broker = PositionTrackingPaperBroker()
    result = run_live_pilot(
        plan=_whole_share_authority_plan(),
        broker=broker,
        env=_paper_env(dry_run="0"),
        run_id="paper-whole-share-authority-submit",
        output_root=tmp_path / "outputs" / "paper_lane",
        now_et=_market_open_now(),
    )

    assert result["terminal_status"] == "SUBMITTED"
    assert result["mode"] == "PAPER"
    assert result["submitted_count"] == 1
    assert result["filled_count"] == 1
    assert broker.submit_calls == 1

    run_root = (
        tmp_path
        / "outputs"
        / "paper_lane"
        / "runs"
        / "paper-whole-share-authority-submit"
    )
    required = {
        "live_pilot_broker_snapshot_pre.json",
        "live_pilot_broker_snapshot_post.json",
        "live_pilot_orders_submitted.json",
        "live_pilot_reconciliation.json",
        "execution_results.json",
        "operator_summary.json",
        "execution_payload.json",
        "authority_audit_package.json",
    }
    assert required <= {path.name for path in run_root.iterdir()}
    reconciliation = json.loads(
        (run_root / "live_pilot_reconciliation.json").read_text(encoding="utf-8")
    )
    assert reconciliation["status"] == "CLEAN"
    results = json.loads((run_root / "execution_results.json").read_text(encoding="utf-8"))
    assert results["submitted_count"] == 1
    assert results["filled_count"] == 1
    assert results["execution_target_attainment_status"] in {
        "OK_TARGET_ATTAINED",
        "OK_NEAREST_FEASIBLE",
    }


def test_paper_rotation_reuses_only_confirmed_current_run_sell_proceeds(
    tmp_path: Path,
) -> None:
    broker = ConfirmedProceedsPaperBroker()
    env = _paper_env(dry_run="0")
    env["CAERUS_LIVE_PILOT_CAPITAL_CAP"] = "500"
    env["CAERUS_LIVE_PILOT_PLANNING_EQUITY_CAP"] = "500"
    result = run_live_pilot(
        plan={
            "target_portfolio": [
                {
                    "ticker": "NEW",
                    "symbol": "NEW",
                    "side": "BUY",
                    "target_weight": 0.4,
                    "price": 100.0,
                    "order_type": "market",
                    "sleeve": "orion",
                }
            ],
            "cash_target_weight": 0.0,
        },
        broker=broker,
        env=env,
        run_id="paper-confirmed-proceeds",
        output_root=tmp_path / "outputs" / "paper_lane",
        now_et=_market_open_now(),
    )

    assert result["terminal_status"] == "SUBMITTED"
    assert broker.submitted_sides == ["SELL", "BUY"]
    run_root = tmp_path / "outputs" / "paper_lane" / "runs" / "paper-confirmed-proceeds"
    transition = json.loads((run_root / "live_pilot_transition_plan.json").read_text())
    assert transition["buy_orders_intended"][0]["shares"] == pytest.approx(2.0)
    guard = transition["diagnostics"]["post_sell_budget"]["settled_cash_guard"]
    assert guard["settled_cash"] == pytest.approx(150.0)
    assert guard["execution_spendable_cash"] == pytest.approx(250.0)
    assert guard["effective_cash_ceiling"] == pytest.approx(250.0)
    assert guard["cash_ceiling_source"] == (
        "paper_settled_plus_confirmed_current_run_sells"
    )
    settled = json.loads((run_root / "live_pilot_settled_cash.json").read_text())
    assert settled["confirmed_current_run_sell_proceeds"] == pytest.approx(100.0)
    assert settled["confirmed_proceeds_reuse_enabled"] is True
    assert settled["execution_spendable_cash"] == pytest.approx(250.0)


def test_paper_lane_dry_pass_does_not_submit(tmp_path: Path) -> None:
    broker = FakePaperBroker()
    result = run_live_pilot(
        plan=_plan(),
        broker=broker,
        env=_paper_env(dry_run="1"),
        run_id="paper-dry",
        output_root=tmp_path / "outputs" / "paper_lane",
        now_et=_market_open_now(),
    )
    assert result["terminal_status"] == "DRY_RUN"
    assert broker.submit_calls == 0


def test_paper_mode_on_live_host_blocks_before_any_submission(tmp_path: Path) -> None:
    broker = FakePaperBroker(base_url=LIVE_HOST)
    result = run_live_pilot(
        plan=_plan(),
        broker=broker,
        env=_paper_env(dry_run="0", base_url=LIVE_HOST),
        run_id="paper-wrong-host",
        output_root=tmp_path / "outputs" / "paper_lane",
        now_et=_market_open_now(),
    )
    assert result["terminal_status"] == "BLOCKED"
    assert result["reason_code"] == "paper_broker_non_paper_endpoint"
    assert broker.submit_calls == 0


def test_paper_snapshot_timeout_is_classified_for_lane_wide_retry(tmp_path: Path) -> None:
    broker = FailingSnapshotPaperBroker(
        RuntimeError('Alpaca get_account failed: {"code":50410000,"message":"request timed out"}')
    )
    result = run_live_pilot(
        plan=_plan(),
        broker=broker,
        env=_paper_env(dry_run="1"),
        run_id="paper-transient-snapshot",
        output_root=tmp_path / "outputs" / "paper_lane",
        now_et=_market_open_now(),
    )

    assert result["terminal_status"] == "BLOCKED"
    assert result["reason_code"] == "paper_broker_snapshot_transient_failed"
    assert result["run_root"].endswith("paper-transient-snapshot")
    assert broker.account_calls == 1
    assert broker.submit_calls == 0


@pytest.mark.parametrize("stage", ["positions", "open_orders"])
def test_paper_full_snapshot_stage_timeouts_are_retryable(tmp_path: Path, stage: str) -> None:
    broker = FailingSnapshotPaperBroker(RuntimeError("504 request timed out"), stage=stage)
    result = run_live_pilot(
        plan=_plan(),
        broker=broker,
        env=_paper_env(dry_run="1"),
        run_id=f"paper-transient-{stage}",
        output_root=tmp_path / "outputs" / "paper_lane",
        now_et=_market_open_now(),
    )

    assert result["terminal_status"] == "BLOCKED"
    assert result["reason_code"] == "paper_broker_snapshot_transient_failed"
    assert broker.submit_calls == 0


def test_paper_snapshot_auth_failure_is_not_retryable(tmp_path: Path) -> None:
    broker = FailingSnapshotPaperBroker(RuntimeError("401 unauthorized: invalid paper credentials"))
    result = run_live_pilot(
        plan=_plan(),
        broker=broker,
        env=_paper_env(dry_run="1"),
        run_id="paper-auth-snapshot",
        output_root=tmp_path / "outputs" / "paper_lane",
        now_et=_market_open_now(),
    )

    assert result["terminal_status"] == "BLOCKED"
    assert result["reason_code"] == "paper_broker_snapshot_non_retryable"
    assert broker.account_calls == 1
    assert broker.submit_calls == 0


def _concentrated_plan() -> dict[str, object]:
    """A concentrated-alpha style target (top-name weight 0.50)."""
    return {
        "target_portfolio": [
            {
                "symbol": "AAPL",
                "target_weight": 0.50,
                "price": 200.0,
                "side": "BUY",
                "order_type": "market",
                "sleeve": "orion",
            }
        ],
        "cash_target_weight": 0.0,
    }


class RichPaperBroker(FakePaperBroker):
    """Paper account far above the $10k staging pin (Alpaca paper default ~$100k)."""

    def get_account(self):
        return {
            "id": "paper-acct-rich",
            "status": "ACTIVE",
            "cash": "100000",
            "equity": "100000",
            "buying_power": "100000",
            "portfolio_value": "100000",
        }


def test_large_paper_account_without_equity_pin_hard_blocks_over_cap(tmp_path: Path) -> None:
    """Regression control for the plan/execution scale mismatch: sizing weight *
    REAL equity on a $100k paper account exceeds the $10k approved cap and
    fail-blocks the whole lane before the dry pass."""
    env = _paper_env(dry_run="1")
    env.pop("CAERUS_LIVE_PILOT_PLANNING_EQUITY_CAP")
    broker = RichPaperBroker()
    result = run_live_pilot(
        plan=_concentrated_plan(),
        broker=broker,
        env=env,
        run_id="paper-nopin",
        output_root=tmp_path / "outputs" / "paper_lane",
        now_et=_market_open_now(),
    )
    assert result["terminal_status"] == "BLOCKED"
    assert result["reason_code"] == "live_pilot_total_notional_exceeds_cap"


def test_planning_equity_pin_sizes_paper_lane_at_staging_scale(tmp_path: Path) -> None:
    """With CAERUS_LIVE_PILOT_PLANNING_EQUITY_CAP=10000 (exported by
    cron_execute.sh alongside the capital cap) the executor sizes targets at the
    pinned $10k scale, so the over-cap guard no longer fires on a drifted paper
    account and the lane trades: 0.50 * 10000 / 200 = 25 shares."""
    broker = RichPaperBroker()
    result = run_live_pilot(
        plan=_concentrated_plan(),
        broker=broker,
        env=_paper_env(dry_run="1"),
        run_id="paper-pinned",
        output_root=tmp_path / "outputs" / "paper_lane",
        now_et=_market_open_now(),
    )
    assert result["terminal_status"] == "DRY_RUN"
    assert broker.submit_calls == 0
    run_root = tmp_path / "outputs" / "paper_lane" / "runs" / "paper-pinned"
    intended = json.loads((run_root / "live_pilot_orders_intended.json").read_text())
    orders = intended["orders"]
    assert len(orders) == 1
    assert orders[0]["symbol"] == "AAPL"
    assert float(orders[0].get("qty") or orders[0].get("shares")) == pytest.approx(25.0)


def test_cron_execute_pins_planning_equity_to_capital_cap() -> None:
    text = (REPO_ROOT / "scripts" / "cron_execute.sh").read_text(encoding="utf-8")
    assert 'export CAERUS_LIVE_PILOT_CAPITAL_CAP="10000"' in text
    assert (
        'export CAERUS_LIVE_PILOT_PLANNING_EQUITY_CAP="${CAERUS_LIVE_PILOT_CAPITAL_CAP}"' in text
    ), "paper lane must pin the executor's planning equity to the same staging scale as the cap"
    # The live lane must NOT pin planning equity (live sizes against real equity).
    live = (REPO_ROOT / "scripts" / "cron_live_pilot_execute.sh").read_text(encoding="utf-8")
    assert "CAERUS_LIVE_PILOT_PLANNING_EQUITY_CAP" not in live


def test_choice2_cron_has_one_authority_and_terminal_verification_order() -> None:
    text = (REPO_ROOT / "scripts" / "cron_execute.sh").read_text(encoding="utf-8")
    assert text.index("scripts/authorize_exact_execution_plan.py") < text.index(
        "=== PAPER LANE DRY RUN ==="
    ) < text.index("=== PAPER LANE SUBMISSION ===")
    assert text.index("scripts.live_vs_shadow_reconciliation") < text.index(
        "scripts/run_operational_drag_analysis.py"
    ) < text.index("scripts.caerus_daily_health_check")
    assert "execute_options_review.py" not in text
    assert "execute_options_orders.py" not in text
    assert "options_execution_authority=DISABLED_PENDING_EXACT_PLAN_INTEGRATION" in text


def test_cron_execute_uses_governed_intraday_paper_epoch_without_live_eligibility() -> None:
    text = (REPO_ROOT / "scripts" / "cron_execute.sh").read_text(encoding="utf-8")
    policy = json.loads(
        (REPO_ROOT / "config" / "paper_intraday_drill_policy_2026-08-13.json").read_text(
            encoding="utf-8"
        )
    )
    assert "CAERUS_PAPER_DRILL_EPOCH" in text
    assert "--drill-epoch" in text
    assert "paper_intraday_drill_policy_2026-08-13.json" in text
    assert policy["paper_only"] is True
    assert policy["live_eligible"] is False
    assert "2026-08-13T1230ET" in policy["allowed_epochs"]


def test_cron_execute_pins_two_minute_confirmed_proceeds_rotation() -> None:
    paper = (REPO_ROOT / "scripts" / "cron_execute.sh").read_text(encoding="utf-8")
    ci_dry_run = (REPO_ROOT / "scripts" / "ci_dry_run.sh").read_text(encoding="utf-8")
    live = (REPO_ROOT / "scripts" / "cron_live_pilot_execute.sh").read_text(encoding="utf-8")
    for pin in (
        'export CAERUS_LIVE_PILOT_SETTLEMENT_TIMEOUT_SECONDS="120"',
        'export CAERUS_LIVE_PILOT_SETTLEMENT_MAX_ATTEMPTS="61"',
        'export CAERUS_LIVE_PILOT_SETTLEMENT_BASE_DELAY_SECONDS="2"',
        'export CAERUS_LIVE_PILOT_SETTLEMENT_MAX_DELAY_SECONDS="2"',
        'export CAERUS_PAPER_REUSE_CONFIRMED_SELL_PROCEEDS="1"',
    ):
        assert pin in paper
        assert pin in ci_dry_run
    assert "CAERUS_PAPER_REUSE_CONFIRMED_SELL_PROCEEDS" not in live


def test_retry_harness_wraps_only_paper_cron() -> None:
    paper = (REPO_ROOT / "scripts" / "cron_execute.sh").read_text(encoding="utf-8")
    live = (REPO_ROOT / "scripts" / "cron_live_pilot_execute.sh").read_text(encoding="utf-8")

    assert "scripts.paper_execution_retry" in paper
    assert "CAERUS_PAPER_RETRY_CHILD" in paper
    assert "scripts.paper_execution_retry" not in live
    assert "CAERUS_PAPER_RETRY_CHILD" not in live


def test_cron_execute_does_not_export_kill_switch() -> None:
    """The paper cron must never pre-disarm the live kill switch via inherited
    env: the paper gate stack short-circuits before consulting it, so exporting
    =0 would be inert for paper while silently disarming any future non-paper
    child process."""
    text = (REPO_ROOT / "scripts" / "cron_execute.sh").read_text(encoding="utf-8")
    assert 'export CAERUS_LIVE_PILOT_KILL_SWITCH' not in text
    assert 'export CAERUS_REQUIRE_APPROVED_EXECUTION_PACKAGE="1"' in text


def test_ci_dry_run_builds_the_canonical_governed_paper_lane() -> None:
    text = (REPO_ROOT / "scripts" / "ci_dry_run.sh").read_text(encoding="utf-8")
    assert '--lane paper' in text
    assert 'CAERUS_LIVE_PILOT_SLEEVE_ID:-caerus_orion' in text
    assert '--approved-sleeve "${CAERUS_LIVE_PILOT_SLEEVE_ID}"' in text


def test_production_paper_gate_rejects_mutable_plan_without_approved_package(
    tmp_path: Path,
) -> None:
    env = _paper_env(dry_run="1")
    env["CAERUS_REQUIRE_APPROVED_EXECUTION_PACKAGE"] = "1"
    broker = FakePaperBroker()
    result = run_live_pilot(
        plan=_plan(),
        broker=broker,
        env=env,
        run_id="paper-missing-approved-package",
        output_root=tmp_path / "outputs" / "paper_lane",
        now_et=_market_open_now(),
    )
    assert result["terminal_status"] == "BLOCKED"
    assert result["reason_code"] == "approved_execution_package_required"
    assert broker.submit_calls == 0


def test_summary_field_extracts_last_json_object_from_noisy_output(tmp_path: Path) -> None:
    """summary_field() must tolerate stderr noise around the executor's final
    JSON summary (the passes are captured with 2>&1): warnings or tracebacks
    must not blank out terminal_status/run_root into failed_unknown."""
    import shlex
    import subprocess
    import sys as _sys

    text = (REPO_ROOT / "scripts" / "cron_execute.sh").read_text(encoding="utf-8")
    match = re.search(r"summary_field\(\) \{.*?\nPY\n\}", text, re.S)
    assert match, "cron_execute.sh must define summary_field()"
    harness = tmp_path / "harness.sh"
    harness.write_text(
        f"PYTHON_BIN={shlex.quote(_sys.executable)}\n"
        + match.group(0)
        + '\nsummary_field "$1" "$2"\n',
        encoding="utf-8",
    )
    summary = {
        "terminal_status": "SUBMITTED",
        "run_root": "/tmp/runs/paper-x",
        "reason_code": "",
    }
    noisy = (
        "DeprecationWarning: something old\n"
        '{"stage": "not the summary"}\n'
        "Traceback (most recent call last):\n"
        "  File \"x.py\", line 1, in <module>\n"
        + json.dumps(summary, indent=2, sort_keys=True)
        + "\n"
    )
    for field, expected in (
        ("terminal_status", "SUBMITTED"),
        ("run_root", "/tmp/runs/paper-x"),
        ("reason_code", ""),
    ):
        out = subprocess.run(
            ["bash", str(harness), noisy, field],
            capture_output=True,
            text=True,
            check=True,
        )
        assert out.stdout.strip() == expected

    # Clean output (the normal case) still parses.
    clean = subprocess.run(
        ["bash", str(harness), json.dumps(summary, indent=2, sort_keys=True), "terminal_status"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert clean.stdout.strip() == "SUBMITTED"


def test_live_broker_is_blocked_by_owner_policy_before_account_gates(tmp_path: Path) -> None:
    """Choice 2 keeps live capital structurally disabled regardless of arming."""

    class FakeLiveBroker(FakePaperBroker):
        paper = False
        base_url = LIVE_HOST

    env = {
        "TRADING_MODE": "live_pilot",
        "ALPACA_PAPER": "0",
        "ALPACA_BASE_URL": LIVE_HOST,
        "CAERUS_LIVE_PILOT_APPROVED": "1",
        "CAERUS_LIVE_PILOT_CAPITAL_CAP": "500",
        "CAERUS_LIVE_PILOT_SLEEVE_ID": "orion",
        "CAERUS_LIVE_PILOT_ACCOUNT_ID": "some-other-account",
        "CAERUS_LIVE_PILOT_MAX_ORDERS": "50",
        "CAERUS_LIVE_PILOT_DRY_RUN": "1",
        "CAERUS_LIVE_PILOT_KILL_SWITCH": "0",
    }
    broker = FakeLiveBroker(base_url=LIVE_HOST, paper=False)
    result = run_live_pilot(
        plan=_plan(),
        broker=broker,
        env=env,
        run_id="live-pin-mismatch",
        output_root=tmp_path / "outputs" / "live_pilot",
        now_et=_market_open_now(),
    )
    assert result["terminal_status"] == "BLOCKED"
    assert result["reason_code"] == "live_capital_disabled_by_owner_policy"
    assert broker.submit_calls == 0


# ---------------------------------------------------------------------------
# (b) State-dir isolation between paper and live
# ---------------------------------------------------------------------------


def _stub_prices(prices: dict[str, float]):
    def _fetch(tickers, run_date):  # noqa: ANN001
        rows = [
            {"ticker": t, "open": prices[t], "price_date": run_date}
            for t in tickers
            if t in prices
        ]
        return pd.DataFrame(rows, columns=["ticker", "open", "price_date"])

    return _fetch


def _bundle(tmp_path: Path, trade_date: str = "2026-07-09") -> Path:
    bundle = tmp_path / "outputs" / "precompute" / trade_date
    bundle.mkdir(parents=True, exist_ok=True)
    (bundle / "planned_execution_payload.json").write_text(
        json.dumps(
            {
                "trade_date": trade_date,
                "mode": "PAPER",
                "execution_status": "PLANNED",
                "trades": [],
            }
        ),
        encoding="utf-8",
    )
    (bundle / "signals.json").write_text(
        json.dumps(
            {
                "snapshot_date": trade_date,
                "signals": [
                    {"ticker": "AAPL", "target_weight": 0.6, "sleeve": "sleeve_trend"},
                    {"ticker": "MSFT", "target_weight": 0.4, "sleeve": "sleeve_trend"},
                ],
            }
        ),
        encoding="utf-8",
    )
    return bundle / "planned_execution_payload.json"


def test_state_dir_isolation_between_paper_and_live(tmp_path: Path) -> None:
    payload_path = _bundle(tmp_path)
    paper_state = tmp_path / "outputs" / "paper_lane" / "state"
    live_state = tmp_path / "outputs" / "live_pilot" / "state"
    prices = {"AAPL": 200.0, "MSFT": 400.0}
    sector_map = {"AAPL": "Information Technology", "MSFT": "Information Technology"}

    paper_plan = build_live_pilot_plan(
        payload_path=payload_path,
        approved_sleeve="orion",
        capital_cap=10000.0,
        max_orders=50,
        output_dir=tmp_path / "outputs" / "paper_lane" / "plans",
        price_fetcher=_stub_prices(prices),
        sector_map=sector_map,
        state_dir=paper_state,
    )
    live_plan = build_live_pilot_plan(
        payload_path=payload_path,
        approved_sleeve="orion",
        capital_cap=500.0,
        max_orders=50,
        output_dir=tmp_path / "outputs" / "live_pilot" / "plans",
        price_fetcher=_stub_prices(prices),
        sector_map=sector_map,
        state_dir=live_state,
    )
    assert paper_plan["status"] == "READY_FOR_MANUAL_APPROVAL"
    assert live_plan["status"] == "READY_FOR_MANUAL_APPROVAL"

    paper_peak = json.loads((paper_state / "peak_equity.json").read_text())
    live_peak = json.loads((live_state / "peak_equity.json").read_text())
    # Each lane tracks its own peak equity at its own scale; paper's $10k peak
    # must never leak into live's drawdown math (and vice versa).
    assert paper_peak["peak_equity"] == 10000.0
    assert live_peak["peak_equity"] == 500.0


def test_plan_builder_cli_exposes_isolated_state_dir() -> None:
    from scripts.live_pilot_build_plan_from_precompute import (
        LIVE_PILOT_STATE_DIR,
        _parse_args,
    )

    args = _parse_args(["--approved-sleeve", "orion", "--state-dir", "outputs/paper_lane/state"])
    assert args.state_dir == "outputs/paper_lane/state"
    # Default (unset) keeps the live-scoped state dir -> live behavior unchanged.
    args_default = _parse_args(["--approved-sleeve", "orion"])
    assert args_default.state_dir is None
    assert str(LIVE_PILOT_STATE_DIR) == "outputs/live_pilot/state"


# ---------------------------------------------------------------------------
# (c) Static: both cron lane scripts source lane_params.sh after env load
# ---------------------------------------------------------------------------


def test_both_cron_lanes_source_lane_params_after_env_load() -> None:
    paper = (REPO_ROOT / "scripts" / "cron_execute.sh").read_text(encoding="utf-8")
    live = (REPO_ROOT / "scripts" / "cron_live_pilot_execute.sh").read_text(encoding="utf-8")

    source_re = re.compile(r'^\s*source\s+"?\$\{?REPO_ROOT\}?"?/scripts/lane_params\.sh', re.M)
    assert source_re.search(paper), "cron_execute.sh must source scripts/lane_params.sh"
    assert source_re.search(live), "cron_live_pilot_execute.sh must source scripts/lane_params.sh"

    # lane_params.sh must be sourced AFTER the env-file load so it is the final word.
    assert paper.index("/.env") < source_re.search(paper).start()
    assert live.index("live_pilot.env") < source_re.search(live).start()

    # Both lanes log the shared-params fingerprint so divergence is visible.
    assert "lane_params_fingerprint=${CAERUS_LANE_PARAMS_FINGERPRINT}" in paper
    assert "lane_params_fingerprint=${CAERUS_LANE_PARAMS_FINGERPRINT}" in live


LANE_PARAM_DEFAULTS = {
    "CAERUS_LIVE_PILOT_MIN_TRADE_USD": "10",
    "CAERUS_LIVE_PILOT_MAX_ORDERS": "50",
    "CAERUS_LIVE_PILOT_CAP_PCT": "0.95",
    "CAERUS_CONCENTRATED_MAX_WEIGHT": "0.50",
}


def test_lane_params_do_not_carry_removed_concentration_knobs() -> None:
    """Concentration is ALWAYS ON with regime-adaptive N: the enable flag and a
    defaulted top-N must not exist in lane_params.sh or any cron lane script.
    (CAERUS_CONCENTRATED_TOP_N stays available as an emergency env override, but
    is never exported/defaulted by the lanes.)"""
    for rel in (
        "scripts/lane_params.sh",
        "scripts/cron_execute.sh",
        "scripts/cron_live_pilot_execute.sh",
        "scripts/cron_precompute.sh",
    ):
        text = (REPO_ROOT / rel).read_text(encoding="utf-8")
        assert "export CAERUS_CONCENTRATED_ALPHA" not in text, rel
        assert "export CAERUS_CONCENTRATED_TOP_N" not in text, rel
        assert "CAERUS_LIVE_PILOT_USE_BROAD_TARGETS" not in text, rel


def test_lane_params_defaults_use_env_wins_semantics() -> None:
    """Every shared behavior param must be an env-wins DEFAULT (${VAR:-default}),
    never an unconditional export that would silently bypass a tighter
    operator-set value in ~/.caerus/live_pilot.env (e.g. CAP_PCT=0.50 or a lower
    CONCENTRATED_MAX_WEIGHT set as a live blast-radius restriction)."""
    text = (REPO_ROOT / "scripts" / "lane_params.sh").read_text(encoding="utf-8")
    for name, default in LANE_PARAM_DEFAULTS.items():
        pin = f'export {name}="${{{name}:-{default}}}"'
        assert pin in text, f"lane_params.sh must default {name} via env-wins {pin!r}"
        assert f"export {name}={default}\n" not in text, (
            f"lane_params.sh must not export {name} unconditionally"
        )
    # No approval/arming gate may ever live here (they stay lane-owned, fail-closed).
    for forbidden in (
        "CAERUS_LIVE_PILOT_APPROVED",
        "CAERUS_LIVE_PILOT_SUBMIT_APPROVED",
        "CAERUS_LIVE_PILOT_CRON_APPROVED",
        "CAERUS_LIVE_PILOT_KILL_SWITCH",
    ):
        assert f"export {forbidden}" not in text


def test_lane_params_operator_env_value_wins(tmp_path: Path) -> None:
    """Functional: sourcing lane_params.sh must keep a pre-set (operator) value
    and fill in the default only when unset."""
    import subprocess

    script = tmp_path / "probe.sh"
    script.write_text(
        'source "$1"\n'
        'printf "%s|%s|%s\\n" "${CAERUS_LIVE_PILOT_CAP_PCT}" '
        '"${CAERUS_LIVE_PILOT_MAX_ORDERS}" "${CAERUS_CONCENTRATED_MAX_WEIGHT}"\n',
        encoding="utf-8",
    )
    lane_params = str(REPO_ROOT / "scripts" / "lane_params.sh")
    base_env = {"PATH": "/usr/bin:/bin"}

    # Operator-tightened values survive.
    tightened = subprocess.run(
        ["bash", str(script), lane_params],
        env={
            **base_env,
            "CAERUS_LIVE_PILOT_CAP_PCT": "0.50",
            "CAERUS_LIVE_PILOT_MAX_ORDERS": "10",
            "CAERUS_CONCENTRATED_MAX_WEIGHT": "0.25",
        },
        capture_output=True,
        text=True,
        check=True,
    )
    assert tightened.stdout.strip() == "0.50|10|0.25"

    # Unset -> shared defaults.
    defaults = subprocess.run(
        ["bash", str(script), lane_params],
        env=base_env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert defaults.stdout.strip() == "0.95|50|0.50"


# ---------------------------------------------------------------------------
# (d) Run-root separation
# ---------------------------------------------------------------------------


def test_run_roots_are_separated_with_same_artifact_schema(tmp_path: Path) -> None:
    broker = FakePaperBroker()
    run_live_pilot(
        plan=_plan(),
        broker=broker,
        env=_paper_env(dry_run="0"),
        run_id="paper-sep",
        output_root=tmp_path / "outputs" / "paper_lane",
        now_et=_market_open_now(),
    )
    paper_run = tmp_path / "outputs" / "paper_lane" / "runs" / "paper-sep"
    assert paper_run.is_dir()
    assert not (tmp_path / "outputs" / "live_pilot").exists()

    # Same artifact schema as the live lane's run roots.
    for artifact in (
        "live_pilot_preflight.json",
        "live_pilot_execution_payload.json",
        "live_pilot_orders_intended.json",
        "live_pilot_orders_submitted.json",
        "live_pilot_broker_snapshot_pre.json",
        "live_pilot_broker_snapshot_post.json",
        "live_pilot_reconciliation.json",
        "live_pilot_evidence_metrics.json",
        "live_pilot_capital_usage.json",
        "live_pilot_gate_state.json",
        "live_pilot_operator_summary.json",
        "execution_results.json",
    ):
        assert (paper_run / artifact).exists(), f"missing artifact {artifact}"


def _strip_shell_comments(text: str) -> str:
    return "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )


def test_cron_scripts_use_lane_scoped_output_roots() -> None:
    paper = _strip_shell_comments(
        (REPO_ROOT / "scripts" / "cron_execute.sh").read_text(encoding="utf-8")
    )
    live = _strip_shell_comments(
        (REPO_ROOT / "scripts" / "cron_live_pilot_execute.sh").read_text(encoding="utf-8")
    )
    assert "outputs/paper_lane" in paper
    assert "outputs/live_pilot" not in paper, "paper lane must never write under outputs/live_pilot"
    assert "outputs/live_pilot" in live
    assert "outputs/paper_lane" not in live, "live lane must never write under outputs/paper_lane"
    # The dormant path must no longer be invoked by the paper cron.
    assert "run_precomputed_alpaca_execution" not in paper


# ---------------------------------------------------------------------------
# Confirm-flow pointer compatibility (cron_confirm.sh reads execution.json)
# ---------------------------------------------------------------------------


def test_paper_lane_writes_compatible_execution_pointer(tmp_path: Path) -> None:
    run_root = tmp_path / "outputs" / "paper_lane" / "runs" / "paper-ptr"
    run_root.mkdir(parents=True)
    (run_root / "execution_results.json").write_text("{}", encoding="utf-8")

    result = write_paper_lane_pointers(
        trade_date="2026-07-09",
        run_id="paper-ptr",
        run_root=str(run_root),
        terminal_status="SUBMITTED",
        workspace_root=str(tmp_path),
    )
    assert result["status"] == "success"
    assert result["lane_exit_ok"] is True

    # cron_confirm.sh resolves Phase 2 via read_trade_stage_pointer(date, "execution").
    pointer = read_trade_stage_pointer("2026-07-09", "execution", workspace_root=str(tmp_path))
    assert pointer is not None
    assert pointer["stage"] == "execution"
    assert pointer["mode"] == "PAPER"
    assert pointer["run_id"] == "paper-ptr"
    assert pointer["status"] == "success"
    assert (Path(pointer["run_root"]) / "execution_results.json").exists()
    latest = json.loads((tmp_path / "outputs" / "latest_run.json").read_text())
    assert latest["run_id"] == "paper-ptr"
    assert latest["workflow_stage"] == "execution"

    # Lane-scoped pointer written beside it.
    lane_pointer = json.loads(
        (tmp_path / "outputs" / "workflow" / "2026-07-09" / "paper_lane_execution.json").read_text()
    )
    assert lane_pointer["stage"] == "paper_lane_execution"
    assert lane_pointer["run_root"] == str(run_root)


@pytest.mark.parametrize(
    ("terminal", "reason", "expected_status", "expected_ok"),
    [
        ("SUBMITTED", "", "success", True),
        ("DRY_RUN", "", "no_action", True),
        ("BLOCKED", "live_pilot_transition_no_actionable_order", "no_action", True),
        ("BLOCKED", "live_pilot_kill_switch_enabled", "failed_blocked", False),
        ("FAILED_RECONCILIATION", "PARTIAL", "failed_reconciliation", False),
        ("FAILED_PLAN_INTEGRITY", "HASH_MISMATCH", "failed_integrity", False),
        ("SUBMITTED_UNFILLED", "", "failed_incomplete", False),
        ("", "", "failed_unknown", False),
        ("running", "", "running", True),
    ],
)
def test_pointer_terminal_status_mapping(
    terminal: str, reason: str, expected_status: str, expected_ok: bool
) -> None:
    status, _substatus = map_terminal_status(terminal, reason)
    assert status == expected_status
    from scripts.paper_lane_write_execution_pointer import lane_exit_ok

    assert lane_exit_ok(status) is expected_ok
