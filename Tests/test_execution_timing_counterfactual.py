from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from research.execution_timing_counterfactual import build_execution_timing_counterfactual
from research.execution_timing_cache import build_execution_timing_cache, resolve_execution_timing_cache_request
from scripts.research.intraday_research_cache import cache_path_for


ET = ZoneInfo("America/New_York")


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def _bars(symbol: str, trade_date: str, prices: dict[int, float]) -> pd.DataFrame:
    base = dt.datetime.combine(dt.date.fromisoformat(trade_date), dt.time(9, 30), tzinfo=ET)
    rows = []
    for offset, price in sorted(prices.items()):
        rows.append(
            {
                "symbol": symbol,
                "trade_date": trade_date,
                "bar_start_ts": (base + dt.timedelta(minutes=offset)).astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "volume": 1000,
                "trade_count": 1,
                "vwap": price,
                "source": "alpaca:1Min",
                "feed": "iex",
                "retrieved_at": "2026-06-01T15:00:00Z",
            }
        )
    return pd.DataFrame(rows)


def _write_plan(root: Path, trade_date: str, trades: list[dict]) -> None:
    _write_json(
        root / "outputs" / "precompute" / trade_date / "planned_execution_payload.json",
        {"trade_date": trade_date, "planned_for": f"{trade_date}T09:30:00-04:00", "trades": trades},
    )


def _write_cache(root: Path, symbol: str, trade_date: str, prices: dict[int, float]) -> None:
    path = cache_path_for(symbol, trade_date, root / "data" / "research_cache" / "intraday")
    path.parent.mkdir(parents=True, exist_ok=True)
    _bars(symbol, trade_date, prices).to_parquet(path, index=False)


def test_execution_timing_all_offsets_and_baseline_zero(tmp_path):
    trade_date = "2026-06-01"
    _write_plan(tmp_path, trade_date, [{"ticker": "AAA", "side": "BUY", "shares": 10}])
    _write_cache(tmp_path, "AAA", trade_date, {0: 100, 1: 101, 2: 102, 5: 105, 10: 110})

    payload = build_execution_timing_counterfactual(trade_date=trade_date, repo_root=tmp_path)

    assert payload["available"] is True
    assert [row["offset_label"] for row in payload["offsets"]] == ["T+0m", "T+1m", "T+2m", "T+5m", "T+10m"]
    baseline = [row for row in payload["offsets"] if row["is_baseline"]][0]
    assert baseline["estimated_slippage_vs_baseline_usd"] == 0.0
    assert baseline["total_estimated_bps_impact_vs_baseline"] == 0.0
    assert (tmp_path / "outputs" / "research" / "execution_timing" / trade_date / "execution_timing_counterfactual.json").exists()
    assert (tmp_path / "outputs" / "research" / "execution_timing" / trade_date / "execution_timing_summary.md").exists()


def test_execution_timing_missing_bars_and_partial_coverage(tmp_path):
    trade_date = "2026-06-01"
    _write_plan(
        tmp_path,
        trade_date,
        [
            {"ticker": "AAA", "side": "BUY", "shares": 10},
            {"ticker": "BBB", "side": "SELL", "shares": 5},
        ],
    )
    _write_cache(tmp_path, "AAA", trade_date, {0: 100, 1: 101, 2: 102, 5: 105, 10: 110})

    payload = build_execution_timing_counterfactual(trade_date=trade_date, repo_root=tmp_path)

    assert payload["available"] is False
    assert "coverage_below_threshold" in payload["reason_codes"]
    assert "BBB" in payload["symbols_missing_bars"]


def test_execution_timing_does_not_use_pre_cutoff_bar(tmp_path):
    trade_date = "2026-06-01"
    _write_plan(tmp_path, trade_date, [{"ticker": "AAA", "side": "BUY", "shares": 10}])
    _write_cache(tmp_path, "AAA", trade_date, {4: 104})

    payload = build_execution_timing_counterfactual(trade_date=trade_date, repo_root=tmp_path)

    assert payload["available"] is False
    baseline = [row for row in payload["offsets"] if row["is_baseline"]][0]
    assert baseline["symbols_evaluated"] == 0
    assert "baseline_bars_missing" in payload["reason_codes"]


def test_execution_timing_empty_payload(tmp_path):
    trade_date = "2026-06-01"
    _write_plan(tmp_path, trade_date, [])

    payload = build_execution_timing_counterfactual(trade_date=trade_date, repo_root=tmp_path)

    assert payload["available"] is False
    assert "empty_planned_payload" in payload["reason_codes"]


def test_execution_timing_cache_resolves_execution_date_from_planned_for(tmp_path):
    plan_date = "2026-06-01"
    _write_json(
        tmp_path / "outputs" / "precompute" / plan_date / "planned_execution_payload.json",
        {
            "trade_date": plan_date,
            "planned_for": "2026-06-02T09:35:00-04:00",
            "trades": [{"ticker": "AAA", "side": "BUY", "shares": 10}],
        },
    )

    request = resolve_execution_timing_cache_request(plan_date=plan_date, plan_root=tmp_path / "outputs" / "precompute")

    assert request.execution_date == "2026-06-02"
    assert "execution_date_derived_from_planned_for" in request.reason_codes


def test_execution_timing_cache_dry_run_writes_deterministic_status_without_fetch(tmp_path):
    plan_date = "2026-06-01"
    _write_plan(tmp_path, plan_date, [{"ticker": "AAA", "side": "BUY", "shares": 10}])

    payload = build_execution_timing_cache(plan_date=plan_date, repo_root=tmp_path, dry_run=True)

    assert payload["overall_status"] == "DRY_RUN"
    assert payload["execution_date"] == plan_date
    status_path = tmp_path / "outputs" / "research" / "execution_timing" / plan_date / "cache_status.json"
    assert status_path.exists()
    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert status["dry_run"] is True
    assert status["reason_codes"] == ["ok"]


def test_execution_timing_cache_missing_plan_skips_with_reason(tmp_path):
    payload = build_execution_timing_cache(plan_date="2026-06-01", repo_root=tmp_path)

    assert payload["overall_status"] == "SKIPPED"
    assert "plan_payload_missing" in payload["reason_codes"]
