"""Capability-based research-question router.

This module replaces the original narrow regex shim in
``answer_research_question`` with a small, declarative, machine-readable
registry of *capabilities*. Each capability describes:

* what kinds of questions it can answer (``patterns`` — case-insensitive
  regex strings),
* which existing MCP tool to invoke (``tool_name`` — ``None`` if the
  capability is recognised but not yet implemented),
* which artifacts must exist on disk for the tool to produce a real
  answer (``required_artifact_globs`` — paths or glob patterns under the
  repo root),
* the schema of the tool's output (``output_fields``),
* honest limitations (``limitations``),
* example phrasings (``example_questions``), and
* — for unimplemented capabilities — a one-paragraph
  ``suggested_next_build`` that tells the operator exactly what would
  need to be wired up.

The router that consumes this registry is deterministic, regex-driven,
and never calls an LLM. It returns one of four high-level statuses:

* ``OK`` — a capability matched and its tool produced a real answer.
* ``NEEDS_DATA`` — a capability matched but at least one required
  artifact is missing on disk. The response names the missing paths
  and the next command.
* ``NEEDS_CAPABILITY`` — a capability matched but no tool is wired yet.
  The response includes the suggested next build paragraph.
* ``UNSUPPORTED_INTENT`` — no capability matched. The response includes
  the top-N closest capabilities ranked by token overlap, so the
  operator can either rephrase or file a ticket for a new capability.

Adding a new question family is a one-entry change to
``CAPABILITY_REGISTRY``. Adding a new *implemented* capability is the
same plus a new MCP tool function + schema entry; everything else
(routing, artifact-checking, error envelope) is reused.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Capability:
    name: str
    description: str
    patterns: tuple[str, ...]
    required_artifact_globs: tuple[str, ...] = ()
    tool_name: Optional[str] = None
    tool_kwargs: dict[str, Any] = field(default_factory=dict)
    output_fields: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    example_questions: tuple[str, ...] = ()
    suggested_next_build: Optional[str] = None

    def is_implemented(self) -> bool:
        return self.tool_name is not None


@dataclass(frozen=True)
class ClassificationResult:
    capability: Optional[Capability]
    score: int  # number of regex pattern matches
    closest: tuple[Capability, ...] = ()


# ---------------------------------------------------------------------------
# Registry — single source of truth for what this MCP can / cannot answer.
# ---------------------------------------------------------------------------


CAPABILITY_REGISTRY: tuple[Capability, ...] = (
    Capability(
        name="timing_by_vix_regime",
        description=(
            "Stratify execution-timing replay opportunities by VIX regime. "
            "Joins outputs/research/execution_timing/<RUN_DATE>/per_trade_timing.json "
            "to outputs/vix_regime/regime_history.csv on execution_date and "
            "returns per-regime, per-offset mean/median opportunity in USD and bps."
        ),
        patterns=(
            r"timing.*(vix|regime)",
            r"(vix|regime).*timing",
            r"high\s*vix.*timing",
            r"timing.*high\s*vix",
            r"execution[-_\s]*timing.*(vix|regime)",
        ),
        required_artifact_globs=(
            "outputs/research/execution_timing/*/timing_summary.json",
            "outputs/vix_regime/regime_history.csv",
        ),
        tool_name="execution_timing_by_vix_regime",
        tool_kwargs={},
        output_fields=("regime_aggregates", "coverage", "baseline_offset"),
        limitations=(
            "Phase-1 fill model only (open of bar at-or-after the cutoff; no half-spread).",
            "Buckets with fewer than the configured threshold of days flagged insufficient_sample.",
        ),
        example_questions=(
            "Does execution timing matter more in high-VIX regimes?",
            "Is the timing penalty worse when VIX is elevated?",
            "How does regime affect execution timing?",
        ),
    ),
    Capability(
        name="execution_timing_summary",
        description=(
            "Aggregate (non-regime-stratified) summary of execution-timing "
            "replay opportunities at each offset T+0..T+10m vs the 9:35 "
            "baseline, with a conservative retain / earlier-is-better / "
            "insufficient_evidence recommendation."
        ),
        patterns=(
            r"9:?30",
            r"9:?35",
            r"9:?40",
            r"\bbar open\b",
            r"\bnear[- ]?open\b",
            r"T\+\d+m",
            r"execution\s+(timing|minute|offset|time)",
            r"is\s+\d:?\d{2}\s+better",
            r"best\s+execution\s+time",
            r"best\s+(timing|offset)",
            r"compare\s+9:?\d{2}\s+(and|vs|with|to)\s+9:?\d{2}",
        ),
        required_artifact_globs=(
            "outputs/research/execution_timing/*/timing_summary.json",
        ),
        tool_name="execution_timing_summary",
        tool_kwargs={},
        output_fields=(
            "by_offset",
            "best_offset",
            "highlighted_offsets",
            "recommendation",
            "recommendation_reason",
            "days_replayed",
            "baseline_offset",
        ),
        limitations=(
            "Aggregates across all regimes; for a regime-stratified view use "
            "timing_by_vix_regime instead.",
            "Recommendation requires >= 5 replayed days; below that returns "
            "insufficient_evidence regardless of point estimates.",
        ),
        example_questions=(
            "Is 9:35 better than 9:30?",
            "What is the best execution time?",
            "Compare 9:30 and 9:35 execution timing.",
        ),
    ),
    Capability(
        name="promotion_readiness",
        description=(
            "Assess challenger strategy readiness for promotion from shadow to "
            "live based on persisted shadow artifacts."
        ),
        patterns=(
            r"ready\s+for\s+(promotion|promote|live|production)",
            r"(can|should|when)\s+(we\s+)?promote",
            r"promotion\s+ready",
            r"(orion|polaris|lyra|leda)\s+ready",
            r"is\s+(orion|polaris|lyra|leda)\s+ready",
        ),
        required_artifact_globs=(),
        tool_name="promotion_readiness",
        tool_kwargs={},
        output_fields=("current_leader", "observation_count", "recommendation"),
        limitations=(
            "Assesses challenger generically; per-strategy filtering by name is "
            "not yet supported — the strategy name in the question is informational.",
        ),
        example_questions=(
            "Is Orion ready for promotion?",
            "Can we promote the shadow strategy?",
        ),
    ),
    Capability(
        name="anomaly_report",
        description="Surface operational and research anomalies from persisted artifacts.",
        patterns=(
            r"anomal(y|ies)",
            r"what'?s?\s+(wrong|broken|stale)",
            r"today'?s?\s+(issue|problem|alert)",
            r"recent\s+findings?",
            r"any.*(issue|problem|alert).*today",
        ),
        required_artifact_globs=(),
        tool_name="anomaly_report",
        tool_kwargs={},
        output_fields=("findings",),
        limitations=(),
        example_questions=(
            "What are today's anomalies?",
            "Anything broken or stale today?",
        ),
    ),
    Capability(
        name="morning_brief",
        description="Compact daily operator brief of artifact-backed intelligence.",
        patterns=(
            r"what\s+ran\s+today",
            r"morning\s+brief",
            r"today'?s?\s+(brief|summary|state|status)",
            r"did\s+\w+\s+run",
            r"daily\s+brief",
            r"what\s+happened\s+today",
        ),
        required_artifact_globs=(),
        tool_name="morning_cio_brief",
        tool_kwargs={},
        output_fields=("artifact_summary", "strategy_leadership", "regime_context"),
        limitations=(),
        example_questions=(
            "What ran today?",
            "Give me today's morning brief.",
        ),
    ),
    Capability(
        name="shadow_comparison",
        description=(
            "Pairwise / multi-strategy shadow-portfolio comparison from "
            "outputs/shadow_candidates/<DATE>/shadow_evaluation.json. "
            "Per-strategy NAV, cumulative return, excess vs SPY, turnover, "
            "concentration, max drawdown when available; pairwise overlap "
            "when two strategies are named. Strategy name parse restricted "
            "to polaris|orion|lyra|leda."
        ),
        patterns=(
            r"(polaris|orion|lyra|leda)\s+(vs|versus|against|compared)",
            r"compare\s+(strateg|sleev|polaris|orion|lyra|leda)",
            r"shadow\s+(vs|versus|comparison)",
            r"how\s+is\s+(polaris|orion|lyra|leda)\s+(doing|performing)",
            r"(live|shadow)\s+vs\s+(live|shadow)",
            r"which\s+strateg\w+\s+(is\s+)?(performing|best|leading|ahead)",
            r"best\s+(performing\s+)?strateg",
            r"strateg\w+\s+leader",
        ),
        required_artifact_globs=(),
        tool_name="shadow_comparison",
        tool_kwargs={},
        output_fields=(
            "panels",
            "pairwise_overlap",
            "leader_by_cumulative_return",
            "leader_by_excess_vs_spy",
            "leader_summary",
            "available_strategies",
            "requested_strategies",
            "missing_strategies",
        ),
        limitations=(
            "Reports the metrics that the shadow_evaluation.json file actually "
            "populates; never invents values. Null fields are reported as null "
            "and surfaced in per-strategy unavailable_metrics.",
            "Strategy slugs are restricted to caerus_{polaris,orion,lyra,leda}; "
            "unknown names → NEEDS_DATA with missing_strategies populated.",
        ),
        example_questions=(
            "How is Polaris doing versus Orion?",
            "Compare Polaris and Orion.",
            "Which strategy is performing best?",
        ),
    ),
    Capability(
        name="attribution_analysis",
        description=(
            "Per-strategy performance attribution. Reads "
            "outputs/attribution/<DATE>/{attribution_summary,contribution_report,"
            "factor_exposure,regime_performance_breakdown}.json. Returns top "
            "contributors / detractors, top drawdown contributors, factor "
            "exposures (market beta, momentum, vol, sector concentration), "
            "regime-stratified performance, and a deterministic narrative. "
            "Two strategy names in the question add a comparison block."
        ),
        patterns=(
            r"attribut(ion|e)",
            r"alpha\s+(contribution|attribut|breakdown)",
            r"performance\s+(attribut|driver|breakdown)",
            r"learning\s+loop",
            r"what\s+drove\b.*?(return|alpha|loss|gain|performance)",
            r"what\s+drove\s+returns",
            r"factor\s+(decomposition|attribution)",
            r"what\s+factors?\s+(drove|driven|contribut)",
            r"what\s+(contributed|hurt|helped)\s+(most\s+)?to?\s*performance",
            r"(top|biggest|largest)\s+(contributors?|detractors?)",
            r"what\s+hurt\s+(performance|returns?)",
            r"why\s+did\s+(polaris|orion|lyra|leda)\s+(outperform|underperform|beat|lose)",
        ),
        required_artifact_globs=(
            "outputs/attribution/*/attribution_summary.json",
        ),
        tool_name="attribution_analysis",
        tool_kwargs={},
        output_fields=(
            "panels",
            "comparison",
            "leader_by_return",
            "narrative",
            "available_strategies",
            "requested_strategies",
            "missing_strategies",
        ),
        limitations=(
            "Attribution is current-book trailing exposure (~21 days), not historical realised positions.",
            "Selection alpha vs factors is partial; growth/value and quality/profitability tilts often UNAVAILABLE in source.",
            "Strategy slugs restricted to caerus_{polaris,orion,lyra,leda}; unknown names → NEEDS_DATA.",
            "Never invents missing metrics — null fields are surfaced via per-strategy unavailable_metrics.",
        ),
        example_questions=(
            "What drove returns?",
            "Why did Orion outperform Polaris?",
            "What contributed most to performance?",
            "What hurt performance?",
            "What factors drove returns?",
        ),
    ),
    Capability(
        name="stable_window_evaluation",
        description=(
            "Evaluate strategy stability across rolling / random backtest "
            "windows. Reads outputs/research/random_windows_*.csv for "
            "dispersion (p10/median/p90 of CAGR, max drawdown, Sharpe, "
            "ulcer index) and consistency (fraction of windows with "
            "positive return); reads outputs/research/stable_window_evaluation/ "
            "for promotion-grade window-validity counts. Flags "
            "insufficient_sample when n_windows < 30."
        ),
        patterns=(
            r"stable\s+window",
            r"rolling\s+window",
            r"window\s+evaluation",
            r"random\s+window",
            r"window\s+sweep",
            r"start[- ]date\s+sensitivity",
            r"backtest\s+(dispersion|stability|consistency)",
            r"how\s+(stable|consistent|robust)\s+is\s+",
        ),
        required_artifact_globs=(
            "outputs/research/random_windows_*.csv",
        ),
        tool_name="stable_window_evaluation",
        tool_kwargs={},
        output_fields=(
            "policy_panels",
            "promotion_validity",
            "confidence_caveats",
            "narrative",
        ),
        limitations=(
            "Requires random-window backtest CSVs; absent → NO_WINDOW_DATA.",
            "Windows are in-sample to whatever policy generated them — treat as descriptive of historical sensitivity, not as out-of-sample evidence.",
            "promotion_validity counts may show zero valid days when no live executed sessions qualify yet; the random-window panel is the primary signal.",
        ),
        example_questions=(
            "How does the strategy perform across random windows?",
            "Stable window Sharpe distribution.",
            "How consistent is the strategy across backtest windows?",
            "Start-date sensitivity for the 3-year backtest.",
        ),
    ),
    Capability(
        name="regime_intelligence",
        description=(
            "Current VIX regime state, position scaling, and regime history "
            "context — surfaced via the morning brief's regime block."
        ),
        patterns=(
            r"current\s+regime",
            r"what\s+regime",
            r"regime\s+(state|now|currently|today)",
            r"volatility\s+regime",
            r"vix\s+(now|current|today|state)",
        ),
        required_artifact_globs=(
            "outputs/vix_regime/regime_current.json",
        ),
        tool_name="morning_cio_brief",
        tool_kwargs={},
        output_fields=("regime_context",),
        limitations=(
            "Returns the full morning brief; the regime block is one section. "
            "A dedicated regime-only tool could surface just regime_current.json "
            "+ a short history slice."
        ),
        example_questions=(
            "What is the current VIX regime?",
            "Are we in a high-volatility regime?",
        ),
    ),
)


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------


def _score_capability(question: str, capability: Capability) -> int:
    """Return the count of regex patterns that match ``question``.

    Patterns are compiled lazily and cached on the capability tuple; the
    cache is a module-level dict so capability instances stay frozen.
    """
    text = question or ""
    score = 0
    for pattern in capability.patterns:
        if re.search(pattern, text, flags=re.IGNORECASE):
            score += 1
    return score


def classify_question(
    question: str,
    registry: tuple[Capability, ...] = CAPABILITY_REGISTRY,
) -> ClassificationResult:
    """Return the best-matching capability for ``question``, or no match.

    Ties are broken by **registry order** so routing is deterministic. If
    no pattern matches anywhere, ``capability`` is ``None`` and the
    ``closest`` field carries up to three suggestions ranked by simple
    token-overlap heuristic.
    """
    text = (question or "").strip()
    if not text:
        return ClassificationResult(capability=None, score=0, closest=())

    best_score = 0
    best: Optional[Capability] = None
    for capability in registry:
        score = _score_capability(text, capability)
        if score > best_score:
            best_score = score
            best = capability

    if best is not None:
        return ClassificationResult(capability=best, score=best_score, closest=())

    closest = tuple(closest_capabilities(text, registry=registry))
    return ClassificationResult(capability=None, score=0, closest=closest)


def closest_capabilities(
    question: str,
    *,
    k: int = 3,
    registry: tuple[Capability, ...] = CAPABILITY_REGISTRY,
) -> list[Capability]:
    """Heuristic suggestion when no regex matches.

    Tokenises the question into lowercase alpha words and counts overlap
    against each capability's description + example_questions. Returns
    the top ``k`` by overlap, then by registry order on ties. If no
    overlap, returns ``[]`` — empty rather than arbitrary picks, so the
    response doesn't suggest unrelated capabilities.
    """
    tokens = set(re.findall(r"[a-zA-Z]+", (question or "").lower()))
    if not tokens:
        return []
    scored: list[tuple[int, int, Capability]] = []
    for index, capability in enumerate(registry):
        haystack = " ".join(
            (capability.description,) + capability.example_questions
        ).lower()
        haystack_tokens = set(re.findall(r"[a-zA-Z]+", haystack))
        overlap = len(tokens & haystack_tokens)
        if overlap > 0:
            # Negative index to break ties by ascending registry order.
            scored.append((-overlap, index, capability))
    scored.sort()
    return [item[2] for item in scored[:k]]


# ---------------------------------------------------------------------------
# Artifact availability
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ArtifactStatus:
    ready: bool
    matched: tuple[str, ...]
    missing: tuple[str, ...]


def check_artifacts(
    globs: tuple[str, ...],
    *,
    repo_root: Path | None = None,
) -> ArtifactStatus:
    """For each glob, confirm at least one filesystem match exists.

    A glob is a path relative to ``repo_root`` (default: ``Path.cwd()``).
    Patterns without wildcards are checked via :meth:`Path.exists`; glob
    patterns are checked via :meth:`Path.glob`. Returns a structured
    result that the router converts into ``NEEDS_DATA``.
    """
    root = Path(repo_root) if repo_root is not None else Path.cwd()
    matched: list[str] = []
    missing: list[str] = []
    for pattern in globs:
        has_glob = any(ch in pattern for ch in "*?[")
        if has_glob:
            hits = list(root.glob(pattern))
            if hits:
                matched.append(pattern)
            else:
                missing.append(pattern)
        else:
            candidate = root / pattern
            if candidate.exists():
                matched.append(pattern)
            else:
                missing.append(pattern)
    return ArtifactStatus(
        ready=not missing,
        matched=tuple(matched),
        missing=tuple(missing),
    )


# ---------------------------------------------------------------------------
# Serialisation helpers — used by the MCP wrapper so the response is JSON-safe.
# ---------------------------------------------------------------------------


def capability_summary(capability: Capability) -> dict[str, Any]:
    """JSON-safe snapshot of a capability, suitable for the MCP response."""
    return {
        "name": capability.name,
        "description": capability.description,
        "patterns": list(capability.patterns),
        "required_artifact_globs": list(capability.required_artifact_globs),
        "tool_name": capability.tool_name,
        "tool_kwargs": dict(capability.tool_kwargs),
        "output_fields": list(capability.output_fields),
        "limitations": list(capability.limitations),
        "example_questions": list(capability.example_questions),
        "suggested_next_build": capability.suggested_next_build,
        "is_implemented": capability.is_implemented(),
    }


def available_intents(
    registry: tuple[Capability, ...] = CAPABILITY_REGISTRY,
) -> list[dict[str, Any]]:
    """Back-compat shape for the existing answer_research_question response.

    The legacy field was called ``available_intents`` with ``intent`` /
    ``matches`` / ``routed_tool`` / ``example_question`` keys. We preserve
    that contract here so existing tests + the gateway renderer keep
    working without changes.
    """
    out: list[dict[str, Any]] = []
    for capability in registry:
        first_example = capability.example_questions[0] if capability.example_questions else None
        out.append(
            {
                "intent": capability.name,
                "matches": list(capability.example_questions),
                "routed_tool": capability.tool_name,
                "example_question": first_example,
                "is_implemented": capability.is_implemented(),
                "description": capability.description,
            }
        )
    return out
