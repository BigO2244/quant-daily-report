from __future__ import annotations

import copy
import hashlib
import json
import os
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from authority.contracts import AuthorityContractError
from authority.exact_plan import build_exact_execution_plan, exact_execution_plan_from_dict
from core.failure_semantics import TerminalOutcome
from core.orchestrator_state import load_orchestrator_state
from execution.exact_executor import execute_exact_plan
from scripts.live_pilot_execute import run_live_pilot
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
        sell_orders=[] if no_trade else [{"symbol": "OLD", "side": "SELL", "quantity": 1, "expected_price": 100, "notional": 100}],
        buy_orders=[] if no_trade else [{"symbol": "AAPL", "side": "BUY", "quantity": 2, "expected_price": 50, "notional": 100}],
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


def _rebuild_exact(payload: dict, **overrides):
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
    return build_exact_execution_plan(**values)


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
    assert blocked.reason_code == "plan_claim_conflicts_with_authorized_plan"
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
            if self.account_reads >= 2:
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
            notional=sell["notional"], sleeve="caerus_orion", time_in_force="day",
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


def test_exact_extended_hours_limit_round_trip_is_sealed_and_reconciled(tmp_path: Path):
    broker = TrackingPaperBroker()
    base = _plan().to_dict()
    exact = _rebuild_exact(
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

    result = execute_exact_plan(
        plan_payload=exact.to_dict(),
        broker=broker,
        env=_execution_env(tmp_path),
        wal_root=tmp_path / "wal",
        attempt_id="extended-hours-round-trip",
        dry_run=False,
    )

    assert result.terminal_outcome is TerminalOutcome.RECONCILED_SUCCESS
    assert len(broker.limit_submissions) == 2
    assert all(row["extended_hours"] is True for row in broker.limit_submissions)


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
    target_rows = [{"symbol": "AAPL", "target_weight": 0.1, "price": 50.0}]
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
    assert exact.constraints["paper_drill_epoch"] == "2026-08-12T1230ET"
    assert exact.constraints["paper_drill_live_eligible"] is False


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
    )
    regime_state_root = tmp_path / "regime-state"
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
        broker=TrackingPaperBroker(),
        env={**_env(), "CAERUS_LIVE_PILOT_PLANNING_EQUITY_CAP": "1000"},
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
