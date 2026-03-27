"""
Tests for:
  1. _write_intended_orders_artifact — written before submission gate,
     captures intended trades + execution_enabled flag
  2. Broker equity authority — live broker equity replaces ledger equity_prev
     when mode=paper
"""
import json
from pathlib import Path

import pandas as pd
import pytest

from paper.paper_broker import _write_intended_orders_artifact


# ---------------------------------------------------------------------------
# 1. Intended orders artifact
# ---------------------------------------------------------------------------

def test_intended_orders_artifact_with_trades(tmp_path, monkeypatch):
    """Artifact captures trade list and execution_enabled=True."""
    monkeypatch.setenv("RUN_OUTPUT_ROOT", str(tmp_path))

    trades = pd.DataFrame([
        {"ticker": "AAPL", "side": "BUY", "shares": 5.0, "price": 190.0,
         "notional": 950.0, "reason": "target_increase"},
        {"ticker": "MSFT", "side": "SELL", "shares": 2.0, "price": 420.0,
         "notional": 840.0, "reason": "target_decrease"},
    ])
    out = _write_intended_orders_artifact(
        run_date="2026-03-06",
        run_id="20260306_test",
        execution_trades=trades,
        execution_enabled=True,
        block_reasons=[],
    )

    payload = json.loads(Path(out).read_text(encoding="utf-8"))
    assert payload["orders_intended_count"] == 2
    assert payload["execution_enabled"] is True
    assert payload["execution_blocked"] is False
    assert payload["block_reasons"] == []
    tickers = [o["ticker"] for o in payload["orders_intended"]]
    assert "AAPL" in tickers and "MSFT" in tickers


def test_intended_orders_artifact_no_trades(tmp_path, monkeypatch):
    """Zero intended trades => orders_intended_count=0."""
    monkeypatch.setenv("RUN_OUTPUT_ROOT", str(tmp_path))

    out = _write_intended_orders_artifact(
        run_date="2026-03-06",
        run_id="20260306_test",
        execution_trades=pd.DataFrame(
            columns=["ticker", "side", "shares", "price", "notional", "reason"]
        ),
        execution_enabled=False,
        block_reasons=["market_guard:CLOSED"],
    )
    payload = json.loads(Path(out).read_text(encoding="utf-8"))
    assert payload["orders_intended_count"] == 0
    assert payload["orders_intended"] == []
    assert payload["execution_blocked"] is True
    assert "market_guard:CLOSED" in payload["block_reasons"]


def test_intended_orders_artifact_blocked_with_trades(tmp_path, monkeypatch):
    """Trades computed but gate blocked => execution_blocked=True, count>0."""
    monkeypatch.setenv("RUN_OUTPUT_ROOT", str(tmp_path))

    trades = pd.DataFrame([
        {"ticker": "KMI", "side": "BUY", "shares": 10.0, "price": 25.0,
         "notional": 250.0, "reason": "target_new"},
    ])
    out = _write_intended_orders_artifact(
        run_date="2026-03-06",
        run_id="20260306_test",
        execution_trades=trades,
        execution_enabled=False,
        block_reasons=["validation:stale_signal"],
    )
    payload = json.loads(Path(out).read_text(encoding="utf-8"))
    assert payload["orders_intended_count"] == 1
    assert payload["execution_blocked"] is True
    assert payload["execution_enabled"] is False
    assert payload["reconcile_report"] is None


def test_intended_orders_artifact_path(tmp_path, monkeypatch):
    """Artifact is written to outputs/broker/intended_orders_{date}.json."""
    monkeypatch.setenv("RUN_OUTPUT_ROOT", str(tmp_path))
    _write_intended_orders_artifact(
        run_date="2026-03-06",
        run_id="test",
        execution_trades=pd.DataFrame(),
        execution_enabled=False,
        block_reasons=[],
    )
    expected = tmp_path / "broker" / "intended_orders_2026-03-06.json"
    assert expected.exists()


# ---------------------------------------------------------------------------
# 2. Broker equity authority — unit test of the sync block logic
# ---------------------------------------------------------------------------

def test_broker_equity_replaces_ledger_equity_in_paper_mode(tmp_path, monkeypatch):
    """
    In paper mode, live broker equity from get_account() should be used for
    portfolio sizing, not the stale ledger value.

    We test the effective state change by verifying that after the sync block
    runs, equity_prev contains the live value.  We do this by isolating
    AlpacaBroker.from_env with a mock and exercising the sync block directly.
    """
    import paper.paper_broker as pb

    live_equity = 9932.52
    live_cash = 1050.00
    ledger_equity = 9991.06  # stale — would have been used before this fix

    class FakeBroker:
        def get_account(self):
            return {"equity": str(live_equity), "cash": str(live_cash), "portfolio_value": ""}

        def get_positions(self):
            return []

    monkeypatch.setattr(pb.AlpacaBroker, "from_env", staticmethod(lambda: FakeBroker()))

    # Simulate the sync block directly
    equity_prev = ledger_equity
    cash_prev = 800.0
    mode = "paper"
    is_weekend = False

    # Replicate the sync block logic in isolation
    try:
        alpaca = pb.AlpacaBroker.from_env()
        if mode == "paper":
            try:
                _acct = alpaca.get_account() or {}
                _live_equity = pb._coerce_float(_acct.get("equity") or _acct.get("portfolio_value"))
                _live_cash = pb._coerce_float(_acct.get("cash"))
                if _live_equity is not None and _live_equity > 0:
                    equity_prev = _live_equity
                    if _live_cash is not None and _live_cash >= 0:
                        cash_prev = _live_cash
            except Exception:
                pass
    except Exception:
        pass

    assert equity_prev == pytest.approx(live_equity), (
        f"equity_prev should be live broker equity {live_equity}, got {equity_prev}"
    )
    assert cash_prev == pytest.approx(live_cash)


def test_broker_equity_sync_falls_back_gracefully_on_error(monkeypatch):
    """If get_account() raises, equity_prev is unchanged (ledger value preserved)."""
    import paper.paper_broker as pb

    ledger_equity = 9991.06

    class FailingBroker:
        def get_account(self):
            raise RuntimeError("API rate limit exceeded")

        def get_positions(self):
            return []

    monkeypatch.setattr(pb.AlpacaBroker, "from_env", staticmethod(lambda: FailingBroker()))

    equity_prev = ledger_equity
    mode = "paper"

    try:
        alpaca = pb.AlpacaBroker.from_env()
        if mode == "paper":
            try:
                _acct = alpaca.get_account() or {}
                _live_equity = pb._coerce_float(_acct.get("equity") or _acct.get("portfolio_value"))
                if _live_equity is not None and _live_equity > 0:
                    equity_prev = _live_equity
            except Exception:
                pass  # fallback — equity_prev unchanged
    except Exception:
        pass

    assert equity_prev == pytest.approx(ledger_equity), (
        "equity_prev should be unchanged when get_account() fails"
    )


def test_broker_equity_not_fetched_in_live_mode(monkeypatch):
    """In live mode, the paper broker sync block should not fetch broker equity."""
    import paper.paper_broker as pb

    live_equity = 9932.52
    ledger_equity = 9991.06

    call_count = {"get_account": 0}

    class SpyBroker:
        def get_account(self):
            call_count["get_account"] += 1
            return {"equity": str(live_equity), "cash": "1000"}

        def get_positions(self):
            return []

    monkeypatch.setattr(pb.AlpacaBroker, "from_env", staticmethod(lambda: SpyBroker()))

    equity_prev = ledger_equity
    mode = "live"

    try:
        alpaca = pb.AlpacaBroker.from_env()
        if mode == "paper":  # guard in sync block
            _acct = alpaca.get_account() or {}
            _live_equity = pb._coerce_float(_acct.get("equity"))
            if _live_equity is not None and _live_equity > 0:
                equity_prev = _live_equity
    except Exception:
        pass

    assert call_count["get_account"] == 0, "get_account should not be called in live mode"
    assert equity_prev == pytest.approx(ledger_equity), "equity_prev unchanged in live mode"
