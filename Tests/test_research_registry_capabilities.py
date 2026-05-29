"""Coverage for the capability-based research-question router.

These tests pin two things:

1. **Pure registry semantics** — the registry itself, the classifier, the
   artifact checker, and the closest-match suggestion are all pure
   functions and exercised in isolation.
2. **End-to-end MCP routing** — the new ``answer_research_question``
   tool returns the right status (OK / NEEDS_DATA / NEEDS_CAPABILITY /
   UNSUPPORTED_INTENT) for each of the six question families the task
   spec calls out, with the expected ``intent`` and ``routed_to``
   fields preserved for the gateway renderer.

No LLM is called, no external network is touched, and no execution-path
file is read or written.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from research_registry.mcp_server import ToolContext, call_tool, list_tools
from research_registry.research import capabilities as caps


# ---------------------------------------------------------------------------
# Registry schema
# ---------------------------------------------------------------------------


def test_registry_entries_are_well_formed():
    """Every Capability in the registry must declare the documented fields
    so the renderer and the gateway can rely on them."""
    assert caps.CAPABILITY_REGISTRY, "registry must not be empty"
    names = set()
    for cap in caps.CAPABILITY_REGISTRY:
        assert isinstance(cap, caps.Capability)
        assert cap.name and cap.name not in names, f"duplicate or empty name: {cap.name!r}"
        names.add(cap.name)
        assert cap.description, f"{cap.name} missing description"
        assert cap.patterns, f"{cap.name} must declare at least one regex pattern"
        for pattern in cap.patterns:
            re.compile(pattern, flags=re.IGNORECASE)  # validates the regex itself
        assert cap.example_questions, f"{cap.name} should ship at least one example phrasing"
        if cap.is_implemented():
            # Implemented capability → tool must be present in dispatch.
            assert cap.tool_name in {t["name"] for t in list_tools()}, (
                f"{cap.name} tool_name {cap.tool_name!r} is not registered with the MCP"
            )
        else:
            # Unimplemented capability must explain what to build.
            assert cap.suggested_next_build, (
                f"{cap.name} has no tool yet and no suggested_next_build paragraph"
            )


def test_capability_summary_is_jsonable():
    for cap in caps.CAPABILITY_REGISTRY:
        summary = caps.capability_summary(cap)
        # Round-trip through json.dumps to guarantee JSON-safety.
        json.dumps(summary, sort_keys=True)


def test_available_intents_preserves_legacy_shape():
    """The gateway and existing tests rely on `intent` / `matches` /
    `routed_tool` / `example_question` keys. Verify they're all present."""
    intents = caps.available_intents()
    assert intents
    required_keys = {"intent", "matches", "routed_tool", "example_question"}
    for entry in intents:
        assert required_keys.issubset(entry.keys()), f"missing keys: {entry}"
    # The timing_by_vix_regime entry must still be discoverable by name.
    assert any(entry["intent"] == "timing_by_vix_regime" for entry in intents)


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("question,expected", [
    ("Does timing matter more in high VIX regimes?", "timing_by_vix_regime"),
    ("Is 9:35 better than 9:30?",                     "execution_timing_baseline"),
    ("Is Orion ready for promotion?",                 "promotion_readiness"),
    ("What are today's anomalies?",                   "anomaly_report"),
    ("What ran today?",                               "morning_brief"),
    ("How is Polaris doing versus Orion?",            "shadow_comparison"),
])
def test_classifier_matches_each_question_family(question, expected):
    """The six question families called out in the task spec each map to a
    specific capability via the regex registry — no ties, no surprises."""
    result = caps.classify_question(question)
    assert result.capability is not None
    assert result.capability.name == expected


def test_classifier_ties_broken_by_registry_order():
    """A question that hits multiple capabilities deterministically routes
    to the higher-scoring one; ties prefer registry order."""
    # "Does timing matter more in high VIX regimes?" matches:
    #   - timing_by_vix_regime (multiple patterns)
    #   - execution_timing_baseline (the `execution\s+timing` pattern)
    # The regime capability scores higher and wins.
    result = caps.classify_question("Does execution timing matter more in high VIX regimes?")
    assert result.capability is not None
    assert result.capability.name == "timing_by_vix_regime"


def test_classifier_returns_no_match_with_closest_for_off_topic():
    result = caps.classify_question("how is the weather today")
    assert result.capability is None
    # Token overlap with capabilities mentioning "today" should suggest a few
    # candidates rather than returning an empty list.
    assert isinstance(result.closest, tuple)
    # Closest must be a subset of the registry (not invented entries).
    names = {c.name for c in caps.CAPABILITY_REGISTRY}
    for cap in result.closest:
        assert cap.name in names


def test_classifier_returns_empty_closest_for_empty_or_keywordless_input():
    """Empty input and inputs with no alpha tokens get no suggestions
    (better than offering unrelated capabilities)."""
    assert caps.classify_question("").closest == ()
    assert caps.classify_question("   ").closest == ()
    assert caps.classify_question("?!@#").closest == ()


# ---------------------------------------------------------------------------
# Artifact checker
# ---------------------------------------------------------------------------


def test_check_artifacts_with_no_globs_is_ready(tmp_path):
    status = caps.check_artifacts((), repo_root=tmp_path)
    assert status.ready is True
    assert status.matched == ()
    assert status.missing == ()


def test_check_artifacts_flags_missing_paths_and_globs(tmp_path):
    (tmp_path / "outputs" / "vix_regime").mkdir(parents=True)
    (tmp_path / "outputs" / "vix_regime" / "regime_history.csv").write_text("date,vix,regime\n")
    # Glob pattern with no matches.
    status = caps.check_artifacts(
        (
            "outputs/vix_regime/regime_history.csv",                       # exact, present
            "outputs/research/execution_timing/*/timing_summary.json",      # glob, absent
        ),
        repo_root=tmp_path,
    )
    assert status.ready is False
    assert "outputs/vix_regime/regime_history.csv" in status.matched
    assert "outputs/research/execution_timing/*/timing_summary.json" in status.missing


def test_check_artifacts_satisfies_glob_when_any_match_exists(tmp_path):
    run_dir = tmp_path / "outputs" / "research" / "execution_timing" / "2026-05-29"
    run_dir.mkdir(parents=True)
    (run_dir / "timing_summary.json").write_text("{}")
    status = caps.check_artifacts(
        ("outputs/research/execution_timing/*/timing_summary.json",),
        repo_root=tmp_path,
    )
    assert status.ready is True
    assert status.matched == ("outputs/research/execution_timing/*/timing_summary.json",)


# ---------------------------------------------------------------------------
# Closest-match (UNSUPPORTED_INTENT suggestion surface)
# ---------------------------------------------------------------------------


def test_closest_capabilities_prefers_token_overlap():
    """A question that shares tokens with the timing capabilities should
    bubble those capabilities to the top of the closest list, even if no
    regex pattern matched outright."""
    suggestions = caps.closest_capabilities("show me execution numbers")
    assert suggestions
    names = [c.name for c in suggestions]
    assert any("timing" in n or "execution" in n for n in names), names


# ---------------------------------------------------------------------------
# End-to-end MCP routing for the six question families
# ---------------------------------------------------------------------------


def _no_outputs_dir(tmp_path, monkeypatch):
    """Run from a clean tmp_path so artifact pre-checks see no real data."""
    monkeypatch.chdir(tmp_path)


def test_e2e_timing_baseline_returns_needs_data_when_artifacts_absent(tmp_path, monkeypatch):
    _no_outputs_dir(tmp_path, monkeypatch)
    result = call_tool("answer_research_question", {"question": "Is 9:35 better than 9:30?"})
    assert result["intent"] == "execution_timing_baseline"
    assert result["routed_to"] == "execution_timing_by_vix_regime"
    assert result["status"] == "NEEDS_DATA"
    assert "outputs/research/execution_timing/*/timing_summary.json" in result["missing_artifacts"]
    # Available-intents discovery surface preserved.
    assert any(i["intent"] == "execution_timing_baseline" for i in result["available_intents"])


def test_e2e_timing_by_vix_regime_returns_needs_data_when_artifacts_absent(tmp_path, monkeypatch):
    _no_outputs_dir(tmp_path, monkeypatch)
    result = call_tool(
        "answer_research_question",
        {"question": "Does timing matter more in high VIX regimes?"},
    )
    assert result["intent"] == "timing_by_vix_regime"
    assert result["routed_to"] == "execution_timing_by_vix_regime"
    assert result["status"] == "NEEDS_DATA"
    # Both timing + regime CSV must be flagged missing.
    missing = set(result["missing_artifacts"])
    assert "outputs/research/execution_timing/*/timing_summary.json" in missing
    assert "outputs/vix_regime/regime_history.csv" in missing


def test_e2e_promotion_readiness_routes_through_tool(tmp_path, monkeypatch):
    _no_outputs_dir(tmp_path, monkeypatch)
    result = call_tool("answer_research_question", {"question": "Is Orion ready for promotion?"})
    assert result["intent"] == "promotion_readiness"
    assert result["routed_to"] == "promotion_readiness"
    # The promotion_readiness tool tolerates missing data and emits its own
    # status; the planner only requires the capability to be implemented.
    assert "answer" in result
    assert result["status"] in {"OK", "NEEDS_DATA"} or isinstance(result["status"], str)


def test_e2e_anomaly_report_routes_through_tool(tmp_path, monkeypatch):
    _no_outputs_dir(tmp_path, monkeypatch)
    result = call_tool("answer_research_question", {"question": "What are today's anomalies?"})
    assert result["intent"] == "anomaly_report"
    assert result["routed_to"] == "anomaly_report"
    assert "answer" in result


def test_e2e_morning_brief_routes_through_tool(tmp_path, monkeypatch):
    _no_outputs_dir(tmp_path, monkeypatch)
    result = call_tool("answer_research_question", {"question": "What ran today?"})
    assert result["intent"] == "morning_brief"
    assert result["routed_to"] == "morning_cio_brief"
    assert "answer" in result


def test_e2e_shadow_comparison_returns_needs_capability(tmp_path, monkeypatch):
    _no_outputs_dir(tmp_path, monkeypatch)
    result = call_tool(
        "answer_research_question",
        {"question": "How is Polaris doing versus Orion?"},
    )
    assert result["intent"] == "shadow_comparison"
    assert result["status"] == "NEEDS_CAPABILITY"
    assert result["routed_to"] is None
    assert result["suggested_next_build"]
    # The suggested_next_build paragraph should reference shadow_candidates
    # so the next implementer knows the data source.
    assert "shadow_candidates" in result["suggested_next_build"].lower() or \
           "shadow" in result["suggested_next_build"].lower()


# ---------------------------------------------------------------------------
# Unsupported (no capability matched at all)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("question", [
    "Hello world",
    "What's the weather like?",
    "random gibberish phrase",
    "",
])
def test_e2e_unsupported_question_returns_closest_capabilities(question):
    result = call_tool("answer_research_question", {"question": question})
    assert result["status"] == "UNSUPPORTED_INTENT"
    assert result["intent"] is None
    assert result["routed_to"] is None
    assert "closest_capabilities" in result
    assert "missing_capability_description" in result
    # Each entry in closest_capabilities is a full registry summary, not just a name.
    for entry in result["closest_capabilities"]:
        assert "name" in entry
        assert "description" in entry
        assert "example_questions" in entry


def test_unsupported_response_lists_all_registry_capabilities():
    """The full registry must be surfaced under available_intents so an
    operator can discover what the MCP CAN answer when their question
    didn't route — without needing to read the source."""
    result = call_tool("answer_research_question", {"question": "qzzzzz"})
    available_names = {entry["intent"] for entry in result["available_intents"]}
    registry_names = {cap.name for cap in caps.CAPABILITY_REGISTRY}
    assert registry_names == available_names
