"""Offline tests for the broker-truth ledger and its performance report.

No network: exercises the append-only store semantics, date handling, and the
metric/gap math with synthetic fixtures.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
import math
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import broker_ledger_report as rpt  # noqa: E402
import build_broker_truth_ledger as bld  # noqa: E402
import build_tca as tca  # noqa: E402


# ---------------------------------------------------------------------------
# build_broker_truth_ledger
# ---------------------------------------------------------------------------

def test_parse_iso_tolerates_short_fractional_seconds():
    ts = bld.parse_iso("2026-03-05T15:46:28.52469+00:00")
    assert ts.year == 2026 and ts.microsecond == 524690
    ts2 = bld.parse_iso("2026-03-05T15:46:28Z")
    assert ts2.tzinfo is not None


def test_et_trade_date_shifts_post_midnight_utc_to_prior_et_day():
    # 00:30 UTC Saturday == 20:30 ET Friday
    assert bld.et_trade_date("2026-03-28T00:30:00Z") == "2026-03-27"


def test_parse_env_file_handles_export_and_quotes(tmp_path):
    env = tmp_path / "x.env"
    env.write_text(
        "# comment\nexport ALPACA_API_KEY_ID='abc'\nALPACA_API_SECRET_KEY=\"def\"\nALPACA_PAPER=1\n"
    )
    creds = bld.parse_env_file(env)
    assert creds["ALPACA_API_KEY_ID"] == "abc"
    assert creds["ALPACA_API_SECRET_KEY"] == "def"


def test_append_jsonl_dedupe_is_idempotent(tmp_path):
    path = tmp_path / "activities.jsonl"
    rows = [{"id": "a1", "v": 1}, {"id": "a2", "v": 2}]
    bld.append_jsonl(path, rows)
    existing = bld.read_jsonl(path)
    seen = {r["id"] for r in existing}
    new = [r for r in rows if r["id"] not in seen]
    bld.append_jsonl(path, new)
    assert len(bld.read_jsonl(path)) == 2


def test_revisioned_daily_state_is_idempotent_and_preserves_corrections(tmp_path):
    path = tmp_path / "daily_state.jsonl"
    row = {"date": "2026-07-13", "equity": 100.0, "positions": []}
    assert bld.append_revisioned_daily_state(path, [row], "2026-07-14T00:00:00Z") == 1
    assert bld.append_revisioned_daily_state(path, [row], "2026-07-14T01:00:00Z") == 0
    corrected = {**row, "equity": 101.0}
    assert bld.append_revisioned_daily_state(path, [corrected], "2026-07-14T02:00:00Z") == 1
    rows = bld.read_jsonl(path)
    assert [r["revision"] for r in rows] == [1, 2]
    assert [r["equity"] for r in rows] == [100.0, 101.0]


def test_missing_trading_dates_excludes_market_holidays():
    # July 3, 2026 was the observed Independence Day closure.
    have = {"2026-07-02", "2026-07-06"}
    assert bld.missing_trading_dates(
        dt.date(2026, 7, 2), dt.date(2026, 7, 7), have
    ) == []


def test_build_daily_states_reconstructs_positions_market_value_and_cash():
    nav = _nav([("2026-07-01", 1000.0), ("2026-07-02", 1015.0)])
    fills = [
        {"trade_date_et": "2026-07-01", "symbol": "AAPL", "side": "buy", "qty": 2},
        {"trade_date_et": "2026-07-02", "symbol": "AAPL", "side": "sell", "qty": 1},
    ]
    states, flags = bld.build_daily_states(
        nav, fills, {"AAPL": {"2026-07-01": 100.0, "2026-07-02": 105.0}}
    )
    assert flags == []
    assert states[0]["positions"][0]["qty"] == 2
    assert states[0]["cash"] == 800.0
    assert states[1]["positions"][0]["qty"] == 1
    assert states[1]["cash"] == 910.0


def test_build_daily_states_fails_closed_on_missing_mark():
    states, flags = bld.build_daily_states(
        _nav([("2026-07-01", 1000.0)]),
        [{"trade_date_et": "2026-07-01", "symbol": "AAPL", "side": "buy", "qty": 2}],
        {},
    )
    assert states[0]["valuation_complete"] is False
    assert states[0]["cash"] is None
    assert flags == ["DAILY_STATE_UNPRICED date=2026-07-01 symbols=AAPL"]


def test_build_daily_states_applies_option_contract_multiplier():
    symbol = "SPY260417P00680000"
    states, flags = bld.build_daily_states(
        _nav([("2026-04-10", 1000.0)]),
        [{"trade_date_et": "2026-04-10", "symbol": symbol, "side": "buy", "qty": 1}],
        {symbol: {"2026-04-10": 5.52}},
    )
    assert flags == []
    assert states[0]["positions"][0]["multiplier"] == 100.0
    assert states[0]["positions"][0]["market_value"] == 552.0
    assert states[0]["cash"] == 448.0


def test_reconcile_flags_position_mismatch():
    acct = {"equity": "1000", "cash": "500"}
    positions = [{"symbol": "AAPL", "qty": "2", "market_value": "500"}]
    nav_rows = [{"date": "2026-07-13", "equity": "1000"}]
    fills = [
        {"activity_type": "FILL", "symbol": "AAPL", "qty": "3", "side": "buy", "id": "x"}
    ]
    recon = bld.reconcile("test", acct, positions, nav_rows, fills)
    assert not recon["checks"]["positions_vs_fill_history"]["pass"]
    assert recon["checks"]["positions_vs_fill_history"]["mismatches"][0]["symbol"] == "AAPL"


def test_reconcile_passes_when_consistent():
    acct = {"equity": "1000", "last_equity": "1005", "cash": "500"}
    positions = [{"symbol": "AAPL", "qty": "2", "market_value": "500"}]
    nav_rows = [{"date": "2026-07-13", "equity": "1005"}]
    fills = [
        {"activity_type": "FILL", "symbol": "AAPL", "qty": "5", "side": "buy", "id": "x"},
        {"activity_type": "FILL", "symbol": "AAPL", "qty": "3", "side": "sell", "id": "y"},
    ]
    recon = bld.reconcile("test", acct, positions, nav_rows, fills)
    assert recon["pass"], recon


# ---------------------------------------------------------------------------
# broker_ledger_report — metrics
# ---------------------------------------------------------------------------

def _nav(rows):
    return [{"date": d, "equity": str(e)} for d, e in rows]


def test_daily_returns_flow_adjusted():
    nav = _nav([("2026-06-22", 500.0), ("2026-06-23", 505.0), ("2026-06-24", 1010.0)])
    flows = {"2026-06-24": 500.0}  # deposit, not P&L
    rets = rpt.daily_returns(nav, flows)
    assert len(rets) == 2
    assert rets[0]["ret"] == pytest.approx(0.01)
    # (1010 - 500 - 505) / 505
    assert rets[1]["ret"] == pytest.approx(5.0 / 505.0)


def test_daily_returns_skips_prefunding_zero_days():
    nav = _nav([("2026-06-20", 0.0), ("2026-06-22", 500.0), ("2026-06-23", 510.0)])
    rets = rpt.daily_returns(nav, {})
    assert [r["date"] for r in rets] == ["2026-06-23"]


def test_perf_metrics_tie_out_known_series():
    # 2 days: +1%, -1% -> cum = 1.01*0.99 - 1 = -0.0001
    nav = _nav([("2026-07-01", 100.0), ("2026-07-02", 101.0), ("2026-07-06", 99.99)])
    rets = rpt.daily_returns(nav, {})
    m = rpt.perf_metrics(rets, fills=[])
    assert m["cumulative_return"] == pytest.approx(1.01 * 0.99 - 1, abs=1e-9)
    assert m["max_drawdown"] == pytest.approx(0.99 - 1, abs=1e-9)
    # sharpe: mean 0, positive vol
    assert abs(m["sharpe_rf0"]) < 1e-6
    idx = (1 + rets[0]["ret"]) * (1 + rets[1]["ret"])
    years = 2 / 252
    assert m["annualized_return"] == pytest.approx(idx ** (1 / years) - 1, abs=1e-6)


def test_perf_metrics_turnover():
    nav = _nav([("2026-07-01", 100.0), ("2026-07-02", 100.0)])
    rets = rpt.daily_returns(nav, {})
    fills = [{"trade_date_et": "2026-07-02", "notional": "50"}]
    m = rpt.perf_metrics(rets, fills)
    assert m["gross_traded_notional"] == 50.0
    # (50/2)/100 per 1/252 years
    assert m["annualized_turnover_two_sided_over_2"] == pytest.approx(
        (50 / 2) / 100 / (1 / 252), rel=1e-6
    )


# ---------------------------------------------------------------------------
# broker_ledger_report — shadow gap
# ---------------------------------------------------------------------------

def test_shadow_vs_realized_identical_series_zero_gap():
    nav = _nav([("2026-07-01", 100.0), ("2026-07-02", 102.0), ("2026-07-06", 101.0)])
    rets = rpt.daily_returns(nav, {})
    shadow = {"2026-07-02": 1.02, "2026-07-06": 1.01}
    gap = rpt.shadow_vs_realized(rets, shadow)
    assert gap["cum_gap_realized_minus_shadow"] == pytest.approx(0.0, abs=1e-9)
    assert gap["overlap_days"] == 1


def test_shadow_vs_realized_bridges_missing_dates_consistently():
    # Shadow misses 07-03; sampled at common dates, both series bridge
    # 07-02 -> 07-06 in one step, so identical endpoints give zero gap.
    nav = _nav(
        [
            ("2026-07-01", 100.0),
            ("2026-07-02", 102.0),
            ("2026-07-03", 103.0),
            ("2026-07-06", 101.0),
        ]
    )
    rets = rpt.daily_returns(nav, {})
    shadow = {"2026-07-02": 1.02, "2026-07-06": 1.01}
    gap = rpt.shadow_vs_realized(rets, shadow)
    assert gap["overlap_days"] == 1
    # reported values are rounded to 6 dp
    assert gap["realized_cum_return"] == pytest.approx(101.0 / 102.0 - 1, abs=1e-6)
    assert gap["shadow_cum_return"] == pytest.approx(1.01 / 1.02 - 1, abs=1e-6)
    assert gap["cum_gap_realized_minus_shadow"] == pytest.approx(0.0, abs=1e-6)


def test_shadow_vs_realized_gap_sign():
    # Realized underperforms shadow -> negative gap
    nav = _nav([("2026-07-01", 100.0), ("2026-07-02", 100.0), ("2026-07-06", 100.0)])
    rets = rpt.daily_returns(nav, {})
    shadow = {"2026-07-02": 1.0, "2026-07-06": 1.05}
    gap = rpt.shadow_vs_realized(rets, shadow)
    assert gap["cum_gap_realized_minus_shadow"] == pytest.approx(-0.05, abs=1e-9)


def test_shadow_vs_realized_insufficient_overlap():
    nav = _nav([("2026-07-01", 100.0), ("2026-07-02", 101.0)])
    rets = rpt.daily_returns(nav, {})
    assert "error" in rpt.shadow_vs_realized(rets, {"2026-07-02": 1.0})


# ---------------------------------------------------------------------------
# flows extraction
# ---------------------------------------------------------------------------

def test_flows_by_date_only_external_types():
    acts = [
        {"activity_type": "CSD", "date": "2026-06-22", "net_amount": "500"},
        {"activity_type": "FEE", "date": "2026-06-26", "net_amount": "-0.01"},
        {"activity_type": "DIV", "date": "2026-06-27", "net_amount": "1.20"},
        {"activity_type": "FILL", "transaction_time": "2026-06-26T14:00:00Z", "qty": "1"},
    ]
    flows = rpt.flows_by_date(acts)
    assert flows == {"2026-06-22": 500.0}


# ---------------------------------------------------------------------------
# build_tca — arrival and adversarial reconciliation
# ---------------------------------------------------------------------------

def test_quote_mid_uses_latest_quote_at_or_before_submission():
    sub = bld.parse_iso("2026-07-13T13:35:30Z")
    quotes = [
        {"t": "2026-07-13T13:35:20Z", "bp": 99.0, "ap": 101.0},
        {"t": "2026-07-13T13:35:31Z", "bp": 100.0, "ap": 102.0},  # future
    ]
    mid, ts = tca.quote_mid_at_or_before(quotes, sub)
    assert mid == 100.0
    assert ts == bld.parse_iso("2026-07-13T13:35:20Z")


def test_quote_mid_rejects_stale_or_crossed_quotes():
    sub = bld.parse_iso("2026-07-13T13:35:30Z")
    assert tca.quote_mid_at_or_before(
        [{"t": "2026-07-13T13:20:00Z", "bp": 99.0, "ap": 101.0}], sub
    ) == (None, None)
    assert tca.quote_mid_at_or_before(
        [{"t": "2026-07-13T13:35:20Z", "bp": 102.0, "ap": 101.0}], sub
    ) == (None, None)


def test_decision_price_prefers_generating_run_over_trade_date_rerun():
    intended = {
        "by_run": {
            "2026-03-10:main:growth_engine_v4": {
                "prices": {("AAPL", "buy"): 100.0}
            }
        },
        "by_date": {
            "2026-03-11": {"prices": {("AAPL", "buy"): 105.0}}
        },
    }
    order = {
        "symbol": "AAPL",
        "side": "buy",
        "client_order_id": "2026-03-10:main:growth_engine_v4_AAPL_BUY_2.0",
    }
    assert tca.decision_price_for_order(order, "2026-03-11", intended) == (
        100.0,
        "intended_orders_run_id_exact_side",
    )


def test_tca_forced_residual_does_not_count_as_validated_reconciliation():
    row = {b: 0.0 for b in tca.BUCKETS}
    row.update({"date": "2026-07-13", "gap": -0.01, "r_real": 0.0, "r_intended_series": 0.01})
    row["residual"] = -0.01
    summary = tca.summarize(
        "paper", [row], [], {"overlap": ("2026-07-12", "2026-07-13"), "days": 1, "flags": []}, "fixture.csv"
    )
    assert summary["reconciles"] is True
    assert summary["attribution_validated"] is False
    assert summary["validation"]["residual_share_of_absolute_attribution"] == 1.0
