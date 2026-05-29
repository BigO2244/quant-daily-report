"""Targeted coverage for the new timing × VIX regime research MCP tools.

These tests never read the real ``outputs/`` directory — every input is
materialised in ``tmp_path``. They confirm:

1. The pure loader joins timing-replay output to the regime CSV on
   execution_date, not plan_date.
2. The aggregator computes mean/median per (regime, offset) and tags
   buckets below the configured sample threshold.
3. The ``execution_timing_by_vix_regime`` MCP tool fails closed when
   either timing or regime artifacts are missing.
4. The ``answer_research_question`` matcher whitelists exactly the
   intents in the task spec and returns ``UNSUPPORTED_INTENT`` for
   anything else.
5. Both new tools appear in ``list_tools()`` and dispatch via
   ``call_tool()`` so MCP clients can call them by name.
6. The ingestion adapters for execution_timing and vix_regime_history
   produce envelopes that pass validation.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from research_registry.ingestion import (
    ExecutionTimingArtifactAdapter,
    VixRegimeHistoryAdapter,
    ingest_artifact_family,
)
from research_registry.mcp_server import ToolContext, call_tool, list_tools
from research_registry.research import timing_regime as tr


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


_OFFSETS = ("T+0m", "T+1m", "T+2m", "T+3m", "T+4m", "T+5m", "T+10m")
_BASELINE = "T+5m"


def _per_trade_day(
    *,
    plan_date: str,
    execution_date: str,
    trades: list[dict],
) -> dict:
    return {
        "plan_date": plan_date,
        "execution_date": execution_date,
        "status": "ok",
        "trades": trades,
    }


def _fill(offset_label: str, ref_price: float) -> dict:
    return {
        "status": "ok",
        "modeled_fill": ref_price,
        "ref_price": ref_price,
        "simulated_execution_ts": f"2026-01-01T13:{30 + int(offset_label[2:-1]):02d}:00Z",
        "asof_cutoff_ts": f"2026-01-01T13:{30 + int(offset_label[2:-1]):02d}:00Z",
        "bar_start_ts": f"2026-01-01T13:{30 + int(offset_label[2:-1]):02d}:00Z",
        "offset_label": offset_label,
        "offset_minutes": int(offset_label[2:-1]),
    }


def _rallying_buy(opens: dict[str, float]) -> dict:
    """A BUY-1 trade with prices rallying across offsets so T+0 is cheapest."""
    return {
        "ticker": "AAPL",
        "side": "BUY",
        "shares": 1,
        "fills_by_offset": {label: _fill(label, opens[label]) for label in _OFFSETS},
    }


def _write_timing_run(
    timing_root: Path,
    run_date: str,
    days: list[dict],
) -> Path:
    run_dir = timing_root / run_date
    run_dir.mkdir(parents=True, exist_ok=True)
    per_trade = {
        "schema_version": "1.0",
        "run_date": run_date,
        "generated_at": "2026-05-29T14:00:00Z",
        "cache_key_version": "intraday_bars_v1_iex_0925_1030",
        "offsets": list(_OFFSETS),
        "baseline_offset": _BASELINE,
        "days": days,
    }
    summary = {
        "schema_version": "1.0",
        "run_date": run_date,
        "generated_at": "2026-05-29T14:00:00Z",
        "cache_key_version": "intraday_bars_v1_iex_0925_1030",
        "offsets": list(_OFFSETS),
        "baseline_offset": _BASELINE,
        "plan_dates": sorted(d["plan_date"] for d in days),
        "execution_dates": sorted(d["execution_date"] for d in days),
        "coverage_summary": {
            "days_in_scope": len(days),
            "days_replayed": len(days),
            "days_dropped_no_plan": 0,
            "days_dropped_empty_plan": 0,
            "days_dropped_no_cache": 0,
            "days_with_partial_cache": 0,
        },
    }
    (run_dir / "per_trade_timing.json").write_text(json.dumps(per_trade, sort_keys=True), encoding="utf-8")
    (run_dir / "timing_summary.json").write_text(json.dumps(summary, sort_keys=True), encoding="utf-8")
    return run_dir


def _write_regime_history(regime_csv: Path, rows: list[tuple[str, str, float]]) -> None:
    regime_csv.parent.mkdir(parents=True, exist_ok=True)
    lines = ["as_of,regime,vix,position_scale,max_positions"]
    for as_of, regime, vix in rows:
        lines.append(f"{as_of},{regime},{vix},0.75,7")
    regime_csv.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Pure-loader tests
# ---------------------------------------------------------------------------


def test_loader_joins_on_execution_date_not_plan_date(tmp_path):
    """The plan dated 2026-04-15 executes on 2026-04-16; the regime label
    must come from the execution date's row in the CSV."""
    timing_root = tmp_path / "outputs" / "research" / "execution_timing"
    regime_csv = tmp_path / "outputs" / "vix_regime" / "regime_history.csv"

    day = _per_trade_day(
        plan_date="2026-04-15",
        execution_date="2026-04-16",
        trades=[_rallying_buy({
            "T+0m": 100.0, "T+1m": 101.0, "T+2m": 102.0, "T+3m": 103.0,
            "T+4m": 104.0, "T+5m": 105.0, "T+10m": 110.0,
        })],
    )
    _write_timing_run(timing_root, "2026-05-29", [day])
    _write_regime_history(regime_csv, [
        ("2026-04-15", "NORMAL", 14.0),   # plan-date regime — must be IGNORED
        ("2026-04-16", "ELEVATED", 26.0), # execution-date regime — must be USED
    ])

    answer = tr.answer_timing_by_regime_question(
        timing_root=timing_root,
        regime_history=regime_csv,
        threshold=1,  # accept 1-day buckets so test sees the math
    )

    assert answer["status"] == "OK"
    assert answer["baseline_offset"] == _BASELINE
    by_regime = {a["regime"]: a for a in answer["regime_aggregates"]}
    assert "ELEVATED" in by_regime, by_regime.keys()
    assert "NORMAL" not in by_regime, "plan-date regime leaked into the join"

    # Buy rallying from $100 (T+0) to $105 (baseline) → T+0 saves $5 per share.
    t0 = by_regime["ELEVATED"]["by_offset"]["T+0m"]
    assert t0["mean_opportunity_usd"] == pytest.approx(5.0)
    assert t0["median_opportunity_usd"] == pytest.approx(5.0)


def test_loader_returns_no_timing_data_when_root_missing(tmp_path):
    answer = tr.answer_timing_by_regime_question(
        timing_root=tmp_path / "does" / "not" / "exist",
        regime_history=tmp_path / "regime.csv",
    )
    assert answer["status"] == "NO_TIMING_DATA"
    assert answer["regime_aggregates"] == []
    assert answer["warnings"]


def test_loader_returns_no_regime_data_when_csv_missing(tmp_path):
    timing_root = tmp_path / "outputs" / "research" / "execution_timing"
    day = _per_trade_day(
        plan_date="2026-04-15",
        execution_date="2026-04-15",
        trades=[_rallying_buy({label: 100.0 for label in _OFFSETS})],
    )
    _write_timing_run(timing_root, "2026-05-29", [day])

    answer = tr.answer_timing_by_regime_question(
        timing_root=timing_root,
        regime_history=tmp_path / "missing.csv",
    )
    assert answer["status"] == "NO_REGIME_DATA"
    assert answer["regime_aggregates"] == []
    assert answer["baseline_offset"] == _BASELINE


def test_loader_picks_latest_run_date_lexicographically(tmp_path):
    timing_root = tmp_path / "outputs" / "research" / "execution_timing"
    older_day = _per_trade_day(
        plan_date="2026-03-10",
        execution_date="2026-03-10",
        trades=[_rallying_buy({label: 100.0 for label in _OFFSETS})],
    )
    newer_day = _per_trade_day(
        plan_date="2026-04-16",
        execution_date="2026-04-16",
        trades=[_rallying_buy({label: 100.0 for label in _OFFSETS})],
    )
    _write_timing_run(timing_root, "2026-04-01", [older_day])
    _write_timing_run(timing_root, "2026-05-29", [newer_day])

    chosen = tr.select_timing_run(timing_root)
    assert chosen is not None
    assert chosen.name == "2026-05-29"


def test_aggregator_flags_insufficient_sample(tmp_path):
    timing_root = tmp_path / "outputs" / "research" / "execution_timing"
    regime_csv = tmp_path / "outputs" / "vix_regime" / "regime_history.csv"
    # Two ELEVATED days, one NORMAL day — both buckets are below the
    # default threshold of 5 days; both must be tagged.
    days = []
    rows = []
    for i, execution_date in enumerate(["2026-04-15", "2026-04-16"]):
        days.append(_per_trade_day(
            plan_date=execution_date,
            execution_date=execution_date,
            trades=[_rallying_buy({"T+0m": 100.0 + i, "T+1m": 101.0 + i,
                                    "T+2m": 102.0 + i, "T+3m": 103.0 + i,
                                    "T+4m": 104.0 + i, "T+5m": 105.0 + i,
                                    "T+10m": 110.0 + i})],
        ))
        rows.append((execution_date, "ELEVATED", 25.0))
    days.append(_per_trade_day(
        plan_date="2026-04-17",
        execution_date="2026-04-17",
        trades=[_rallying_buy({label: 100.0 for label in _OFFSETS})],
    ))
    rows.append(("2026-04-17", "NORMAL", 14.0))

    _write_timing_run(timing_root, "2026-05-29", days)
    _write_regime_history(regime_csv, rows)

    answer = tr.answer_timing_by_regime_question(
        timing_root=timing_root,
        regime_history=regime_csv,
    )
    assert answer["status"] == "OK"
    by_regime = {a["regime"]: a for a in answer["regime_aggregates"]}
    assert by_regime["ELEVATED"]["insufficient_sample"] is True
    assert by_regime["NORMAL"]["insufficient_sample"] is True
    assert by_regime["ELEVATED"]["n_days"] == 2
    assert by_regime["NORMAL"]["n_days"] == 1
    # The warnings list calls out which regimes are insufficient.
    insufficient_warning = next(
        (w for w in answer["warnings"] if "insufficient_sample" in w),
        None,
    )
    assert insufficient_warning is not None
    assert "ELEVATED" in insufficient_warning
    assert "NORMAL" in insufficient_warning


# ---------------------------------------------------------------------------
# MCP tool wiring
# ---------------------------------------------------------------------------


def test_list_tools_registers_new_research_tools():
    names = {t["name"] for t in list_tools()}
    assert "execution_timing_by_vix_regime" in names
    assert "answer_research_question" in names


def test_call_tool_routes_execution_timing_by_vix_regime(tmp_path):
    timing_root = tmp_path / "outputs" / "research" / "execution_timing"
    regime_csv = tmp_path / "outputs" / "vix_regime" / "regime_history.csv"
    _write_timing_run(timing_root, "2026-05-29", [
        _per_trade_day(plan_date="2026-04-16", execution_date="2026-04-16",
                       trades=[_rallying_buy({"T+0m": 100.0, "T+1m": 101.0,
                                              "T+2m": 102.0, "T+3m": 103.0,
                                              "T+4m": 104.0, "T+5m": 105.0,
                                              "T+10m": 110.0})])
    ])
    _write_regime_history(regime_csv, [("2026-04-16", "ELEVATED", 26.0)])

    result = call_tool(
        "execution_timing_by_vix_regime",
        {
            "timing_root": str(timing_root),
            "regime_history": str(regime_csv),
            "insufficient_sample_threshold": 1,
        },
        context=ToolContext(),
    )
    assert result["status"] == "OK"
    assert result["tool"] == "execution_timing_by_vix_regime"
    assert result["baseline_offset"] == _BASELINE
    elevated = next(a for a in result["regime_aggregates"] if a["regime"] == "ELEVATED")
    assert elevated["by_offset"]["T+0m"]["mean_opportunity_usd"] == pytest.approx(5.0)


def test_execution_timing_tool_fails_closed_on_missing_inputs(tmp_path):
    result = call_tool(
        "execution_timing_by_vix_regime",
        {
            "timing_root": str(tmp_path / "nope"),
            "regime_history": str(tmp_path / "regime.csv"),
        },
    )
    assert result["status"] == "NO_TIMING_DATA"
    assert result["regime_aggregates"] == []


# ---------------------------------------------------------------------------
# Natural-language wrapper (regex whitelist only — no LLM)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("question", [
    "Does timing matter more in high VIX regimes?",
    "does execution timing matter in different VIX regimes",
    "high VIX timing question",
    "Is there a regime effect on execution timing?",
    "timing vs vix",
])
def test_answer_research_question_routes_timing_regime_intents(tmp_path, question):
    result = call_tool(
        "answer_research_question",
        {
            "question": question,
            "timing_root": str(tmp_path / "missing"),  # forces NO_TIMING_DATA underneath
            "regime_history": str(tmp_path / "regime.csv"),
        },
    )
    # Intent is matched; underlying answer reports the fail-closed status.
    assert result["intent"] == "timing_by_vix_regime"
    assert result["routed_to"] == "execution_timing_by_vix_regime"
    assert result["status"] == "NO_TIMING_DATA"


@pytest.mark.parametrize("question", [
    "What was the alpha last quarter?",
    "show me the largest drawdown",
    "compare strategies",
    "",  # empty input
    "VIX",  # too underspecified — doesn't mention timing
    "execution",  # too underspecified — doesn't mention regime
])
def test_answer_research_question_returns_unsupported_intent_otherwise(question):
    result = call_tool("answer_research_question", {"question": question})
    assert result["status"] == "UNSUPPORTED_INTENT"
    assert result["intent"] is None
    # The available_intents block is the discoverability surface.
    assert any(
        intent["intent"] == "timing_by_vix_regime"
        for intent in result["available_intents"]
    )


# ---------------------------------------------------------------------------
# Ingestion adapters
# ---------------------------------------------------------------------------


def test_execution_timing_artifact_adapter_hydrates_envelope(tmp_path):
    timing_root = tmp_path / "outputs" / "research" / "execution_timing"
    run_dir = _write_timing_run(timing_root, "2026-05-29", [
        _per_trade_day(plan_date="2026-04-16", execution_date="2026-04-16",
                       trades=[_rallying_buy({label: 100.0 for label in _OFFSETS})])
    ])
    result = ExecutionTimingArtifactAdapter().hydrate_path(run_dir)
    assert not result.findings
    assert len(result.envelopes) == 1
    env = result.envelopes[0]
    assert env.data["artifact_role"] == "execution_timing_replay"
    assert env.data["run_date"] == "2026-05-29"
    assert env.data["baseline_offset"] == _BASELINE


def test_execution_timing_adapter_records_finding_when_missing(tmp_path):
    result = ExecutionTimingArtifactAdapter().hydrate_path(tmp_path / "nope")
    assert not result.envelopes
    assert result.findings
    assert result.findings[0].code == "EXECUTION_TIMING_ARTIFACT_MISSING"


def test_vix_regime_history_adapter_hydrates_envelope(tmp_path):
    regime_csv = tmp_path / "outputs" / "vix_regime" / "regime_history.csv"
    _write_regime_history(regime_csv, [
        ("2026-04-15", "NORMAL", 14.0),
        ("2026-04-16", "ELEVATED", 26.0),
    ])
    result = VixRegimeHistoryAdapter().hydrate_path(regime_csv)
    assert not result.findings
    env = result.envelopes[0]
    assert env.data["row_count"] == 2
    assert env.data["latest_regime"] == "ELEVATED"
    assert env.data["latest_vix"] == 26.0
    assert env.data["observed_regimes"] == ["ELEVATED", "NORMAL"]


def test_ingest_artifact_family_handles_new_families(tmp_path):
    """Round-trip both new family names through the canonical entry point."""
    regime_csv = tmp_path / "outputs" / "vix_regime" / "regime_history.csv"
    _write_regime_history(regime_csv, [("2026-04-16", "ELEVATED", 26.0)])
    timing_dir = _write_timing_run(
        tmp_path / "outputs" / "research" / "execution_timing", "2026-05-29",
        [_per_trade_day(plan_date="2026-04-16", execution_date="2026-04-16",
                        trades=[_rallying_buy({label: 100.0 for label in _OFFSETS})])],
    )
    for family, paths in (
        ("execution_timing", [timing_dir]),
        ("vix_regime_history", [regime_csv]),
    ):
        result = ingest_artifact_family(family=family, artifact_paths=paths)
        assert not result.findings, f"family {family} produced findings: {result.findings}"
        assert len(result.envelopes) == 1
