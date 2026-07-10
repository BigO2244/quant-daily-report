"""Targeted coverage for the research-only execution-timing replay engine.

The tests exercise the no-look-ahead contract, the BUY/SELL signed-cost
math, the deterministic on-disk output, missing-bar handling, and the
``no_cache`` skip path. They never read the real precompute or cache
directories — every fixture is built into ``tmp_path``.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from core.research import timing_fill_model as fm
from scripts.research import execution_timing_replay as etr
from scripts.research.intraday_research_cache import CACHE_KEY_VERSION, cache_path_for


ET = ZoneInfo("America/New_York")
TRADE_DATE = "2026-03-24"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_bars(symbol: str, trade_date: str, open_prices: dict[int, float]) -> pd.DataFrame:
    """Build a minute-bar DataFrame matching the cache parquet schema.

    ``open_prices`` maps minute-after-09:30 (int) → open price.
    """
    day = dt.date.fromisoformat(trade_date)
    base = dt.datetime.combine(day, dt.time(9, 30), tzinfo=ET)
    rows = []
    for minute_offset, open_px in sorted(open_prices.items()):
        ts = (base + dt.timedelta(minutes=minute_offset)).astimezone(dt.timezone.utc)
        rows.append(
            {
                "symbol": symbol,
                "trade_date": trade_date,
                "bar_start_ts": ts.isoformat().replace("+00:00", "Z"),
                "open": open_px,
                "high": open_px + 0.5,
                "low": open_px - 0.5,
                "close": open_px + 0.1,
                "volume": 1000.0,
                "trade_count": 10.0,
                "vwap": open_px,
                "source": "alpaca:1Min",
                "feed": "iex",
                "retrieved_at": "2026-05-29T14:00:00Z",
            }
        )
    return pd.DataFrame(rows, columns=[
        "symbol", "trade_date", "bar_start_ts", "open", "high", "low",
        "close", "volume", "trade_count", "vwap", "source", "feed", "retrieved_at",
    ])


def _write_plan(
    plan_root: Path,
    trade_date: str,
    trades: list[dict],
    *,
    planned_for: str | None = None,
) -> Path:
    plan_dir = plan_root / trade_date
    plan_dir.mkdir(parents=True, exist_ok=True)
    plan_path = plan_dir / "planned_execution_payload.json"
    payload: dict = {
        "trade_date": trade_date,
        "plan_only": True,
        "pricing_source": "PREV_CLOSE",
        "trades": trades,
    }
    if planned_for is not None:
        payload["planned_for"] = planned_for
    plan_path.write_text(json.dumps(payload), encoding="utf-8")
    return plan_path


def _write_cache_bars(cache_root: Path, symbol: str, trade_date: str, bars: pd.DataFrame) -> Path:
    path = cache_path_for(symbol, trade_date, cache_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    bars.to_parquet(path, index=False)
    return path


# ---------------------------------------------------------------------------
# Unit tests — fill model
# ---------------------------------------------------------------------------


def test_pick_reference_bar_picks_first_bar_at_or_after_sim_ts():
    bars = _fake_bars("AAPL", TRADE_DATE, {0: 100.0, 1: 101.0, 2: 102.0, 3: 103.0})
    sim = fm.simulated_execution_ts(TRADE_DATE, 1)  # 09:31 ET
    bar = fm.pick_reference_bar(bars, sim)
    assert bar is not None
    # 09:31 ET = 13:31 UTC on the day; the picked bar must start at-or-after.
    assert bar["bar_start_ts"] == "2026-03-24T13:31:00Z"
    assert bar["open"] == 101.0


def test_pick_reference_bar_returns_none_when_no_bar_in_window():
    bars = _fake_bars("AAPL", TRADE_DATE, {0: 100.0, 1: 101.0})  # only 09:30 and 09:31
    sim = fm.simulated_execution_ts(TRADE_DATE, 10)  # 09:40 — no bar that late
    assert fm.pick_reference_bar(bars, sim) is None


def test_no_look_ahead_invariant_on_every_offset():
    bars = _fake_bars("AAPL", TRADE_DATE, {0: 100.0, 1: 101.0, 2: 102.0, 3: 103.0, 4: 104.0, 5: 105.0, 10: 110.0})
    records = fm.replay_trade(side="BUY", shares=1, bars=bars, trade_date=TRADE_DATE)
    for rec in records.values():
        if rec.status != "ok":
            continue
        # core invariant: chosen bar starts at or after the as-of cutoff.
        assert rec.bar_start_ts is not None
        assert rec.bar_start_ts >= rec.asof_cutoff_ts, (
            f"look-ahead at offset {rec.offset_label}: "
            f"bar_start_ts={rec.bar_start_ts} < asof_cutoff_ts={rec.asof_cutoff_ts}"
        )


def test_signed_cost_math_for_buy_and_sell():
    """At 09:30 the open is 100; at 09:35 the open is 105. With one BUY 10 and
    one SELL 10 share-equal trade, the day-cost math is:

      cost(T+0) = (+10 * 100) + (-10 * 100) = 0
      cost(T+5) = (+10 * 105) + (-10 * 105) = 0
      opportunity(T+0 vs T+5) = 0 - 0 = 0

    But if the BUY and SELL are different tickers (one rallies, one stays),
    the math should reflect that. Use two symbols to make it interesting.
    """
    bars_a = _fake_bars("A", TRADE_DATE, {0: 100.0, 5: 110.0})  # rallies
    bars_b = _fake_bars("B", TRADE_DATE, {0: 200.0, 5: 200.0})  # flat
    trades = [
        {"ticker": "A", "side": "BUY", "shares": 10, "prev_close_ref": None, "reason": None},
        {"ticker": "B", "side": "SELL", "shares": 10, "prev_close_ref": None, "reason": None},
    ]
    fills = [
        fm.replay_trade(side="BUY", shares=10, bars=bars_a, trade_date=TRADE_DATE, offsets_minutes=(0, 5)),
        fm.replay_trade(side="SELL", shares=10, bars=bars_b, trade_date=TRADE_DATE, offsets_minutes=(0, 5)),
    ]
    day_costs = fm.compute_day_costs(trades=trades, fills_by_trade=fills, offsets_minutes=(0, 5))

    # cost(T+0) = +10*100 + (-10)*200 = -1000
    # cost(T+5) = +10*110 + (-10)*200 = -900
    assert day_costs["T+0m"]["cost_usd"] == pytest.approx(-1000.0)
    assert day_costs["T+5m"]["cost_usd"] == pytest.approx(-900.0)

    # gross notional = |10*100| + |-10*200| = 3000 (at T+0)
    assert day_costs["T+0m"]["gross_notional_usd"] == pytest.approx(3000.0)

    opps = fm.compute_opportunity_vs_baseline(day_costs=day_costs, baseline_offset_minutes=5)
    # opp(T+0) = cost(T+5) - cost(T+0) = -900 - (-1000) = +100
    # >0 means T+0 was *cheaper* (less cash out) than T+5 — true here, because the BUY
    # got a $10/share better entry at T+0.
    assert opps["T+0m"]["opportunity_usd"] == pytest.approx(100.0)
    # bps = 100 / 3000 * 10000 ≈ 333.333
    assert opps["T+0m"]["opportunity_bps"] == pytest.approx(333.333333, abs=1e-3)
    # baseline-vs-baseline opportunity is zero by construction.
    assert opps["T+5m"]["opportunity_usd"] == pytest.approx(0.0)


def test_signed_cost_drops_offset_with_unequal_trade_subset():
    """If the trade subset that fills at offset Δ differs from the subset that
    fills at the baseline, the opportunity for that offset must be ``None`` so
    we never silently compare apples to oranges.
    """
    bars_a = _fake_bars("A", TRADE_DATE, {0: 100.0, 1: 101.0, 2: 102.0, 3: 103.0, 4: 104.0, 5: 105.0, 10: 110.0})
    # B has bars only for the first few minutes — missing T+5 and T+10
    # (no bar at-or-after 09:35 ET in B's cache).
    bars_b = _fake_bars("B", TRADE_DATE, {0: 200.0, 1: 200.0, 2: 200.0})
    trades = [
        {"ticker": "A", "side": "BUY", "shares": 1, "prev_close_ref": None, "reason": None},
        {"ticker": "B", "side": "BUY", "shares": 1, "prev_close_ref": None, "reason": None},
    ]
    fills = [
        fm.replay_trade(side="BUY", shares=1, bars=bars_a, trade_date=TRADE_DATE),
        fm.replay_trade(side="BUY", shares=1, bars=bars_b, trade_date=TRADE_DATE),
    ]
    day_costs = fm.compute_day_costs(trades=trades, fills_by_trade=fills)
    # At T+0 both A and B have bars (fillable=2); at T+5 only A (fillable=1).
    assert day_costs["T+0m"]["fillable_trades"] == 2
    assert day_costs["T+5m"]["fillable_trades"] == 1

    opps = fm.compute_opportunity_vs_baseline(day_costs=day_costs)
    # Mismatched subset (2 vs 1) → opportunity at T+0 must be None with a reason
    # so we don't silently compare a 2-trade total against a 1-trade baseline.
    assert opps["T+0m"]["opportunity_usd"] is None
    assert opps["T+0m"]["reason"] == "trade_subset_mismatch_or_unavailable"


# ---------------------------------------------------------------------------
# End-to-end: run_replay
# ---------------------------------------------------------------------------


def _set_up_fixture(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    plan_root = tmp_path / "outputs" / "precompute"
    cache_root = tmp_path / "data" / "research_cache" / "intraday"
    output_root = tmp_path / "outputs" / "research" / "execution_timing"
    report_root = tmp_path / "reports" / "execution_timing"

    _write_plan(plan_root, TRADE_DATE, [
        {"ticker": "A", "side": "BUY", "shares": 10, "entry_price": 99.0, "reason": "rebalance"},
        {"ticker": "B", "side": "SELL", "shares": 10, "entry_price": 200.0, "reason": "exit"},
    ])
    _write_cache_bars(
        cache_root, "A", TRADE_DATE,
        _fake_bars("A", TRADE_DATE, {0: 100.0, 1: 101.0, 2: 102.0, 3: 103.0, 4: 104.0, 5: 105.0, 10: 110.0}),
    )
    _write_cache_bars(
        cache_root, "B", TRADE_DATE,
        _fake_bars("B", TRADE_DATE, {0: 200.0, 1: 200.0, 2: 200.0, 3: 200.0, 4: 200.0, 5: 200.0, 10: 200.0}),
    )
    return plan_root, cache_root, output_root, report_root


def test_run_replay_writes_artifacts_and_passes_no_look_ahead_audit(tmp_path):
    plan_root, cache_root, output_root, report_root = _set_up_fixture(tmp_path)

    result = etr.run_replay(
        run_date="2026-05-29",
        plan_root=plan_root,
        cache_root=cache_root,
        output_root=output_root,
        report_root=report_root,
        now=dt.datetime(2026, 5, 29, 14, 15, tzinfo=dt.timezone.utc),
    )
    assert result.overall_status == "OK"
    assert result.days_with_full_coverage == 1
    assert result.per_trade_path.exists()
    assert result.summary_path.exists()
    assert result.report_path.exists()

    per_trade = json.loads(result.per_trade_path.read_text())
    summary = json.loads(result.summary_path.read_text())

    assert per_trade["schema_version"] == "1.0"
    assert per_trade["baseline_offset"] == "T+5m"
    assert summary["baseline_offset"] == "T+5m"
    assert summary["coverage_summary"]["days_replayed"] == 1
    assert summary["coverage_summary"]["days_dropped_no_cache"] == 0

    # Defensive re-check: every recorded fill in the artifact satisfies
    # bar_start_ts >= asof_cutoff_ts (no look-ahead).
    for day in per_trade["days"]:
        for trade in day["trades"]:
            for label, fill in trade["fills_by_offset"].items():
                if fill["status"] != "ok":
                    continue
                assert fill["bar_start_ts"] >= fill["asof_cutoff_ts"], (
                    f"look-ahead in artifact at plan={day['plan_date']} "
                    f"exec={day['execution_date']} {trade['ticker']} {label}: "
                    f"bar_start={fill['bar_start_ts']} cutoff={fill['asof_cutoff_ts']}"
                )

    # Opportunity at T+0 vs T+5 (BUY A at 100 vs 105 plus SELL B flat at 200):
    # cost(T+0) = +10*100 + (-10)*200 = -1000
    # cost(T+5) = +10*105 + (-10)*200 = -950
    # opp(T+0)  = -950 - (-1000) = +50
    opp_t0 = summary["by_offset"]["T+0m"]["opportunity_usd"]
    assert opp_t0["sum"] == pytest.approx(50.0)
    assert opp_t0["mean"] == pytest.approx(50.0)
    # Baseline opportunity sums to zero.
    assert summary["by_offset"]["T+5m"]["opportunity_usd"]["sum"] == pytest.approx(0.0)


def test_run_replay_is_deterministic_byte_for_byte(tmp_path):
    plan_root, cache_root, output_root, report_root = _set_up_fixture(tmp_path)
    frozen_now = dt.datetime(2026, 5, 29, 14, 15, tzinfo=dt.timezone.utc)

    etr.run_replay(
        run_date="2026-05-29",
        plan_root=plan_root,
        cache_root=cache_root,
        output_root=output_root,
        report_root=report_root,
        now=frozen_now,
    )
    digests_a = {
        p.relative_to(tmp_path): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in (output_root / "2026-05-29").iterdir()
    }
    report_digest_a = hashlib.sha256((report_root / "2026-05-29" / "summary.md").read_bytes()).hexdigest()

    # Rerun with exactly the same inputs and the same wall-clock injected.
    etr.run_replay(
        run_date="2026-05-29",
        plan_root=plan_root,
        cache_root=cache_root,
        output_root=output_root,
        report_root=report_root,
        now=frozen_now,
    )
    digests_b = {
        p.relative_to(tmp_path): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in (output_root / "2026-05-29").iterdir()
    }
    report_digest_b = hashlib.sha256((report_root / "2026-05-29" / "summary.md").read_bytes()).hexdigest()

    assert digests_a == digests_b
    assert report_digest_a == report_digest_b


def test_run_replay_skips_dates_with_no_cache(tmp_path):
    plan_root, cache_root, output_root, report_root = _set_up_fixture(tmp_path)
    # Add a second plan date with NO cache.
    _write_plan(plan_root, "2026-03-25", [
        {"ticker": "A", "side": "BUY", "shares": 1, "entry_price": 99.0, "reason": "rebalance"},
    ])

    result = etr.run_replay(
        run_date="2026-05-29",
        plan_root=plan_root,
        cache_root=cache_root,
        output_root=output_root,
        report_root=report_root,
        now=dt.datetime(2026, 5, 29, 14, 15, tzinfo=dt.timezone.utc),
    )
    summary = json.loads(result.summary_path.read_text())
    assert summary["coverage_summary"]["days_in_scope"] == 2
    assert summary["coverage_summary"]["days_replayed"] == 1
    assert summary["coverage_summary"]["days_dropped_no_cache"] == 1


def test_run_replay_handles_missing_bar_window_for_some_trades(tmp_path):
    plan_root, cache_root, output_root, report_root = _set_up_fixture(tmp_path)
    # Overwrite B's cache with a sparse bar set (no T+10m bar).
    _write_cache_bars(
        cache_root, "B", TRADE_DATE,
        _fake_bars("B", TRADE_DATE, {0: 200.0, 5: 200.0}),  # missing T+10m
    )
    result = etr.run_replay(
        run_date="2026-05-29",
        plan_root=plan_root,
        cache_root=cache_root,
        output_root=output_root,
        report_root=report_root,
        now=dt.datetime(2026, 5, 29, 14, 15, tzinfo=dt.timezone.utc),
    )
    summary = json.loads(result.summary_path.read_text())
    # At T+10m, only A's trade is fillable → subset mismatch → opportunity None.
    opp_t10 = summary["by_offset"]["T+10m"]["opportunity_usd"]
    assert opp_t10.get("n") in (0, None) or opp_t10["sum"] is None

    per_trade = json.loads(result.per_trade_path.read_text())
    b_fills = next(
        t for t in per_trade["days"][0]["trades"] if t["ticker"] == "B"
    )["fills_by_offset"]
    assert b_fills["T+10m"]["status"] == "no_bar_in_window"
    assert b_fills["T+10m"]["bar_start_ts"] is None


def test_no_look_ahead_audit_raises_on_synthetic_violation(monkeypatch, tmp_path):
    """Force a fill record to claim a bar_start_ts strictly before its cutoff
    and confirm ``run_replay`` refuses to write artifacts."""
    plan_root, cache_root, output_root, report_root = _set_up_fixture(tmp_path)

    original_replay_trade = fm.replay_trade

    def _evil_replay_trade(**kwargs):
        records = original_replay_trade(**kwargs)
        first_label = next(iter(records))
        bad = records[first_label]
        records[first_label] = fm.FillRecord(
            offset_label=bad.offset_label,
            offset_minutes=bad.offset_minutes,
            simulated_execution_ts=bad.simulated_execution_ts,
            asof_cutoff_ts="2099-01-01T00:00:00Z",  # impossible cutoff
            bar_start_ts="2000-01-01T00:00:00Z",
            ref_price=100.0,
            modeled_fill=100.0,
            bar_source="alpaca:1Min",
            bar_feed="iex",
            status="ok",
        )
        return records

    monkeypatch.setattr(etr, "replay_trade", _evil_replay_trade)

    with pytest.raises(RuntimeError, match="no_look_ahead_violation_detected"):
        etr.run_replay(
            run_date="2026-05-29",
            plan_root=plan_root,
            cache_root=cache_root,
            output_root=output_root,
            report_root=report_root,
            now=dt.datetime(2026, 5, 29, 14, 15, tzinfo=dt.timezone.utc),
        )


# ---------------------------------------------------------------------------
# Execution-date handling — derives execution_date from planned_for, uses
# it for cache lookup AND for the 09:30-ET offset anchor.
# ---------------------------------------------------------------------------


def test_parse_execution_date_falls_back_to_plan_date_when_planned_for_missing():
    assert etr.parse_execution_date({}, "2026-03-24") == "2026-03-24"
    assert etr.parse_execution_date({"planned_for": None}, "2026-03-24") == "2026-03-24"
    assert etr.parse_execution_date({"planned_for": ""}, "2026-03-24") == "2026-03-24"
    assert etr.parse_execution_date({"planned_for": "not-a-date"}, "2026-03-24") == "2026-03-24"


def test_parse_execution_date_uses_date_portion_of_planned_for():
    # tz-aware ET at 12:40 — date stays 2026-03-25
    assert etr.parse_execution_date(
        {"planned_for": "2026-03-25T12:40:00-04:00"}, "2026-03-24"
    ) == "2026-03-25"
    # naive — interpreted as ET-local
    assert etr.parse_execution_date(
        {"planned_for": "2026-03-25T09:30:00"}, "2026-03-24"
    ) == "2026-03-25"
    # bare date
    assert etr.parse_execution_date(
        {"planned_for": "2026-03-25"}, "2026-03-24"
    ) == "2026-03-25"
    # UTC very late evening — converts to ET, which falls on the same calendar day here
    assert etr.parse_execution_date(
        {"planned_for": "2026-03-25T20:30:00Z"}, "2026-03-24"
    ) == "2026-03-25"
    # UTC just past midnight — in ET this is the previous day
    assert etr.parse_execution_date(
        {"planned_for": "2026-03-25T01:00:00Z"}, "2026-03-25"
    ) == "2026-03-24"


def test_replay_uses_execution_date_for_cache_and_anchors_offsets_at_0930(tmp_path):
    """Plan folder is 2026-03-24, ``planned_for`` is 12:40 ET on 2026-03-25.

    Cache lookup must use 2026-03-25 (execution_date). Simulated offsets
    must anchor at 09:30 ET on 2026-03-25 — NOT at 12:40, NOT on 2026-03-24.
    """
    plan_root = tmp_path / "outputs" / "precompute"
    cache_root = tmp_path / "data" / "research_cache" / "intraday"
    output_root = tmp_path / "outputs" / "research" / "execution_timing"
    report_root = tmp_path / "reports" / "execution_timing"

    plan_date = "2026-03-24"
    execution_date = "2026-03-25"

    _write_plan(
        plan_root,
        plan_date,
        [{"ticker": "AAPL", "side": "BUY", "shares": 1, "entry_price": 100.0, "reason": "rebalance"}],
        planned_for="2026-03-25T12:40:00-04:00",  # NOTE: 12:40, not 09:30
    )

    # Cache bars are stored at the EXECUTION date, 2026-03-25. The opens
    # encode the minute offset so we can later verify which bar was picked
    # for each offset.
    bars = _fake_bars("AAPL", execution_date, {
        0: 100.0, 1: 101.0, 2: 102.0, 3: 103.0, 4: 104.0, 5: 105.0, 10: 110.0,
    })
    _write_cache_bars(cache_root, "AAPL", execution_date, bars)

    # Sanity: a cache file ONLY at plan_date would let an off-by-one bug
    # slip past. Verify it doesn't exist.
    assert not (cache_root / CACHE_KEY_VERSION / "AAPL" / f"{plan_date}.parquet").exists()

    result = etr.run_replay(
        run_date="2026-05-29",
        plan_root=plan_root,
        cache_root=cache_root,
        output_root=output_root,
        report_root=report_root,
        now=dt.datetime(2026, 5, 29, 14, 15, tzinfo=dt.timezone.utc),
    )
    assert result.overall_status == "OK"
    assert result.days_with_full_coverage == 1

    per_trade = json.loads(result.per_trade_path.read_text())
    summary = json.loads(result.summary_path.read_text())

    # The day artifact reports BOTH dates and the raw planned_for it parsed.
    day = per_trade["days"][0]
    assert day["plan_date"] == plan_date
    assert day["execution_date"] == execution_date
    assert day["planned_for_raw"] == "2026-03-25T12:40:00-04:00"
    assert day["coverage"]["cache_lookup_date"] == execution_date

    # Summary carries both views.
    assert summary["plan_dates"] == [plan_date]
    assert summary["execution_dates"] == [execution_date]
    dates_row = summary["dates"][0]
    assert dates_row["plan_date"] == plan_date
    assert dates_row["execution_date"] == execution_date
    assert dates_row["planned_for_raw"] == "2026-03-25T12:40:00-04:00"

    # Simulated offsets are anchored to 09:30 ET on the EXECUTION date.
    # 09:30 ET on 2026-03-25 = 13:30 UTC; 09:31 ET = 13:31 UTC; etc.
    fills = day["trades"][0]["fills_by_offset"]
    assert fills["T+0m"]["simulated_execution_ts"] == "2026-03-25T13:30:00Z"
    assert fills["T+1m"]["simulated_execution_ts"] == "2026-03-25T13:31:00Z"
    assert fills["T+5m"]["simulated_execution_ts"] == "2026-03-25T13:35:00Z"
    assert fills["T+10m"]["simulated_execution_ts"] == "2026-03-25T13:40:00Z"

    # The picked bars come from the execution-date cache.
    assert fills["T+0m"]["bar_start_ts"] == "2026-03-25T13:30:00Z"
    assert fills["T+0m"]["ref_price"] == 100.0
    assert fills["T+5m"]["ref_price"] == 105.0
    assert fills["T+10m"]["ref_price"] == 110.0

    # Each fill satisfies the no-look-ahead invariant against its own cutoff.
    for label, fill in fills.items():
        if fill["status"] != "ok":
            continue
        assert fill["bar_start_ts"] >= fill["asof_cutoff_ts"], (
            f"{label}: bar_start={fill['bar_start_ts']} cutoff={fill['asof_cutoff_ts']}"
        )

    # Markdown report exposes the date offset table when plan_date != execution_date.
    md = result.report_path.read_text()
    assert "Plan → execution date mapping" in md
    assert plan_date in md
    assert execution_date in md
    assert "2026-03-25T12:40:00-04:00" in md


def test_replay_falls_back_to_plan_date_when_planned_for_missing(tmp_path):
    plan_root = tmp_path / "outputs" / "precompute"
    cache_root = tmp_path / "data" / "research_cache" / "intraday"
    output_root = tmp_path / "outputs" / "research" / "execution_timing"
    report_root = tmp_path / "reports" / "execution_timing"

    _write_plan(plan_root, TRADE_DATE, [
        {"ticker": "AAPL", "side": "BUY", "shares": 1, "entry_price": 100.0, "reason": "rebalance"},
    ])  # no planned_for
    _write_cache_bars(
        cache_root, "AAPL", TRADE_DATE,
        _fake_bars("AAPL", TRADE_DATE, {0: 100.0, 5: 105.0, 10: 110.0}),
    )

    result = etr.run_replay(
        run_date="2026-05-29",
        plan_root=plan_root,
        cache_root=cache_root,
        output_root=output_root,
        report_root=report_root,
        now=dt.datetime(2026, 5, 29, 14, 15, tzinfo=dt.timezone.utc),
    )
    assert result.overall_status == "OK"
    per_trade = json.loads(result.per_trade_path.read_text())
    day = per_trade["days"][0]
    assert day["plan_date"] == TRADE_DATE
    assert day["execution_date"] == TRADE_DATE
    assert day["planned_for_raw"] is None
    # When the dates match, the markdown report uses the "all days match"
    # variant instead of the mapping table.
    md = result.report_path.read_text()
    assert "Plan → execution date mapping" not in md
    assert "plan_date == execution_date" in md


# ---------------------------------------------------------------------------
# Execution-path isolation
# ---------------------------------------------------------------------------


EXECUTION_PATH_SENTINELS = [
    Path("scripts/cron_execute.sh"),
    Path("scripts/cron_precompute.sh"),
    Path("scripts/cron_research.sh"),
    Path("scripts/crontab.txt"),
    Path("scripts/run_precomputed_alpaca_execution.py"),
    Path("core/timing_policy.py"),
    Path("brokers/alpaca_broker.py"),
    Path("reconciliation.py"),
]


def test_replay_does_not_touch_execution_path_artifacts(tmp_path):
    repo_root = Path(__file__).resolve().parents[1]
    sentinels = [repo_root / p for p in EXECUTION_PATH_SENTINELS if (repo_root / p).exists()]
    assert len(sentinels) >= 7
    before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in sentinels}

    plan_root, cache_root, output_root, report_root = _set_up_fixture(tmp_path)
    etr.run_replay(
        run_date="2026-05-29",
        plan_root=plan_root,
        cache_root=cache_root,
        output_root=output_root,
        report_root=report_root,
        now=dt.datetime(2026, 5, 29, 14, 15, tzinfo=dt.timezone.utc),
    )

    after = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in sentinels}
    assert before == after
