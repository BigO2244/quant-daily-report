from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import os
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from authority.contracts import AuthorityContractError
from authority.exact_plan import build_exact_execution_plan, exact_execution_plan_from_dict
from core.failure_semantics import TerminalOutcome
from core.orchestrator_state import load_orchestrator_state
import execution.exact_executor as exact_executor_module
import scripts.live_pilot_execute as live_pilot_module
from execution.exact_executor import execute_exact_plan as _execute_exact_plan
from scripts.live_pilot_execute import run_live_pilot as _run_live_pilot
from core.regime_state_store import (
    RegimeAuthorityEvent,
    RegimePersistenceResult,
    commit_prepared_regime_authority,
    persist_regime_authority,
)
from scripts.authorize_exact_execution_plan import (
    authorize_exact_execution_plan,
    finalize_regime_committed_handoff,
)


PAPER_HOST = "https://paper-api.alpaca.markets"
TEST_NOW_ET = dt.datetime(
    2026,
    8,
    12,
    9,
    35,
    2,
    tzinfo=ZoneInfo("America/New_York"),
)


def execute_exact_plan(**kwargs):
    """Keep direct executor tests independent of the wall-clock market state."""

    kwargs.setdefault("now_et", TEST_NOW_ET)
    return _execute_exact_plan(**kwargs)


def run_live_pilot(**kwargs):
    """Keep governed-entrypoint tests independent of the wall-clock session."""

    kwargs.setdefault("now_et", TEST_NOW_ET)
    return _run_live_pilot(**kwargs)


class TrackingPaperBroker:
    paper = True
    base_url = PAPER_HOST

    def __init__(
        self,
        *,
        crash_after_accept: bool = False,
        account_id: str = "paper-account",
    ) -> None:
        self.cash = 900.0
        self.positions = [{"symbol": "OLD", "qty": "1", "market_value": "100"}]
        self.orders: dict[str, dict[str, object]] = {}
        self.submit_calls = 0
        self.limit_submissions: list[dict[str, object]] = []
        self.crash_after_accept = crash_after_accept
        self.account_id = account_id

    def get_account(self):
        value = self.cash + sum(float(row.get("market_value") or 0.0) for row in self.positions)
        return {
            "id": self.account_id,
            "status": "ACTIVE",
            "cash": str(self.cash),
            "equity": str(value),
            "portfolio_value": str(value),
            "buying_power": str(self.cash),
        }

    def get_positions(self):
        return copy.deepcopy(self.positions)

    def get_asset(self, symbol):
        return {"symbol": symbol, "status": "active", "asset_class": "us_equity", "tradable": True}

    def get_latest_trades(self, symbols):
        return {
            str(symbol): {
                "symbol": str(symbol),
                "price": "100" if str(symbol) == "OLD" else "50",
                "timestamp": "2026-08-12T13:35:00+00:00",
                "feed": "TEST",
            }
            for symbol in symbols
        }

    def get_market_session_calendar(self, trade_date):
        assert trade_date == "2026-08-12"
        return {
            "calendar": "TEST_XNYS",
            "trade_date": trade_date,
            "session_open_et": "2026-08-12T09:30:00-04:00",
            "session_close_et": "2026-08-12T16:00:00-04:00",
        }

    def list_orders(self, status="open", limit=100, **kwargs):
        del limit, kwargs
        rows = list(self.orders.values())
        return [] if status == "open" else copy.deepcopy(rows)

    def find_order_by_client_id(self, client_id):
        return copy.deepcopy(self.orders.get(client_id))

    def get_order(self, order_id):
        return copy.deepcopy(next(row for row in self.orders.values() if row["id"] == order_id))

    def submit_market_order(self, **kwargs):
        self.submit_calls += 1
        symbol = str(kwargs["symbol"])
        side = str(kwargs["side"]).upper()
        quantity = float(kwargs["qty"])
        notional = float(kwargs["estimated_notional"])
        client_id = str(kwargs["client_order_id"])
        row = {
            "id": f"broker-{self.submit_calls}",
            "client_order_id": client_id,
            "symbol": symbol,
            "side": side,
            "qty": str(quantity),
            "status": "filled",
            "filled_qty": str(quantity),
            "filled_avg_price": str(notional / quantity),
        }
        self.orders[client_id] = row
        if side == "SELL":
            self.positions = [item for item in self.positions if item["symbol"] != symbol]
            self.cash += notional
        else:
            self.positions.append({"symbol": symbol, "qty": str(quantity), "market_value": str(notional)})
            self.cash -= notional
        if self.crash_after_accept:
            self.crash_after_accept = False
            raise TimeoutError("response lost after broker accepted")
        return copy.deepcopy(row)

    def submit_limit_order(self, **kwargs):
        self.limit_submissions.append(copy.deepcopy(kwargs))
        quantity = float(kwargs["qty"])
        limit_price = float(kwargs["limit_price"])
        return self.submit_market_order(
            symbol=kwargs["symbol"],
            qty=quantity,
            side=kwargs["side"],
            client_order_id=kwargs["client_order_id"],
            tif=kwargs.get("tif", "day"),
            estimated_notional=quantity * limit_price,
        )


def _env() -> dict[str, str]:
    env = {
        "MODE": "paper",
        "TRADING_MODE": "paper",
        "ALPACA_PAPER": "1",
        "ALPACA_BASE_URL": PAPER_HOST,
        "CAERUS_LIVE_PILOT_APPROVED": "1",
        "CAERUS_LIVE_PILOT_CRON_APPROVED": "1",
        "CAERUS_LIVE_PILOT_SUBMIT_APPROVED": "1",
        "CAERUS_LIVE_PILOT_CAPITAL_CAP": "1000",
        "CAERUS_LIVE_PILOT_MAX_ORDERS": "50",
        "CAERUS_LIVE_PILOT_DRY_RUN": "0",
        "CAERUS_EXACT_FILL_REFRESH_ATTEMPTS": "1",
        "CAERUS_EXACT_FILL_REFRESH_DELAY_SECONDS": "0",
        "CAERUS_EXACT_MAX_PLAN_AGE_SECONDS": "999999",
    }
    # Hermetic default for injected test brokers. Individual cross-root tests
    # override this with the same explicit root to model production authority.
    test_identity = os.environ.get("PYTEST_CURRENT_TEST", "exact-choice2")
    env["CAERUS_EXACT_ACCOUNT_AUTHORITY_ROOT"] = str(
        Path(tempfile.gettempdir())
        / "caerus-exact-authority-tests"
        / str(os.getpid())
        / hashlib.sha256(test_identity.encode("utf-8")).hexdigest()[:20]
    )
    return env


def _execution_env(tmp_path: Path) -> dict[str, str]:
    return {
        **_env(),
        "CAERUS_EXACT_ACCOUNT_AUTHORITY_ROOT": str(tmp_path / "account_authority"),
    }


_EXACT_REGIME_ROOT = Path(tempfile.mkdtemp(prefix="caerus-exact-regime-"))


def _committed_regime_state() -> dict:
    from core.regime_state_store import persist_regime_authority

    persisted = persist_regime_authority(
        _EXACT_REGIME_ROOT,
        account_scope="PAPER",
        account_id=hashlib.sha256(b"paper-account").hexdigest(),
        sleeve_id="caerus_orion",
        authorization_run_id="authority-run",
        trade_date="2026-08-12",
        recorded_at="2026-08-12T13:35:01Z",
        observed_state="NORMAL",
        confidence=1.0,
        acute_risk=False,
        risk_package_id="risk:exact-fixture",
        risk_package_hash="b" * 64,
        market_state_id="market:2026-08-12",
    )
    return persisted.regime_state()


def _finalize_direct_authorization(state_root: Path, result: dict) -> dict:
    metadata = result["regime_authority_event"]
    prepared = RegimePersistenceResult(
        event=RegimeAuthorityEvent.from_dict(metadata["event"]),
        event_path=Path(metadata["path"]),
        created=False,
        committed=False,
    )
    committed = commit_prepared_regime_authority(state_root, prepared)
    return finalize_regime_committed_handoff(result, committed)


def _plan(*, no_trade: bool = False):
    return build_exact_execution_plan(
        run_id="authority-run",
        as_of="2026-08-12T09:35:00-04:00",
        created_at="2026-08-12T09:35:01-04:00",
        orchestrator_version="choice2.test",
        source_precompute_ids=["precompute:2026-08-12"],
        source_artifact_hashes={"precompute": "a" * 64},
        market_state_id="market:2026-08-12",
        market_state={"session": "OPEN"},
        regime_state=_committed_regime_state(),
        sleeve_allocations=[{"sleeve_id": "caerus_orion", "weight": 1.0, "capital_eligible": True}],
        portfolio_nav=1000.0,
        starting_positions=[{"symbol": "OLD", "quantity": 1.0}],
        starting_cash=900.0,
        account_id_hash=hashlib.sha256(b"paper-account").hexdigest(),
        risk_state={"status": "PASS"},
        sell_orders=[] if no_trade else [{
            "symbol": "OLD", "side": "SELL", "quantity": 1,
            "order_type": "limit", "time_in_force": "day",
            "extended_hours": False, "expected_price": 100,
            "limit_price": 100, "cap_enforcement_price": 100,
            "notional": 100,
        }],
        buy_orders=[] if no_trade else [{
            "symbol": "AAPL", "side": "BUY", "quantity": 2,
            "order_type": "limit", "time_in_force": "day",
            "extended_hours": False, "expected_price": 50,
            "limit_price": 50, "cap_enforcement_price": 50,
            "notional": 100,
        }],
        expected_posttrade_positions=(
            [{"symbol": "OLD", "quantity": 1.0}]
            if no_trade
            else [{"symbol": "AAPL", "quantity": 2.0}]
        ),
        expected_posttrade_cash=900.0,
        constraints={
            "cash_reconciliation_tolerance_usd": 0.01,
            "max_orders": 2,
            "capital_cap_usd": 1000.0,
            "max_adverse_fill_slippage_bps": 100.0,
            "new_order_execution_style": "protective_day_limit",
        },
        authorization_state={
            "status": "AUTHORIZED",
            "authority": "CAERUS_ORCHESTRATOR",
            "authorized_at": "2026-08-12T09:35:01-04:00",
            "authorization_reason": "ORION_PAPER_EXACT_ORDERS_AUTHORIZED",
        },
    )


def _handoff(exact):
    return {
        "schema_version": "caerus.authorized_execution_handoff.v1",
        "trade_date": exact.trade_date,
        "status": "AUTHORIZED_NO_TRADE" if not exact.orders else "AUTHORIZED_EXACT_PLAN",
        "exact_execution_plan": exact.to_dict(),
        "exact_execution_plan_id": exact.plan_id,
        "exact_execution_plan_hash": exact.content_hash,
        "exact_execution_authority_run_id": exact.run_id,
        "execution_authority": "exact_execution_plan_only",
        "precompute_execution_authority": False,
    }


def _legacy_pre_fill_risk_plan():
    payload = _plan().to_dict()
    constraints = dict(payload["constraints"])
    constraints.pop("max_adverse_fill_slippage_bps")
    return _rebuild_exact(
        payload,
        constraints=constraints,
        _allow_legacy_missing_fill_risk_authority=True,
    )


def _legacy_market_plan():
    payload = _plan().to_dict()
    constraints = dict(payload["constraints"])
    constraints.pop("max_adverse_fill_slippage_bps")
    constraints.pop("new_order_execution_style")

    def market_rows(rows):
        result = []
        for raw in rows:
            row = dict(raw)
            row["order_type"] = "market"
            row.pop("limit_price", None)
            row.pop("cap_enforcement_price", None)
            result.append(row)
        return result

    return _rebuild_exact(
        payload,
        sell_orders=market_rows(payload["sell_orders"]),
        buy_orders=market_rows(payload["buy_orders"]),
        constraints=constraints,
        _allow_legacy_missing_fill_risk_authority=True,
    )


def _rebuild_exact(payload: dict, **overrides):
    allow_legacy_missing_fill_risk_authority = bool(
        overrides.pop("_allow_legacy_missing_fill_risk_authority", False)
    )
    values = {
        key: payload[key]
        for key in (
            "run_id", "as_of", "created_at", "orchestrator_version",
            "source_precompute_ids", "source_artifact_hashes", "market_state_id",
            "market_state", "regime_state", "sleeve_allocations", "portfolio_nav",
            "starting_positions", "starting_cash", "risk_state", "sell_orders",
            "buy_orders", "expected_posttrade_positions", "expected_posttrade_cash",
            "constraints", "authorization_state", "strategy_id", "account_scope",
            "account_id_hash",
        )
    }
    values.update(overrides)
    protective_style = str(
        values["constraints"].get("new_order_execution_style") or ""
    ).strip().lower() == "protective_day_limit"
    for order_key in ("sell_orders", "buy_orders"):
        protective_rows = []
        for raw in values[order_key]:
            row = dict(raw)
            if protective_style:
                reference = row.get("expected_price") or row.get("price")
                row.setdefault("order_type", "limit")
                row.setdefault("time_in_force", "day")
                row.setdefault("extended_hours", False)
                row.setdefault("limit_price", reference)
                row.setdefault("cap_enforcement_price", row.get("limit_price"))
            protective_rows.append(row)
        values[order_key] = protective_rows
    return build_exact_execution_plan(
        **values,
        allow_legacy_missing_fill_risk_authority=(
            allow_legacy_missing_fill_risk_authority
        ),
    )


def _write_orion_sleeve_authority(tmp_path: Path) -> tuple[str, str]:
    from core.sleeve_control_plane import dispatch_all_sleeves, load_sleeve_control_registry

    trade_date = "2026-08-12"
    source = tmp_path / "outputs" / "shadow_candidates" / trade_date / "caerus_orion.json"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(
        json.dumps(
            {
                "trade_date": trade_date,
                "effective_trade_date": trade_date,
                "strategy_slug": "caerus_orion",
                "source_variant": "choice2-test",
                "decision_eligible": True,
                "target_weights": {"AAPL": 1.0},
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    registry = load_sleeve_control_registry()
    path = tmp_path / "sleeve_evaluations.json"
    payload = dispatch_all_sleeves(
        trade_date=trade_date,
        run_id="choice2-test-sleeves",
        daily_snapshot={
            "asof": trade_date,
            "sleeve_allocations": {
                key: 0.0 for key in registry.functional_allocation_keys()
            },
        },
        runtime_root=tmp_path,
        registry=registry,
    )
    path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    return str(path), hashlib.sha256(path.read_bytes()).hexdigest()


def _write_authority_chain(
    tmp_path: Path,
    *,
    target_rows: list[dict],
    sleeve_hash: str,
    constraints: dict | None = None,
    include_market_state_id: bool = True,
    target_cash_weight: float = 0.0,
) -> tuple[dict, dict[str, str]]:
    from authority.contracts import build_decision_package, build_evidence_package, build_risk_package
    from authority.pipeline import execution_package_from_risk

    refs = [f"sha256:{sleeve_hash}"]
    evidence = build_evidence_package(
        package_id="evidence:choice2-test",
        trade_date="2026-08-12",
        source_refs=refs,
        observations=target_rows,
    )
    decision = build_decision_package(
        package_id="decision:choice2-test",
        trade_date="2026-08-12",
        evidence=evidence,
        target_rows=target_rows,
        source_refs=refs,
        target_cash_weight=target_cash_weight,
    )
    governed_constraints = copy.deepcopy(constraints or {"regime": "NORMAL"})
    if include_market_state_id and not any(
        isinstance(governed_constraints.get(key), dict)
        and governed_constraints[key].get("market_state_id")
        for key in ("regime_authority", "market_state")
    ) and not governed_constraints.get("market_state_id"):
        governed_constraints.setdefault("market_state", {})["market_state_id"] = (
            "market:2026-08-12:fixture-source-bar"
        )
    risk = build_risk_package(
        package_id="risk:choice2-test",
        decision=decision,
        approved_target_rows=target_rows,
        constraints=governed_constraints,
        source_refs=[f"decision:{decision.package_id}"],
        approved_cash_weight=target_cash_weight,
    )
    execution = execution_package_from_risk(risk)
    authority_dir = tmp_path / "authority"
    authority_dir.mkdir(parents=True, exist_ok=True)
    packages = {
        "evidence": evidence.to_dict(),
        "decision": decision.to_dict(),
        "risk": risk.to_dict(),
        "execution": execution.to_dict(),
    }
    paths: dict[str, str] = {}
    for name, payload in packages.items():
        path = authority_dir / f"{name}_package.json"
        path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
        paths[name] = str(path)
    return execution.to_dict(), paths


def _governed_authorizer_fixture(
    tmp_path: Path,
    *,
    target_rows: list[dict],
) -> tuple[dict, Path, Path]:
    source = tmp_path / "governed-target-plan.json"
    source.write_text("{}\n", encoding="utf-8")
    sleeve_path, sleeve_hash = _write_orion_sleeve_authority(tmp_path)
    approved_package, authority_paths = _write_authority_chain(
        tmp_path,
        target_rows=target_rows,
        sleeve_hash=sleeve_hash,
    )
    plan = {
        "trade_date": "2026-08-12",
        "execution_lane": "paper",
        "approved_sleeve": "caerus_orion",
        "allow_fractional": False,
        "target_portfolio": target_rows,
        "approved_execution_package": approved_package,
        "authority_package_paths": authority_paths,
        "cash_target_weight": 0.0,
        "risk_controls": {"regime": "NORMAL"},
        "source_precompute_payload": "precompute.json",
        "source_signals": "signals.json",
        "source_sleeve_evaluations": sleeve_path,
        "source_sleeve_evaluations_sha256": sleeve_hash,
    }
    return plan, source, tmp_path / "regime-state"


class SessionFinalBarPaperBroker(TrackingPaperBroker):
    def __init__(
        self,
        *,
        final_bar_mode: str = "valid",
        calendar_mode: str = "valid",
    ) -> None:
        super().__init__()
        self.final_bar_mode = final_bar_mode
        self.calendar_mode = calendar_mode
        self.latest_trade_calls = 0
        self.session_calendar_calls = 0
        self.session_final_bar_calls = 0

    def get_latest_trades(self, symbols):
        self.latest_trade_calls += 1
        return super().get_latest_trades(symbols)

    def get_market_session_calendar(self, trade_date):
        self.session_calendar_calls += 1
        assert trade_date == "2026-08-12"
        row = {
            "trade_date": trade_date,
            "session_open_et": "2026-08-12T09:30:00-04:00",
            "session_close_et": "2026-08-12T16:00:00-04:00",
        }
        if self.calendar_mode == "empty":
            return {}
        if self.calendar_mode == "mismatch":
            row["session_close_et"] = "2026-08-12T15:59:00-04:00"
        return row

    def get_session_final_bars(
        self,
        symbols,
        *,
        session_open_et,
        session_close_et,
    ):
        self.session_final_bar_calls += 1
        assert session_open_et.isoformat() == "2026-08-12T09:30:00-04:00"
        assert session_close_et.isoformat() == "2026-08-12T16:00:00-04:00"
        bar_start = session_close_et - dt.timedelta(minutes=1)
        rows = {}
        for symbol in symbols:
            price = 100.0 if str(symbol) == "OLD" else 50.0
            rows[str(symbol)] = {
                "symbol": str(symbol),
                "price": price,
                "close": price,
                "bar_start": bar_start.isoformat(),
                "bar_end_exclusive": session_close_et.isoformat(),
                "open": price,
                "high": price,
                "low": price,
                "volume": 1000.0,
                "trade_count": 100.0,
                "vwap": price,
                "timeframe": "1Min",
                "feed": "IEX",
                "adjustment": "raw",
                "currency": "USD",
            }
        if symbols:
            victim = str(sorted(symbols)[0])
            if self.final_bar_mode == "missing":
                rows.pop(victim)
            elif self.final_bar_mode == "wrong_timestamp":
                rows[victim]["bar_start"] = (
                    session_close_et - dt.timedelta(minutes=2)
                ).isoformat()
            elif self.final_bar_mode == "nonfinite":
                rows[victim]["price"] = float("nan")
                rows[victim]["close"] = float("nan")
        return rows


def test_exact_contract_is_deterministic_and_rejects_tampering():
    plan = _plan()
    assert exact_execution_plan_from_dict(plan.to_dict()).content_hash == plan.content_hash
    rebuilt = _plan()
    assert rebuilt.plan_id == plan.plan_id
    assert [row["client_order_id"] for row in rebuilt.orders] == [row["client_order_id"] for row in plan.orders]
    tampered = plan.to_dict()
    tampered["buy_orders"][0]["quantity"] = 3
    with pytest.raises(AuthorityContractError):
        exact_execution_plan_from_dict(tampered)
    missing_ids = plan.to_dict()
    missing_ids["sell_orders"][0].pop("order_id")
    with pytest.raises(AuthorityContractError, match="order IDs are required"):
        exact_execution_plan_from_dict(missing_ids)
    with pytest.raises(AuthorityContractError, match="authority"):
        _rebuild_exact(plan.to_dict(), authorization_state="AUTHORIZED")


def test_operational_retry_run_id_does_not_change_exact_order_identity():
    original = _plan()
    retry = _rebuild_exact(original.to_dict(), run_id="authority-run-retry")

    assert retry.plan_id == original.plan_id
    assert [row["order_id"] for row in retry.orders] == [
        row["order_id"] for row in original.orders
    ]
    assert [row["client_order_id"] for row in retry.orders] == [
        row["client_order_id"] for row in original.orders
    ]
    assert retry.content_hash != original.content_hash


def test_exact_contract_rejects_cap_authority_and_economic_lies():
    payload = _plan().to_dict()
    missing_account_binding = copy.deepcopy(payload)
    missing_account_binding.pop("account_id_hash")
    with pytest.raises(AuthorityContractError, match="account_id_hash"):
        exact_execution_plan_from_dict(missing_account_binding)
    with pytest.raises(AuthorityContractError, match="account_id_hash"):
        _rebuild_exact(payload, account_id_hash="not-a-sha256")
    with pytest.raises(AuthorityContractError, match="unknown top-level fields"):
        exact_execution_plan_from_dict(
            {**payload, "alternate_target_artifact": "mutable-plan.json"}
        )
    understated = copy.deepcopy(payload["buy_orders"])
    understated[0]["notional"] = 1.0
    with pytest.raises(AuthorityContractError, match="notional"):
        _rebuild_exact(payload, buy_orders=understated)
    with pytest.raises(AuthorityContractError, match="order count"):
        _rebuild_exact(payload, constraints={**payload["constraints"], "max_orders": 1})
    missing_slippage_authority = dict(payload["constraints"])
    missing_slippage_authority.pop("max_adverse_fill_slippage_bps")
    with pytest.raises(AuthorityContractError, match="max_adverse_fill_slippage_bps"):
        _rebuild_exact(payload, constraints=missing_slippage_authority)
    with pytest.raises(AuthorityContractError, match="exceeds 100 basis points"):
        _rebuild_exact(
            payload,
            constraints={
                **payload["constraints"],
                "max_adverse_fill_slippage_bps": 100.01,
            },
        )
    mismatched_cap_price = copy.deepcopy(payload["buy_orders"])
    mismatched_cap_price[0].update(
        {
            "expected_price": 50,
            "limit_price": 600,
            "cap_enforcement_price": 50,
            "notional": 100,
        }
    )
    with pytest.raises(AuthorityContractError, match="must equal limit_price"):
        _rebuild_exact(payload, buy_orders=mismatched_cap_price)
    overwide_collar = copy.deepcopy(payload["buy_orders"])
    overwide_collar[0].update(
        {
            "expected_price": 50,
            "limit_price": 50.51,
            "cap_enforcement_price": 50.51,
            "notional": 101.02,
        }
    )
    with pytest.raises(AuthorityContractError, match="adverse-fill collar"):
        _rebuild_exact(payload, buy_orders=overwide_collar)
    with pytest.raises(AuthorityContractError, match="exceeds 50 basis points"):
        _rebuild_exact(
            payload,
            constraints={
                **payload["constraints"],
                "sleeve_attribution_mark_timing_tolerance_bps": 50.01,
            },
        )
    with pytest.raises(AuthorityContractError, match="capital-eligible"):
        _rebuild_exact(
            payload,
            sleeve_allocations=[{"sleeve_id": "caerus_lyra", "capital_eligible": True}],
        )
    with pytest.raises(AuthorityContractError, match="emergency regime"):
        _rebuild_exact(
            payload,
            regime_state={
                **payload["regime_state"],
                "effective_state": "EMERGENCY_RISK_OFF",
                "action": "EMERGENCY_RISK_RESPONSE",
                "risk_veto_buys": True,
            },
        )
    forged = copy.deepcopy(payload["buy_orders"])
    forged[0].update(
        {
            "id": "forged-broker-order",
            "status": "filled",
            "filled_qty": "2",
            "filled_avg_price": "50",
        }
    )
    with pytest.raises(AuthorityContractError, match="broker-owned fields"):
        _rebuild_exact(payload, buy_orders=forged)


@pytest.mark.parametrize("regime_state", [{}, {"effective_state": "NORMAL"}])
def test_exact_contract_rejects_empty_or_arbitrary_regime_schema(regime_state: dict):
    payload = _plan().to_dict()
    with pytest.raises(AuthorityContractError, match="governed regime authority schema"):
        _rebuild_exact(payload, regime_state=regime_state)


@pytest.mark.parametrize(
    ("event_scope", "event_account", "event_sleeve", "event_date"),
    [
        ("LIVE", hashlib.sha256(b"paper-account").hexdigest(), "caerus_orion", "2026-08-12"),
        ("PAPER", "c" * 64, "caerus_orion", "2026-08-12"),
        ("PAPER", hashlib.sha256(b"paper-account").hexdigest(), "caerus_lyra", "2026-08-12"),
        ("PAPER", hashlib.sha256(b"paper-account").hexdigest(), "caerus_orion", "2026-08-11"),
    ],
)
def test_exact_reader_rejects_committed_regime_event_from_wrong_identity_scope(
    tmp_path: Path,
    event_scope: str,
    event_account: str,
    event_sleeve: str,
    event_date: str,
):
    payload = _plan().to_dict()
    persisted = persist_regime_authority(
        tmp_path / "regime-state",
        account_scope=event_scope,
        account_id=event_account,
        sleeve_id=event_sleeve,
        authorization_run_id="wrong-regime-scope",
        trade_date=event_date,
        recorded_at=f"{event_date}T13:35:01Z",
        observed_state="NORMAL",
        confidence=1.0,
        acute_risk=False,
        risk_package_id="risk:wrong-scope",
        risk_package_hash="d" * 64,
        market_state_id=f"market:{event_date}",
    )
    forged = _rebuild_exact(payload, regime_state=persisted.regime_state())
    with pytest.raises(AuthorityContractError, match="identity scope mismatch"):
        exact_execution_plan_from_dict(forged.to_dict())
    with pytest.raises(AuthorityContractError, match="SHA-256"):
        _rebuild_exact(payload, source_artifact_hashes={"precompute": "not-a-hash"})
    with pytest.raises(AuthorityContractError, match="posttrade_positions"):
        _rebuild_exact(payload, expected_posttrade_positions=payload["starting_positions"])


def test_exact_executor_submits_sealed_sell_then_buy_and_reconciles(tmp_path: Path):
    broker = TrackingPaperBroker()
    plan = _plan()
    result = execute_exact_plan(
        plan_payload=plan.to_dict(),
        broker=broker,
        env=_env(),
        wal_root=tmp_path / "wal",
        attempt_id="submit-attempt",
        dry_run=False,
    )
    assert result.terminal_outcome is TerminalOutcome.RECONCILED_SUCCESS
    assert result.reconciliation_status == "CLEAN"
    assert [row["side"] for row in result.orders_submitted] == ["SELL", "BUY"]
    assert [row["order_id"] for row in result.orders_submitted] == [row["order_id"] for row in plan.orders]
    assert not result.orders_suppressed


def test_exact_executor_reconciles_market_order_cash_at_actual_fill_prices(
    tmp_path: Path,
):
    class SlippagePaperBroker(TrackingPaperBroker):
        def submit_market_order(self, **kwargs):
            adjusted = dict(kwargs)
            quantity = float(adjusted["qty"])
            actual_price = 120.0 if str(adjusted["side"]).upper() == "SELL" else 45.0
            adjusted["estimated_notional"] = quantity * actual_price
            return super().submit_market_order(**adjusted)

    broker = SlippagePaperBroker()
    plan = _plan()
    result = execute_exact_plan(
        plan_payload=plan.to_dict(),
        broker=broker,
        env=_execution_env(tmp_path),
        wal_root=tmp_path / "wal",
        attempt_id="market-slippage-cash-reconciliation",
        dry_run=False,
    )

    assert result.terminal_outcome is TerminalOutcome.RECONCILED_SUCCESS
    assert result.reconciliation_status == "CLEAN"
    assert result.final_cash == pytest.approx(930.0)
    assert result.final_cash != pytest.approx(plan.expected_posttrade_cash)


def _fractional_addition_plan(*, canonical_expected: bool = False):
    return build_exact_execution_plan(
        run_id="fractional-authority-run",
        as_of="2026-08-12T09:35:00-04:00",
        created_at="2026-08-12T09:35:01-04:00",
        orchestrator_version="choice2.fractional-reconciliation-test",
        source_precompute_ids=["precompute:2026-08-12:fractional"],
        source_artifact_hashes={"precompute": "f" * 64},
        market_state_id="market:2026-08-12:fractional",
        market_state={"session": "OPEN"},
        regime_state=_committed_regime_state(),
        sleeve_allocations=[
            {
                "sleeve_id": "caerus_orion",
                "weight": 1.0,
                "capital_eligible": True,
            }
        ],
        portfolio_nav=2500.0,
        starting_positions=[{"symbol": "LRCX", "quantity": 6.657142}],
        starting_cash=516.82,
        account_id_hash=hashlib.sha256(b"paper-account").hexdigest(),
        risk_state={"status": "PASS"},
        sell_orders=[],
        buy_orders=[
            {
                "symbol": "LRCX",
                "side": "BUY",
                "quantity": 0.158,
                "order_type": "limit",
                "time_in_force": "day",
                "extended_hours": False,
                "expected_price": 288.415,
                "limit_price": 291.29,
                "cap_enforcement_price": 291.29,
                "notional": 46.02382,
            }
        ],
        # Preserve the historical binary-float tail from the September 2
        # incident to prove old immutable plans remain recoverable.
        expected_posttrade_positions=[
            {
                "symbol": "LRCX",
                "quantity": 6.815142 if canonical_expected else 6.657142 + 0.158,
            }
        ],
        expected_posttrade_cash=470.79618,
        constraints={
            "allow_fractional": True,
            "cash_reconciliation_tolerance_usd": 1.0,
            "max_orders": 2,
            "capital_cap_usd": 2500.0,
            "max_adverse_fill_slippage_bps": 100.0,
            "new_order_execution_style": "protective_day_limit",
        },
        authorization_state={
            "status": "AUTHORIZED",
            "authority": "CAERUS_ORCHESTRATOR",
            "authorized_at": "2026-08-12T09:35:01-04:00",
            "authorization_reason": "ORION_PAPER_EXACT_ORDERS_AUTHORIZED",
        },
    )


def test_fractional_exact_plan_accepts_canonical_six_decimal_expected_state():
    plan = _fractional_addition_plan(canonical_expected=True)

    assert plan.expected_posttrade_positions[0]["quantity"] == 6.815142


class FractionalAggregatePaperBroker(TrackingPaperBroker):
    def __init__(self, *, reported_quantity: str = "6.815142") -> None:
        super().__init__()
        self.cash = 516.82
        self.positions = [
            {"symbol": "LRCX", "qty": "6.657142", "market_value": "1919.05"}
        ]
        self.reported_quantity = reported_quantity

    def submit_limit_order(self, **kwargs):
        self.limit_submissions.append(copy.deepcopy(kwargs))
        self.submit_calls += 1
        quantity = float(kwargs["qty"])
        fill_price = 288.34
        client_id = str(kwargs["client_order_id"])
        self.cash -= quantity * fill_price
        self.positions[0]["qty"] = self.reported_quantity
        row = {
            "id": f"broker-{self.submit_calls}",
            "client_order_id": client_id,
            "symbol": "LRCX",
            "side": "BUY",
            "qty": str(quantity),
            "status": "filled",
            "filled_qty": str(quantity),
            "filled_avg_price": str(fill_price),
        }
        self.orders[client_id] = row
        return copy.deepcopy(row)


def test_fractional_binary_tail_reconciles_and_recovers_without_resubmission(
    tmp_path: Path,
):
    broker = FractionalAggregatePaperBroker()
    plan = _fractional_addition_plan()

    first = execute_exact_plan(
        plan_payload=plan.to_dict(),
        broker=broker,
        env=_execution_env(tmp_path),
        wal_root=tmp_path / "wal",
        attempt_id="fractional-binary-tail",
        dry_run=False,
    )

    assert plan.expected_posttrade_positions[0]["quantity"] == 6.815142000000001
    assert first.terminal_outcome is TerminalOutcome.RECONCILED_SUCCESS
    assert first.reconciliation_status == "CLEAN"
    assert broker.submit_calls == 1

    recovered = execute_exact_plan(
        plan_payload=plan.to_dict(),
        broker=broker,
        env=_execution_env(tmp_path),
        wal_root=tmp_path / "wal",
        attempt_id="fractional-binary-tail-recovery",
        dry_run=False,
    )

    assert recovered.terminal_outcome is TerminalOutcome.RECONCILED_SUCCESS
    assert recovered.reconciliation_status == "CLEAN"
    assert broker.submit_calls == 1


def test_fractional_reconciliation_rejects_real_submicro_precision_drift(
    tmp_path: Path,
):
    broker = FractionalAggregatePaperBroker(reported_quantity="6.8151424")

    result = execute_exact_plan(
        plan_payload=_fractional_addition_plan().to_dict(),
        broker=broker,
        env=_execution_env(tmp_path),
        wal_root=tmp_path / "wal",
        attempt_id="fractional-real-drift",
        dry_run=False,
    )

    assert result.terminal_outcome is TerminalOutcome.SYSTEM_FAILURE
    assert result.status == "FAILED_RECONCILIATION"
    assert result.reason_code == "exact_posttrade_state_mismatch"


class AdverseFillPaperBroker(TrackingPaperBroker):
    def __init__(self, *, sell_price: float = 100.0, buy_price: float = 50.0):
        super().__init__()
        self.sell_price = float(sell_price)
        self.buy_price = float(buy_price)

    def submit_market_order(self, **kwargs):
        self.submit_calls += 1
        symbol = str(kwargs["symbol"])
        side = str(kwargs["side"]).upper()
        quantity = float(kwargs["qty"])
        price = self.sell_price if side == "SELL" else self.buy_price
        notional = quantity * price
        client_id = str(kwargs["client_order_id"])
        row = {
            "id": f"broker-{self.submit_calls}",
            "client_order_id": client_id,
            "symbol": symbol,
            "side": side,
            "qty": str(quantity),
            "status": "filled",
            "filled_qty": str(quantity),
            "filled_avg_price": str(price),
        }
        self.orders[client_id] = row
        if side == "SELL":
            self.positions = [
                item for item in self.positions if item["symbol"] != symbol
            ]
            self.cash += notional
        else:
            self.positions.append(
                {
                    "symbol": symbol,
                    "qty": str(quantity),
                    "market_value": str(notional),
                }
            )
            self.cash -= notional
        return copy.deepcopy(row)


def _collared_plan(*, capital_cap_usd: float = 1000.0):
    payload = _plan().to_dict()
    return _rebuild_exact(
        payload,
        sell_orders=[
            {
                "symbol": "OLD",
                "side": "SELL",
                "quantity": 1,
                "expected_price": 100,
                "limit_price": 99,
                "cap_enforcement_price": 99,
                "notional": 99,
            }
        ],
        buy_orders=[
            {
                "symbol": "AAPL",
                "side": "BUY",
                "quantity": 2,
                "expected_price": 50,
                "limit_price": 50.5,
                "cap_enforcement_price": 50.5,
                "notional": 101,
            }
        ],
        expected_posttrade_cash=898,
        constraints={
            **payload["constraints"],
            "capital_cap_usd": capital_cap_usd,
        },
    )


def test_market_fill_at_adverse_boundary_reconciles(tmp_path: Path):
    broker = AdverseFillPaperBroker(sell_price=99.0, buy_price=50.5)
    result = execute_exact_plan(
        plan_payload=_collared_plan().to_dict(),
        broker=broker,
        env=_execution_env(tmp_path),
        wal_root=tmp_path / "wal",
        attempt_id="adverse-fill-at-boundary",
        dry_run=False,
    )

    assert result.terminal_outcome is TerminalOutcome.RECONCILED_SUCCESS
    assert result.status == "RECONCILED_SUCCESS"
    assert broker.submit_calls == 2


def test_sell_fill_below_protective_limit_stops_buy_phase(tmp_path: Path):
    broker = AdverseFillPaperBroker(sell_price=98.99)
    result = execute_exact_plan(
        plan_payload=_collared_plan().to_dict(),
        broker=broker,
        env=_execution_env(tmp_path),
        wal_root=tmp_path / "wal",
        attempt_id="adverse-sell-fill-over-boundary",
        dry_run=False,
    )

    assert result.status == "SUBMISSION_UNKNOWN"
    assert result.failure_class.value == "BROKER_FAILURE"
    assert ":invalid_broker_evidence:" in result.reason_code
    assert "broker fill price violates durable limit order" in result.reason_code
    assert broker.submit_calls == 1
    assert len(list((tmp_path / "wal").rglob("*/intents/*.json"))) == 1


def test_protective_buy_limit_cannot_be_authorized_above_sealed_cap():
    with pytest.raises(AuthorityContractError, match="buy notional exceeds"):
        _collared_plan(capital_cap_usd=100.0)


def test_exact_executor_blocks_identical_state_from_different_paper_account(
    tmp_path: Path,
):
    plan = _plan()
    # Cash and positions are byte-for-byte economically identical to the
    # authorizing account; only the actual PAPER account identity differs.
    other_account = TrackingPaperBroker(account_id="different-paper-account")
    result = execute_exact_plan(
        plan_payload=plan.to_dict(),
        broker=other_account,
        env=_env(),
        wal_root=tmp_path / "wal",
        attempt_id="wrong-paper-account",
        dry_run=False,
    )
    assert result.status == "BLOCKED"
    assert result.reason_code == "exact_plan_account_identity_mismatch"
    assert other_account.submit_calls == 0
    assert len(result.orders_suppressed) == len(plan.orders)
    assert {
        row["suppression"]["reason_code"] for row in result.orders_suppressed
    } == {"PRE_SUBMIT_VALIDATION_BLOCKED"}
    date_root = tmp_path / "wal" / plan.trade_date
    assert not (date_root / "intents").exists()
    assert not (date_root / "claims").exists()


def test_intentional_zero_order_plan_is_authorized_no_trade(tmp_path: Path):
    broker = TrackingPaperBroker()
    result = execute_exact_plan(
        plan_payload=_plan(no_trade=True).to_dict(),
        broker=broker,
        env=_env(),
        wal_root=tmp_path / "wal",
        attempt_id="no-trade-attempt",
        dry_run=False,
    )
    assert result.terminal_outcome is TerminalOutcome.AUTHORIZED_NO_TRADE
    assert result.status == "AUTHORIZED_NO_TRADE"
    assert broker.submit_calls == 0


def test_direct_exact_new_intents_are_blocked_after_close_without_wal_or_submit(
    tmp_path: Path,
):
    broker = TrackingPaperBroker()
    plan = _plan()
    result = execute_exact_plan(
        plan_payload=plan.to_dict(),
        broker=broker,
        env=_execution_env(tmp_path),
        wal_root=tmp_path / "wal",
        attempt_id="direct-after-close",
        dry_run=False,
        now_et=dt.datetime(2026, 8, 12, 16, 15, tzinfo=ZoneInfo("America/New_York")),
    )

    assert result.status == "BLOCKED"
    assert result.reason_code == "exact_execution_market_closed:new_intents_forbidden"
    assert result.reconciliation_status == "FAILED_PRE_SUBMIT"
    assert broker.submit_calls == 0
    assert not list((tmp_path / "wal").rglob("*.json"))
    assert not list((tmp_path / "account_authority").rglob("plan_claim.json"))


def test_direct_exact_zero_order_plan_reconciles_after_close(tmp_path: Path):
    broker = TrackingPaperBroker()
    result = execute_exact_plan(
        plan_payload=_plan(no_trade=True).to_dict(),
        broker=broker,
        env=_execution_env(tmp_path),
        wal_root=tmp_path / "wal",
        attempt_id="direct-after-close-no-trade",
        dry_run=False,
        now_et=dt.datetime(2026, 8, 12, 16, 15, tzinfo=ZoneInfo("America/New_York")),
    )

    assert result.terminal_outcome is TerminalOutcome.AUTHORIZED_NO_TRADE
    assert result.status == "AUTHORIZED_NO_TRADE"
    assert broker.submit_calls == 0
    assert not list((tmp_path / "wal").rglob("*.json"))


def test_completed_wal_recovery_after_close_is_lookup_only_and_reconciles(
    tmp_path: Path,
):
    broker = TrackingPaperBroker()
    plan = _plan()
    first = execute_exact_plan(
        plan_payload=plan.to_dict(),
        broker=broker,
        env=_execution_env(tmp_path),
        wal_root=tmp_path / "wal",
        attempt_id="before-close",
        dry_run=False,
    )
    calls = broker.submit_calls
    assert first.terminal_outcome is TerminalOutcome.RECONCILED_SUCCESS

    recovered = execute_exact_plan(
        plan_payload=plan.to_dict(),
        broker=broker,
        env=_execution_env(tmp_path),
        wal_root=tmp_path / "wal",
        attempt_id="after-close-recovery",
        dry_run=False,
        now_et=dt.datetime(2026, 8, 12, 16, 15, tzinfo=ZoneInfo("America/New_York")),
    )

    assert recovered.terminal_outcome is TerminalOutcome.RECONCILED_SUCCESS
    assert broker.submit_calls == calls
    recovered_ids = [row["client_order_id"] for row in recovered.orders_submitted]
    assert len(recovered_ids) == len(set(recovered_ids)) == len(plan.orders)
    assert all(row["recovered_by_client_order_id"] for row in recovered.orders_submitted)


def test_exact_run_recovery_preserves_original_target_lineage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    run_root = tmp_path / "outputs" / "paper_lane" / "runs" / "original-run"
    run_root.mkdir(parents=True)
    exact = _plan()
    approved_package = {
        "content_hash": "approved-package-hash",
        "approved_cash_weight": 0.05,
        "approved_target_rows": [
            {"symbol": "AAPL", "target_weight": 0.95}
        ],
        "constraints": {
            "target_attainment_policy": {
                "schema_version": "caerus.target_attainment_policy.v1",
                "account_scope": "PAPER",
                "share_mode": "FRACTIONAL_SHARES",
                "target_cash_weight": 0.05,
                "minimum_cash_weight": 0.025,
                "fixed_drift_tolerance": 0.02,
                "nearest_feasible_required": False,
                "comparison_epoch_policy": "FIRST_CLEAN_POST_FIX_PAPER_RUN",
                "strict_green_propagation": True,
                "owner_approved_at": "2026-08-31",
            }
        },
    }
    decision_source = {
        "path": "outputs/precompute/2026-08-12/paper_target_package.json",
        "content_hash": "decision-source-hash",
    }
    payload = {
        "execution_source": "exact_execution_plan_v3",
        "exact_execution_plan": exact.to_dict(),
        "approved_execution_package": approved_package,
        "decision_source_artifact": decision_source,
        "target_attainment_policy": approved_package["constraints"][
            "target_attainment_policy"
        ],
        "target_attainment_tolerance": 0.02,
    }
    (run_root / "execution_payload.json").write_text(
        json.dumps(payload),
        encoding="utf-8",
    )
    captured: dict[str, object] = {}

    def fake_run_live_pilot(**kwargs):
        captured.update(kwargs)
        return {"terminal_status": "SUBMITTED"}

    monkeypatch.setattr(live_pilot_module, "run_live_pilot", fake_run_live_pilot)

    result = live_pilot_module.recover_exact_run(run_root)

    assert result["terminal_status"] == "SUBMITTED"
    recovered_handoff = captured["plan"]
    assert recovered_handoff["exact_execution_plan_hash"] == exact.content_hash
    assert recovered_handoff["approved_execution_package"] == approved_package
    assert recovered_handoff["decision_source_artifact"] == decision_source
    assert recovered_handoff["target_attainment_policy"] == (
        approved_package["constraints"]["target_attainment_policy"]
    )
    assert recovered_handoff["target_attainment_tolerance"] == 0.02


def test_immediate_order_id_only_response_refreshes_by_canonical_broker_id(
    tmp_path: Path,
):
    class OrderIdOnlyAcceptedBroker(TrackingPaperBroker):
        def submit_limit_order(self, **kwargs):
            self.submit_calls += 1
            broker_id = f"broker-{self.submit_calls}"
            client_id = str(kwargs["client_order_id"])
            row = {
                "order_id": broker_id,
                "client_order_id": client_id,
                "symbol": str(kwargs["symbol"]),
                "side": str(kwargs["side"]).upper(),
                "qty": str(float(kwargs["qty"])),
                "status": "accepted",
                "filled_qty": "0",
                "limit_price": str(float(kwargs["limit_price"])),
            }
            self.orders[client_id] = row
            return copy.deepcopy(row)

        def get_order(self, order_id):
            row = next(
                item for item in self.orders.values()
                if item["order_id"] == order_id
            )
            if row["status"] != "filled":
                quantity = float(row["qty"])
                price = float(row["limit_price"])
                notional = quantity * price
                row["status"] = "filled"
                row["filled_qty"] = str(quantity)
                row["filled_avg_price"] = str(price)
                if row["side"] == "SELL":
                    self.positions = [
                        item for item in self.positions
                        if item["symbol"] != row["symbol"]
                    ]
                    self.cash += notional
                else:
                    self.positions.append(
                        {
                            "symbol": row["symbol"],
                            "qty": str(quantity),
                            "market_value": str(notional),
                        }
                    )
                    self.cash -= notional
            return copy.deepcopy(row)

    broker = OrderIdOnlyAcceptedBroker()
    result = execute_exact_plan(
        plan_payload=_plan().to_dict(),
        broker=broker,
        env=_execution_env(tmp_path),
        wal_root=tmp_path / "wal",
        attempt_id="order-id-only-response",
        dry_run=False,
    )

    assert result.terminal_outcome is TerminalOutcome.RECONCILED_SUCCESS
    assert broker.submit_calls == 2
    assert [row["id"] for row in result.orders_submitted] == [
        "broker-1",
        "broker-2",
    ]


def _seed_legacy_durable_orders(
    *,
    plan,
    broker: TrackingPaperBroker,
    wal_root: Path,
    count: int,
) -> None:
    from core.submission_wal import OrderIntent, prepare_order_intent

    for order in plan.orders[:count]:
        if order["order_type"] == "limit":
            broker.submit_limit_order(
                symbol=order["symbol"],
                qty=order["quantity"],
                side=order["side"],
                client_order_id=order["client_order_id"],
                tif=order["time_in_force"],
                limit_price=order.get("limit_price"),
                extended_hours=order["extended_hours"],
            )
        else:
            broker.submit_market_order(
                symbol=order["symbol"],
                qty=order["quantity"],
                side=order["side"],
                client_order_id=order["client_order_id"],
                tif=order["time_in_force"],
                estimated_notional=order["notional"],
            )
        prepare_order_intent(
            wal_root,
            OrderIntent(
                trade_date=plan.trade_date,
                plan_id=plan.plan_id,
                plan_hash=plan.content_hash,
                attempt_id="legacy-predeploy",
                order_id=order["order_id"],
                client_order_id=order["client_order_id"],
                symbol=order["symbol"],
                side=order["side"],
                quantity=order["quantity"],
                order_type=order["order_type"],
                created_at="2026-08-12T13:35:01Z",
                limit_price=order.get("limit_price"),
                expected_price=order["expected_price"],
                notional=order["notional"],
                time_in_force=order["time_in_force"],
                extended_hours=order["extended_hours"],
                sleeve="caerus_orion",
            ),
        )


def test_legacy_plan_without_fill_risk_authority_cannot_create_a_new_intent(
    tmp_path: Path,
):
    plan = _legacy_pre_fill_risk_plan()
    assert exact_execution_plan_from_dict(plan.to_dict()).content_hash == plan.content_hash
    broker = TrackingPaperBroker()

    result = execute_exact_plan(
        plan_payload=plan.to_dict(),
        broker=broker,
        env=_execution_env(tmp_path),
        wal_root=tmp_path / "wal",
        attempt_id="legacy-fresh-blocked",
        dry_run=False,
    )

    assert result.status == "BLOCKED"
    assert result.reason_code == "exact_fill_slippage_authority_invalid"
    assert broker.submit_calls == 0
    assert not list((tmp_path / "wal").rglob("*/intents/*.json"))


def test_legacy_plan_with_all_durable_intents_recovers_lookup_only(
    tmp_path: Path,
):
    plan = _legacy_pre_fill_risk_plan()
    broker = TrackingPaperBroker()
    wal_root = tmp_path / "wal"
    _seed_legacy_durable_orders(
        plan=plan,
        broker=broker,
        wal_root=wal_root,
        count=len(plan.orders),
    )
    calls = broker.submit_calls

    result = execute_exact_plan(
        plan_payload=plan.to_dict(),
        broker=broker,
        env=_execution_env(tmp_path),
        wal_root=wal_root,
        attempt_id="legacy-lookup-only",
        dry_run=False,
    )

    assert result.terminal_outcome is TerminalOutcome.RECONCILED_SUCCESS
    assert broker.submit_calls == calls
    assert all(row["recovered_by_client_order_id"] for row in result.orders_submitted)


def test_predeploy_market_plan_with_all_durable_intents_recovers_lookup_only(
    tmp_path: Path,
):
    plan = _legacy_market_plan()
    assert "new_order_execution_style" not in plan.constraints
    assert "max_adverse_fill_slippage_bps" not in plan.constraints
    assert {order["order_type"] for order in plan.orders} == {"market"}
    broker = TrackingPaperBroker()
    wal_root = tmp_path / "wal"
    _seed_legacy_durable_orders(
        plan=plan,
        broker=broker,
        wal_root=wal_root,
        count=len(plan.orders),
    )
    calls = broker.submit_calls

    result = execute_exact_plan(
        plan_payload=plan.to_dict(),
        broker=broker,
        env=_execution_env(tmp_path),
        wal_root=wal_root,
        attempt_id="predeploy-market-lookup-only",
        dry_run=False,
    )

    assert result.terminal_outcome is TerminalOutcome.RECONCILED_SUCCESS
    assert broker.submit_calls == calls
    assert all(row["recovered_by_client_order_id"] for row in result.orders_submitted)


def test_legacy_partial_recovery_resolves_prior_fill_but_never_submits_remainder(
    tmp_path: Path,
):
    plan = _legacy_pre_fill_risk_plan()
    broker = TrackingPaperBroker()
    wal_root = tmp_path / "wal"
    _seed_legacy_durable_orders(
        plan=plan,
        broker=broker,
        wal_root=wal_root,
        count=1,
    )
    calls = broker.submit_calls

    result = execute_exact_plan(
        plan_payload=plan.to_dict(),
        broker=broker,
        env=_execution_env(tmp_path),
        wal_root=wal_root,
        attempt_id="legacy-partial-blocked",
        dry_run=False,
    )

    assert result.status == "FAILED_RECONCILIATION"
    assert result.reason_code == "exact_fill_slippage_authority_invalid"
    assert len(result.orders_submitted) == len(result.orders_filled) == 1
    assert broker.submit_calls == calls == 1
    assert len(list(wal_root.rglob("*/intents/*.json"))) == 1


def test_legacy_partial_recovery_dry_run_does_not_mutate_wal(
    tmp_path: Path,
):
    plan = _legacy_pre_fill_risk_plan()
    broker = TrackingPaperBroker()
    wal_root = tmp_path / "wal"
    _seed_legacy_durable_orders(
        plan=plan,
        broker=broker,
        wal_root=wal_root,
        count=1,
    )
    before = {
        path.relative_to(wal_root).as_posix(): path.read_bytes()
        for path in wal_root.rglob("*")
        if path.is_file()
    }
    calls = broker.submit_calls

    result = execute_exact_plan(
        plan_payload=plan.to_dict(),
        broker=broker,
        env=_execution_env(tmp_path),
        wal_root=wal_root,
        attempt_id="legacy-partial-dry-run",
        dry_run=True,
    )

    after = {
        path.relative_to(wal_root).as_posix(): path.read_bytes()
        for path in wal_root.rglob("*")
        if path.is_file()
    }
    assert result.status == "FAILED_RECONCILIATION"
    assert result.reason_code == "exact_fill_slippage_authority_invalid"
    assert broker.submit_calls == calls == 1
    assert after == before


def test_restart_after_accepted_response_loss_recovers_without_duplicate(tmp_path: Path):
    broker = TrackingPaperBroker(crash_after_accept=True)
    plan = _plan()
    first = execute_exact_plan(
        plan_payload=plan.to_dict(), broker=broker, env=_env(), wal_root=tmp_path / "wal", attempt_id="attempt-one", dry_run=False
    )
    assert first.terminal_outcome is TerminalOutcome.RECONCILED_SUCCESS
    calls_after_first = broker.submit_calls
    replay = execute_exact_plan(
        plan_payload=plan.to_dict(), broker=broker, env=_env(), wal_root=tmp_path / "wal", attempt_id="attempt-two", dry_run=False
    )
    assert replay.terminal_outcome is TerminalOutcome.RECONCILED_SUCCESS
    assert broker.submit_calls == calls_after_first


def test_completed_wal_recovery_ignores_later_cap_tightening_and_never_duplicates(
    tmp_path: Path,
):
    broker = TrackingPaperBroker()
    plan = _plan()
    first = execute_exact_plan(
        plan_payload=plan.to_dict(), broker=broker, env=_env(),
        wal_root=tmp_path / "wal", attempt_id="cap-before", dry_run=False,
    )
    calls = broker.submit_calls
    assert first.terminal_outcome is TerminalOutcome.RECONCILED_SUCCESS
    recovered = execute_exact_plan(
        plan_payload=plan.to_dict(), broker=broker,
        env={**_env(), "CAERUS_LIVE_PILOT_CAP_PCT": "0.0001"},
        wal_root=tmp_path / "wal", attempt_id="cap-after", dry_run=False,
    )
    assert recovered.terminal_outcome is TerminalOutcome.RECONCILED_SUCCESS
    assert len(recovered.orders_submitted) == 2
    assert all(row["recovered_by_client_order_id"] for row in recovered.orders_submitted)
    assert broker.submit_calls == calls


def test_partial_buy_recovery_counts_prior_fill_against_tightened_runtime_cap(
    tmp_path: Path,
):
    class MidBatchOpenOrderBroker(TrackingPaperBroker):
        def __init__(self):
            super().__init__()
            self.block_after_first_submission = True

        def list_orders(self, status="open", limit=100, **kwargs):
            del limit, kwargs
            if status != "open" or not self.block_after_first_submission:
                return []
            if self.submit_calls < 1:
                return []
            return [
                {
                    "id": "external-mid-batch",
                    "client_order_id": "external-mid-batch",
                    "symbol": "QQQ",
                    "side": "BUY",
                    "status": "accepted",
                    "filled_qty": "0",
                }
            ]

    base = _plan().to_dict()
    plan = _rebuild_exact(
        base,
        run_id="partial-buy-runtime-cap",
        sell_orders=[],
        buy_orders=[
            {
                "symbol": "AAPL",
                "side": "BUY",
                "quantity": 6,
                "expected_price": 50,
                "notional": 300,
            },
            {
                "symbol": "MSFT",
                "side": "BUY",
                "quantity": 6,
                "expected_price": 50,
                "notional": 300,
            },
        ],
        expected_posttrade_positions=[
            {"symbol": "AAPL", "quantity": 6.0},
            {"symbol": "MSFT", "quantity": 6.0},
            {"symbol": "OLD", "quantity": 1.0},
        ],
        expected_posttrade_cash=300.0,
    )
    broker = MidBatchOpenOrderBroker()
    wal_root = tmp_path / "wal"
    first = execute_exact_plan(
        plan_payload=plan.to_dict(),
        broker=broker,
        env=_env(),
        wal_root=wal_root,
        attempt_id="partial-buy-first",
        dry_run=False,
    )
    assert first.status == "FAILED_RECONCILIATION"
    assert broker.submit_calls == 1

    broker.block_after_first_submission = False
    recovered = execute_exact_plan(
        plan_payload=plan.to_dict(),
        broker=broker,
        env={**_env(), "CAERUS_LIVE_PILOT_CAPITAL_CAP": "500"},
        wal_root=wal_root,
        attempt_id="partial-buy-tightened-cap",
        dry_run=False,
    )

    assert recovered.status == "FAILED_RECONCILIATION"
    assert recovered.reason_code == (
        "runtime_dynamic_cap_below_authorized_buy_notional"
    )
    assert len(recovered.orders_submitted) == len(recovered.orders_filled) == 1
    assert broker.submit_calls == 1
    assert len(list(wal_root.rglob("*/intents/*.json"))) == 1
    resolution_states = {
        json.loads(path.read_text(encoding="utf-8"))["state"]
        for path in wal_root.rglob("*/resolutions/*/*.json")
    }
    assert "ECONOMICALLY_RECONCILED" in resolution_states


def test_partial_wal_recovery_after_close_is_truthful_and_never_duplicates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    broker = TrackingPaperBroker()
    plan = _plan()
    first_clock = iter((True, True, False))
    monkeypatch.setattr(
        "execution.exact_executor._exact_market_is_open",
        lambda **_kwargs: next(first_clock),
    )
    crossed = execute_exact_plan(
        plan_payload=plan.to_dict(),
        broker=broker,
        env=_execution_env(tmp_path),
        wal_root=tmp_path / "wal",
        attempt_id="crossed-close",
        dry_run=False,
    )
    assert crossed.status == "FAILED_RECONCILIATION"
    assert broker.submit_calls == 1
    assert len(crossed.orders_submitted) == 1
    assert len(list((tmp_path / "wal").rglob("*/intents/*.json"))) == 1

    monkeypatch.setattr(
        "execution.exact_executor._exact_market_is_open",
        lambda **_kwargs: False,
    )
    closed = execute_exact_plan(
        plan_payload=plan.to_dict(),
        broker=broker,
        env=_execution_env(tmp_path),
        wal_root=tmp_path / "wal",
        attempt_id="partial-closed",
        dry_run=False,
    )
    assert closed.status == "FAILED_RECONCILIATION"
    assert closed.reconciliation_status == "FAILED_RECONCILIATION"
    assert len(closed.orders_submitted) == 1
    assert broker.submit_calls == 1

    recovery_clock = iter((True, False))
    monkeypatch.setattr(
        "execution.exact_executor._exact_market_is_open",
        lambda **_kwargs: next(recovery_clock),
    )
    recovery_crossed = execute_exact_plan(
        plan_payload=plan.to_dict(),
        broker=broker,
        env=_execution_env(tmp_path),
        wal_root=tmp_path / "wal",
        attempt_id="partial-recovery-crossed",
        dry_run=False,
    )
    client_ids = [row["client_order_id"] for row in recovery_crossed.orders_submitted]
    assert recovery_crossed.status == "FAILED_RECONCILIATION"
    assert len(client_ids) == len(set(client_ids)) == 1
    assert broker.submit_calls == 1
    assert len(list((tmp_path / "wal").rglob("*/intents/*.json"))) == 1


def test_foreign_same_date_wal_blocks_a_different_exact_plan_before_broker_mutation(
    tmp_path: Path,
):
    broker = TrackingPaperBroker()
    first = _plan()
    execute_exact_plan(
        plan_payload=first.to_dict(), broker=broker, env=_env(),
        wal_root=tmp_path / "wal", attempt_id="first-plan", dry_run=False,
    )
    calls = broker.submit_calls
    different = _plan(no_trade=True)
    blocked = execute_exact_plan(
        plan_payload=different.to_dict(), broker=broker, env=_env(),
        wal_root=tmp_path / "wal", attempt_id="different-plan", dry_run=False,
    )
    assert blocked.reason_code == "foreign_or_mixed_submission_wal_plan"
    assert broker.submit_calls == calls


def _epoch_plan(plan, epoch: str):
    payload = plan.to_dict()
    return _rebuild_exact(
        payload,
        constraints={
            **payload["constraints"],
            "paper_drill_epoch": epoch,
            "paper_drill_live_eligible": False,
        },
    )


def test_distinct_paper_drill_epochs_have_isolated_wal_and_claims(tmp_path: Path):
    broker = TrackingPaperBroker()
    first = _epoch_plan(_plan(), "2026-08-12T1030ET")
    first_result = execute_exact_plan(
        plan_payload=first.to_dict(), broker=broker, env=_execution_env(tmp_path),
        wal_root=tmp_path / "wal", attempt_id="epoch-1030", dry_run=False,
    )
    assert first_result.terminal_outcome is TerminalOutcome.RECONCILED_SUCCESS

    second = _rebuild_exact(
        first.to_dict(),
        run_id="authority-epoch-1130",
        starting_positions=[{"symbol": "AAPL", "quantity": 2.0}],
        starting_cash=900.0,
        sell_orders=[],
        buy_orders=[],
        expected_posttrade_positions=[{"symbol": "AAPL", "quantity": 2.0}],
        expected_posttrade_cash=900.0,
        constraints={
            **first.to_dict()["constraints"],
            "paper_drill_epoch": "2026-08-12T1130ET",
        },
    )
    second_result = execute_exact_plan(
        plan_payload=second.to_dict(), broker=broker, env=_execution_env(tmp_path),
        wal_root=tmp_path / "wal", attempt_id="epoch-1130", dry_run=False,
    )
    assert second_result.terminal_outcome is TerminalOutcome.AUTHORIZED_NO_TRADE
    assert broker.submit_calls == 2
    assert len(list((tmp_path / "account_authority").rglob("plan_claim.json"))) == 2
    assert (tmp_path / "wal/epochs/2026-08-12T1030ET/2026-08-12/intents").is_dir()


def test_filled_prior_epoch_blocks_later_epoch_on_lagging_account_snapshot(
    tmp_path: Path,
):
    broker = TrackingPaperBroker()
    first = _epoch_plan(_plan(), "2026-08-12T1030ET")
    first_result = execute_exact_plan(
        plan_payload=first.to_dict(),
        broker=broker,
        env=_execution_env(tmp_path),
        wal_root=tmp_path / "wal",
        attempt_id="filled-1030",
        dry_run=False,
    )
    assert first_result.terminal_outcome is TerminalOutcome.RECONCILED_SUCCESS
    assert broker.submit_calls == 2

    # Simulate an account endpoint lagging backward even though stable-ID order
    # lookup already reports both prior orders filled. A plan built from this
    # stale snapshot would repeat the same economic transition under new IDs.
    broker.positions = [
        {"symbol": "OLD", "qty": "1", "market_value": "100"}
    ]
    broker.cash = 900.0
    second = _epoch_plan(
        _rebuild_exact(first.to_dict(), run_id="lagging-snapshot-1130"),
        "2026-08-12T1130ET",
    )
    blocked = execute_exact_plan(
        plan_payload=second.to_dict(),
        broker=broker,
        env=_execution_env(tmp_path),
        wal_root=tmp_path / "wal",
        attempt_id="lagging-snapshot-1130",
        dry_run=False,
    )

    assert blocked.reason_code == "prior_epoch_submission_unresolved"
    assert broker.submit_calls == 2
    assert len(list((tmp_path / "wal").rglob("*/intents/*.json"))) == 2


def test_three_successive_epochs_validate_ordered_reconciled_state_chain(
    tmp_path: Path,
):
    broker = TrackingPaperBroker()
    first = _epoch_plan(_plan(), "2026-08-12T1030ET")
    assert execute_exact_plan(
        plan_payload=first.to_dict(),
        broker=broker,
        env=_execution_env(tmp_path),
        wal_root=tmp_path / "wal",
        attempt_id="chain-1030",
        dry_run=False,
    ).terminal_outcome is TerminalOutcome.RECONCILED_SUCCESS

    second = _rebuild_exact(
        first.to_dict(),
        run_id="chain-1130",
        starting_positions=[{"symbol": "AAPL", "quantity": 2.0}],
        starting_cash=900.0,
        sell_orders=[
            {
                "symbol": "AAPL",
                "side": "SELL",
                "quantity": 2,
                "expected_price": 50,
                "notional": 100,
            }
        ],
        buy_orders=[
            {
                "symbol": "MSFT",
                "side": "BUY",
                "quantity": 2,
                "expected_price": 50,
                "notional": 100,
            }
        ],
        expected_posttrade_positions=[{"symbol": "MSFT", "quantity": 2.0}],
        expected_posttrade_cash=900.0,
        constraints={
            **first.to_dict()["constraints"],
            "paper_drill_epoch": "2026-08-12T1130ET",
            "paper_drill_live_eligible": False,
        },
    )
    assert execute_exact_plan(
        plan_payload=second.to_dict(),
        broker=broker,
        env=_execution_env(tmp_path),
        wal_root=tmp_path / "wal",
        attempt_id="chain-1130",
        dry_run=False,
    ).terminal_outcome is TerminalOutcome.RECONCILED_SUCCESS

    third = _rebuild_exact(
        second.to_dict(),
        run_id="chain-1230",
        starting_positions=[{"symbol": "MSFT", "quantity": 2.0}],
        starting_cash=900.0,
        sell_orders=[],
        buy_orders=[],
        expected_posttrade_positions=[{"symbol": "MSFT", "quantity": 2.0}],
        expected_posttrade_cash=900.0,
        constraints={
            **second.to_dict()["constraints"],
            "paper_drill_epoch": "2026-08-12T1230ET",
            "paper_drill_live_eligible": False,
        },
    )
    third_result = execute_exact_plan(
        plan_payload=third.to_dict(),
        broker=broker,
        env=_execution_env(tmp_path),
        wal_root=tmp_path / "wal",
        attempt_id="chain-1230",
        dry_run=False,
    )

    assert third_result.terminal_outcome is TerminalOutcome.AUTHORIZED_NO_TRADE
    assert broker.submit_calls == 4


def test_latest_epoch_success_replay_uses_current_wal_without_resubmission(
    tmp_path: Path,
):
    broker = TrackingPaperBroker()
    first = _epoch_plan(_plan(), "2026-08-12T1030ET")
    assert execute_exact_plan(
        plan_payload=first.to_dict(),
        broker=broker,
        env=_execution_env(tmp_path),
        wal_root=tmp_path / "wal",
        attempt_id="replay-chain-1030",
        dry_run=False,
    ).terminal_outcome is TerminalOutcome.RECONCILED_SUCCESS
    second = _rebuild_exact(
        first.to_dict(),
        run_id="replay-chain-1130",
        starting_positions=[{"symbol": "AAPL", "quantity": 2.0}],
        starting_cash=900.0,
        sell_orders=[
            {
                "symbol": "AAPL",
                "side": "SELL",
                "quantity": 2,
                "expected_price": 50,
                "notional": 100,
            }
        ],
        buy_orders=[
            {
                "symbol": "MSFT",
                "side": "BUY",
                "quantity": 2,
                "expected_price": 50,
                "notional": 100,
            }
        ],
        expected_posttrade_positions=[{"symbol": "MSFT", "quantity": 2.0}],
        expected_posttrade_cash=900.0,
        constraints={
            **first.to_dict()["constraints"],
            "paper_drill_epoch": "2026-08-12T1130ET",
            "paper_drill_live_eligible": False,
        },
    )
    first_second = execute_exact_plan(
        plan_payload=second.to_dict(),
        broker=broker,
        env=_execution_env(tmp_path),
        wal_root=tmp_path / "wal",
        attempt_id="replay-chain-1130-first",
        dry_run=False,
    )
    assert first_second.terminal_outcome is TerminalOutcome.RECONCILED_SUCCESS
    assert broker.submit_calls == 4

    replay = execute_exact_plan(
        plan_payload=second.to_dict(),
        broker=broker,
        env=_execution_env(tmp_path),
        wal_root=tmp_path / "wal",
        attempt_id="replay-chain-1130-recovery",
        dry_run=False,
    )

    assert replay.terminal_outcome is TerminalOutcome.RECONCILED_SUCCESS
    assert len(replay.orders_submitted) == 2
    assert all(
        row.get("recovered_by_client_order_id") is True
        for row in replay.orders_submitted
    )
    assert broker.submit_calls == 4
    assert len(list((tmp_path / "wal").rglob("*/intents/*.json"))) == 4


def test_recovery_dry_run_is_wal_nonmutating_and_does_not_poison_next_epoch(
    tmp_path: Path,
):
    broker = TrackingPaperBroker()
    first = _epoch_plan(_plan(), "2026-08-12T1030ET")
    assert execute_exact_plan(
        plan_payload=first.to_dict(),
        broker=broker,
        env=_execution_env(tmp_path),
        wal_root=tmp_path / "wal",
        attempt_id="dry-replay-1030",
        dry_run=False,
    ).terminal_outcome is TerminalOutcome.RECONCILED_SUCCESS
    resolution_paths = sorted(
        (tmp_path / "wal").rglob("*/resolutions/*/*.json")
    )
    before = {path: path.read_bytes() for path in resolution_paths}

    dry = execute_exact_plan(
        plan_payload=first.to_dict(),
        broker=broker,
        env=_execution_env(tmp_path),
        wal_root=tmp_path / "wal",
        attempt_id="dry-replay-1030-validation",
        dry_run=True,
    )
    assert dry.status == "DRY_RUN"
    after_paths = sorted((tmp_path / "wal").rglob("*/resolutions/*/*.json"))
    assert after_paths == resolution_paths
    assert {path: path.read_bytes() for path in after_paths} == before

    next_epoch = _rebuild_exact(
        first.to_dict(),
        run_id="dry-replay-1130",
        starting_positions=[{"symbol": "AAPL", "quantity": 2.0}],
        starting_cash=900.0,
        sell_orders=[],
        buy_orders=[],
        expected_posttrade_positions=[{"symbol": "AAPL", "quantity": 2.0}],
        expected_posttrade_cash=900.0,
        constraints={
            **first.to_dict()["constraints"],
            "paper_drill_epoch": "2026-08-12T1130ET",
            "paper_drill_live_eligible": False,
        },
    )
    result = execute_exact_plan(
        plan_payload=next_epoch.to_dict(),
        broker=broker,
        env=_execution_env(tmp_path),
        wal_root=tmp_path / "wal",
        attempt_id="dry-replay-1130",
        dry_run=False,
    )
    assert result.terminal_outcome is TerminalOutcome.AUTHORIZED_NO_TRADE
    assert broker.submit_calls == 2


def test_out_of_order_paper_drill_epoch_is_blocked_before_wal_or_submission(
    tmp_path: Path,
):
    broker = TrackingPaperBroker()
    later = _epoch_plan(_plan(), "2026-08-12T1130ET")
    assert execute_exact_plan(
        plan_payload=later.to_dict(),
        broker=broker,
        env=_execution_env(tmp_path),
        wal_root=tmp_path / "wal",
        attempt_id="epoch-order-1130",
        dry_run=False,
    ).terminal_outcome is TerminalOutcome.RECONCILED_SUCCESS
    earlier = _rebuild_exact(
        later.to_dict(),
        run_id="epoch-order-1030",
        starting_positions=[{"symbol": "AAPL", "quantity": 2.0}],
        starting_cash=900.0,
        sell_orders=[],
        buy_orders=[],
        expected_posttrade_positions=[{"symbol": "AAPL", "quantity": 2.0}],
        expected_posttrade_cash=900.0,
        constraints={
            **later.to_dict()["constraints"],
            "paper_drill_epoch": "2026-08-12T1030ET",
            "paper_drill_live_eligible": False,
        },
    )
    blocked = execute_exact_plan(
        plan_payload=earlier.to_dict(),
        broker=broker,
        env=_execution_env(tmp_path),
        wal_root=tmp_path / "wal",
        attempt_id="epoch-order-1030",
        dry_run=False,
    )
    assert blocked.reason_code == "paper drill epoch order is not monotonic"
    assert "not monotonic" in blocked.reason_code
    assert broker.submit_calls == 2
    assert not (
        tmp_path
        / "wal/epochs/2026-08-12T1030ET/2026-08-12/intents"
    ).exists()


def test_later_no_trade_claim_blocks_earlier_trade_epoch(tmp_path: Path):
    broker = TrackingPaperBroker()
    later_no_trade = _epoch_plan(
        _plan(no_trade=True),
        "2026-08-12T1130ET",
    )
    first = execute_exact_plan(
        plan_payload=later_no_trade.to_dict(),
        broker=broker,
        env=_execution_env(tmp_path),
        wal_root=tmp_path / "wal",
        attempt_id="claim-order-no-trade-1130",
        dry_run=False,
    )
    assert first.terminal_outcome is TerminalOutcome.AUTHORIZED_NO_TRADE
    assert broker.submit_calls == 0

    earlier_trade = _epoch_plan(_plan(), "2026-08-12T1030ET")
    blocked = execute_exact_plan(
        plan_payload=earlier_trade.to_dict(),
        broker=broker,
        env=_execution_env(tmp_path),
        wal_root=tmp_path / "wal",
        attempt_id="claim-order-trade-1030",
        dry_run=False,
    )
    assert blocked.status == "BLOCKED"
    assert blocked.reason_code == "paper drill epoch order is not monotonic"
    assert broker.submit_calls == 0
    assert not (
        tmp_path
        / "wal/epochs/2026-08-12T1030ET/2026-08-12/intents"
    ).exists()


def test_current_epoch_recovery_explains_own_fill_after_prior_epoch(
    tmp_path: Path,
):
    class OneAmbiguousSecondEpochSellBroker(TrackingPaperBroker):
        def __init__(self):
            super().__init__()
            self.fail_next_lookup = False

        def submit_market_order(self, **kwargs):
            row = super().submit_market_order(**kwargs)
            if (
                str(kwargs["symbol"]).upper() == "AAPL"
                and str(kwargs["side"]).upper() == "SELL"
                and not self.fail_next_lookup
            ):
                self.fail_next_lookup = True
                raise TimeoutError("response lost after broker acceptance")
            return row

        def find_order_by_client_id(self, client_id):
            if self.fail_next_lookup:
                self.fail_next_lookup = False
                raise TimeoutError("stable lookup temporarily unavailable")
            return super().find_order_by_client_id(client_id)

    broker = OneAmbiguousSecondEpochSellBroker()
    first = _epoch_plan(_plan(), "2026-08-12T1030ET")
    assert execute_exact_plan(
        plan_payload=first.to_dict(),
        broker=broker,
        env=_execution_env(tmp_path),
        wal_root=tmp_path / "wal",
        attempt_id="own-fill-1030",
        dry_run=False,
    ).terminal_outcome is TerminalOutcome.RECONCILED_SUCCESS
    second = _rebuild_exact(
        first.to_dict(),
        run_id="own-fill-1130",
        starting_positions=[{"symbol": "AAPL", "quantity": 2.0}],
        starting_cash=900.0,
        sell_orders=[
            {
                "symbol": "AAPL",
                "side": "SELL",
                "quantity": 2,
                "expected_price": 50,
                "notional": 100,
            }
        ],
        buy_orders=[
            {
                "symbol": "MSFT",
                "side": "BUY",
                "quantity": 2,
                "expected_price": 50,
                "notional": 100,
            }
        ],
        expected_posttrade_positions=[{"symbol": "MSFT", "quantity": 2.0}],
        expected_posttrade_cash=900.0,
        constraints={
            **first.to_dict()["constraints"],
            "paper_drill_epoch": "2026-08-12T1130ET",
            "paper_drill_live_eligible": False,
        },
    )
    ambiguous = execute_exact_plan(
        plan_payload=second.to_dict(),
        broker=broker,
        env=_execution_env(tmp_path),
        wal_root=tmp_path / "wal",
        attempt_id="own-fill-1130-ambiguous",
        dry_run=False,
    )
    assert ambiguous.terminal_outcome is TerminalOutcome.SUBMISSION_UNKNOWN
    assert broker.submit_calls == 3

    recovered = execute_exact_plan(
        plan_payload=second.to_dict(),
        broker=broker,
        env=_execution_env(tmp_path),
        wal_root=tmp_path / "wal",
        attempt_id="own-fill-1130-recovery",
        dry_run=False,
    )

    assert recovered.terminal_outcome is TerminalOutcome.RECONCILED_SUCCESS
    assert [row["side"] for row in recovered.orders_submitted] == ["SELL", "BUY"]
    assert recovered.orders_submitted[0]["recovered_by_client_order_id"] is True
    assert broker.submit_calls == 4


def test_distinct_epoch_cannot_change_account_date_wal_base(tmp_path: Path):
    broker = TrackingPaperBroker()
    first = _epoch_plan(_plan(), "2026-08-12T1030ET")
    assert execute_exact_plan(
        plan_payload=first.to_dict(),
        broker=broker,
        env=_execution_env(tmp_path),
        wal_root=tmp_path / "wal-a",
        attempt_id="wal-base-1030",
        dry_run=False,
    ).terminal_outcome is TerminalOutcome.RECONCILED_SUCCESS
    second = _rebuild_exact(
        first.to_dict(),
        run_id="wal-base-1130",
        starting_positions=[{"symbol": "AAPL", "quantity": 2.0}],
        starting_cash=900.0,
        sell_orders=[],
        buy_orders=[],
        expected_posttrade_positions=[{"symbol": "AAPL", "quantity": 2.0}],
        expected_posttrade_cash=900.0,
        constraints={
            **first.to_dict()["constraints"],
            "paper_drill_epoch": "2026-08-12T1130ET",
            "paper_drill_live_eligible": False,
        },
    )

    blocked = execute_exact_plan(
        plan_payload=second.to_dict(),
        broker=broker,
        env=_execution_env(tmp_path),
        wal_root=tmp_path / "wal-b",
        attempt_id="wal-base-1130",
        dry_run=False,
    )

    assert blocked.reason_code == "submission_wal_base_conflicts_with_account_date"
    assert broker.submit_calls == 2
    assert not (tmp_path / "wal-b").exists()


def test_reusing_epoch_with_different_plan_is_blocked(tmp_path: Path):
    broker = TrackingPaperBroker()
    first = _epoch_plan(_plan(), "2026-08-12T1030ET")
    execute_exact_plan(
        plan_payload=first.to_dict(), broker=broker, env=_execution_env(tmp_path),
        wal_root=tmp_path / "wal", attempt_id="epoch-first", dry_run=False,
    )
    different = _epoch_plan(_plan(no_trade=True), "2026-08-12T1030ET")
    blocked = execute_exact_plan(
        plan_payload=different.to_dict(), broker=broker, env=_execution_env(tmp_path),
        wal_root=tmp_path / "wal", attempt_id="epoch-reuse", dry_run=False,
    )
    assert blocked.reason_code in {
        "foreign_or_mixed_submission_wal_plan",
        "plan_claim_conflicts_with_authorized_plan",
    }
    assert broker.submit_calls == 2


def test_unresolved_prior_epoch_blocks_new_epoch(tmp_path: Path):
    class UnknownAfterAcceptBroker(TrackingPaperBroker):
        def find_order_by_client_id(self, client_id):
            raise TimeoutError(f"broker lookup unavailable for {client_id}")

    broker = UnknownAfterAcceptBroker(crash_after_accept=True)
    first = _epoch_plan(_plan(), "2026-08-12T1030ET")
    failed = execute_exact_plan(
        plan_payload=first.to_dict(), broker=broker, env=_execution_env(tmp_path),
        wal_root=tmp_path / "wal", attempt_id="epoch-unknown", dry_run=False,
    )
    assert failed.terminal_outcome is TerminalOutcome.SUBMISSION_UNKNOWN
    calls = broker.submit_calls
    second = _epoch_plan(_plan(no_trade=True), "2026-08-12T1130ET")
    blocked = execute_exact_plan(
        plan_payload=second.to_dict(), broker=broker, env=_execution_env(tmp_path),
        wal_root=tmp_path / "wal", attempt_id="epoch-after-unknown", dry_run=False,
    )
    assert blocked.reason_code == "prior_epoch_submission_unresolved"
    assert broker.submit_calls == calls


@pytest.mark.parametrize(
    "second_epoch",
    ["2026-08-12T1130ET", None],
    ids=["epoch-to-epoch", "epoch-to-legacy"],
)
def test_prior_accepted_order_blocks_unrelated_later_namespace(
    tmp_path: Path,
    second_epoch: str | None,
):
    class AcceptedOrderBroker(TrackingPaperBroker):
        def submit_market_order(self, **kwargs):
            self.submit_calls += 1
            row = {
                "id": f"broker-{self.submit_calls}",
                "client_order_id": str(kwargs["client_order_id"]),
                "symbol": str(kwargs["symbol"]),
                "side": str(kwargs["side"]).upper(),
                "qty": str(kwargs["qty"]),
                "status": "accepted",
                "filled_qty": "0",
            }
            self.orders[str(row["client_order_id"])] = row
            return copy.deepcopy(row)

    broker = AcceptedOrderBroker()
    first = _epoch_plan(_plan(), "2026-08-12T1030ET")
    accepted = execute_exact_plan(
        plan_payload=first.to_dict(),
        broker=broker,
        env=_execution_env(tmp_path),
        wal_root=tmp_path / "wal",
        attempt_id="accepted-1030",
        dry_run=False,
    )
    assert accepted.status == "SUBMITTED_UNFILLED"
    assert broker.submit_calls == 1

    constraints = dict(first.to_dict()["constraints"])
    if second_epoch is None:
        constraints.pop("paper_drill_epoch", None)
        constraints.pop("paper_drill_live_eligible", None)
    else:
        constraints.update(
            {
                "paper_drill_epoch": second_epoch,
                "paper_drill_live_eligible": False,
            }
        )
    second = _rebuild_exact(
        first.to_dict(),
        run_id=f"authority-{second_epoch or 'legacy'}",
        sell_orders=[],
        buy_orders=[
            {
                "symbol": "MSFT",
                "side": "BUY",
                "quantity": 1,
                "expected_price": 50,
                "notional": 50,
            }
        ],
        starting_positions=[{"symbol": "OLD", "quantity": 1.0}],
        starting_cash=900.0,
        expected_posttrade_positions=[
            {"symbol": "MSFT", "quantity": 1.0},
            {"symbol": "OLD", "quantity": 1.0},
        ],
        expected_posttrade_cash=850.0,
        constraints=constraints,
    )
    blocked = execute_exact_plan(
        plan_payload=second.to_dict(),
        broker=broker,
        env=_execution_env(tmp_path),
        wal_root=tmp_path / "wal",
        attempt_id=f"after-accepted-{second_epoch or 'legacy'}",
        dry_run=False,
    )

    if second_epoch is None:
        assert blocked.reason_code == "paper drill epoch order is not monotonic"
    else:
        assert blocked.reason_code == "prior_epoch_submission_unresolved"
    assert broker.submit_calls == 1
    assert len(list((tmp_path / "wal").rglob("*/intents/*.json"))) == 1


def test_authorizer_precheck_blocks_foreign_accepted_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    import scripts.authorize_exact_execution_plan as authorizer
    from core.submission_wal import (
        OrderIntent,
        ResolutionState,
        append_resolution,
        new_resolution,
        prepare_order_intent,
    )

    output = tmp_path / "paper_lane" / "plans" / "exact.latest.json"
    base_wal = tmp_path / "paper_lane" / "submission_wal"
    prior_wal = base_wal / "epochs" / "2026-08-12T1030ET"
    intent = OrderIntent(
        trade_date="2026-08-12",
        plan_id="plan:prior-1030",
        plan_hash="a" * 64,
        attempt_id="attempt-prior-1030",
        order_id="order:prior:accepted",
        client_order_id="cx-prior-accepted",
        symbol="AAPL",
        side="BUY",
        quantity=1,
        order_type="market",
        created_at="2026-08-12T14:30:00Z",
        expected_price=50,
        notional=50,
    )
    prepared = prepare_order_intent(prior_wal, intent)
    append_resolution(
        prior_wal,
        new_resolution(
            resolution_id="resolution-prior-submitted",
            intent=prepared.intent,
            state=ResolutionState.SUBMITTED,
            broker_order_id="broker-prior-accepted",
        ),
    )
    broker = TrackingPaperBroker()
    broker.orders[intent.client_order_id] = {
        "id": "broker-prior-accepted",
        "client_order_id": intent.client_order_id,
        "symbol": "AAPL",
        "side": "BUY",
        "status": "accepted",
        "filled_qty": "0",
    }
    monkeypatch.setattr(authorizer.AlpacaBroker, "from_env", lambda: broker)
    source = tmp_path / "plan.json"
    source.write_text(
        json.dumps({"trade_date": "2026-08-12"}) + "\n",
        encoding="utf-8",
    )

    assert authorizer.main(
        [
            "--plan",
            str(source),
            "--run-id",
            "blocked-by-prior-accepted",
            "--output",
            str(output),
        ]
    ) == 1
    emitted = json.loads(capsys.readouterr().out)
    assert emitted["reason_code"] == "paper_drill_prior_submission_unresolved"
    assert emitted["orders_submitted"] == 0
    assert broker.submit_calls == 0
    assert not output.exists()


@pytest.mark.parametrize("no_trade", [False, True], ids=["trade", "no-trade"])
def test_any_unrelated_broker_open_order_blocks_exact_plan(
    tmp_path: Path,
    no_trade: bool,
):
    class UnrelatedOpenOrderBroker(TrackingPaperBroker):
        def list_orders(self, status="open", limit=100, **kwargs):
            del limit, kwargs
            if status != "open":
                return []
            return [
                {
                    "id": "external-open-order",
                    "client_order_id": "external-unrelated-order",
                    "symbol": "MSFT",
                    "side": "BUY",
                    "status": "accepted",
                    "filled_qty": "0",
                }
            ]

    broker = UnrelatedOpenOrderBroker()
    blocked = execute_exact_plan(
        plan_payload=_plan(no_trade=no_trade).to_dict(),
        broker=broker,
        env=_execution_env(tmp_path),
        wal_root=tmp_path / "wal",
        attempt_id=f"unrelated-open-{no_trade}",
        dry_run=False,
    )

    assert blocked.reason_code == "unresolved_broker_open_order"
    assert blocked.reconciliation_status == "FAILED_PRE_SUBMIT"
    assert broker.submit_calls == 0
    assert not list((tmp_path / "wal").rglob("*/intents/*.json"))


def test_open_order_appearing_mid_batch_blocks_every_later_intent(
    tmp_path: Path,
):
    class MidBatchExternalOrderBroker(TrackingPaperBroker):
        def __init__(self):
            super().__init__()
            self.open_order_checks = 0

        def list_orders(self, status="open", limit=100, **kwargs):
            del limit, kwargs
            if status != "open":
                return []
            self.open_order_checks += 1
            if self.submit_calls == 0:
                return []
            return [
                {
                    "id": "external-open-mid-batch",
                    "client_order_id": "external-unrelated-mid-batch",
                    "symbol": "MSFT",
                    "side": "BUY",
                    "status": "accepted",
                    "filled_qty": "0",
                }
            ]

    broker = MidBatchExternalOrderBroker()
    result = execute_exact_plan(
        plan_payload=_plan().to_dict(),
        broker=broker,
        env=_execution_env(tmp_path),
        wal_root=tmp_path / "wal",
        attempt_id="external-open-mid-batch",
        dry_run=False,
    )

    assert result.status == "FAILED_RECONCILIATION"
    assert result.reason_code == (
        "broker_open_order_unresolved_order:mid_batch_submission_halted"
    )
    assert broker.submit_calls == 1
    assert broker.open_order_checks >= 3
    assert len(result.orders_submitted) == len(result.orders_filled) == 1
    assert len(list((tmp_path / "wal").rglob("*/intents/*.json"))) == 1


def test_external_fill_disappearing_from_open_orders_blocks_before_new_wal(
    tmp_path: Path,
):
    class ExternalTerminalFillBroker(TrackingPaperBroker):
        def __init__(self):
            super().__init__()
            self.open_order_checks = 0

        def list_orders(self, status="open", limit=100, **kwargs):
            del limit, kwargs
            if status != "open":
                return []
            self.open_order_checks += 1
            if self.open_order_checks == 2:
                # An unrelated order fills between the initial account snapshot
                # and the per-order open-order check, then vanishes from the
                # open-order endpoint before it can be observed there.
                self.cash -= 100.0
                self.positions.append(
                    {"symbol": "MSFT", "qty": "1", "market_value": "100"}
                )
            return []

    broker = ExternalTerminalFillBroker()
    blocked = execute_exact_plan(
        plan_payload=_plan().to_dict(),
        broker=broker,
        env=_execution_env(tmp_path),
        wal_root=tmp_path / "wal",
        attempt_id="external-terminal-fill-boundary",
        dry_run=False,
    )

    assert blocked.status == "BLOCKED"
    assert blocked.reason_code == "submission_boundary_broker_state_changed"
    assert broker.submit_calls == 0
    assert broker.open_order_checks >= 2
    assert not list((tmp_path / "wal").rglob("*/intents/*.json"))


def test_concurrent_different_same_date_plans_have_one_os_locked_winner(
    tmp_path: Path,
):
    broker = TrackingPaperBroker()
    first = _plan()
    payload = first.to_dict()
    second = _rebuild_exact(
        payload,
        buy_orders=[
            {
                "symbol": "MSFT",
                "side": "BUY",
                "quantity": 1,
                "expected_price": 100,
                "notional": 100,
            }
        ],
        expected_posttrade_positions=[{"symbol": "MSFT", "quantity": 1.0}],
    )
    barrier = threading.Barrier(2)

    def run(plan, attempt_id):
        barrier.wait(timeout=5)
        return execute_exact_plan(
            plan_payload=plan.to_dict(),
            broker=broker,
            env=_execution_env(tmp_path),
            wal_root=tmp_path / "wal",
            attempt_id=attempt_id,
            dry_run=False,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(run, first, "concurrent-first"),
            pool.submit(run, second, "concurrent-second"),
        ]
        results = [future.result(timeout=10) for future in futures]

    assert sum(
        result.terminal_outcome is TerminalOutcome.RECONCILED_SUCCESS
        for result in results
    ) == 1
    blocked = next(
        result
        for result in results
        if result.terminal_outcome is TerminalOutcome.SYSTEM_FAILURE
    )
    assert blocked.reason_code in {
        "foreign_or_mixed_submission_wal_plan",
        "plan_claim_conflicts_with_authorized_plan",
    }
    assert broker.submit_calls == 2
    claims = list((tmp_path / "account_authority").rglob("plan_claim.json"))
    assert len(claims) == 1
    claim = json.loads(claims[0].read_text(encoding="utf-8"))
    unhashed = dict(claim)
    content_hash = unhashed.pop("content_hash")
    assert content_hash == hashlib.sha256(
        json.dumps(
            unhashed,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    winner = next(
        result
        for result in results
        if result.terminal_outcome is TerminalOutcome.RECONCILED_SUCCESS
    )
    assert claim["plan_id"] == winner.plan_id_received
    assert claim["plan_hash"] == winner.plan_hash_received


def test_concurrent_different_plans_cannot_escape_via_distinct_wal_roots(
    tmp_path: Path,
):
    broker = TrackingPaperBroker()
    first = _plan()
    payload = first.to_dict()
    second = _rebuild_exact(
        payload,
        buy_orders=[
            {
                "symbol": "MSFT",
                "side": "BUY",
                "quantity": 1,
                "expected_price": 100,
                "notional": 100,
            }
        ],
        expected_posttrade_positions=[{"symbol": "MSFT", "quantity": 1.0}],
    )
    env = _execution_env(tmp_path)
    barrier = threading.Barrier(2)

    def run(plan, attempt_id, wal_name):
        barrier.wait(timeout=5)
        return execute_exact_plan(
            plan_payload=plan.to_dict(),
            broker=broker,
            env=env,
            wal_root=tmp_path / wal_name,
            attempt_id=attempt_id,
            dry_run=False,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [
            future.result(timeout=10)
            for future in (
                pool.submit(run, first, "two-root-first", "wal-a"),
                pool.submit(run, second, "two-root-second", "wal-b"),
            )
        ]

    assert sum(
        row.terminal_outcome is TerminalOutcome.RECONCILED_SUCCESS
        for row in results
    ) == 1
    blocked = next(
        row for row in results
        if row.terminal_outcome is TerminalOutcome.SYSTEM_FAILURE
    )
    assert blocked.reason_code in {
        "plan_claim_conflicts_with_authorized_plan",
        "submission_wal_base_conflicts_with_account_date",
    }
    assert broker.submit_calls == 2
    claims = list((tmp_path / "account_authority").rglob("plan_claim.json"))
    assert len(claims) == 1
    claim = json.loads(claims[0].read_text(encoding="utf-8"))
    losing_wal = (
        tmp_path / "wal-b"
        if Path(claim["submission_wal_root"]) == (tmp_path / "wal-a").resolve()
        else tmp_path / "wal-a"
    )
    assert not (losing_wal / first.trade_date / "intents").exists()


def test_immutable_plan_claim_tamper_blocks_recovery_without_resubmission(
    tmp_path: Path,
):
    broker = TrackingPaperBroker()
    plan = _plan()
    first = execute_exact_plan(
        plan_payload=plan.to_dict(), broker=broker, env=_execution_env(tmp_path),
        wal_root=tmp_path / "wal", attempt_id="claim-before-tamper", dry_run=False,
    )
    assert first.terminal_outcome is TerminalOutcome.RECONCILED_SUCCESS
    calls = broker.submit_calls
    claim_path = next((tmp_path / "account_authority").rglob("plan_claim.json"))
    claim = json.loads(claim_path.read_text(encoding="utf-8"))
    claim["plan_id"] = "plan:tampered"
    claim_path.write_text(json.dumps(claim, sort_keys=True) + "\n", encoding="utf-8")

    blocked = execute_exact_plan(
        plan_payload=plan.to_dict(), broker=broker, env=_execution_env(tmp_path),
        wal_root=tmp_path / "wal", attempt_id="claim-after-tamper", dry_run=False,
    )
    assert blocked.status == "BLOCKED"
    assert blocked.reason_code == "plan_claim_integrity_failed:content_hash_mismatch"
    assert len(blocked.orders_filled) == 2
    assert broker.submit_calls == calls


class PartialSellBroker(TrackingPaperBroker):
    def submit_market_order(self, **kwargs):
        if str(kwargs["side"]).upper() != "SELL":
            return super().submit_market_order(**kwargs)
        self.submit_calls += 1
        quantity = float(kwargs["qty"])
        partial_quantity = quantity * 0.4
        price = float(kwargs["estimated_notional"]) / quantity
        client_id = str(kwargs["client_order_id"])
        row = {
            "id": f"broker-{self.submit_calls}",
            "client_order_id": client_id,
            "symbol": str(kwargs["symbol"]),
            "side": "SELL",
            "qty": str(quantity),
            "status": "partially_filled",
            "filled_qty": str(partial_quantity),
            "filled_avg_price": str(price),
        }
        self.orders[client_id] = row
        remaining = quantity - partial_quantity
        self.positions = [
            {
                "symbol": str(kwargs["symbol"]),
                "qty": str(remaining),
                "market_value": str(remaining * price),
            }
        ]
        self.cash += partial_quantity * price
        return copy.deepcopy(row)

    def transition_sell(self, status: str) -> None:
        sell = next(row for row in self.orders.values() if row["side"] == "SELL")
        if status == "filled":
            remaining = float(self.positions[0]["qty"])
            price = float(sell["filled_avg_price"])
            self.cash += remaining * price
            self.positions = []
            sell["filled_qty"] = "1.0"
            sell["status"] = "filled"
        elif status == "canceled":
            sell["status"] = "canceled"
        else:
            raise AssertionError(f"unsupported transition {status}")


@pytest.mark.parametrize(
    ("filled_qty", "filled_avg_price"),
    [
        ("0", "100"),
        ("0.4", "100"),
        ("1", ""),
    ],
    ids=["zero-fill", "partial-fill", "missing-fill-price"],
)
def test_contradictory_filled_sell_never_advances_to_buy(
    tmp_path: Path,
    filled_qty: str,
    filled_avg_price: str,
):
    class ContradictoryFilledSellBroker(TrackingPaperBroker):
        def submit_market_order(self, **kwargs):
            if str(kwargs["side"]).upper() != "SELL":
                raise AssertionError("BUY must never be reached")
            self.submit_calls += 1
            row = {
                "id": f"broker-{self.submit_calls}",
                "client_order_id": str(kwargs["client_order_id"]),
                "symbol": str(kwargs["symbol"]),
                "side": "SELL",
                "qty": str(kwargs["qty"]),
                "status": "filled",
                "filled_qty": filled_qty,
                "filled_avg_price": filled_avg_price,
            }
            self.orders[str(row["client_order_id"])] = copy.deepcopy(row)
            return row

    broker = ContradictoryFilledSellBroker()
    result = execute_exact_plan(
        plan_payload=_plan().to_dict(),
        broker=broker,
        env=_execution_env(tmp_path),
        wal_root=tmp_path / "wal",
        attempt_id="contradictory-filled-sell",
        dry_run=False,
    )

    assert result.terminal_outcome is TerminalOutcome.SUBMISSION_UNKNOWN
    assert result.status == "SUBMISSION_UNKNOWN"
    assert broker.submit_calls == 1
    assert not any(row.get("side") == "BUY" for row in broker.orders.values())
    assert len(list((tmp_path / "wal").rglob("*/intents/*.json"))) == 1


def test_broker_observation_persistence_failure_stops_after_first_submission(
    tmp_path: Path,
    monkeypatch,
):
    broker = TrackingPaperBroker()

    def fail_observation(*_args, **_kwargs):
        raise exact_executor_module.WalPersistenceError("simulated fsync failure")

    monkeypatch.setattr(
        exact_executor_module,
        "_append_broker_observation",
        fail_observation,
    )
    result = execute_exact_plan(
        plan_payload=_plan().to_dict(),
        broker=broker,
        env=_execution_env(tmp_path),
        wal_root=tmp_path / "wal",
        attempt_id="broker-observation-fsync-failure",
        dry_run=False,
    )

    assert result.terminal_outcome is TerminalOutcome.SUBMISSION_UNKNOWN
    assert result.status == "SUBMISSION_UNKNOWN"
    assert broker.submit_calls == 1
    assert len(list((tmp_path / "wal").rglob("*/intents/*.json"))) == 1
    assert not list((tmp_path / "wal").rglob("*/resolutions/*/*.json"))


def test_prior_partial_fill_blocks_unrelated_later_epoch(tmp_path: Path):
    broker = PartialSellBroker()
    first = _epoch_plan(_plan(), "2026-08-12T1030ET")
    partial = execute_exact_plan(
        plan_payload=first.to_dict(),
        broker=broker,
        env=_execution_env(tmp_path),
        wal_root=tmp_path / "wal",
        attempt_id="partial-1030",
        dry_run=False,
    )
    assert partial.status == "SUBMITTED_UNFILLED"
    assert broker.submit_calls == 1

    second = _rebuild_exact(
        first.to_dict(),
        run_id="authority-after-partial-1130",
        sell_orders=[],
        buy_orders=[
            {
                "symbol": "MSFT",
                "side": "BUY",
                "quantity": 1,
                "expected_price": 50,
                "notional": 50,
            }
        ],
        starting_positions=[{"symbol": "OLD", "quantity": 0.6}],
        starting_cash=940.0,
        expected_posttrade_positions=[
            {"symbol": "MSFT", "quantity": 1.0},
            {"symbol": "OLD", "quantity": 0.6},
        ],
        expected_posttrade_cash=890.0,
        constraints={
            **first.to_dict()["constraints"],
            "paper_drill_epoch": "2026-08-12T1130ET",
            "paper_drill_live_eligible": False,
        },
    )
    blocked = execute_exact_plan(
        plan_payload=second.to_dict(),
        broker=broker,
        env=_execution_env(tmp_path),
        wal_root=tmp_path / "wal",
        attempt_id="after-partial-1130",
        dry_run=False,
    )

    assert blocked.reason_code == "prior_epoch_submission_unresolved"
    assert broker.submit_calls == 1
    assert len(list((tmp_path / "wal").rglob("*/intents/*.json"))) == 1


def test_durable_partial_fill_cannot_be_erased_by_lookup_and_lagging_snapshot(
    tmp_path: Path,
):
    broker = PartialSellBroker()
    first = _epoch_plan(_plan(), "2026-08-12T1030ET")
    partial = execute_exact_plan(
        plan_payload=first.to_dict(),
        broker=broker,
        env=_execution_env(tmp_path),
        wal_root=tmp_path / "wal",
        attempt_id="durable-partial-1030",
        dry_run=False,
    )
    assert partial.status == "SUBMITTED_UNFILLED"
    assert broker.submit_calls == 1

    sell = next(row for row in broker.orders.values() if row["side"] == "SELL")
    sell["status"] = "canceled"
    sell["filled_qty"] = "0"
    sell["filled_avg_price"] = None
    # Simulate both order-history and account endpoints lagging/regressing.  The
    # append-only WAL must retain the previously observed 0.4-share fill.
    broker.positions = [
        {"symbol": "OLD", "qty": "1", "market_value": "100"}
    ]
    broker.cash = 900.0

    second = _rebuild_exact(
        first.to_dict(),
        run_id="durable-partial-1130",
        sell_orders=[],
        buy_orders=[
            {
                "symbol": "MSFT",
                "side": "BUY",
                "quantity": 1,
                "expected_price": 50,
                "notional": 50,
            }
        ],
        starting_positions=[{"symbol": "OLD", "quantity": 1.0}],
        starting_cash=900.0,
        expected_posttrade_positions=[
            {"symbol": "MSFT", "quantity": 1.0},
            {"symbol": "OLD", "quantity": 1.0},
        ],
        expected_posttrade_cash=850.0,
        constraints={
            **first.to_dict()["constraints"],
            "paper_drill_epoch": "2026-08-12T1130ET",
            "paper_drill_live_eligible": False,
        },
    )
    blocked = execute_exact_plan(
        plan_payload=second.to_dict(),
        broker=broker,
        env=_execution_env(tmp_path),
        wal_root=tmp_path / "wal",
        attempt_id="durable-partial-1130",
        dry_run=False,
    )

    assert blocked.status == "BLOCKED"
    assert blocked.reason_code.startswith("prior_epoch_wal_integrity_failed:")
    assert "filled quantity regressed" in blocked.reason_code
    assert broker.submit_calls == 1
    assert len(list((tmp_path / "wal").rglob("*/intents/*.json"))) == 1


def test_partial_sell_open_then_filled_recovers_stable_id_before_buy(
    tmp_path: Path,
):
    broker = PartialSellBroker()
    plan = _plan()
    first = execute_exact_plan(
        plan_payload=plan.to_dict(), broker=broker, env=_env(),
        wal_root=tmp_path / "wal", attempt_id="partial-open", dry_run=False,
    )
    assert first.status == "SUBMITTED_UNFILLED"
    assert len(first.orders_submitted) == 1
    assert len(first.orders_filled) == 1
    assert float(first.orders_filled[0]["filled_qty"]) == pytest.approx(0.4)
    assert [row["symbol"] for row in first.orders_suppressed] == ["AAPL"]
    assert first.orders_suppressed[0]["suppression"]["reason_code"] == (
        "PRIOR_ORDER_UNRESOLVED"
    )
    assert broker.submit_calls == 1

    still_open = execute_exact_plan(
        plan_payload=plan.to_dict(), broker=broker, env=_env(),
        wal_root=tmp_path / "wal", attempt_id="partial-recovery-open", dry_run=False,
    )
    assert still_open.status == "SUBMITTED_UNFILLED"
    assert len(still_open.orders_filled) == 1
    assert still_open.orders_submitted[0]["recovered_by_client_order_id"] is True
    assert still_open.orders_suppressed[0]["suppression"]["reason_code"] == (
        "PRIOR_ORDER_UNRESOLVED"
    )
    assert broker.submit_calls == 1

    broker.transition_sell("filled")
    completed = execute_exact_plan(
        plan_payload=plan.to_dict(), broker=broker, env=_env(),
        wal_root=tmp_path / "wal", attempt_id="partial-recovery-filled", dry_run=False,
    )
    assert completed.terminal_outcome is TerminalOutcome.RECONCILED_SUCCESS
    assert [row["side"] for row in completed.orders_submitted] == ["SELL", "BUY"]
    assert completed.orders_submitted[0]["recovered_by_client_order_id"] is True
    assert not completed.orders_suppressed
    assert broker.submit_calls == 2


def test_partial_sell_then_canceled_preserves_fill_and_never_buys(tmp_path: Path):
    broker = PartialSellBroker()
    plan = _plan()
    first = execute_exact_plan(
        plan_payload=plan.to_dict(), broker=broker, env=_env(),
        wal_root=tmp_path / "wal", attempt_id="partial-before-cancel", dry_run=False,
    )
    assert first.status == "SUBMITTED_UNFILLED"
    broker.transition_sell("canceled")

    canceled = execute_exact_plan(
        plan_payload=plan.to_dict(), broker=broker, env=_env(),
        wal_root=tmp_path / "wal", attempt_id="partial-canceled", dry_run=False,
    )
    assert canceled.status == "ORDER_REJECTED"
    assert len(canceled.orders_filled) == 1
    assert float(canceled.orders_filled[0]["filled_qty"]) == pytest.approx(0.4)
    assert len(canceled.orders_rejected) == 1
    assert canceled.orders_rejected[0]["status"] == "canceled"
    assert [row["symbol"] for row in canceled.orders_suppressed] == ["AAPL"]
    assert canceled.orders_suppressed[0]["suppression"]["reason_code"] == (
        "PRIOR_ORDER_REJECTED"
    )
    assert broker.submit_calls == 1
    assert "AAPL" not in {row["symbol"] for row in broker.orders.values()}

    next_epoch = _rebuild_exact(
        plan.to_dict(),
        run_id="after-terminal-partial-1130",
        starting_positions=[{"symbol": "OLD", "quantity": 0.6}],
        starting_cash=940.0,
        sell_orders=[],
        buy_orders=[],
        expected_posttrade_positions=[{"symbol": "OLD", "quantity": 0.6}],
        expected_posttrade_cash=940.0,
        constraints={
            **plan.to_dict()["constraints"],
            "paper_drill_epoch": "2026-08-12T1130ET",
            "paper_drill_live_eligible": False,
        },
    )
    next_result = execute_exact_plan(
        plan_payload=next_epoch.to_dict(),
        broker=broker,
        env=_execution_env(tmp_path),
        wal_root=tmp_path / "wal",
        attempt_id="after-terminal-partial-1130",
        dry_run=False,
    )
    assert next_result.terminal_outcome is TerminalOutcome.AUTHORIZED_NO_TRADE
    assert broker.submit_calls == 1


def test_alpaca_enum_qualified_terminal_status_is_recognized(tmp_path: Path):
    class EnumStringBroker(TrackingPaperBroker):
        def submit_market_order(self, **kwargs):
            row = super().submit_market_order(**kwargs)
            stored = self.orders[str(row["client_order_id"])]
            stored["status"] = "OrderStatus.FILLED"
            stored["side"] = f"OrderSide.{str(row['side']).upper()}"
            # Alpaca commonly returns a nonterminal submission response and an
            # enum-qualified terminal row on the follow-up get_order call.
            row["status"] = "OrderStatus.PENDING_NEW"
            row["side"] = stored["side"]
            return row

    broker = EnumStringBroker()
    result = execute_exact_plan(
        plan_payload=_plan().to_dict(), broker=broker, env=_env(),
        wal_root=tmp_path / "wal", attempt_id="alpaca-enum-status", dry_run=False,
    )
    assert result.terminal_outcome is TerminalOutcome.RECONCILED_SUCCESS
    assert len(result.orders_filled) == 2
    assert [row["side"] for row in result.orders_filled] == ["SELL", "BUY"]


def test_august_7_alternative_target_cannot_influence_exact_executor(tmp_path: Path):
    alternate_evidence = {
        "approved_target_rows": [{"symbol": "QCOM", "target_weight": 1.0}],
        "alternate_target_artifact": (
            "outputs/paper_lane/plans/live_pilot_plan_2026-08-07.json"
        ),
    }
    payload = _plan().to_dict()
    assert set(alternate_evidence).isdisjoint(payload)
    broker = TrackingPaperBroker()
    result = execute_exact_plan(
        plan_payload=payload, broker=broker, env=_env(), wal_root=tmp_path / "wal", attempt_id="aug7", dry_run=False
    )
    assert result.terminal_outcome is TerminalOutcome.RECONCILED_SUCCESS
    assert {row["symbol"] for row in result.orders_submitted} == {"OLD", "AAPL"}
    assert "QCOM" not in {row["symbol"] for row in result.orders_submitted}


def test_full_batch_asset_failure_blocks_before_any_submission(tmp_path: Path):
    class BadAssetBroker(TrackingPaperBroker):
        def get_asset(self, symbol):
            row = super().get_asset(symbol)
            if symbol == "AAPL":
                row["tradable"] = False
            return row

    broker = BadAssetBroker()
    result = execute_exact_plan(
        plan_payload=_plan().to_dict(), broker=broker, env=_env(), wal_root=tmp_path / "wal", attempt_id="asset-fail", dry_run=False
    )
    assert result.terminal_outcome is TerminalOutcome.SYSTEM_FAILURE
    assert result.status == "BLOCKED"
    assert broker.submit_calls == 0


def test_stale_exact_plan_fails_closed_before_submission(tmp_path: Path):
    broker = TrackingPaperBroker()
    result = execute_exact_plan(
        plan_payload=_plan().to_dict(),
        broker=broker,
        env={**_env(), "CAERUS_EXACT_MAX_PLAN_AGE_SECONDS": "60"},
        wal_root=tmp_path / "wal",
        attempt_id="stale-plan",
        dry_run=False,
    )
    assert result.status == "BLOCKED"
    assert result.reason_code == "stale_or_future_exact_execution_plan"
    assert broker.submit_calls == 0


def test_runtime_dynamic_cap_tightening_blocks_before_submission(tmp_path: Path):
    broker = TrackingPaperBroker()
    result = execute_exact_plan(
        plan_payload=_plan().to_dict(),
        broker=broker,
        env={**_env(), "CAERUS_LIVE_PILOT_CAP_PCT": "0.05"},
        wal_root=tmp_path / "wal",
        attempt_id="runtime-cap-tightened",
        dry_run=False,
    )
    assert result.reason_code == "runtime_dynamic_cap_below_authorized_buy_notional"
    assert result.reconciliation_status == "FAILED_PRE_SUBMIT"
    assert broker.submit_calls == 0


@pytest.mark.parametrize("freshness", ["", "not-a-number", "0"])
def test_missing_or_invalid_freshness_never_executes_old_plan(tmp_path: Path, freshness: str):
    broker = TrackingPaperBroker()
    env = _env()
    if freshness == "":
        env.pop("CAERUS_EXACT_MAX_PLAN_AGE_SECONDS", None)
    else:
        env["CAERUS_EXACT_MAX_PLAN_AGE_SECONDS"] = freshness
    if freshness == "not-a-number":
        with pytest.raises(AuthorityContractError):
            execute_exact_plan(
                plan_payload=_plan().to_dict(), broker=broker, env=env,
                wal_root=tmp_path / "wal", attempt_id="bad-freshness", dry_run=False,
            )
    else:
        result = execute_exact_plan(
            plan_payload=_plan().to_dict(), broker=broker, env=env,
            wal_root=tmp_path / "wal", attempt_id="bad-freshness", dry_run=False,
        )
        assert result.status == "BLOCKED"
    assert broker.submit_calls == 0


def test_post_submit_status_timeout_preserves_submitted_evidence(tmp_path: Path):
    class StatusTimeoutBroker(TrackingPaperBroker):
        def submit_market_order(self, **kwargs):
            row = super().submit_market_order(**kwargs)
            row["status"] = "accepted"
            self.orders[str(row["client_order_id"])]["status"] = "accepted"
            return row

        def get_order(self, order_id):
            raise TimeoutError("broker status read timed out")

    broker = StatusTimeoutBroker()
    result = execute_exact_plan(
        plan_payload=_plan().to_dict(), broker=broker, env=_env(),
        wal_root=tmp_path / "wal", attempt_id="status-timeout", dry_run=False,
    )
    assert result.status == "FAILED_RECONCILIATION"
    assert result.failure_class.value == "BROKER_FAILURE"
    assert len(result.orders_submitted) == 1
    assert broker.submit_calls == 1
    assert "post_submit_broker_status_refresh_failed" in result.reason_code


def test_post_fill_snapshot_timeout_preserves_all_submitted_evidence(tmp_path: Path):
    class FinalSnapshotTimeoutBroker(TrackingPaperBroker):
        def __init__(self):
            super().__init__()
            self.account_reads = 0

        def get_account(self):
            self.account_reads += 1
            if self.account_reads >= 4:
                raise TimeoutError("posttrade account read timed out")
            return super().get_account()

    broker = FinalSnapshotTimeoutBroker()
    result = execute_exact_plan(
        plan_payload=_plan().to_dict(), broker=broker, env=_env(),
        wal_root=tmp_path / "wal", attempt_id="snapshot-timeout", dry_run=False,
    )
    assert result.status == "FAILED_RECONCILIATION"
    assert len(result.orders_submitted) == 2
    assert len(result.orders_filled) == 2
    assert broker.submit_calls == 2
    assert "post_submit_broker_snapshot_failed" in result.reason_code


def test_recovery_blocks_unexplained_external_state_drift_before_new_order(tmp_path: Path):
    broker = TrackingPaperBroker()
    plan = _plan()
    # Durable first sell exists and is filled at broker; the buy has not begun.
    sell = plan.sell_orders[0]
    broker.submit_market_order(
        symbol=sell["symbol"], qty=sell["quantity"], side=sell["side"],
        client_order_id=sell["client_order_id"], tif="day",
        estimated_notional=sell["notional"],
    )
    from core.submission_wal import OrderIntent, prepare_order_intent
    prepare_order_intent(
        tmp_path / "wal",
        OrderIntent(
            trade_date=plan.trade_date, plan_id=plan.plan_id, plan_hash=plan.content_hash,
            attempt_id="prior", order_id=sell["order_id"],
            client_order_id=sell["client_order_id"], symbol=sell["symbol"],
            side=sell["side"], quantity=sell["quantity"], order_type=sell["order_type"],
            created_at="2026-08-12T13:35:01Z", expected_price=sell["expected_price"],
            limit_price=sell["limit_price"], notional=sell["notional"],
            sleeve="caerus_orion", time_in_force="day",
        ),
    )
    broker.cash += 7.0  # unexplained external mutation
    calls = broker.submit_calls
    result = execute_exact_plan(
        plan_payload=plan.to_dict(), broker=broker, env=_env(),
        wal_root=tmp_path / "wal", attempt_id="recovery", dry_run=False,
    )
    assert result.reason_code == "unexplained_broker_state_drift_during_recovery"
    assert broker.submit_calls == calls


def test_mutable_paper_target_without_v3_is_structurally_blocked(tmp_path: Path):
    broker = TrackingPaperBroker()
    env = _env()
    env.pop("CAERUS_REQUIRE_EXACT_EXECUTION_PLAN", None)
    result = run_live_pilot(
        plan={"trade_date": "2026-08-12", "target_portfolio": [{"symbol": "QCOM", "target_weight": 1, "price": 100}]},
        broker=broker, env=env, run_id="aug7-legacy-block", output_root=tmp_path / "paper_lane",
    )
    assert result["reason_code"] == "exact_execution_plan_required"
    assert broker.submit_calls == 0


def test_fully_armed_live_capital_is_structurally_disabled(tmp_path: Path):
    class LiveBroker(TrackingPaperBroker):
        paper = False
        base_url = "https://api.alpaca.markets"

    broker = LiveBroker()
    env = {
        **_env(),
        "MODE": "live_pilot",
        "TRADING_MODE": "live_pilot",
        "ALPACA_PAPER": "0",
        "ALPACA_BASE_URL": broker.base_url,
        "CAERUS_LIVE_PILOT_KILL_SWITCH": "0",
        "CAERUS_LIVE_PILOT_ACCOUNT_ID": "paper-account",
    }
    result = run_live_pilot(
        plan={"trade_date": "2026-08-12", "target_portfolio": [{"symbol": "AAPL", "target_weight": 1, "price": 50}]},
        broker=broker, env=env, run_id="live-owner-policy-block", output_root=tmp_path / "live",
    )
    assert result["reason_code"] == "live_capital_disabled_by_owner_policy"
    assert broker.submit_calls == 0


def test_production_entrypoint_routes_v3_directly_and_writes_canonical_artifacts(tmp_path: Path):
    broker = TrackingPaperBroker()
    env = {**_env(), "CAERUS_REQUIRE_EXACT_EXECUTION_PLAN": "1"}
    exact = _plan()
    result = run_live_pilot(
        plan=_handoff(exact),
        broker=broker,
        env=env,
        run_id="production-entrypoint",
        output_root=tmp_path / "outputs" / "paper_lane",
    )
    run_root = Path(result["run_root"])
    assert result["terminal_status"] == "SUBMITTED"
    assert result["execution_source"] == "exact_execution_plan_v3"
    assert result["canonical_economic_verification_status"] == "RECONCILED"
    assert result["attempt_registry_status"] == "RESOLVED"
    assert json.loads((run_root / "live_pilot_reconciliation.json").read_text())["status"] == "CLEAN"
    assert json.loads((run_root / "audit" / "execution_integrity.json").read_text())["status"] == "OK"
    assert json.loads((run_root / "canonical_economic_verification.json").read_text())["status"] == "RECONCILED"
    assert (tmp_path / "outputs" / "paper_lane" / "execution_attempts" / "2026-08-12" / "selection.json").exists()
    assert json.loads((run_root / "operator_summary.json").read_text()) == json.loads(
        (run_root / "live_pilot_operator_summary.json").read_text()
    )
    payload = json.loads((run_root / "execution_payload.json").read_text())
    assert payload["price_freshness_scope"] == "fresh_broker_state_at_authorization"
    assert payload["execution_status"] == "EXECUTED"
    assert payload["operator_execution_status"] == "reconciled_success"
    assert payload["orders_submitted_count"] == 2
    assert {row["ticker"] for row in payload["trades"]} == {"OLD", "AAPL"}
    timeline = json.loads((run_root / "execution_timeline.json").read_text())
    assert timeline["provenance"]["execution_source"] == "exact_execution_plan_v3"
    state = load_orchestrator_state(
        tmp_path / "outputs" / "paper_lane" / "orchestrator_state",
        trade_date="2026-08-12",
        plan_id=f"{exact.plan_id}:production-entrypoint",
    )
    assert [row.stage for row in state] == [
        "OBSERVE", "RESEARCH", "PRECOMPUTE", "DECIDE", "AUTHORIZE",
        "EXECUTE", "VERIFY", "RECONCILE", "LEARN",
    ]


def test_exact_executor_fails_terminal_verification_for_wrong_equity_basis(
    tmp_path: Path,
):
    from core.whole_share_feasibility import seal_whole_share_proof

    package_hash = "approved-package-hash"
    policy = {
        "schema_version": "caerus.target_attainment_policy.v1",
        "account_scope": "PAPER",
        "share_mode": "WHOLE_SHARES",
        "target_cash_weight": 0.90,
        "minimum_cash_weight": 0.85,
        "fixed_drift_tolerance": 0.02,
        "nearest_feasible_required": True,
        "comparison_epoch_policy": "FIRST_CLEAN_POST_FIX_PAPER_RUN",
        "strict_green_propagation": True,
        "owner_approved_at": "2026-08-11",
    }
    proof = seal_whole_share_proof(
        {
            "schema_version": "caerus.whole_share_feasibility.v1",
            "status": "PASS",
            "approved_execution_package_hash": package_hash,
            "equity_basis": 500.0,
            "allocation": [{"symbol": "AAPL", "target_quantity": 2}],
            "policy": policy,
        }
    )
    exact_payload = _plan().to_dict()
    exact = _rebuild_exact(
        exact_payload,
        risk_state={
            **dict(exact_payload["risk_state"]),
            "trade_meta": {"whole_share_feasibility": proof},
            "decision_nav_reconstruction": {
                "authoritative_account_nav": 1000.0,
                "planning_equity": 1000.0,
                "planning_equity_cap": None,
                "planning_cash": 900.0,
            },
        },
    )
    handoff = _handoff(exact)
    handoff["approved_execution_package"] = {
        "content_hash": package_hash,
        "approved_cash_weight": 0.90,
        "approved_target_rows": [
            {"symbol": "AAPL", "target_weight": 0.10},
        ],
        "constraints": {"target_attainment_policy": policy},
    }
    broker = TrackingPaperBroker()

    result = run_live_pilot(
        plan=handoff,
        broker=broker,
        env={**_env(), "CAERUS_REQUIRE_EXACT_EXECUTION_PLAN": "1"},
        run_id="wrong-equity-basis-terminal-failure",
        output_root=tmp_path / "outputs" / "paper_lane",
    )
    attainment = json.loads(
        (
            Path(result["run_root"])
            / "audit"
            / "execution_target_attainment_2026-08-12.json"
        ).read_text()
    )

    assert broker.submit_calls == 0
    assert result["terminal_status"] in {"BLOCKED", "FAILED_RECONCILIATION"}
    assert result["terminal_outcome"] == "SYSTEM_FAILURE"
    assert "full_account_execution_invariant_failed" in result["reason_code"]
    assert "whole_share_proof_equity_not_authoritative_account_nav" in result[
        "reason_code"
    ]
    assert attainment["whole_share_feasibility_equity_basis"] == 500.0
    assert attainment["expected_execution_equity_basis"] == 1000.0
    assert attainment["whole_share_feasibility_equity_basis_valid"] is False

    recovery_broker = TrackingPaperBroker()
    recovery_wal = tmp_path / "invalid-plan-recovery-wal"
    _seed_legacy_durable_orders(
        plan=exact,
        broker=recovery_broker,
        wal_root=recovery_wal,
        count=1,
    )
    accepted_calls = recovery_broker.submit_calls
    recovered = execute_exact_plan(
        plan_payload=exact.to_dict(),
        broker=recovery_broker,
        env=_execution_env(tmp_path / "invalid-plan-recovery"),
        wal_root=recovery_wal,
        attempt_id="invalid-full-account-proof-recovery",
        dry_run=False,
    )

    assert recovery_broker.submit_calls == accepted_calls
    assert len(recovered.orders_submitted) == 1
    assert recovered.reason_code.startswith(
        "full_account_execution_invariant_failed_after_prior_intent:"
    )


def test_exact_orders_are_blocked_after_market_close_before_submission(tmp_path: Path):
    broker = TrackingPaperBroker()
    exact = _plan()

    result = run_live_pilot(
        plan=_handoff(exact),
        broker=broker,
        env={**_env(), "CAERUS_REQUIRE_EXACT_EXECUTION_PLAN": "1"},
        run_id="exact-after-close-blocked",
        output_root=tmp_path / "outputs" / "paper_lane",
        now_et=dt.datetime(2026, 8, 12, 16, 15),
    )

    run_root = Path(result["run_root"])
    gate = json.loads((run_root / "live_pilot_market_hours_gate.json").read_text())
    assert result["terminal_status"] == "BLOCKED"
    assert result["reason_code"].startswith("exact_execution_market_closed:")
    assert broker.submit_calls == 0
    assert gate["status"] == "BLOCKED"
    assert gate["decision"] == "BLOCK_SUBMISSION"
    assert gate["exact_order_count"] == 2
    assert gate["submission_allowed"] is False


def test_exact_zero_order_verification_is_allowed_after_market_close(tmp_path: Path):
    broker = TrackingPaperBroker()
    exact = _plan(no_trade=True)

    result = run_live_pilot(
        plan=_handoff(exact),
        broker=broker,
        env={**_env(), "CAERUS_REQUIRE_EXACT_EXECUTION_PLAN": "1"},
        run_id="exact-after-close-no-trade",
        output_root=tmp_path / "outputs" / "paper_lane",
        now_et=dt.datetime(2026, 8, 12, 16, 15),
    )

    run_root = Path(result["run_root"])
    gate = json.loads((run_root / "live_pilot_market_hours_gate.json").read_text())
    assert result["terminal_status"] == "AUTHORIZED_NO_TRADE"
    assert result["canonical_economic_verification_status"] == "RECONCILED"
    assert broker.submit_calls == 0
    assert gate["status"] == "BLOCKED"
    assert gate["decision"] == "ALLOW_ZERO_ORDER_VERIFICATION"
    assert gate["exact_order_count"] == 0
    assert gate["zero_order_verification_allowed"] is True


def test_governed_entrypoint_allows_lookup_only_wal_recovery_after_close(
    tmp_path: Path,
):
    broker = TrackingPaperBroker()
    exact = _plan()
    output_root = tmp_path / "outputs" / "paper_lane"
    env = {**_execution_env(tmp_path), "CAERUS_REQUIRE_EXACT_EXECUTION_PLAN": "1"}
    first = run_live_pilot(
        plan=_handoff(exact),
        broker=broker,
        env=env,
        run_id="exact-before-close",
        output_root=output_root,
    )
    calls = broker.submit_calls
    assert first["terminal_status"] == "SUBMITTED"

    recovered = run_live_pilot(
        plan=_handoff(exact),
        broker=broker,
        env=env,
        run_id="exact-after-close-recovery",
        output_root=output_root,
        now_et=dt.datetime(2026, 8, 12, 16, 15),
    )

    submitted = json.loads(
        (Path(recovered["run_root"]) / "live_pilot_orders_submitted.json").read_text()
    )["orders"]
    client_ids = [row["client_order_id"] for row in submitted]
    assert recovered["terminal_status"] == "SUBMITTED"
    assert recovered["canonical_economic_verification_status"] == "RECONCILED"
    assert broker.submit_calls == calls
    assert len(client_ids) == len(set(client_ids)) == len(exact.orders)
    assert all(row["recovered_by_client_order_id"] for row in submitted)


def test_no_trade_attribution_uses_execution_pre_to_post_nav_not_authorization_nav(
    tmp_path: Path,
):
    class MovingMarkNoTradeBroker(TrackingPaperBroker):
        def __init__(self):
            super().__init__()
            self.account_reads = 0
            self.position_reads = 0

        def get_account(self):
            self.account_reads += 1
            # Authorization NAV in the exact plan is $1,000. Execution begins
            # after a $23 market move and ends another $0.20 lower.
            equity = 977.0 if self.account_reads < 3 else 976.8
            return {
                "id": self.account_id,
                "status": "ACTIVE",
                "cash": "900",
                "equity": str(equity),
                "portfolio_value": str(equity),
                "buying_power": "900",
            }

        def get_positions(self):
            self.position_reads += 1
            market_value = 77.0 if self.position_reads < 3 else 76.8
            return [{"symbol": "OLD", "qty": "1", "market_value": str(market_value)}]

    broker = MovingMarkNoTradeBroker()
    exact = _plan(no_trade=True)
    result = run_live_pilot(
        plan=_handoff(exact),
        broker=broker,
        env=_execution_env(tmp_path),
        run_id="moving-mark-no-trade",
        output_root=tmp_path / "paper_lane",
    )
    assert result["terminal_status"] == "AUTHORIZED_NO_TRADE"
    assert result["canonical_economic_verification_status"] == "RECONCILED"
    assert broker.submit_calls == 0
    verification = json.loads(
        (Path(result["run_root"]) / "canonical_economic_verification.json").read_text()
    )
    attribution = verification["sleeve_attribution_reconciliation"]
    assert attribution["portfolio_result"] == pytest.approx(-0.2)
    assert attribution["attributed_result"] == pytest.approx(-0.2)
    assert attribution["attribution_delta"] == pytest.approx(0.0)
    timing = attribution["timing_evidence"]
    assert timing["interval"] == "execution_pre_to_post_broker_nav"
    assert timing["authorization_nav"] == pytest.approx(1000.0)
    assert timing["authorization_to_execution_pre_nav_delta"] == pytest.approx(-23.0)
    assert timing["starting_snapshot_residual"] == pytest.approx(0.0)
    assert timing["ending_snapshot_residual"] == pytest.approx(0.0)


def test_no_trade_fails_when_pre_snapshot_residual_exceeds_its_bps_budget(
    tmp_path: Path,
):
    class ExcessPreSnapshotResidualBroker(TrackingPaperBroker):
        def __init__(self):
            super().__init__()
            self.account_reads = 0

        def get_account(self):
            self.account_reads += 1
            # Pre: cash 900 + position MV 98 differs from equity 1000 by $2.
            # Post: the same economics equal equity 998. Interval attribution
            # delta is only $2 (inside the $2.50 total 25-bps allowance), but
            # pre snapshot residual exceeds its independently allocated $1.25.
            equity = 1000.0 if self.account_reads < 3 else 998.0
            return {
                "id": self.account_id,
                "status": "ACTIVE",
                "cash": "900",
                "equity": str(equity),
                "portfolio_value": str(equity),
                "buying_power": "900",
            }

        def get_positions(self):
            return [{"symbol": "OLD", "qty": "1", "market_value": "98"}]

    broker = ExcessPreSnapshotResidualBroker()
    result = run_live_pilot(
        plan=_handoff(_plan(no_trade=True)),
        broker=broker,
        env=_execution_env(tmp_path),
        run_id="excess-pre-snapshot-residual",
        output_root=tmp_path / "paper_lane",
    )
    assert result["terminal_status"] == "FAILED_RECONCILIATION"
    assert result["canonical_economic_verification_status"] == (
        "FAILED_RECONCILIATION"
    )
    assert broker.submit_calls == 0
    verification = json.loads(
        (Path(result["run_root"]) / "canonical_economic_verification.json").read_text()
    )
    attribution = verification["sleeve_attribution_reconciliation"]
    assert attribution["attribution_delta"] == pytest.approx(2.0)
    assert attribution["tolerance"] == pytest.approx(2.5)
    assert "SLEEVE_SUM_PORTFOLIO_MISMATCH" not in attribution["reason_codes"]
    assert "STARTING_SNAPSHOT_NAV_IDENTITY_MISMATCH" in attribution["reason_codes"]


def test_protective_exact_style_rejects_extended_hours_orders():
    base = _plan().to_dict()
    with pytest.raises(
        AuthorityContractError,
        match="violates protective DAY-limit execution style",
    ):
        _rebuild_exact(
            base,
            sell_orders=[
                {
                    "symbol": "OLD",
                    "side": "SELL",
                    "quantity": 1,
                    "order_type": "limit",
                    "time_in_force": "day",
                    "extended_hours": True,
                    "limit_price": 99,
                    "expected_price": 99,
                    "notional": 99,
                }
            ],
            buy_orders=[
                {
                    "symbol": "AAPL",
                    "side": "BUY",
                    "quantity": 2,
                    "order_type": "limit",
                    "time_in_force": "day",
                    "extended_hours": True,
                    "limit_price": 51,
                    "expected_price": 51,
                    "notional": 102,
                }
            ],
            expected_posttrade_cash=897,
        )


def test_orchestrator_state_prewrite_failure_blocks_before_broker_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    import core.orchestrator_state as orchestrator_state

    monkeypatch.setattr(
        orchestrator_state,
        "append_orchestrator_transition",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk unavailable")),
    )
    broker = TrackingPaperBroker()
    exact = _plan()
    result = run_live_pilot(
        plan=_handoff(exact),
        broker=broker,
        env=_env(),
        run_id="state-prewrite-fail",
        output_root=tmp_path / "paper_lane",
    )
    assert result["terminal_status"] == "BLOCKED"
    assert result["terminal_outcome"] == "SYSTEM_FAILURE"
    assert result["reconciliation_status"] == "FAILED_PRE_SUBMIT"
    assert result["reason_code"].startswith("orchestrator_state_persistence_failed")
    assert broker.submit_calls == 0


def test_wrapper_preserves_submission_unknown_when_economic_truth_is_unavailable(
    tmp_path: Path,
):
    class AmbiguousAcceptedBroker(TrackingPaperBroker):
        def __init__(self):
            super().__init__(crash_after_accept=True)

        def find_order_by_client_id(self, client_id):
            del client_id
            raise TimeoutError("stable client lookup unavailable")

    from core.execution_attempt_registry import read_attempts, select_from_registry

    broker = AmbiguousAcceptedBroker()
    exact = _plan()
    result = run_live_pilot(
        plan=_handoff(exact),
        broker=broker,
        env=_env(),
        run_id="submission-unknown-economic-drift",
        output_root=tmp_path / "paper_lane",
    )
    run_root = Path(result["run_root"])
    assert result["terminal_status"] == "SUBMISSION_UNKNOWN"
    assert result["terminal_outcome"] == "SUBMISSION_UNKNOWN"
    assert result["suppressed_count"] == 2
    execution_results = json.loads((run_root / "execution_results.json").read_text())
    assert execution_results["terminal_outcome"] == "SUBMISSION_UNKNOWN"
    assert execution_results["orders_suppressed_count"] == 2
    assert {
        row["suppression"]["reason_code"]
        for row in execution_results["orders_suppressed"]
    } == {"PRIOR_SUBMISSION_UNKNOWN"}
    reconciliation = json.loads(
        (run_root / "live_pilot_reconciliation.json").read_text()
    )
    assert reconciliation["terminal_outcome"] == "SUBMISSION_UNKNOWN"
    assert reconciliation["suppressed_count"] == 2
    assert len(reconciliation["suppressed_orders"]) == 2
    intended_artifact = json.loads(
        (run_root / "live_pilot_orders_intended.json").read_text()
    )
    assert len(intended_artifact["suppressed_orders"]) == 2
    equality = json.loads((run_root / "equality_gate.json").read_text())
    assert len(equality["orders_suppressed"]) == 2
    registry_root = tmp_path / "paper_lane" / "execution_attempts"
    attempts = read_attempts(registry_root, trade_date="2026-08-12")
    assert attempts[-1].terminal_outcome is TerminalOutcome.SUBMISSION_UNKNOWN
    assert select_from_registry(
        registry_root, trade_date="2026-08-12"
    ).status.value == "BLOCKED_SUBMISSION_UNKNOWN"


def test_dry_run_postvalidation_snapshot_failure_is_not_green(
    tmp_path: Path,
):
    class DryPostSnapshotFailureBroker(TrackingPaperBroker):
        def __init__(self):
            super().__init__()
            self.account_reads = 0

        def get_account(self):
            self.account_reads += 1
            if self.account_reads >= 3:
                raise TimeoutError("dry post-validation snapshot unavailable")
            return super().get_account()

    broker = DryPostSnapshotFailureBroker()
    result = run_live_pilot(
        plan=_handoff(_plan()),
        broker=broker,
        env={**_env(), "CAERUS_LIVE_PILOT_DRY_RUN": "1"},
        run_id="dry-post-snapshot-fail",
        output_root=tmp_path / "paper_lane",
    )
    run_root = Path(result["run_root"])
    assert result["terminal_status"] == "BLOCKED"
    assert result["terminal_outcome"] == "SYSTEM_FAILURE"
    assert json.loads((run_root / "audit" / "execution_integrity.json").read_text())[
        "status"
    ] == "FAIL"


def test_clean_dry_run_reports_validated_no_submission_consistently(
    tmp_path: Path,
):
    result = run_live_pilot(
        plan=_handoff(_plan()),
        broker=TrackingPaperBroker(),
        env={**_env(), "CAERUS_LIVE_PILOT_DRY_RUN": "1"},
        run_id="dry-validation-clean",
        output_root=tmp_path / "paper_lane",
    )
    run_root = Path(result["run_root"])
    equality = json.loads((run_root / "equality_gate.json").read_text())
    integrity = json.loads(
        (run_root / "audit" / "execution_integrity.json").read_text()
    )
    assert result["terminal_status"] == "DRY_RUN"
    assert equality["decision"] == "VALIDATED_NO_SUBMISSION"
    assert integrity["status"] == "OK"


def test_actual_execution_failure_is_hash_chained_as_failed_not_predeclared_pass(
    tmp_path: Path,
):
    class BadAssetBroker(TrackingPaperBroker):
        def get_asset(self, symbol):
            row = super().get_asset(symbol)
            if symbol == "AAPL":
                row["tradable"] = False
            return row

    broker = BadAssetBroker()
    exact = _plan()
    result = run_live_pilot(
        plan=_handoff(exact),
        broker=broker,
        env=_env(),
        run_id="execution-state-fail",
        output_root=tmp_path / "paper_lane",
    )
    state = load_orchestrator_state(
        tmp_path / "paper_lane" / "orchestrator_state",
        trade_date="2026-08-12",
        plan_id=f"{exact.plan_id}:execution-state-fail",
    )
    assert result["terminal_outcome"] == "SYSTEM_FAILURE"
    assert state[-1].stage == "EXECUTE"
    assert state[-1].status == "FAILED"
    assert broker.submit_calls == 0


def test_economic_verification_failure_is_consistent_in_every_terminal_view(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    import core.economic_reconciliation as economics
    from core.execution_attempt_registry import read_attempts

    monkeypatch.setattr(
        economics,
        "write_canonical_economic_verification",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("verification write failed")),
    )
    broker = TrackingPaperBroker()
    exact = _plan()
    result = run_live_pilot(
        plan=_handoff(exact),
        broker=broker,
        env=_env(),
        run_id="economic-fail",
        output_root=tmp_path / "paper_lane",
    )
    run_root = Path(result["run_root"])
    assert result["terminal_status"] == "FAILED_RECONCILIATION"
    assert result["terminal_outcome"] == "SYSTEM_FAILURE"
    assert json.loads((run_root / "execution_results.json").read_text())["terminal_outcome"] == "SYSTEM_FAILURE"
    assert json.loads((run_root / "operator_summary.json").read_text())["terminal_outcome"] == "SYSTEM_FAILURE"
    assert json.loads((run_root / "audit" / "execution_integrity.json").read_text())["status"] == "FAIL"
    assert json.loads((run_root / "live_pilot_reconciliation.json").read_text())["status"] == "FAILED_RECONCILIATION"
    attempts = read_attempts(tmp_path / "paper_lane" / "execution_attempts", trade_date="2026-08-12")
    assert attempts[-1].terminal_outcome is TerminalOutcome.SYSTEM_FAILURE


def test_attempt_registry_failure_cannot_leave_success_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    import core.execution_attempt_registry as registry

    monkeypatch.setattr(
        registry,
        "append_attempt",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("registry write failed")),
    )
    broker = TrackingPaperBroker()
    exact = _plan()
    result = run_live_pilot(
        plan=_handoff(exact),
        broker=broker,
        env=_env(),
        run_id="registry-fail",
        output_root=tmp_path / "paper_lane",
    )
    run_root = Path(result["run_root"])
    assert result["terminal_status"] == "FAILED_RECONCILIATION"
    assert result["terminal_outcome"] == "SYSTEM_FAILURE"
    assert json.loads((run_root / "execution_results.json").read_text())["terminal_outcome"] == "SYSTEM_FAILURE"
    assert json.loads((run_root / "operator_summary.json").read_text())["terminal_outcome"] == "SYSTEM_FAILURE"
    assert json.loads((run_root / "audit" / "execution_integrity.json").read_text())["status"] == "FAIL"


def test_attempt_selection_pointer_failure_cannot_leave_success_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    import core.execution_attempt_registry as registry

    monkeypatch.setattr(
        registry,
        "write_selection_pointer",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("selection write failed")),
    )
    broker = TrackingPaperBroker()
    exact = _plan()
    result = run_live_pilot(
        plan=_handoff(exact),
        broker=broker,
        env=_env(),
        run_id="selection-fail",
        output_root=tmp_path / "paper_lane",
    )
    run_root = Path(result["run_root"])
    assert result["terminal_status"] == "FAILED_RECONCILIATION"
    assert result["terminal_outcome"] == "SYSTEM_FAILURE"
    assert json.loads((run_root / "live_pilot_reconciliation.json").read_text())[
        "status"
    ] == "FAILED_RECONCILIATION"
    from core.execution_attempt_registry import read_attempts, select_from_registry

    attempts = read_attempts(
        tmp_path / "paper_lane" / "execution_attempts", trade_date="2026-08-12"
    )
    assert attempts[-1].terminal_outcome is TerminalOutcome.SYSTEM_FAILURE
    assert select_from_registry(
        tmp_path / "paper_lane" / "execution_attempts", trade_date="2026-08-12"
    ).status.value == "FAILED"
    results = json.loads((run_root / "execution_results.json").read_text())
    assert results["status"] == results["terminal_status"] == "FAILED_RECONCILIATION"


def test_fresh_broker_decision_seals_transition_before_executor(tmp_path: Path):
    broker = TrackingPaperBroker()
    source = tmp_path / "target-plan.json"
    source.write_text("{}\n", encoding="utf-8")
    sleeve_path, sleeve_hash = _write_orion_sleeve_authority(tmp_path)
    # The package mark is lineage from precompute, not an execution-time price.
    # Make it materially different from the broker quote so this test proves the
    # final Decision and allocation use the same fresh, broker-authoritative mark.
    target_rows = [{"symbol": "AAPL", "target_weight": 0.1, "price": 5.0}]
    approved_package, authority_paths = _write_authority_chain(
        tmp_path, target_rows=target_rows, sleeve_hash=sleeve_hash
    )
    regime_state_root = tmp_path / "regime-state"
    authorized = authorize_exact_execution_plan(
        plan={
            "trade_date": "2026-08-12",
            "execution_lane": "paper",
            "approved_sleeve": "caerus_orion",
            "allow_fractional": False,
            "target_portfolio": target_rows,
            "approved_execution_package": approved_package,
            "authority_package_paths": authority_paths,
            "cash_target_weight": 0.0,
            "risk_controls": {"regime": "NORMAL"},
            "source_precompute_payload": "outputs/precompute/2026-08-12/planned_execution_payload.json",
            "source_signals": "outputs/precompute/2026-08-12/signals.json",
            "source_sleeve_evaluations": sleeve_path,
            "source_sleeve_evaluations_sha256": sleeve_hash,
        },
        broker=broker,
        env={**_env(), "CAERUS_LIVE_PILOT_PLANNING_EQUITY_CAP": "1000"},
        run_id="authority-fresh-state",
        plan_path=source,
        created_at="2026-08-12T13:35:01+00:00",
        regime_state_root=regime_state_root,
        drill_epoch="2026-08-12T1230ET",
    )
    authorized = _finalize_direct_authorization(regime_state_root, authorized)
    exact = exact_execution_plan_from_dict(authorized["exact_execution_plan"])
    assert authorized["execution_authority"] == "exact_execution_plan_only"
    assert authorized["precompute_execution_authority"] is False
    assert [row["side"] for row in exact.orders] == ["SELL", "BUY"]
    assert [row["symbol"] for row in exact.orders] == ["OLD", "AAPL"]
    buy = exact.buy_orders[0]
    assert buy["quantity"] == 2.0
    assert buy["expected_price"] == 50.0
    assert buy["order_type"] == "limit"
    assert buy["time_in_force"] == "day"
    assert buy["limit_price"] == 50.5
    assert buy["notional"] == 101.0
    sell = exact.sell_orders[0]
    assert sell["limit_price"] == 99.0
    assert sell["notional"] == 99.0
    assert exact.expected_posttrade_cash == 898.0
    quote_by_symbol = {
        row["symbol"]: row
        for row in exact.market_state["quote_evidence"]["quotes"]
    }
    assert quote_by_symbol["AAPL"]["price"] == 50.0
    assert exact.risk_state["trade_meta"]["broker_authoritative_prices"] is True
    assert exact.constraints["sleeve_attribution_interval"] == (
        "execution_pre_to_post_broker_nav"
    )
    assert exact.constraints["sleeve_attribution_mark_timing_tolerance_bps"] == 25.0
    assert exact.constraints["paper_drill_epoch"] == "2026-08-12T1230ET"
    assert exact.constraints["paper_drill_live_eligible"] is False
    assert exact.constraints["max_adverse_fill_slippage_bps"] == 100.0
    assert exact.constraints["new_order_execution_style"] == (
        "protective_day_limit"
    )


def test_authorizer_sizes_target_from_full_current_broker_account(tmp_path: Path):
    broker = TrackingPaperBroker()
    broker.cash = 1100.0
    source = tmp_path / "full-account-target-plan.json"
    source.write_text("{}\n", encoding="utf-8")
    sleeve_path, sleeve_hash = _write_orion_sleeve_authority(tmp_path)
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
    target_rows = [{"symbol": "AAPL", "target_weight": 0.95, "price": 5.0}]
    approved_package, authority_paths = _write_authority_chain(
        tmp_path,
        target_rows=target_rows,
        sleeve_hash=sleeve_hash,
        constraints={"regime": "NORMAL", "target_attainment_policy": policy},
        target_cash_weight=0.05,
    )
    env = _env()
    env.pop("CAERUS_LIVE_PILOT_CAPITAL_CAP", None)
    env.pop("CAERUS_LIVE_PILOT_PLANNING_EQUITY_CAP", None)
    env["CAERUS_REQUIRE_EXACT_EXECUTION_PLAN"] = "1"
    regime_state_root = tmp_path / "regime-state"

    authorized = authorize_exact_execution_plan(
        plan={
            "trade_date": "2026-08-12",
            "execution_lane": "paper",
            "approved_sleeve": "caerus_orion",
            "allow_fractional": False,
            "target_portfolio": target_rows,
            "approved_execution_package": approved_package,
            "authority_package_paths": authority_paths,
            "cash_target_weight": 0.05,
            "risk_controls": {"regime": "NORMAL"},
            "source_precompute_payload": "precompute.json",
            "source_signals": "signals.json",
            "source_sleeve_evaluations": sleeve_path,
            "source_sleeve_evaluations_sha256": sleeve_hash,
        },
        broker=broker,
        env=env,
        run_id="authority-full-current-account",
        plan_path=source,
        created_at="2026-08-12T13:35:01+00:00",
        regime_state_root=regime_state_root,
    )
    authorized = _finalize_direct_authorization(regime_state_root, authorized)
    exact = exact_execution_plan_from_dict(authorized["exact_execution_plan"])

    assert exact.portfolio_nav == pytest.approx(1200.0)
    assert exact.risk_state["decision_nav_reconstruction"][
        "authoritative_account_nav"
    ] == pytest.approx(1200.0)
    assert exact.buy_orders[0]["symbol"] == "AAPL"
    assert exact.buy_orders[0]["quantity"] == pytest.approx(23.0)
    assert exact.constraints["capital_cap_usd"] == pytest.approx(1200.0)

    result = run_live_pilot(
        plan=authorized,
        broker=broker,
        env=env,
        run_id="execute-full-current-account",
        output_root=tmp_path / "outputs" / "paper_lane",
    )
    run_root = Path(result["run_root"])
    attainment = json.loads(
        (
            run_root
            / "audit"
            / "execution_target_attainment_2026-08-12.json"
        ).read_text()
    )
    assert result["terminal_status"] == "SUBMITTED"
    assert result["execution_target_attainment_status"] in {
        "OK_TARGET_ATTAINED",
        "OK_NEAREST_FEASIBLE",
    }
    assert attainment["expected_execution_equity_basis"] == pytest.approx(1200.0)
    assert attainment["whole_share_feasibility_equity_basis_valid"] is True
    assert broker.positions == [
        {"symbol": "AAPL", "qty": "23.0", "market_value": "1161.5"}
    ]


def test_august_17_full_account_replay_has_no_avoidable_round_trip(
    tmp_path: Path,
):
    class August17Broker(TrackingPaperBroker):
        prices = {
            "INTC": 103.12,
            "LRCX": 335.83,
            "MU": 1006.775,
            "STX": 985.09,
            "WDC": 528.075,
        }

        def __init__(self) -> None:
            super().__init__()
            self.cash = 1855.38
            quantities = {"INTC": 18, "LRCX": 6, "MU": 2, "STX": 2, "WDC": 4}
            self.positions = [
                {
                    "symbol": symbol,
                    "qty": str(quantity),
                    "market_value": str(quantity * self.prices[symbol]),
                }
                for symbol, quantity in quantities.items()
            ]

        def get_latest_trades(self, symbols):
            return {
                str(symbol): {
                    "symbol": str(symbol),
                    "price": str(self.prices[str(symbol)]),
                    "timestamp": "2026-08-12T13:35:00+00:00",
                    "feed": "AUGUST_17_REPLAY",
                }
                for symbol in symbols
            }

    broker = August17Broker()
    target_rows = [
        {"symbol": symbol, "target_weight": 0.19, "price": price}
        for symbol, price in broker.prices.items()
    ]
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
    source = tmp_path / "august-17-replay.json"
    source.write_text("{}\n", encoding="utf-8")
    sleeve_path, sleeve_hash = _write_orion_sleeve_authority(tmp_path)
    approved_package, authority_paths = _write_authority_chain(
        tmp_path,
        target_rows=target_rows,
        sleeve_hash=sleeve_hash,
        constraints={"regime": "NORMAL", "target_attainment_policy": policy},
        target_cash_weight=0.05,
    )
    plan = {
        "trade_date": "2026-08-12",
        "execution_lane": "paper",
        "approved_sleeve": "caerus_orion",
        "allow_fractional": False,
        "target_portfolio": target_rows,
        "approved_execution_package": approved_package,
        "authority_package_paths": authority_paths,
        "cash_target_weight": 0.05,
        "risk_controls": {"regime": "NORMAL"},
        "source_precompute_payload": "precompute.json",
        "source_signals": "signals.json",
        "source_sleeve_evaluations": sleeve_path,
        "source_sleeve_evaluations_sha256": sleeve_hash,
    }
    env = _env()
    env.pop("CAERUS_LIVE_PILOT_CAPITAL_CAP", None)
    env.pop("CAERUS_LIVE_PILOT_PLANNING_EQUITY_CAP", None)

    authorized = authorize_exact_execution_plan(
        plan=plan,
        broker=broker,
        env=env,
        run_id="august-17-full-account-replay",
        plan_path=source,
        created_at="2026-08-12T13:35:01+00:00",
        regime_state_root=tmp_path / "regime-state",
    )
    authorized = _finalize_direct_authorization(
        tmp_path / "regime-state", authorized
    )
    exact = exact_execution_plan_from_dict(authorized["exact_execution_plan"])

    assert exact.portfolio_nav == pytest.approx(11822.55)
    assert not exact.sell_orders
    assert [(row["symbol"], row["quantity"]) for row in exact.buy_orders] == [
        ("INTC", 4.0),
        ("LRCX", 1.0),
        ("WDC", 1.0),
    ]
    assert exact.expected_posttrade_positions == (
        {"quantity": 22.0, "symbol": "INTC"},
        {"quantity": 7.0, "symbol": "LRCX"},
        {"quantity": 2.0, "symbol": "MU"},
        {"quantity": 2.0, "symbol": "STX"},
        {"quantity": 5.0, "symbol": "WDC"},
    )
    assert exact.constraints["full_current_account_invariant"] == "PASS"


@pytest.mark.parametrize(
    "cap_env",
    [
        {"CAERUS_LIVE_PILOT_PLANNING_EQUITY_CAP": "1000"},
        {"CAERUS_LIVE_PILOT_CAPITAL_CAP": "1000"},
        {"CAERUS_LIVE_PILOT_CAP_PCT": "0.85"},
    ],
)
def test_governed_full_account_authorization_rejects_synthetic_caps_before_plan(
    tmp_path: Path,
    cap_env: dict[str, str],
):
    broker = TrackingPaperBroker()
    broker.cash = 1100.0
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
    rows = [{"symbol": "AAPL", "target_weight": 0.95, "price": 50.0}]
    sleeve_path, sleeve_hash = _write_orion_sleeve_authority(tmp_path)
    approved_package, authority_paths = _write_authority_chain(
        tmp_path,
        target_rows=rows,
        sleeve_hash=sleeve_hash,
        constraints={"regime": "NORMAL", "target_attainment_policy": policy},
        target_cash_weight=0.05,
    )
    env = _env()
    env.pop("CAERUS_LIVE_PILOT_CAPITAL_CAP", None)
    env.pop("CAERUS_LIVE_PILOT_PLANNING_EQUITY_CAP", None)
    env.update(cap_env)

    with pytest.raises(RuntimeError, match="governed PAPER target-attainment"):
        authorize_exact_execution_plan(
            plan={
                "trade_date": "2026-08-12",
                "execution_lane": "paper",
                "approved_sleeve": "caerus_orion",
                "allow_fractional": False,
                "target_portfolio": rows,
                "approved_execution_package": approved_package,
                "authority_package_paths": authority_paths,
                "cash_target_weight": 0.05,
                "risk_controls": {"regime": "NORMAL"},
                "source_precompute_payload": "precompute.json",
                "source_signals": "signals.json",
                "source_sleeve_evaluations": sleeve_path,
                "source_sleeve_evaluations_sha256": sleeve_hash,
            },
            broker=broker,
            env=env,
            run_id="synthetic-cap-rejected",
            plan_path=tmp_path / "synthetic-cap.json",
            created_at="2026-08-12T13:35:01+00:00",
            regime_state_root=tmp_path / "regime-state",
        )

    assert broker.submit_calls == 0


def test_fresh_broker_decision_preserves_explicit_zero_weight_exit(
    tmp_path: Path,
):
    broker = TrackingPaperBroker()
    rows = [{"symbol": "OLD", "target_weight": 0.0, "price": 5.0}]
    plan, source, regime_state_root = _governed_authorizer_fixture(
        tmp_path,
        target_rows=rows,
    )

    authorized = authorize_exact_execution_plan(
        plan=plan,
        broker=broker,
        env={**_env(), "CAERUS_LIVE_PILOT_PLANNING_EQUITY_CAP": "1000"},
        run_id="authority-zero-weight-exit",
        plan_path=source,
        created_at="2026-08-12T13:35:01+00:00",
        regime_state_root=regime_state_root,
    )
    authorized = _finalize_direct_authorization(regime_state_root, authorized)
    exact = exact_execution_plan_from_dict(authorized["exact_execution_plan"])

    assert authorized["status"] == "AUTHORIZED_EXACT_PLAN"
    assert len(exact.orders) == 1
    sell = exact.sell_orders[0]
    assert sell["symbol"] == "OLD"
    assert sell["side"] == "SELL"
    assert sell["quantity"] == 1.0
    assert sell["expected_price"] == 100.0
    assert sell["order_type"] == "limit"
    assert sell["time_in_force"] == "day"
    assert sell["limit_price"] == 99.0
    assert sell["notional"] == 99.0
    assert exact.risk_state["trade_meta"]["broker_authoritative_prices"] is True


def test_closed_session_final_bar_can_authorize_natural_no_trade(tmp_path: Path):
    broker = SessionFinalBarPaperBroker()
    rows = [{"symbol": "OLD", "target_weight": 0.1, "price": 5.0}]
    plan, source, regime_state_root = _governed_authorizer_fixture(
        tmp_path,
        target_rows=rows,
    )

    authorized = authorize_exact_execution_plan(
        plan=plan,
        broker=broker,
        env={**_env(), "CAERUS_LIVE_PILOT_PLANNING_EQUITY_CAP": "1000"},
        run_id="closed-session-natural-no-trade",
        plan_path=source,
        created_at="2026-08-12T20:15:00+00:00",
        regime_state_root=regime_state_root,
    )
    authorized = _finalize_direct_authorization(regime_state_root, authorized)
    exact = exact_execution_plan_from_dict(authorized["exact_execution_plan"])
    evidence = exact.market_state["quote_evidence"]

    assert authorized["status"] == "AUTHORIZED_NO_TRADE"
    assert authorized["reason_code"] == "market_closed_authorized_no_trade"
    assert exact.orders == ()
    assert exact.starting_positions == exact.expected_posttrade_positions
    assert exact.starting_cash == exact.expected_posttrade_cash
    assert exact.market_state["pricing_basis"] == (
        "alpaca_iex_regular_session_final_minute_bar_close"
    )
    assert exact.market_state["price_as_of"] == "2026-08-12T16:00:00-04:00"
    assert exact.portfolio_nav == 1000.0
    assert evidence["market_closed_at_authorization"] is True
    assert evidence["new_order_submission_allowed_at_authorization"] is False
    assert evidence["session_reason"] == "AFTER_MARKET_CUTOFF"
    assert evidence["quotes"][0]["bar_start"] == "2026-08-12T15:59:00-04:00"
    assert exact.constraints["market_closed_at_authorization"] is True
    assert exact.constraints["new_order_submission_allowed_at_authorization"] is False
    assert exact.authorization_state["authorization_reason"] == (
        "MARKET_CLOSED_AUTHORIZED_NO_TRADE"
    )
    nav_evidence = exact.risk_state["decision_nav_reconstruction"]
    assert nav_evidence["authoritative_position_value"] == 100.0
    assert nav_evidence["authoritative_account_nav"] == 1000.0
    assert nav_evidence["broker_reported_to_authoritative_nav_delta"] == 0.0
    assert broker.latest_trade_calls == 0
    assert broker.session_calendar_calls == 1
    assert broker.session_final_bar_calls == 1

    result = run_live_pilot(
        plan=authorized,
        broker=broker,
        env={
            **_execution_env(tmp_path),
            "CAERUS_REQUIRE_EXACT_EXECUTION_PLAN": "1",
        },
        run_id="closed-session-natural-no-trade-execution",
        output_root=tmp_path / "outputs" / "paper_lane",
        now_et=dt.datetime(
            2026,
            8,
            12,
            16,
            15,
            tzinfo=ZoneInfo("America/New_York"),
        ),
    )
    run_root = Path(result["run_root"])
    selection = json.loads(
        (
            tmp_path
            / "outputs"
            / "paper_lane"
            / "execution_attempts"
            / "2026-08-12"
            / "selection.json"
        ).read_text()
    )

    assert result["terminal_status"] == "AUTHORIZED_NO_TRADE"
    assert result["canonical_economic_verification_status"] == "RECONCILED"
    assert result["attempt_registry_status"] == "RESOLVED"
    assert selection["status"] == "RESOLVED"
    assert broker.submit_calls == 0
    assert not list((tmp_path / "outputs" / "paper_lane" / "submission_wal").rglob("*.json"))
    assert json.loads(
        (run_root / "audit" / "execution_integrity.json").read_text()
    )["status"] == "OK"


def test_governed_no_trade_attains_proven_whole_share_target(tmp_path: Path):
    from core.whole_share_feasibility import seal_whole_share_proof

    package_hash = "approved-package-hash"
    policy = {
        "schema_version": "caerus.target_attainment_policy.v1",
        "account_scope": "PAPER",
        "share_mode": "WHOLE_SHARES",
        "target_cash_weight": 0.90,
        "minimum_cash_weight": 0.85,
        "fixed_drift_tolerance": 0.02,
        "nearest_feasible_required": True,
        "comparison_epoch_policy": "FIRST_CLEAN_POST_FIX_PAPER_RUN",
        "strict_green_propagation": True,
        "owner_approved_at": "2026-08-11",
    }
    proof = seal_whole_share_proof(
        {
            "schema_version": "caerus.whole_share_feasibility.v1",
            "status": "PASS",
            "approved_execution_package_hash": package_hash,
            "equity_basis": 1000.0,
            "allocation": [{"symbol": "OLD", "target_quantity": 1}],
        }
    )
    exact_payload = _plan(no_trade=True).to_dict()
    exact = _rebuild_exact(
        exact_payload,
        risk_state={
            **dict(exact_payload["risk_state"]),
            "trade_meta": {"whole_share_feasibility": proof},
        },
    )
    handoff = _handoff(exact)
    handoff["target_attainment_policy"] = policy
    handoff["approved_execution_package"] = {
        "content_hash": package_hash,
        "approved_cash_weight": 0.90,
        "approved_target_rows": [
            {"symbol": "OLD", "target_weight": 0.10},
        ],
        "constraints": {"target_attainment_policy": policy},
    }
    broker = TrackingPaperBroker()

    result = run_live_pilot(
        plan=handoff,
        broker=broker,
        env={**_execution_env(tmp_path), "CAERUS_REQUIRE_EXACT_EXECUTION_PLAN": "1"},
        run_id="governed-nearest-feasible-no-trade",
        output_root=tmp_path / "outputs" / "paper_lane",
    )
    run_root = Path(result["run_root"])
    attainment = json.loads(
        (
            run_root
            / "audit"
            / "execution_target_attainment_2026-08-12.json"
        ).read_text()
    )

    assert broker.submit_calls == 0
    assert result["terminal_status"] == "AUTHORIZED_NO_TRADE"
    assert result["terminal_outcome"] == "AUTHORIZED_NO_TRADE"
    assert result["execution_target_attainment_status"] == "OK_NEAREST_FEASIBLE"
    assert attainment["nearest_feasible_verified"] is True
    assert attainment["reconciliation_status"] == "NOT_APPLICABLE_NO_TRADE"
    assert json.loads(
        (run_root / "audit" / "execution_integrity.json").read_text()
    )["status"] == "OK"


def test_authorizer_rejects_unrelated_open_order_before_market_pricing(
    tmp_path: Path,
):
    class OpenOrderDecisionBroker(SessionFinalBarPaperBroker):
        def list_orders(self, status="open", limit=100, **kwargs):
            del limit, kwargs
            if status != "open":
                return []
            return [
                {
                    "id": "external-open-order",
                    "client_order_id": "external-unrelated-order",
                    "symbol": "MSFT",
                    "side": "BUY",
                    "status": "accepted",
                }
            ]

    broker = OpenOrderDecisionBroker()
    rows = [{"symbol": "AAPL", "target_weight": 0.1, "price": 5.0}]
    plan, source, regime_state_root = _governed_authorizer_fixture(
        tmp_path,
        target_rows=rows,
    )

    with pytest.raises(RuntimeError, match="unresolved open orders"):
        authorize_exact_execution_plan(
            plan=plan,
            broker=broker,
            env={**_env(), "CAERUS_LIVE_PILOT_PLANNING_EQUITY_CAP": "1000"},
            run_id="authorizer-unrelated-open-order",
            plan_path=source,
            created_at="2026-08-12T13:35:01+00:00",
            regime_state_root=regime_state_root,
        )

    assert broker.latest_trade_calls == 0
    assert broker.session_final_bar_calls == 0
    assert broker.submit_calls == 0


def test_closed_session_all_cash_book_needs_no_market_mark(tmp_path: Path):
    broker = SessionFinalBarPaperBroker()
    broker.positions = []
    broker.cash = 1000.0
    plan, source, regime_state_root = _governed_authorizer_fixture(
        tmp_path,
        target_rows=[],
    )

    authorized = authorize_exact_execution_plan(
        plan=plan,
        broker=broker,
        env={**_env(), "CAERUS_LIVE_PILOT_PLANNING_EQUITY_CAP": "1000"},
        run_id="closed-session-all-cash",
        plan_path=source,
        created_at="2026-08-12T20:15:00+00:00",
        regime_state_root=regime_state_root,
    )
    authorized = _finalize_direct_authorization(regime_state_root, authorized)
    exact = exact_execution_plan_from_dict(authorized["exact_execution_plan"])
    evidence = exact.market_state["quote_evidence"]

    assert authorized["status"] == "AUTHORIZED_NO_TRADE"
    assert exact.orders == ()
    assert exact.starting_positions == ()
    assert exact.expected_posttrade_positions == ()
    assert exact.starting_cash == exact.expected_posttrade_cash == 1000.0
    assert exact.portfolio_nav == 1000.0
    assert evidence["requested_symbols"] == ()
    assert evidence["returned_symbols"] == ()
    assert evidence["quotes"] == ()
    assert evidence["nav_reconstruction"]["authoritative_position_value"] == 0.0
    assert broker.submit_calls == 0


def test_closed_session_material_drift_is_sealed_but_never_submittable(
    tmp_path: Path,
):
    broker = SessionFinalBarPaperBroker()
    rows = [{"symbol": "AAPL", "target_weight": 0.1, "price": 5.0}]
    plan, source, regime_state_root = _governed_authorizer_fixture(
        tmp_path,
        target_rows=rows,
    )

    authorized = authorize_exact_execution_plan(
        plan=plan,
        broker=broker,
        env={**_env(), "CAERUS_LIVE_PILOT_PLANNING_EQUITY_CAP": "1000"},
        run_id="closed-session-material-drift",
        plan_path=source,
        created_at="2026-08-12T20:15:00+00:00",
        regime_state_root=regime_state_root,
    )
    authorized = _finalize_direct_authorization(regime_state_root, authorized)
    exact = exact_execution_plan_from_dict(authorized["exact_execution_plan"])

    assert authorized["status"] == "AUTHORIZED_EXACT_PLAN"
    assert authorized["reason_code"] == (
        "market_closed_exact_plan_sealed_no_submission_authority"
    )
    assert [(row["symbol"], row["side"]) for row in exact.orders] == [
        ("OLD", "SELL"),
        ("AAPL", "BUY"),
    ]
    assert exact.constraints["new_order_submission_allowed_at_authorization"] is False
    assert exact.market_state["price_as_of"] == "2026-08-12T16:00:00-04:00"
    assert exact.market_state["pricing_basis"] == (
        "alpaca_iex_regular_session_final_minute_bar_close"
    )
    assert exact.portfolio_nav == 1000.0
    assert exact.risk_state["decision_nav_reconstruction"][
        "authoritative_account_nav"
    ] == 1000.0
    assert exact.authorization_state["authorization_reason"] == (
        "MARKET_CLOSED_EXACT_PLAN_SEALED_NO_NEW_ORDER_AUTHORITY"
    )

    outcome = execute_exact_plan(
        plan_payload=exact.to_dict(),
        broker=broker,
        env=_execution_env(tmp_path),
        wal_root=tmp_path / "wal",
        attempt_id="closed-session-material-drift-execution",
        dry_run=False,
        # Prove the immutable closed-session authority blocks submission even
        # independently of the executor's own wall-clock market-hours guard.
        now_et=TEST_NOW_ET,
    )

    assert outcome.status == "BLOCKED"
    assert outcome.reason_code == "exact_plan_new_order_submission_authority_forbidden"
    assert outcome.reconciliation_status == "FAILED_PRE_SUBMIT"
    assert broker.submit_calls == 0
    assert not list((tmp_path / "wal").rglob("*.json"))


@pytest.mark.parametrize("final_bar_mode", ["missing", "wrong_timestamp", "nonfinite"])
def test_closed_session_final_bar_validation_fails_closed(
    tmp_path: Path,
    final_bar_mode: str,
):
    broker = SessionFinalBarPaperBroker(final_bar_mode=final_bar_mode)
    rows = [{"symbol": "AAPL", "target_weight": 0.1, "price": 5.0}]
    plan, source, regime_state_root = _governed_authorizer_fixture(
        tmp_path,
        target_rows=rows,
    )

    with pytest.raises(RuntimeError, match="session-final bar"):
        authorize_exact_execution_plan(
            plan=plan,
            broker=broker,
            env={**_env(), "CAERUS_LIVE_PILOT_PLANNING_EQUITY_CAP": "1000"},
            run_id=f"closed-session-invalid-{final_bar_mode}",
            plan_path=source,
            created_at="2026-08-12T20:15:00+00:00",
            regime_state_root=regime_state_root,
        )

    assert broker.submit_calls == 0
    assert broker.latest_trade_calls == 0
    assert broker.session_calendar_calls == 1
    assert broker.session_final_bar_calls == 1


@pytest.mark.parametrize("calendar_mode", ["empty", "mismatch"])
def test_closed_session_broker_calendar_must_match_governed_session(
    tmp_path: Path,
    calendar_mode: str,
):
    broker = SessionFinalBarPaperBroker(calendar_mode=calendar_mode)
    rows = [{"symbol": "AAPL", "target_weight": 0.1, "price": 5.0}]
    plan, source, regime_state_root = _governed_authorizer_fixture(
        tmp_path,
        target_rows=rows,
    )

    with pytest.raises(RuntimeError, match=r"broker market[- ]calendar"):
        authorize_exact_execution_plan(
            plan=plan,
            broker=broker,
            env={**_env(), "CAERUS_LIVE_PILOT_PLANNING_EQUITY_CAP": "1000"},
            run_id=f"closed-session-calendar-{calendar_mode}",
            plan_path=source,
            created_at="2026-08-12T20:15:00+00:00",
            regime_state_root=regime_state_root,
        )

    assert broker.submit_calls == 0
    assert broker.latest_trade_calls == 0
    assert broker.session_calendar_calls == 1
    assert broker.session_final_bar_calls == 0


def test_immediately_pre_close_stale_latest_trade_cannot_use_final_bar_branch(
    tmp_path: Path,
):
    broker = SessionFinalBarPaperBroker()
    rows = [{"symbol": "AAPL", "target_weight": 0.1, "price": 5.0}]
    plan, source, regime_state_root = _governed_authorizer_fixture(
        tmp_path,
        target_rows=rows,
    )

    with pytest.raises(RuntimeError, match="latest trade"):
        authorize_exact_execution_plan(
            plan=plan,
            broker=broker,
            env={**_env(), "CAERUS_LIVE_PILOT_PLANNING_EQUITY_CAP": "1000"},
            run_id="immediately-pre-close-stale-latest",
            plan_path=source,
            created_at="2026-08-12T19:59:59+00:00",
            regime_state_root=regime_state_root,
        )

    assert broker.latest_trade_calls == 1
    assert broker.session_calendar_calls == 1
    assert broker.session_final_bar_calls == 0
    assert broker.submit_calls == 0


def test_open_session_authorization_cannot_cross_the_official_close(
    tmp_path: Path,
):
    class LastSecondBroker(SessionFinalBarPaperBroker):
        def get_latest_trades(self, symbols):
            self.latest_trade_calls += 1
            return {
                str(symbol): {
                    "symbol": str(symbol),
                    "price": "100" if str(symbol) == "OLD" else "50",
                    "timestamp": "2026-08-12T19:59:58+00:00",
                    "feed": "TEST",
                }
                for symbol in symbols
            }

    broker = LastSecondBroker()
    rows = [{"symbol": "AAPL", "target_weight": 0.1, "price": 5.0}]
    plan, source, regime_state_root = _governed_authorizer_fixture(
        tmp_path,
        target_rows=rows,
    )

    with pytest.raises(RuntimeError, match="market session changed"):
        authorize_exact_execution_plan(
            plan=plan,
            broker=broker,
            env={**_env(), "CAERUS_LIVE_PILOT_PLANNING_EQUITY_CAP": "1000"},
            run_id="authorization-crossed-close",
            plan_path=source,
            created_at="2026-08-12T19:59:59+00:00",
            authorization_completed_at="2026-08-12T20:00:00+00:00",
            regime_state_root=regime_state_root,
        )

    assert broker.latest_trade_calls == 1
    assert broker.session_final_bar_calls == 0
    assert broker.submit_calls == 0


def test_open_session_quote_freshness_is_rechecked_at_authorization_seal(
    tmp_path: Path,
):
    broker = SessionFinalBarPaperBroker()
    rows = [{"symbol": "AAPL", "target_weight": 0.1, "price": 5.0}]
    plan, source, regime_state_root = _governed_authorizer_fixture(
        tmp_path,
        target_rows=rows,
    )

    with pytest.raises(RuntimeError, match="stale before authorization seal"):
        authorize_exact_execution_plan(
            plan=plan,
            broker=broker,
            env={
                **_env(),
                "CAERUS_LIVE_PILOT_PLANNING_EQUITY_CAP": "1000",
                "CAERUS_AUTHORIZATION_QUOTE_MAX_AGE_SECONDS": "120",
            },
            run_id="open-quote-stale-at-seal",
            plan_path=source,
            created_at="2026-08-12T13:35:01+00:00",
            authorization_completed_at="2026-08-12T13:38:01+00:00",
            regime_state_root=regime_state_root,
        )

    assert broker.latest_trade_calls == 1
    assert broker.session_final_bar_calls == 0
    assert broker.submit_calls == 0


@pytest.mark.parametrize(
    ("completed_at", "should_pass"),
    [
        ("2026-08-12T13:37:00+00:00", True),
        ("2026-08-12T13:37:00.001000+00:00", False),
    ],
    ids=["exactly-120-seconds", "120-seconds-plus-1ms"],
)
def test_open_session_quote_seal_freshness_boundary(
    tmp_path: Path,
    completed_at: str,
    should_pass: bool,
):
    broker = SessionFinalBarPaperBroker()
    rows = [{"symbol": "AAPL", "target_weight": 0.1, "price": 5.0}]
    plan, source, regime_state_root = _governed_authorizer_fixture(
        tmp_path,
        target_rows=rows,
    )
    kwargs = {
        "plan": plan,
        "broker": broker,
        "env": {
            **_env(),
            "CAERUS_LIVE_PILOT_PLANNING_EQUITY_CAP": "1000",
            "CAERUS_AUTHORIZATION_QUOTE_MAX_AGE_SECONDS": "120",
        },
        "run_id": f"open-quote-boundary-{should_pass}",
        "plan_path": source,
        "created_at": "2026-08-12T13:35:01+00:00",
        "authorization_completed_at": completed_at,
        "regime_state_root": regime_state_root,
    }

    if not should_pass:
        with pytest.raises(RuntimeError, match="stale before authorization seal"):
            authorize_exact_execution_plan(**kwargs)
        assert broker.submit_calls == 0
        return

    authorized = authorize_exact_execution_plan(**kwargs)
    authorized = _finalize_direct_authorization(regime_state_root, authorized)
    exact = exact_execution_plan_from_dict(authorized["exact_execution_plan"])
    ages = {
        row["age_at_authorization_seal_seconds"]
        for row in exact.market_state["quote_evidence"]["quotes"]
    }
    assert ages == {120.0}
    assert exact.market_state["quote_evidence"][
        "freshness_revalidated_at_seal"
    ] is True
    assert broker.submit_calls == 0


@pytest.mark.parametrize(
    ("execution_time", "should_submit"),
    [
        (dt.datetime(2026, 8, 12, 9, 37, 0, tzinfo=ZoneInfo("America/New_York")), True),
        (
            dt.datetime(
                2026,
                8,
                12,
                9,
                37,
                0,
                1000,
                tzinfo=ZoneInfo("America/New_York"),
            ),
            False,
        ),
    ],
    ids=["executor-exactly-120-seconds", "executor-120-seconds-plus-1ms"],
)
def test_executor_revalidates_open_quote_freshness_before_first_wal(
    tmp_path: Path,
    execution_time: dt.datetime,
    should_submit: bool,
):
    broker = SessionFinalBarPaperBroker()
    rows = [{"symbol": "AAPL", "target_weight": 0.1, "price": 5.0}]
    plan, source, regime_state_root = _governed_authorizer_fixture(
        tmp_path,
        target_rows=rows,
    )
    authorized = authorize_exact_execution_plan(
        plan=plan,
        broker=broker,
        env={
            **_env(),
            "CAERUS_LIVE_PILOT_PLANNING_EQUITY_CAP": "1000",
            "CAERUS_AUTHORIZATION_QUOTE_MAX_AGE_SECONDS": "120",
        },
        run_id=f"executor-freshness-{should_submit}",
        plan_path=source,
        created_at="2026-08-12T13:35:01+00:00",
        authorization_completed_at="2026-08-12T13:35:02+00:00",
        regime_state_root=regime_state_root,
    )
    authorized = _finalize_direct_authorization(regime_state_root, authorized)
    exact = exact_execution_plan_from_dict(authorized["exact_execution_plan"])

    result = execute_exact_plan(
        plan_payload=exact.to_dict(),
        broker=broker,
        env=_execution_env(tmp_path),
        wal_root=tmp_path / "wal",
        attempt_id=f"executor-freshness-{should_submit}",
        dry_run=False,
        now_et=execution_time,
    )

    if should_submit:
        assert result.terminal_outcome is TerminalOutcome.RECONCILED_SUCCESS
        assert broker.submit_calls == len(exact.orders)
        return
    assert result.status == "BLOCKED"
    assert "quote_stale_at_submission_boundary" in result.reason_code
    assert broker.submit_calls == 0
    assert not list((tmp_path / "wal").rglob("*/intents/*.json"))


def test_executor_revalidates_quote_freshness_before_each_new_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    broker = SessionFinalBarPaperBroker()
    rows = [{"symbol": "AAPL", "target_weight": 0.1, "price": 5.0}]
    plan, source, regime_state_root = _governed_authorizer_fixture(
        tmp_path,
        target_rows=rows,
    )
    authorized = authorize_exact_execution_plan(
        plan=plan,
        broker=broker,
        env={
            **_env(),
            "CAERUS_LIVE_PILOT_PLANNING_EQUITY_CAP": "1000",
            "CAERUS_AUTHORIZATION_QUOTE_MAX_AGE_SECONDS": "120",
        },
        run_id="executor-mid-batch-freshness",
        plan_path=source,
        created_at="2026-08-12T13:35:01+00:00",
        authorization_completed_at="2026-08-12T13:35:02+00:00",
        regime_state_root=regime_state_root,
    )
    authorized = _finalize_direct_authorization(regime_state_root, authorized)
    exact = exact_execution_plan_from_dict(authorized["exact_execution_plan"])
    clocks = iter(
        [
            dt.datetime(2026, 8, 12, 9, 35, 2, tzinfo=ZoneInfo("America/New_York")),
            dt.datetime(2026, 8, 12, 9, 35, 2, tzinfo=ZoneInfo("America/New_York")),
            dt.datetime(2026, 8, 12, 9, 38, 0, tzinfo=ZoneInfo("America/New_York")),
        ]
    )
    monkeypatch.setattr(
        "execution.exact_executor._execution_clock",
        lambda _now_et: next(clocks),
    )
    monkeypatch.setattr(
        "execution.exact_executor._exact_market_is_open",
        lambda **_kwargs: True,
    )

    result = execute_exact_plan(
        plan_payload=exact.to_dict(),
        broker=broker,
        env=_execution_env(tmp_path),
        wal_root=tmp_path / "wal",
        attempt_id="executor-mid-batch-freshness",
        dry_run=False,
        now_et=TEST_NOW_ET,
    )

    assert result.status == "FAILED_RECONCILIATION"
    assert "quote_stale_at_submission_boundary" in result.reason_code
    assert broker.submit_calls == 1
    assert len(result.orders_submitted) == len(result.orders_filled) == 1
    assert len(list((tmp_path / "wal").rglob("*/intents/*.json"))) == 1


def test_executor_accepts_fresh_extra_quote_for_whole_share_no_action_target(
    tmp_path: Path,
):
    broker = SessionFinalBarPaperBroker()
    rows = [
        {"symbol": "AAPL", "target_weight": 0.1, "price": 5.0},
        {"symbol": "MSFT", "target_weight": 0.01, "price": 5.0},
    ]
    plan, source, regime_state_root = _governed_authorizer_fixture(
        tmp_path,
        target_rows=rows,
    )
    authorized = authorize_exact_execution_plan(
        plan=plan,
        broker=broker,
        env={**_env(), "CAERUS_LIVE_PILOT_PLANNING_EQUITY_CAP": "1000"},
        run_id="executor-extra-no-action-target",
        plan_path=source,
        created_at="2026-08-12T13:35:01+00:00",
        regime_state_root=regime_state_root,
    )
    authorized = _finalize_direct_authorization(regime_state_root, authorized)
    exact = exact_execution_plan_from_dict(authorized["exact_execution_plan"])
    assert {row["symbol"] for row in exact.market_state["quote_evidence"]["quotes"]} == {
        "AAPL",
        "MSFT",
        "OLD",
    }
    assert "MSFT" not in {row["symbol"] for row in exact.orders}

    result = execute_exact_plan(
        plan_payload=exact.to_dict(),
        broker=broker,
        env=_execution_env(tmp_path),
        wal_root=tmp_path / "wal",
        attempt_id="executor-extra-no-action-target",
        dry_run=False,
        now_et=TEST_NOW_ET,
    )
    assert result.terminal_outcome is TerminalOutcome.RECONCILED_SUCCESS
    assert broker.submit_calls == len(exact.orders)


def test_open_session_rejects_a_trade_timestamped_at_or_after_close(
    tmp_path: Path,
):
    class PostCloseQuoteBroker(SessionFinalBarPaperBroker):
        def get_latest_trades(self, symbols):
            self.latest_trade_calls += 1
            return {
                str(symbol): {
                    "symbol": str(symbol),
                    "price": "100" if str(symbol) == "OLD" else "50",
                    "timestamp": "2026-08-12T20:00:01+00:00",
                    "feed": "TEST",
                }
                for symbol in symbols
            }

    broker = PostCloseQuoteBroker()
    rows = [{"symbol": "AAPL", "target_weight": 0.1, "price": 5.0}]
    plan, source, regime_state_root = _governed_authorizer_fixture(
        tmp_path,
        target_rows=rows,
    )

    with pytest.raises(RuntimeError, match="latest trade"):
        authorize_exact_execution_plan(
            plan=plan,
            broker=broker,
            env={**_env(), "CAERUS_LIVE_PILOT_PLANNING_EQUITY_CAP": "1000"},
            run_id="post-close-quote-rejected",
            plan_path=source,
            created_at="2026-08-12T19:59:59+00:00",
            regime_state_root=regime_state_root,
        )

    assert broker.latest_trade_calls == 1
    assert broker.session_final_bar_calls == 0
    assert broker.submit_calls == 0


@pytest.mark.parametrize(
    ("created_at", "reason"),
    [
        ("2026-08-12T12:00:00+00:00", "BEFORE_MARKET_OPEN"),
        ("2026-08-13T20:15:00+00:00", "RUN_DATE_NOT_TODAY"),
    ],
)
def test_non_session_authorization_cannot_use_closed_final_bar_branch(
    tmp_path: Path,
    created_at: str,
    reason: str,
):
    broker = SessionFinalBarPaperBroker()
    rows = [{"symbol": "AAPL", "target_weight": 0.1, "price": 5.0}]
    plan, source, regime_state_root = _governed_authorizer_fixture(
        tmp_path,
        target_rows=rows,
    )

    with pytest.raises(RuntimeError, match=reason):
        authorize_exact_execution_plan(
            plan=plan,
            broker=broker,
            env={**_env(), "CAERUS_LIVE_PILOT_PLANNING_EQUITY_CAP": "1000"},
            run_id=f"non-session-{reason.lower()}",
            plan_path=source,
            created_at=created_at,
            regime_state_root=regime_state_root,
        )

    assert broker.latest_trade_calls == 0
    assert broker.session_calendar_calls == 0
    assert broker.session_final_bar_calls == 0
    assert broker.submit_calls == 0


def test_authorizer_accepts_real_governed_paper_target_attainment_package(tmp_path: Path):
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
    rows = [{"symbol": "AAPL", "target_weight": 0.95, "price": 50.0}]
    sleeve_path, sleeve_hash = _write_orion_sleeve_authority(tmp_path)
    approved_package, authority_paths = _write_authority_chain(
        tmp_path,
        target_rows=rows,
        sleeve_hash=sleeve_hash,
        constraints={"regime": "NORMAL", "target_attainment_policy": policy},
        target_cash_weight=0.05,
    )
    regime_state_root = tmp_path / "regime-state"
    broker = TrackingPaperBroker()
    execution_env = {
        **_env(),
        "CAERUS_REQUIRE_EXACT_EXECUTION_PLAN": "1",
    }
    authorized = authorize_exact_execution_plan(
        plan={
            "trade_date": "2026-08-12",
            "execution_lane": "paper",
            "approved_sleeve": "caerus_orion",
            "allow_fractional": False,
            "target_portfolio": rows,
            "approved_execution_package": approved_package,
            "authority_package_paths": authority_paths,
            "risk_controls": {"regime": "NORMAL"},
            "source_precompute_payload": "outputs/precompute/2026-08-12/planned_execution_payload.json",
            "source_signals": "outputs/precompute/2026-08-12/signals.json",
            "source_sleeve_evaluations": sleeve_path,
            "source_sleeve_evaluations_sha256": sleeve_hash,
        },
        broker=broker,
        env=execution_env,
        run_id="authority-real-package",
        plan_path=tmp_path / "governed.json",
        created_at="2026-08-12T13:35:01+00:00",
        regime_state_root=regime_state_root,
    )
    authorized = _finalize_direct_authorization(regime_state_root, authorized)
    exact = exact_execution_plan_from_dict(authorized["exact_execution_plan"])
    assert exact.orders
    assert exact.account_scope == "PAPER"
    assert exact.account_id_hash == hashlib.sha256(b"paper-account").hexdigest()

    result = run_live_pilot(
        plan=authorized,
        broker=broker,
        env=execution_env,
        run_id="execute-real-target-attainment-package",
        output_root=tmp_path / "outputs" / "paper_lane",
    )
    run_root = Path(result["run_root"])
    target_attainment = json.loads(
        (
            run_root
            / "audit"
            / "execution_target_attainment_2026-08-12.json"
        ).read_text()
    )
    assert result["terminal_status"] == "SUBMITTED"
    assert result["execution_target_attainment_required"] is True
    assert result["execution_target_attainment_status"] in {
        "OK_TARGET_ATTAINED",
        "OK_NEAREST_FEASIBLE",
    }
    assert target_attainment["required_for_terminal_success"] is True
    assert target_attainment["whole_share_feasibility_equity_basis_valid"] is True
    assert target_attainment["expected_execution_equity_basis"] == pytest.approx(
        exact.portfolio_nav
    )


@pytest.mark.parametrize("quote_mode", ["missing", "stale"])
def test_authorizer_fails_closed_on_incomplete_or_stale_final_market_state(
    tmp_path: Path, quote_mode: str
):
    class BadQuoteBroker(TrackingPaperBroker):
        def get_latest_trades(self, symbols):
            rows = super().get_latest_trades(symbols)
            if quote_mode == "missing":
                rows.pop("AAPL", None)
            else:
                rows["AAPL"]["timestamp"] = "2026-08-12T12:00:00+00:00"
            return rows

    rows = [{"symbol": "AAPL", "target_weight": 0.1, "price": 40.0}]
    sleeve_path, sleeve_hash = _write_orion_sleeve_authority(tmp_path)
    approved_package, authority_paths = _write_authority_chain(
        tmp_path, target_rows=rows, sleeve_hash=sleeve_hash
    )
    with pytest.raises(RuntimeError, match="latest trade"):
        authorize_exact_execution_plan(
            plan={
                "trade_date": "2026-08-12",
                "execution_lane": "paper",
                "approved_sleeve": "caerus_orion",
                "allow_fractional": False,
                "target_portfolio": rows,
                "approved_execution_package": approved_package,
                "authority_package_paths": authority_paths,
                "risk_controls": {"regime": "NORMAL"},
                "source_precompute_payload": "precompute.json",
                "source_signals": "signals.json",
                "source_sleeve_evaluations": sleeve_path,
                "source_sleeve_evaluations_sha256": sleeve_hash,
            },
            broker=BadQuoteBroker(),
            env={**_env(), "CAERUS_LIVE_PILOT_PLANNING_EQUITY_CAP": "1000"},
            run_id=f"bad-quote-{quote_mode}",
            plan_path=tmp_path / "governed.json",
            created_at="2026-08-12T13:35:01+00:00",
        )
