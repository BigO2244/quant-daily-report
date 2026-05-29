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


def _write_regime_history_vm_schema(regime_csv: Path, rows: list[tuple[str, str, float]]) -> None:
    """Write a regime CSV using the *VM* column layout:
    ``date,vix,regime,position_scale,max_positions,source,fallback_used``.

    This is the actual schema in production today; the older fixture writer
    above uses the historical ``as_of`` layout. Tests using this writer
    exercise the patched-in `date` column path.
    """
    regime_csv.parent.mkdir(parents=True, exist_ok=True)
    lines = ["date,vix,regime,position_scale,max_positions,source,fallback_used"]
    for date, regime, vix in rows:
        lines.append(f"{date},{vix},{regime},0.75,7,fred,False")
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
# VM schema compatibility — date column, extra bookkeeping columns,
# deterministic dedup, bad-schema fail mode.
# ---------------------------------------------------------------------------


def test_loader_accepts_vm_schema_with_date_column(tmp_path):
    """Real VM CSV header is `date,vix,regime,position_scale,max_positions,
    source,fallback_used`. The loader must accept `date` and ignore the
    extra columns — this was the exact bug that produced NO_REGIME_DATA."""
    regime_csv = tmp_path / "outputs" / "vix_regime" / "regime_history.csv"
    _write_regime_history_vm_schema(regime_csv, [
        ("2026-04-15", "NORMAL", 14.0),
        ("2026-04-16", "ELEVATED", 26.0),
    ])
    out = tr.load_vix_regime_history(regime_csv)
    assert out == {
        "2026-04-15": {"regime": "NORMAL", "vix": 14.0},
        "2026-04-16": {"regime": "ELEVATED", "vix": 26.0},
    }


def test_loader_accepts_historical_as_of_schema(tmp_path):
    """Older local fixtures used `as_of`; back-compat must continue."""
    regime_csv = tmp_path / "outputs" / "vix_regime" / "regime_history.csv"
    _write_regime_history(regime_csv, [
        ("2026-04-15", "NORMAL", 14.0),
        ("2026-04-16", "ELEVATED", 26.0),
    ])
    out = tr.load_vix_regime_history(regime_csv)
    assert "2026-04-15" in out and "2026-04-16" in out


def test_loader_deduplicates_repeated_dates_keeping_last_in_file(tmp_path):
    """Stable sort by date with file-order tiebreak → the LAST row in the
    CSV for a given date wins. The classifier writes intra-day snapshots,
    so the later row is the more authoritative observation."""
    regime_csv = tmp_path / "outputs" / "vix_regime" / "regime_history.csv"
    _write_regime_history_vm_schema(regime_csv, [
        ("2026-04-15", "NORMAL", 14.0),
        ("2026-04-15", "NORMAL", 15.0),   # later observation, same regime
        ("2026-04-15", "ELEVATED", 22.0), # final observation of the day — must win
        ("2026-04-16", "ELEVATED", 26.0),
    ])
    out = tr.load_vix_regime_history(regime_csv)
    assert out["2026-04-15"] == {"regime": "ELEVATED", "vix": 22.0}
    assert out["2026-04-16"] == {"regime": "ELEVATED", "vix": 26.0}


def test_loader_raises_bad_schema_when_required_columns_missing(tmp_path):
    """File exists with rows but no recognisable date column → distinct
    error path (BAD_REGIME_SCHEMA), NOT NO_REGIME_DATA."""
    regime_csv = tmp_path / "outputs" / "vix_regime" / "regime_history.csv"
    regime_csv.parent.mkdir(parents=True, exist_ok=True)
    regime_csv.write_text(
        "wrong_col,regime,vix\nfoo,ELEVATED,25.0\n",
        encoding="utf-8",
    )
    with pytest.raises(tr.RegimeHistoryFormatError) as exc_info:
        tr.load_vix_regime_history(regime_csv)
    # Error message names the specific missing column(s).
    assert "date|as_of|execution_date" in exc_info.value.missing_columns


def test_loader_raises_bad_schema_when_regime_or_vix_missing(tmp_path):
    regime_csv = tmp_path / "outputs" / "vix_regime" / "regime_history.csv"
    regime_csv.parent.mkdir(parents=True, exist_ok=True)
    regime_csv.write_text("date,position_scale\n2026-04-15,0.75\n", encoding="utf-8")
    with pytest.raises(tr.RegimeHistoryFormatError) as exc_info:
        tr.load_vix_regime_history(regime_csv)
    assert "regime" in exc_info.value.missing_columns
    assert "vix" in exc_info.value.missing_columns


def test_loader_returns_empty_dict_when_file_empty_but_well_formed(tmp_path):
    """File present, only the header — no rows. Treated as NO_REGIME_DATA
    (empty dict), not BAD_REGIME_SCHEMA."""
    regime_csv = tmp_path / "outputs" / "vix_regime" / "regime_history.csv"
    regime_csv.parent.mkdir(parents=True, exist_ok=True)
    regime_csv.write_text(
        "date,vix,regime,position_scale,max_positions,source,fallback_used\n",
        encoding="utf-8",
    )
    assert tr.load_vix_regime_history(regime_csv) == {}


def test_orchestrator_surfaces_bad_regime_schema_distinctly(tmp_path):
    """End-to-end: when timing exists but the regime CSV is bad-schema, the
    answer should be BAD_REGIME_SCHEMA, not NO_REGIME_DATA."""
    timing_root = tmp_path / "outputs" / "research" / "execution_timing"
    regime_csv = tmp_path / "outputs" / "vix_regime" / "regime_history.csv"
    _write_timing_run(timing_root, "2026-05-29", [
        _per_trade_day(plan_date="2026-04-16", execution_date="2026-04-16",
                       trades=[_rallying_buy({label: 100.0 for label in _OFFSETS})])
    ])
    regime_csv.parent.mkdir(parents=True, exist_ok=True)
    regime_csv.write_text("wrong_col,regime,vix\nfoo,ELEVATED,25.0\n", encoding="utf-8")

    answer = tr.answer_timing_by_regime_question(
        timing_root=timing_root,
        regime_history=regime_csv,
    )
    assert answer["status"] == "BAD_REGIME_SCHEMA"
    assert "date|as_of|execution_date" in answer["missing_columns"]
    assert answer["regime_aggregates"] == []


def test_orchestrator_distinguishes_missing_file_from_empty_file(tmp_path):
    """NO_REGIME_DATA should carry different reason text for missing vs empty."""
    timing_root = tmp_path / "outputs" / "research" / "execution_timing"
    _write_timing_run(timing_root, "2026-05-29", [
        _per_trade_day(plan_date="2026-04-16", execution_date="2026-04-16",
                       trades=[_rallying_buy({label: 100.0 for label in _OFFSETS})])
    ])

    # Missing file.
    missing = tr.answer_timing_by_regime_question(
        timing_root=timing_root,
        regime_history=tmp_path / "nope.csv",
    )
    assert missing["status"] == "NO_REGIME_DATA"
    assert "does not exist" in missing["reason"]

    # Empty file (header only).
    empty_csv = tmp_path / "outputs" / "vix_regime" / "regime_history.csv"
    empty_csv.parent.mkdir(parents=True, exist_ok=True)
    empty_csv.write_text(
        "date,vix,regime,position_scale,max_positions,source,fallback_used\n",
        encoding="utf-8",
    )
    empty = tr.answer_timing_by_regime_question(
        timing_root=timing_root,
        regime_history=empty_csv,
    )
    assert empty["status"] == "NO_REGIME_DATA"
    assert "empty" in empty["reason"]


def test_join_works_end_to_end_on_vm_schema(tmp_path):
    """The whole pipeline — load timing, load VM-schema regime CSV, join,
    aggregate — produces a populated regime bucket. This is the exact path
    that was broken on the VM."""
    timing_root = tmp_path / "outputs" / "research" / "execution_timing"
    regime_csv = tmp_path / "outputs" / "vix_regime" / "regime_history.csv"
    _write_timing_run(timing_root, "2026-05-29", [
        _per_trade_day(plan_date="2026-04-16", execution_date="2026-04-16",
                       trades=[_rallying_buy({"T+0m": 100.0, "T+1m": 101.0,
                                              "T+2m": 102.0, "T+3m": 103.0,
                                              "T+4m": 104.0, "T+5m": 105.0,
                                              "T+10m": 110.0})])
    ])
    _write_regime_history_vm_schema(regime_csv, [
        ("2026-04-16", "ELEVATED", 26.0),
    ])
    answer = tr.answer_timing_by_regime_question(
        timing_root=timing_root,
        regime_history=regime_csv,
        threshold=1,
    )
    assert answer["status"] == "OK"
    elevated = next(a for a in answer["regime_aggregates"] if a["regime"] == "ELEVATED")
    # +$5 BUY rally savings at T+0 vs baseline T+5.
    assert elevated["by_offset"]["T+0m"]["mean_opportunity_usd"] == pytest.approx(5.0)


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
def test_answer_research_question_routes_timing_regime_intents(tmp_path, monkeypatch, question):
    """Each of these phrasings matches the timing_by_vix_regime capability.

    Under the new capability router, the artifact check runs *before* the
    tool call. Because ``timing_root`` and ``regime_history`` are pointed at
    paths that don't exist, the planner returns ``NEEDS_DATA`` with the
    missing globs named — that's the new and more useful behavior, replacing
    the prior NO_TIMING_DATA bubble-up. The intent and routed_to fields are
    preserved so the gateway and existing assertions still work.
    """
    # The planner's artifact check uses repo-root-relative globs by default
    # (`outputs/research/execution_timing/*/timing_summary.json`). Run from
    # a clean tmp_path so the real repo's outputs/ never satisfies the check.
    monkeypatch.chdir(tmp_path)
    result = call_tool(
        "answer_research_question",
        {
            "question": question,
            "timing_root": str(tmp_path / "missing"),
            "regime_history": str(tmp_path / "regime.csv"),
        },
    )
    assert result["intent"] == "timing_by_vix_regime"
    assert result["routed_to"] == "execution_timing_by_vix_regime"
    assert result["status"] == "NEEDS_DATA"
    assert "outputs/research/execution_timing/*/timing_summary.json" in result["missing_artifacts"]


@pytest.mark.parametrize("question", [
    "show me the largest drawdown",
    "",                       # empty input
    "VIX",                    # bare keyword — too underspecified
    "execution",              # bare keyword — too underspecified
    "hello world",            # genuinely off-topic
])
def test_answer_research_question_returns_unsupported_intent_for_off_topic(question):
    """Truly off-topic queries route to UNSUPPORTED_INTENT. The response now
    carries ``closest_capabilities`` (token-overlap suggestions) and the
    full ``available_intents`` list so the operator can self-serve."""
    result = call_tool("answer_research_question", {"question": question})
    assert result["status"] == "UNSUPPORTED_INTENT"
    assert result["intent"] is None
    assert result["routed_to"] is None
    assert "closest_capabilities" in result
    # available_intents is the discoverability surface for the gateway.
    assert any(
        intent["intent"] == "timing_by_vix_regime"
        for intent in result["available_intents"]
    )


@pytest.mark.parametrize("question,expected_intent", [
    # Phrasings that would have matched attribution / stable_window before
    # those capabilities shipped. All implemented capabilities are now
    # covered by NEEDS_DATA / OK paths in their own test files; nothing
    # currently maps to NEEDS_CAPABILITY.
    pytest.param("placeholder", "n/a", marks=pytest.mark.skip(reason="all capabilities now implemented; reinstate if a new stub is added to CAPABILITY_REGISTRY")),
])
def test_answer_research_question_returns_needs_capability_for_unbuilt(question, expected_intent):
    """Questions that match a registry capability but whose tool is not yet
    wired return ``NEEDS_CAPABILITY`` — distinct from UNSUPPORTED_INTENT.
    The response carries the matched capability and a suggested_next_build
    paragraph so a future implementer has a concrete scope."""
    result = call_tool("answer_research_question", {"question": question})
    assert result["status"] == "NEEDS_CAPABILITY"
    assert result["intent"] == expected_intent
    assert result["routed_to"] is None
    assert result["matched_capability"]["name"] == expected_intent
    assert result["suggested_next_build"]
    assert "tool" in result["suggested_next_build"].lower()


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
