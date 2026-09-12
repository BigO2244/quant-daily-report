from __future__ import annotations

import datetime as dt
import json
import sys
import types
import copy
from pathlib import Path

import pytest

import core.lyra_live_portfolio as subject
from brokers.alpaca_broker import AlpacaBroker, _LYRA_LIVE_PORTFOLIO_CAPABILITY
from core.lyra_live_execution import _mutation_context, execute_portfolio_plan
from scripts.manage_lyra_live_cron import INIT_LINE, WEEKLY_LINE, render
from scripts.run_lyra_live_portfolio import persist_blocked_attempt


ROOT = Path(__file__).resolve().parents[1]
OWNER = json.loads(
    (ROOT / "docs/governance/decision_records/lyra_live_owner_decision_20260819.json").read_text()
)


def _target(signal: str = "2026-08-24") -> bytes:
    return json.dumps({
        "strategy_name": "Caerus Lyra", "strategy_slug": "caerus_lyra",
        "source_variant": "h1_weekly_h6_top5", "trade_date": signal,
        "effective_trade_date": signal,
        "target_weights": {"AAA": .2, "BBB": .2, "CCC": .2, "DDD": .2, "EEE": .2},
    }, sort_keys=True).encode()


def _args(**changes):
    raw = _target()
    base = dict(
        owner_decision=OWNER, raw_target_source=raw, mode="recurring",
        execution_session="2026-08-25", planned_at="2026-08-25T09:35:00-04:00",
        account_id_hash="a" * 64, equity_usd=460.90, cash_usd=460.90,
        buying_power_usd=460.90, positions=[], open_orders=[],
        assets={symbol: {"status": "active", "tradable": True, "fractionable": True}
                for symbol in ("AAA", "BBB", "CCC", "DDD", "EEE")},
        latest_prices={symbol: 100.0 for symbol in ("AAA", "BBB", "CCC", "DDD", "EEE")},
        deployed_sha="b" * 40,
    )
    base.update(changes)
    if 'broker_snapshot' not in changes:
        snapshot = {'schema_version': 'caerus.lyra_broker_snapshot.v1', 'source': 'ALPACA_LIVE_GET',
            'execution_session': base['execution_session'], 'captured_at': base['planned_at'],
            'capture_started_at': base['planned_at'], 'capture_completed_at': base['planned_at'],
            'account': {'id_hash': base['account_id_hash'], 'equity': base['equity_usd'],
                        'cash': base['cash_usd'], 'buying_power': base['buying_power_usd'], 'status': 'ACTIVE'},
            'positions': copy.deepcopy(base['positions']), 'open_orders': copy.deepcopy(base['open_orders']),
            'latest_trades': {symbol: {'price': price, 'timestamp': base['planned_at']}
                              for symbol, price in base['latest_prices'].items()}}
        snapshot['content_hash'] = subject.content_hash(snapshot)
        base['broker_snapshot'] = snapshot
    return base


@pytest.fixture
def execution_clock(monkeypatch):
    import core.lyra_live_execution as execution
    captured = dt.datetime.fromisoformat('2026-08-25T13:36:01+00:00')
    class Clock(dt.datetime):
        @classmethod
        def now(cls, tz=None): return captured
    monkeypatch.setattr(execution, 'dt', types.SimpleNamespace(datetime=Clock,
        timezone=dt.timezone, timedelta=dt.timedelta, time=dt.time))
    return captured


def test_owner_decision_is_exact_and_hashed():
    assert subject.validate_owner_decision(OWNER)["content_hash"] == OWNER["content_hash"]


def test_recurring_plan_uses_actual_nav_full_fractional_basket():
    plan = subject.build_portfolio_plan(**_args())
    assert plan["status"] == "READY"
    assert len(plan["orders"]) == 5
    assert all(order["side"] == "BUY" and order["quantity"] is None for order in plan["orders"])
    assert [order["notional"] for order in plan["orders"]] == [87.57] * 5
    assert plan["maximum_gross_usd"] == pytest.approx(437.855)
    assert plan["required_cash_reserve_usd"] == pytest.approx(23.045)
    assert plan["total_buy_notional_usd"] == pytest.approx(437.85)


def test_nav_compounds_and_declines_without_nominal_cap():
    larger = subject.build_portfolio_plan(**_args(equity_usd=1000, cash_usd=1000, buying_power_usd=1000))
    smaller = subject.build_portfolio_plan(**_args(equity_usd=300, cash_usd=300, buying_power_usd=300))
    assert larger["total_buy_notional_usd"] == pytest.approx(950)
    assert smaller["total_buy_notional_usd"] == pytest.approx(285)


def test_plan_fails_closed_on_open_order_nonfractionable_or_leverage():
    with pytest.raises(subject.LyraLivePortfolioError, match="open orders"):
        subject.build_portfolio_plan(**_args(open_orders=[{"id": "existing"}]))
    assets = _args()["assets"]
    assets["AAA"] = {"status": "active", "tradable": True, "fractionable": False}
    with pytest.raises(subject.LyraLivePortfolioError, match="fractionable"):
        subject.build_portfolio_plan(**_args(assets=assets))
    with pytest.raises(subject.LyraLivePortfolioError, match="leverage"):
        subject.build_portfolio_plan(**_args(buying_power_usd=921.8))


def test_recurring_rebalance_sells_before_buying_and_stays_long_only():
    plan = subject.build_portfolio_plan(**_args(
        cash_usd=23.045, buying_power_usd=460.90,
        positions=[{"symbol": "ZZZ", "qty": 4.37855}],
        latest_prices={**_args()["latest_prices"], "ZZZ": 100.0},
    ))
    assert plan["orders"][0]["side"] == "SELL"
    assert plan["orders"][0]["quantity"] <= 4.37855
    assert len(plan["orders"]) == 6
    assert plan["total_buy_notional_usd"] <= plan["maximum_buy_notional_usd"]


def test_dry_run_persists_intent_without_broker_write(tmp_path):
    plan = subject.build_portfolio_plan(**_args())
    result = execute_portfolio_plan(
        owner_decision=OWNER, plan=plan, broker=object(), state_root=tmp_path,
        executed_at="2026-08-19T22:00:00+00:00", submit_enabled=False,
    )
    assert result["status"] == "DRY_RUN_READY"
    assert result["broker_write_performed"] is False
    assert (tmp_path / "2026-08-25" / "intent.json").exists()


@pytest.mark.parametrize('corruption', [None, 'nav', 'market_value', 'quantity', 'missing_marks',
                                      'missing_market_value', 'duplicate', 'stale_marks', 'nonfinite_market_value'])
def test_full_fake_fill_path_reconciles_scaled_target(tmp_path, execution_clock, corruption):
    plan = subject.build_portfolio_plan(**_args())

    class Broker:
        def __init__(self):
            self.positions = {}
            self.cash = 460.90

        def find_order_by_client_id(self, client_id):
            return None

        def submit_lyra_live_portfolio_market_order(self, **values):
            notional = float(values["notional"])
            quantity = notional / 100.0
            self.positions[values["symbol"]] = quantity
            self.cash -= notional
            return {
                "id": f"broker-{values['symbol']}",
                "client_order_id": values["client_order_id"],
                "symbol": values["symbol"], "side": "BUY", "status": "filled",
                "qty": str(quantity), "filled_qty": str(quantity),
                "filled_avg_price": "100.0",
            }

        def get_order(self, order_id):
            raise AssertionError("filled market receipt should not need polling")

        def get_account(self):
            equity = '461.90' if corruption == 'nav' and len(self.positions) == 5 else '460.90'
            return {"id_hash": 'a'*64, 'status': 'ACTIVE', "equity": equity, "cash": str(self.cash)}

        def get_positions(self):
            rows = [{"symbol": symbol, "qty": str(quantity), 'market_value': str(quantity*100),
                     'current_price':'100'} for symbol, quantity in self.positions.items()]
            if len(rows) == 5:
                if corruption == 'quantity': rows[0]['qty'] = str(float(rows[0]['qty'])+.001)
                elif corruption == 'market_value': rows[0]['market_value'] = str(float(rows[0]['market_value'])+1)
                elif corruption == 'missing_market_value': rows[0].pop('market_value')
                elif corruption == 'duplicate': rows.append(dict(rows[0]))
                elif corruption == 'nonfinite_market_value': rows[0]['market_value'] = 'nan'
            return rows

        def get_latest_trades(self, symbols):
            stamp = execution_clock-dt.timedelta(seconds=180) if corruption == 'stale_marks' else execution_clock
            rows = {symbol: {"price": 100.0, 'timestamp':stamp.isoformat()} for symbol in symbols}
            if corruption == 'missing_marks': rows.pop(symbols[0])
            return rows

    result = execute_portfolio_plan(
        owner_decision=OWNER, plan=plan, broker=Broker(), state_root=tmp_path,
        executed_at="2026-08-25T09:36:00-04:00", submit_enabled=True,
    )
    assert result["status"] == ('COMPLETE' if corruption is None else 'BLOCKED_RECONCILIATION')
    assert result["broker_write_performed"] is True
    assert len(result["submitted_orders"]) == 5
    assert result["posttrade_reconciliation"]["status"] == ('ALIGNED' if corruption is None else 'NOT_ALIGNED')
    snapshot = json.loads((tmp_path/'2026-08-25/broker_posttrade_snapshot.json').read_text())
    assert snapshot['content_hash'] == subject.content_hash(snapshot)
    assert snapshot['capture_started_at'] == snapshot['capture_completed_at'] == execution_clock.isoformat()
    assert snapshot['fills'] and snapshot['positions'] and snapshot['latest_trades']
    if corruption is None:
        assert abs(result['posttrade_reconciliation']['account_nav_reconciliation']['delta_usd']) <= .01
        assert result['posttrade_reconciliation']['quantity_deltas'] == {}
    else:
        assert result['posttrade_reconciliation']['reasons']


def test_initialization_is_hash_and_symbol_pinned(monkeypatch):
    raw = json.dumps({
        "strategy_slug": "caerus_lyra", "source_variant": subject.LYRA_VARIANT,
        "trade_date": "2026-08-17", "effective_trade_date": "2026-08-17",
        "target_weights": {symbol: .2 for symbol in subject.TARGET_SYMBOLS},
    }, sort_keys=True).encode()
    monkeypatch.setattr(subject, "INITIALIZATION_TARGET_SHA256", __import__("hashlib").sha256(raw).hexdigest())
    target = subject.validate_target_source(raw, mode="initialization", execution_session="2026-08-20")
    assert set(target["weights"]) == subject.TARGET_SYMBOLS


def test_stale_effective_date_has_specific_prebroker_failure():
    payload = json.loads(_target())
    payload["effective_trade_date"] = "2026-08-21"
    with pytest.raises(subject.LyraLivePortfolioError, match="effective date differs"):
        subject.validate_target_source(
            json.dumps(payload).encode(),
            mode="recurring",
            execution_session="2026-08-25",
        )


def test_blocked_attempt_is_immutable_and_hash_bound(tmp_path):
    target = tmp_path / "target.json"
    target.write_bytes(_target())
    decision = tmp_path / "decision.json"
    decision.write_text(json.dumps(OWNER), encoding="utf-8")
    state = tmp_path / "state"
    error = subject.LyraLivePortfolioError("Lyra target effective date differs")
    artifact = persist_blocked_attempt(
        state_root=state,
        execution_session="2026-08-25",
        mode="recurring",
        target_source_path=target,
        owner_decision_path=decision,
        submit=True,
        observed_at="2026-08-25T13:35:00+00:00",
        error=error,
    )
    assert artifact["reason_code"] == "target_effective_date_stale_or_mismatched"
    assert artifact["broker_write_performed"] is False
    assert artifact["broker_write_status"] == "PROVEN_NONE_PREMUTATION"
    path = Path(artifact["artifact_path"])
    assert json.loads(path.read_text()) == {k: v for k, v in artifact.items() if k != "artifact_path"}
    assert persist_blocked_attempt(
        state_root=state,
        execution_session="2026-08-25",
        mode="recurring",
        target_source_path=target,
        owner_decision_path=decision,
        submit=True,
        observed_at="2026-08-25T13:35:00+00:00",
        error=error,
    )["content_hash"] == artifact["content_hash"]


def test_blocked_attempt_does_not_claim_no_write_after_mutation_boundary(tmp_path):
    target = tmp_path / "target.json"
    target.write_bytes(_target())
    decision = tmp_path / "decision.json"
    decision.write_text(json.dumps(OWNER), encoding="utf-8")
    state = tmp_path / "state"
    session = state / "2026-08-25"
    session.mkdir(parents=True)
    (session / "mutation-00.json").write_text("{}", encoding="utf-8")
    artifact = persist_blocked_attempt(
        state_root=state, execution_session="2026-08-25", mode="recurring",
        target_source_path=target, owner_decision_path=decision, submit=True,
        observed_at="2026-08-25T13:35:00+00:00",
        error=RuntimeError("broker timeout"),
    )
    assert artifact["broker_write_performed"] is None
    assert artifact["broker_write_status"] == "UNPROVEN_CHECK_BROKER_BY_CLIENT_ORDER_ID"


def test_live_broker_boundary_accepts_fractional_notional_only_with_capability(monkeypatch):
    enums = types.ModuleType("alpaca.trading.enums")
    enums.OrderSide = types.SimpleNamespace(BUY="buy", SELL="sell")
    enums.TimeInForce = types.SimpleNamespace(DAY="day")
    requests = types.ModuleType("alpaca.trading.requests")

    class MarketOrderRequest:
        def __init__(self, **values):
            self.__dict__.update(values)

    requests.MarketOrderRequest = MarketOrderRequest
    monkeypatch.setitem(sys.modules, "alpaca.trading.enums", enums)
    monkeypatch.setitem(sys.modules, "alpaca.trading.requests", requests)

    class Client:
        order_data = None

        def submit_order(self, *, order_data):
            self.order_data = order_data
            return {
                "id": "broker-1", "client_order_id": order_data.client_order_id,
                "symbol": order_data.symbol, "side": order_data.side,
                "status": "accepted", "notional": order_data.notional,
            }

    plan = subject.build_portfolio_plan(**_args())
    order = plan["orders"][0]
    context = _mutation_context(plan, order)
    client = Client()
    broker = AlpacaBroker(client, paper=False, base_url="https://api.alpaca.markets")
    with pytest.raises(PermissionError, match="capability"):
        broker.submit_lyra_live_portfolio_market_order(
            symbol=order["symbol"], side=order["side"],
            client_order_id=order["client_order_id"], notional=order["notional"],
            mutation_context=context,
        )
    receipt = broker.submit_lyra_live_portfolio_market_order(
        symbol=order["symbol"], side=order["side"],
        client_order_id=order["client_order_id"], notional=order["notional"],
        mutation_context=context,
        _lyra_live_portfolio_capability=_LYRA_LIVE_PORTFOLIO_CAPABILITY,
    )
    assert receipt["client_order_id"] == order["client_order_id"]
    assert float(client.order_data.notional) == order["notional"]
    assert client.order_data.qty is None


def test_cron_contains_one_time_initialization_and_tuesday_cadence():
    installed = render("", install=True)
    assert INIT_LINE in installed
    assert WEEKLY_LINE in installed
    assert render(installed, install=True) == installed
    assert render(installed, install=False) == ""


@pytest.mark.parametrize("nav", [300, 463.28, 517.42, 750, 2000])
def test_governed_current_nav_numeric_proof(nav):
    plan = subject.build_portfolio_plan(**_args(equity_usd=nav, cash_usd=nav,
        buying_power_usd=nav))
    assert plan['capital_policy'] == subject.CAPITAL_POLICY
    assert plan['max_live_capital_usd'] == plan["sizing_basis_usd"] == nav
    assert plan["maximum_gross_usd"] == pytest.approx(nav * .95)
    assert plan["required_cash_reserve_usd"] == pytest.approx(nav * .05)
    assert plan["total_buy_notional_usd"] <= nav * .95
    assert subject.validate_plan(plan, owner_decision=OWNER) == plan


@pytest.mark.parametrize("nav", [None, 0, -1, float("nan"), float("inf"), True])
def test_missing_invalid_broker_nav_fails_closed(nav):
    args = _args()
    args['equity_usd'] = nav
    with pytest.raises(subject.LyraLivePortfolioError):
        subject.build_portfolio_plan(**args)


def test_rehashed_wrong_sizing_basis_rejected():
    plan = subject.build_portfolio_plan(**_args())
    plan["sizing_basis_usd"] = 500
    plan["content_hash"] = subject.content_hash(plan)
    with pytest.raises(subject.LyraLivePortfolioError, match="governed NAV"):
        subject.validate_plan(plan, owner_decision=OWNER)


def test_short_confirmed_cash_stops_before_buy(tmp_path):
    plan = subject.build_portfolio_plan(**_args())
    class Broker:
        def find_order_by_client_id(self, _): return None
        def get_account(self): return {"id_hash": 'a'*64, 'status': 'ACTIVE', "cash": "23", "equity": "460.90"}
        def get_positions(self): return []
        def submit_lyra_live_portfolio_market_order(self, **_):
            raise AssertionError("cash gate must prevent submission")
    with pytest.raises(RuntimeError, match="saved plan broker NAV/cash"):
        execute_portfolio_plan(owner_decision=OWNER, plan=plan, broker=Broker(),
            state_root=tmp_path, executed_at="2026-08-25T13:35:00+00:00", submit_enabled=True)


@pytest.mark.parametrize('nav,cash,quantity', [(700, 23, 6.77), (300, 15, 2.85),
                                             (1000, 600, 4.), (300, 0, 3.)])
def test_gains_losses_deposits_withdrawals_use_whole_live_nav(nav, cash, quantity):
    plan = subject.build_portfolio_plan(**_args(equity_usd=nav, cash_usd=cash,
        buying_power_usd=nav, positions=[{'symbol': 'AAA', 'qty': quantity}]))
    assert plan['max_live_capital_usd'] == plan['sizing_basis_usd'] == nav
    assert plan['maximum_gross_usd'] == pytest.approx(nav*.95)
    assert plan['required_cash_reserve_usd'] == pytest.approx(nav*.05)
    assert plan['projected_gross_usd'] <= nav*.95+.01


def test_fixed_ceiling_parameter_is_not_an_authority():
    with pytest.raises(TypeError, match='max_live_capital_usd'):
        subject.build_portfolio_plan(**_args(), max_live_capital_usd=500)


@pytest.mark.parametrize('old_policy', [None, 'MIN_BROKER_NAV_MAX_LIVE_CAPITAL', subject.CAPITAL_POLICY])
def test_rehashed_old_fixed_cap_plan_rejected(old_policy):
    plan = subject.build_portfolio_plan(**_args(equity_usd=750, cash_usd=750, buying_power_usd=750))
    plan.update(capital_policy=old_policy, max_live_capital_usd=500, sizing_basis_usd=500,
                maximum_gross_usd=475, required_cash_reserve_usd=275)
    plan['content_hash'] = subject.content_hash(plan)
    with pytest.raises(subject.LyraLivePortfolioError, match='capital policy|governed NAV'):
        subject.validate_plan(plan, owner_decision=OWNER)


@pytest.mark.parametrize('defect', ['missing', 'source', 'account', 'capture', 'future', 'old',
    'quote_missing', 'quote_stale', 'snapshot_nav', 'snapshot_boolean', 'positions'])
def test_snapshot_defects_fail_even_if_rehashed(defect):
    args = _args()
    snapshot = args['broker_snapshot']
    if defect == 'missing': args['broker_snapshot'] = None
    elif defect == 'source': snapshot['source'] = 'SHADOW_NAV'
    elif defect == 'account': snapshot['account']['id_hash'] = 'c'*64
    elif defect == 'capture': snapshot['capture_started_at'] = '2026-08-25T09:30:00-04:00'
    elif defect == 'future': snapshot['captured_at'] = snapshot['capture_completed_at'] = '2026-08-25T09:36:00-04:00'
    elif defect == 'old': snapshot['captured_at'] = snapshot['capture_completed_at'] = snapshot['capture_started_at'] = '2026-08-25T09:30:00-04:00'
    elif defect == 'quote_missing': snapshot['latest_trades']['AAA'].pop('timestamp')
    elif defect == 'quote_stale': snapshot['latest_trades']['AAA']['timestamp'] = '2026-08-25T09:30:00-04:00'
    elif defect == 'snapshot_nav': snapshot['account']['equity'] = 500
    elif defect == 'snapshot_boolean': snapshot['account']['equity'] = True
    elif defect == 'positions': snapshot['positions'] = [{'symbol': 'AAA', 'qty': 1}]
    snapshot['content_hash'] = subject.content_hash(snapshot)
    with pytest.raises(subject.LyraLivePortfolioError):
        subject.build_portfolio_plan(**args)


@pytest.mark.parametrize('fresh', [{'id_hash': 'c'*64}, {}, {'status': 'INACTIVE'},
    {'trading_blocked': True}, {'account_blocked': True}, {'equity': True}, {'equity': float('nan')}])
def test_new_submission_rejects_wrong_account_status_or_nav(tmp_path, fresh):
    plan = subject.build_portfolio_plan(**_args())
    class Broker:
        def find_order_by_client_id(self, _): return None
        def get_account(self):
            if not fresh: return {'equity':460.9, 'cash':460.9, 'status':'ACTIVE'}
            return {'id_hash':'a'*64, 'equity':460.9, 'cash':460.9, 'status':'ACTIVE', **fresh}
        def get_positions(self): return []
        def submit_lyra_live_portfolio_market_order(self, **_): raise AssertionError('must not submit')
    with pytest.raises((RuntimeError, subject.LyraLivePortfolioError)):
        execute_portfolio_plan(owner_decision=OWNER, plan=plan, broker=Broker(), state_root=tmp_path,
            executed_at='2026-08-25T09:35:01-04:00', submit_enabled=True)


def test_stale_snapshot_stops_before_new_submission_account_read(tmp_path):
    plan = subject.build_portfolio_plan(**_args())
    class Broker:
        def find_order_by_client_id(self, _): return None
        def get_account(self): raise AssertionError('snapshot must block new submissions first')
    with pytest.raises(subject.LyraLivePortfolioError, match='stale'):
        execute_portfolio_plan(owner_decision=OWNER, plan=plan, broker=Broker(), state_root=tmp_path,
            executed_at='2026-08-25T09:38:00-04:00', submit_enabled=True)


@pytest.mark.parametrize('identity', [None, 'c'*64])
def test_posttrade_reconciliation_rejects_other_account(tmp_path, identity, execution_clock):
    plan = subject.build_portfolio_plan(**_args(equity_usd=500, cash_usd=25, buying_power_usd=500,
        positions=[{'symbol': s, 'qty':.95} for s in ['AAA','BBB','CCC','DDD','EEE']]))
    assert plan['orders'] == []
    class Broker:
        def get_account(self): return {'id_hash':identity,'equity':500,'cash':25}
        def get_positions(self): return []
        def get_latest_trades(self, symbols): return {}
    result = execute_portfolio_plan(owner_decision=OWNER, plan=plan, broker=Broker(), state_root=tmp_path,
        executed_at='2026-08-25T09:35:01-04:00', submit_enabled=True)
    assert result['status'] == 'BLOCKED_RECONCILIATION'
    assert 'posttrade broker account identity' in result['posttrade_reconciliation']['reasons'][0]


@pytest.mark.parametrize('nav', [True, float('nan'), 0])
def test_posttrade_reconciliation_rejects_invalid_nav(tmp_path, nav, execution_clock):
    positions = [{'symbol': s, 'qty':.95} for s in ['AAA','BBB','CCC','DDD','EEE']]
    plan = subject.build_portfolio_plan(**_args(equity_usd=500, cash_usd=25, buying_power_usd=500,
                                               positions=positions))
    class Broker:
        def get_account(self): return {'id_hash':'a'*64,'equity':nav,'cash':25}
        def get_positions(self): return positions
        def get_latest_trades(self, symbols): return {s:{'price':100} for s in symbols}
    result = execute_portfolio_plan(owner_decision=OWNER, plan=plan, broker=Broker(), state_root=tmp_path,
        executed_at='2026-08-25T09:35:01-04:00', submit_enabled=True)
    assert result['status'] == 'BLOCKED_RECONCILIATION'
    assert result['posttrade_reconciliation']['reasons']


@pytest.mark.parametrize('legacy_env', [None, '500', 'not-a-number'])
def test_runtime_ignores_legacy_env_and_preserves_capture_timestamps(tmp_path, monkeypatch, legacy_env):
    import scripts.run_lyra_live_portfolio as runtime
    original_datetime = dt.datetime
    start = original_datetime.fromisoformat('2026-08-25T13:35:00+00:00')
    ticks = iter([start, start+dt.timedelta(seconds=2), start+dt.timedelta(seconds=3)])
    class Clock(original_datetime):
        @classmethod
        def now(cls, tz=None): return next(ticks)
    monkeypatch.setattr(runtime, 'dt', types.SimpleNamespace(datetime=Clock, timezone=dt.timezone))
    monkeypatch.setattr(runtime, '_require_runtime', lambda **kwargs: None)
    monkeypatch.setattr(runtime.subprocess, 'run', lambda *args, **kwargs: types.SimpleNamespace(stdout='b'*40))
    class Broker:
        paper = False
        base_url = 'https://api.alpaca.markets'
        def get_account(self): return {'id_hash':'a'*64,'equity':750.,'cash':750.,'buying_power':750.,'status':'ACTIVE'}
        def get_positions(self): return []
        def list_orders(self, **kwargs): return []
        def get_asset(self, symbol): return {'status':'active','tradable':True,'fractionable':True}
        def get_latest_trades(self, symbols): return {s:{'price':100.,'timestamp':start.isoformat()} for s in symbols}
    monkeypatch.setattr(runtime.AlpacaBroker, 'from_env', lambda: Broker())
    if legacy_env is None: monkeypatch.delenv('MAX_LIVE_CAPITAL', raising=False)
    else: monkeypatch.setenv('MAX_LIVE_CAPITAL', legacy_env)
    target = tmp_path/'target.json'; target.write_bytes(_target())
    owner = tmp_path/'owner.json'; owner.write_text(json.dumps(OWNER))
    result = runtime.run(mode='recurring', execution_session='2026-08-25', target_source_path=target,
        owner_decision_path=owner, state_root=tmp_path/'state', submit=False, now=start)
    plan = result['plan']; snapshot = plan['broker_pretrade_snapshot']
    assert plan['max_live_capital_usd'] == plan['sizing_basis_usd'] == 750
    assert snapshot['capture_started_at'] == start.isoformat()
    assert snapshot['capture_completed_at'] == (start+dt.timedelta(seconds=2)).isoformat()
    assert plan['planned_at'] == (start+dt.timedelta(seconds=3)).isoformat()
    assert snapshot['latest_trades']['AAA']['timestamp'] == start.isoformat()
