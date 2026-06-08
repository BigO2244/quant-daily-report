"""Strategy-aware promotion-readiness analysis.

Backs the strategy-aware extension of the ``promotion_readiness`` MCP
tool. Reads existing on-disk artifacts only and never invents metrics:

* ``outputs/shadow_candidates/<DATE>/shadow_evaluation.json`` — the
  per-strategy panel with nav, returns, excess vs SPY, drawdown,
  realized vol, turnover, concentration, ``data_status``.
* ``outputs/shadow_candidates/<DATE>/comparison.json`` — pairwise
  overlap (optional, used for context).
* ``outputs/shadow_candidates/<DATE>/promotion_readiness.json``
  (FR-028 Phase C sidecar) — when present, its ``strategies`` block
  carries authoritative ``readiness_state``, ``confidence``,
  ``reason_codes``, and ``valid_observation_windows``.
* ``outputs/shadow_candidates/<DATE>/<strategy>/stability_analysis.json``
  — per-strategy rolling-window metrics + flags like
  ``INSUFFICIENT_VALID_DAYS``.

Recommendation tiers (deterministic, no LLM):
    promote > hold > research_only > insufficient_evidence

When the Phase C sidecar names a ``readiness_state`` for a strategy
that state maps directly to a recommendation (CANDIDATE_FOR_CAPITAL
→ promote; CONTINUE_SHADOW / EMERGING_CANDIDATE → hold; OBSERVE /
NOT_READY → research_only). When the sidecar is absent we derive
the recommendation from shadow_evaluation metrics with conservative
gating (positive excess vs SPY + non-null drawdown + non-null
realized vol + sufficient valid observation windows). Any missing
gating input is surfaced as an explicit ``blocker`` so the operator
sees exactly what's stopping promotion.

Question parsing reuses the registry-backed security-selection strategy
list from ``shadow_comparison.py``.
Unknown names → NEEDS_DATA with ``missing_strategies`` populated.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from research_registry.research.shadow_comparison import (
    BENCHMARK_SLUG,
    KNOWN_STRATEGY_NAMES,
    parse_strategy_names,
    strategy_slug,
)

DEFAULT_OUTPUTS_ROOT = Path("outputs")
DEFAULT_SHADOW_ROOT = Path("outputs/shadow_candidates")
DEFAULT_NAV_SERIES_PATH = Path("outputs/shadow_candidates/performance/shadow_nav_series.csv")
DEFAULT_STABLE_WINDOW_ROOT = Path("outputs/research/stable_window_evaluation")

# Minimum valid observation windows required before we will recommend
# "promote" off the metric-derived path (i.e. when no Phase C sidecar
# is present). Below this, the best we can recommend is "hold".
MIN_VALID_OBSERVATION_WINDOWS = 20

# Recommendation ranking — used to pick "closest to promotion".
RECOMMENDATION_RANK = {
    "promote": 4,
    "hold": 3,
    "research_only": 2,
    "insufficient_evidence": 1,
    "unknown": 0,
}

# Map Phase C readiness_state → our recommendation tier.
_PHASE_C_STATE_TO_RECOMMENDATION: dict[str, str] = {
    "CANDIDATE_FOR_CAPITAL": "promote",
    "EMERGING_CANDIDATE": "hold",
    "CONTINUE_SHADOW": "hold",
    "OBSERVE": "research_only",
    "NOT_READY": "research_only",
}

_DATE_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ---------------------------------------------------------------------------
# Discovery + IO
# ---------------------------------------------------------------------------


def _safe_json(path: Path) -> Optional[dict[str, Any]]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def select_latest_shadow_date(shadow_root: Path = DEFAULT_SHADOW_ROOT) -> Optional[Path]:
    """Return the latest ``<DATE>`` directory with a ``shadow_evaluation.json``.

    Prefers an explicit ``latest`` alias when it has the required file;
    otherwise picks the lexicographically-highest ISO date directory.
    """
    if not shadow_root.exists() or not shadow_root.is_dir():
        return None
    alias = shadow_root / "latest"
    if alias.exists() and (alias / "shadow_evaluation.json").exists():
        return alias
    dated = [
        p for p in shadow_root.iterdir()
        if p.is_dir() and _DATE_DIR_RE.match(p.name)
        and (p / "shadow_evaluation.json").exists()
    ]
    if not dated:
        return None
    return sorted(dated, key=lambda p: p.name)[-1]


def _coerce_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Per-strategy metric extraction
# ---------------------------------------------------------------------------


def _extract_evaluation_metrics(raw: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "data_status": raw.get("data_status"),
        "data_reason": raw.get("data_reason"),
        "nav": _coerce_float(raw.get("nav")),
        "daily_return": _coerce_float(raw.get("daily_return")),
        "cumulative_return": _coerce_float(raw.get("cumulative_return")),
        "excess_return_vs_spy": _coerce_float(raw.get("excess_return_vs_spy")),
        "max_drawdown": _coerce_float(raw.get("max_drawdown")),
        "realized_volatility_ann": _coerce_float(raw.get("realized_volatility_ann")),
        "avg_turnover": _coerce_float(raw.get("avg_turnover")),
        "avg_top_3_concentration": _coerce_float(raw.get("avg_top_3_concentration")),
        "rolling_count_of_valid_days": _coerce_float(raw.get("rolling_count_of_valid_days")),
    }


def _extract_stability(stability_payload: Optional[Mapping[str, Any]]) -> dict[str, Any]:
    """Pull rolling-window valid_days + flags from per-strategy
    stability_analysis.json. Returns ``{}`` when the file is absent."""
    if not isinstance(stability_payload, Mapping):
        return {}
    flags = list(stability_payload.get("flags") or [])
    rolling = stability_payload.get("rolling_windows") or {}
    rolling_summary: dict[str, dict[str, Any]] = {}
    for window_key in ("10d", "30d"):
        block = rolling.get(window_key)
        if not isinstance(block, Mapping):
            continue
        rolling_summary[window_key] = {
            "valid_days": _coerce_float(block.get("valid_days")),
            "avg_turnover": _coerce_float(block.get("avg_turnover")),
            "max_turnover": _coerce_float(block.get("max_turnover")),
            "excess_return_vs_spy": _coerce_float(block.get("excess_return_vs_spy")),
            "constituent_change_count": _coerce_float(block.get("constituent_change_count")),
            "avg_top_3_concentration": _coerce_float(block.get("avg_top_3_concentration")),
            "top_position_contribution_share": _coerce_float(
                block.get("top_position_contribution_share")
            ),
        }
    return {
        "stability_status": stability_payload.get("status"),
        "stability_flags": flags,
        "rolling_windows": rolling_summary,
    }


def _name_to_short_slug(slug: str) -> str:
    """``caerus_polaris`` → ``polaris``; pass-through for already-short input."""
    if slug.startswith("caerus_"):
        return slug.split("_", 1)[1]
    return slug


def _load_per_strategy_stability(
    date_dir: Path,
    slug: str,
) -> Optional[dict[str, Any]]:
    """Read ``<date_dir>/<short_slug>/stability_analysis.json`` if present."""
    short = _name_to_short_slug(slug)
    path = date_dir / short / "stability_analysis.json"
    if not path.exists():
        return None
    return _safe_json(path)


# ---------------------------------------------------------------------------
# Gating + recommendation
# ---------------------------------------------------------------------------


def _derive_recommendation(
    *,
    metrics: Mapping[str, Any],
    phase_c_state: Optional[str],
    phase_c_confidence: Optional[str],
    phase_c_reason_codes: Iterable[str],
    valid_observation_windows: int,
    stability_flags: Iterable[str],
) -> tuple[str, str, list[str], list[str], str]:
    """Return ``(recommendation, confidence, reason_codes, blockers, explanation)``.

    Phase C sidecar wins when present. Otherwise we derive from
    shadow_evaluation metrics with conservative gating.
    """
    reason_codes: list[str] = list(phase_c_reason_codes or [])
    blockers: list[str] = []
    explanation_parts: list[str] = []

    if phase_c_state:
        rec = _PHASE_C_STATE_TO_RECOMMENDATION.get(phase_c_state, "insufficient_evidence")
        reason_codes.insert(0, f"phase_c_state:{phase_c_state}")
        explanation_parts.append(
            f"Phase C readiness sidecar marks state={phase_c_state}"
            + (f" (confidence={phase_c_confidence})" if phase_c_confidence else "")
            + "."
        )
        if rec != "promote":
            blockers.append(f"phase_c_state:{phase_c_state}")
        confidence = phase_c_confidence or "MODERATE"
        return rec, confidence, reason_codes, blockers, " ".join(explanation_parts)

    # No Phase C → derive from metrics.
    data_status = metrics.get("data_status")
    excess = metrics.get("excess_return_vs_spy")
    drawdown = metrics.get("max_drawdown")
    realized_vol = metrics.get("realized_volatility_ann")
    flags = list(stability_flags or [])

    # Hard stop: data unavailable.
    if (data_status not in (None, "OK")) and (data_status != "OK"):
        blockers.append(f"data_status:{data_status}")
    if excess is None:
        blockers.append("metric_unavailable:excess_return_vs_spy")

    if blockers and (excess is None or data_status not in (None, "OK")):
        explanation_parts.append(
            "Insufficient evidence: required metrics are missing or data_status "
            "is not OK on the latest shadow_evaluation snapshot."
        )
        return (
            "insufficient_evidence",
            "LOW",
            reason_codes,
            blockers,
            " ".join(explanation_parts),
        )

    # Excess is present.
    if excess is not None and excess <= 0:
        blockers.append("negative_excess_vs_spy")
        reason_codes.append("negative_excess_vs_spy")
        explanation_parts.append(
            f"Excess return vs SPY is {excess:+.4f}; strategy is not currently "
            "beating the benchmark."
        )
        return "research_only", "LOW", reason_codes, blockers, " ".join(explanation_parts)

    # Positive excess — gate on drawdown / vol / observation window.
    if realized_vol is None:
        blockers.append("metric_unavailable:realized_volatility_ann")
    if drawdown is None:
        blockers.append("metric_unavailable:max_drawdown")
    if valid_observation_windows < MIN_VALID_OBSERVATION_WINDOWS:
        blockers.append(
            f"insufficient_observation_window:{valid_observation_windows}/{MIN_VALID_OBSERVATION_WINDOWS}"
        )
    if "INSUFFICIENT_VALID_DAYS" in flags:
        blockers.append("stability_flag:INSUFFICIENT_VALID_DAYS")

    if excess is not None:
        explanation_parts.append(f"Excess return vs SPY is {excess:+.4f}.")
    if blockers:
        explanation_parts.append("Hold pending: " + ", ".join(blockers) + ".")
        return "hold", "LOW", reason_codes, blockers, " ".join(explanation_parts)

    explanation_parts.append(
        f"All gating metrics available; excess vs SPY positive across "
        f"{valid_observation_windows} valid observation windows."
    )
    return "promote", "MODERATE", reason_codes, blockers, " ".join(explanation_parts)


# ---------------------------------------------------------------------------
# Panel + answer assembly
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Observation evidence counting (distinct from Phase C strict count)
# ---------------------------------------------------------------------------
#
# The legacy `valid_observation_windows` field counts ONLY the FR-028
# Phase C strict observation windows (execution_status=EXECUTED AND
# reconciliation_status=OK_RECONCILED AND data_status=OK across all
# strategies). That count remains the bar for capital promotion. But
# operators routinely ask "do we have any observation evidence?" and
# the legacy field's 0 reads as "no evidence at all" when in fact we
# typically have hundreds or thousands of backtest NAV days.
#
# The three counts below are additive — they expose distinct evidence
# tiers without weakening the capital-promotion governance threshold.
#
#   valid_shadow_observation_days  per-strategy count of backtest NAV
#                                  days the strategy was actually trading
#                                  (non-zero daily return). Supports
#                                  behavioral analysis. NOT a substitute
#                                  for live execution evidence.
#
#   valid_live_execution_days      count of dated <DATE>/ shadow_candidates
#                                  dirs whose shadow_evaluation.json
#                                  shows data_status == OK for the
#                                  strategy. Supports operational
#                                  observation but not strict Phase C
#                                  promotion.
#
#   promotion_evidence_days        the strict Phase C count from
#                                  outputs/research/stable_window_evaluation/
#                                  latest_{loose,strict}.json — same
#                                  values surfaced by the
#                                  stable_window_evaluation tool. THIS
#                                  is the bar for capital allocation.
# ---------------------------------------------------------------------------


def _count_shadow_nav_days(
    nav_path: Path,
    strategy_slugs: Iterable[str],
) -> tuple[dict[str, int], Optional[tuple[str, str]]]:
    """Per-strategy count of NAV-series days with non-zero return.

    Returns ``({slug: count}, (date_start, date_end))`` or ``({}, None)``
    when the NAV CSV is absent or unreadable.
    """
    out: dict[str, int] = {slug: 0 for slug in strategy_slugs}
    if not nav_path.exists():
        return out, None
    try:
        # Parse minimally to avoid forcing a pandas dependency on this
        # helper (the rest of the module is stdlib + json).
        import csv as _csv
        with nav_path.open(encoding="utf-8") as fh:
            reader = _csv.DictReader(fh)
            if not reader.fieldnames or "date" not in reader.fieldnames:
                return out, None
            tracked = [s for s in strategy_slugs if s in reader.fieldnames]
            prev: dict[str, float] = {s: float("nan") for s in tracked}
            first_date: Optional[str] = None
            last_date: Optional[str] = None
            for row in reader:
                d = (row.get("date") or "").strip()
                if not d:
                    continue
                first_date = first_date or d
                last_date = d
                for slug in tracked:
                    raw = row.get(slug)
                    if raw in (None, "", "nan"):
                        continue
                    try:
                        nav = float(raw)
                    except (TypeError, ValueError):
                        continue
                    prior = prev[slug]
                    if not math.isnan(prior) and nav != prior:
                        out[slug] = out.get(slug, 0) + 1
                    prev[slug] = nav
        date_range = (first_date, last_date) if first_date and last_date else None
        return out, date_range
    except (OSError, UnicodeError):
        return out, None


def _count_live_execution_days(
    shadow_root: Path,
    strategy_slugs: Iterable[str],
) -> dict[str, int]:
    """Per-strategy count of dated <DATE>/ shadow_candidates dirs whose
    shadow_evaluation.json reports data_status == OK for that strategy.
    Distinct from Phase C: doesn't require execution OR reconciliation.
    """
    out: dict[str, int] = {slug: 0 for slug in strategy_slugs}
    if not shadow_root.exists() or not shadow_root.is_dir():
        return out
    for child in shadow_root.iterdir():
        if not child.is_dir():
            continue
        if not (len(child.name) == 10 and child.name[4] == "-" and child.name[7] == "-"):
            continue
        payload = _safe_json(child / "shadow_evaluation.json")
        if not isinstance(payload, Mapping):
            continue
        strategies = payload.get("strategies") or {}
        for slug in strategy_slugs:
            entry = strategies.get(slug) or {}
            if entry.get("data_status") == "OK":
                out[slug] = out.get(slug, 0) + 1
    return out


def _read_phase_c_evidence_counts(
    stable_window_root: Path,
) -> dict[str, Any]:
    """Read the strict Phase C counts from latest_{loose,strict}.json.

    Returns a dict with per-mode counts and the source paths. Missing
    files map to count=0 with `source_path_missing=True` so the
    operator sees exactly why the strict count is zero.
    """
    out: dict[str, Any] = {}
    for mode in ("loose", "strict"):
        path = stable_window_root / f"latest_{mode}.json"
        payload = _safe_json(path)
        if not isinstance(payload, Mapping):
            out[mode] = {
                "valid_days_since_inception": 0,
                "valid_days_stable_window": 0,
                "shadow_only_days_since_inception": 0,
                "diagnostic_excluded_count": 0,
                "source_path": str(path),
                "source_path_missing": True,
            }
            continue
        out[mode] = {
            "valid_days_since_inception": len(payload.get("valid_days_since_inception") or []),
            "valid_days_stable_window": len(payload.get("valid_days_stable_window") or []),
            "shadow_only_days_since_inception": len(payload.get("shadow_only_days_since_inception") or []),
            "diagnostic_excluded_count": len(payload.get("diagnostic_excluded_since") or []),
            "source_path": str(path),
            "source_path_missing": False,
        }
    return out


_EVIDENCE_EXPLANATION = {
    "valid_shadow_observation_days": (
        "Per-strategy count of backtest NAV-series days where the strategy "
        "was actually trading (non-zero daily return). Supports behavioral "
        "analysis (correlation, drawdown co-movement). NOT a substitute "
        "for live execution evidence — does NOT count toward capital-"
        "promotion governance."
    ),
    "valid_live_execution_days": (
        "Count of dated outputs/shadow_candidates/<DATE>/ directories "
        "whose shadow_evaluation.json reports data_status == OK for the "
        "strategy. Supports operational shadow observation. Does NOT "
        "require execution-was-executed or reconciliation-passed; "
        "therefore does NOT meet the strict Phase C capital bar."
    ),
    "promotion_evidence_days": (
        "Strict FR-028 Phase C count from outputs/research/"
        "stable_window_evaluation/latest_{loose,strict}.json. Each "
        "qualifying day must satisfy: execution_status=EXECUTED AND "
        "(in strict mode) reconciliation_status=OK_RECONCILED AND "
        "data_status=OK across all strategies. THIS is the bar for "
        "capital allocation. Below MIN_VALID_OBSERVATION_WINDOWS (20) "
        "the recommendation tier is capped at 'hold'."
    ),
}


def _compute_observation_evidence(
    *,
    outputs_root: Path,
    shadow_root: Path,
    available_strategies: Iterable[str],
    nav_path: Path = DEFAULT_NAV_SERIES_PATH,
    stable_window_root: Path = DEFAULT_STABLE_WINDOW_ROOT,
) -> dict[str, Any]:
    """Build the three-tier evidence breakdown.

    The returned block is purely informational — it does not influence
    the recommendation tier. Capital governance still depends on the
    strict Phase C ``valid_observation_windows`` field carried by each
    panel.
    """
    strategy_list = list(available_strategies)
    shadow_days, date_range = _count_shadow_nav_days(nav_path, strategy_list)
    live_days = _count_live_execution_days(shadow_root, strategy_list)
    phase_c = _read_phase_c_evidence_counts(stable_window_root)

    return {
        "valid_shadow_observation_days": shadow_days,
        "valid_live_execution_days": live_days,
        "promotion_evidence_days": phase_c,
        "nav_series_path": str(nav_path),
        "nav_series_date_range": (
            {"start": date_range[0], "end": date_range[1]} if date_range else None
        ),
        "shadow_root": str(shadow_root),
        "stable_window_root": str(stable_window_root),
        "explanation": _EVIDENCE_EXPLANATION,
    }


def _strategy_panel(
    *,
    slug: str,
    eval_raw: Mapping[str, Any],
    phase_c_entry: Optional[Mapping[str, Any]],
    stability_raw: Optional[Mapping[str, Any]],
) -> dict[str, Any]:
    metrics = _extract_evaluation_metrics(eval_raw)
    stability_block = _extract_stability(stability_raw)
    phase_c_entry = phase_c_entry or {}

    phase_c_state = phase_c_entry.get("readiness_state")
    phase_c_confidence = phase_c_entry.get("confidence")
    phase_c_reason_codes = phase_c_entry.get("reason_codes") or []
    valid_obs = int(phase_c_entry.get("valid_observation_windows") or 0)

    recommendation, confidence, reason_codes, blockers, explanation = _derive_recommendation(
        metrics=metrics,
        phase_c_state=phase_c_state,
        phase_c_confidence=phase_c_confidence,
        phase_c_reason_codes=phase_c_reason_codes,
        valid_observation_windows=valid_obs,
        stability_flags=stability_block.get("stability_flags") or [],
    )

    unavailable = [
        k for k in (
            "nav", "daily_return", "cumulative_return", "excess_return_vs_spy",
            "max_drawdown", "realized_volatility_ann",
            "avg_turnover", "avg_top_3_concentration",
        )
        if metrics.get(k) is None
    ]

    return {
        "strategy_slug": slug,
        "strategy_name": eval_raw.get("strategy_name"),
        "readiness_state": phase_c_state,
        "phase_c_confidence": phase_c_confidence,
        "recommendation": recommendation,
        "confidence": confidence,
        "reason_codes": reason_codes,
        "blockers": blockers,
        "metrics": metrics,
        "stability": stability_block,
        "valid_observation_windows": valid_obs,
        "unavailable_metrics": unavailable,
        "explanation": explanation,
    }


@dataclass(frozen=True)
class StrategyPromotionReadinessAnswer:
    status: str
    trade_date: Optional[str]
    outputs_root: str
    shadow_root: str
    requested_strategies: tuple[str, ...]
    available_strategies: tuple[str, ...]
    strategy_panels: dict[str, dict[str, Any]]
    closest_to_promotion: Optional[str]
    ranking_by_recommendation: list[str]
    has_phase_c_sidecar: bool
    observation_evidence: Optional[dict[str, Any]] = None
    missing_strategies: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    source_paths: list[str] = field(default_factory=list)


def strategy_promotion_readiness_to_dict(
    answer: StrategyPromotionReadinessAnswer,
) -> dict[str, Any]:
    return {
        "status": answer.status,
        "trade_date": answer.trade_date,
        "outputs_root": answer.outputs_root,
        "shadow_root": answer.shadow_root,
        "requested_strategies": list(answer.requested_strategies),
        "available_strategies": list(answer.available_strategies),
        "strategy_panels": answer.strategy_panels,
        "closest_to_promotion": answer.closest_to_promotion,
        "ranking_by_recommendation": list(answer.ranking_by_recommendation),
        "has_phase_c_sidecar": answer.has_phase_c_sidecar,
        "observation_evidence": answer.observation_evidence,
        "missing_strategies": list(answer.missing_strategies),
        "warnings": list(answer.warnings),
        "source_paths": list(answer.source_paths),
    }


def assess_strategy_readiness(
    *,
    outputs_root: Path = DEFAULT_OUTPUTS_ROOT,
    shadow_root: Optional[Path] = None,
    question: Optional[str] = None,
    strategies: Optional[Iterable[str]] = None,
) -> StrategyPromotionReadinessAnswer:
    """Per-strategy promotion readiness over the latest shadow snapshot.

    See module docstring for the artifact contract. Fails closed with
    ``status=NO_SHADOW_DATA`` when no usable directory exists and
    ``status=NEEDS_DATA`` when requested strategies are absent from
    the artifact.
    """
    resolved_shadow_root = (
        shadow_root if shadow_root is not None else outputs_root / "shadow_candidates"
    )

    latest = select_latest_shadow_date(resolved_shadow_root)
    if latest is None:
        return StrategyPromotionReadinessAnswer(
            status="NO_SHADOW_DATA",
            trade_date=None,
            outputs_root=str(outputs_root),
            shadow_root=str(resolved_shadow_root),
            requested_strategies=tuple(strategies or parse_strategy_names(question or "")),
            available_strategies=(),
            strategy_panels={},
            closest_to_promotion=None,
            ranking_by_recommendation=[],
            has_phase_c_sidecar=False,
            warnings=[f"no shadow_evaluation.json found under {resolved_shadow_root}"],
        )

    eval_path = latest / "shadow_evaluation.json"
    phase_c_path = latest / "promotion_readiness.json"

    shadow_eval = _safe_json(eval_path) or {}
    phase_c_payload = _safe_json(phase_c_path) or {}

    raw_strategies = shadow_eval.get("strategies") or {}
    available = tuple(
        sorted(s for s in raw_strategies.keys() if s != BENCHMARK_SLUG)
    )

    requested_names: list[str] = (
        list(strategies) if strategies else parse_strategy_names(question or "")
    )
    if requested_names:
        requested_slugs = [strategy_slug(n) for n in requested_names]
        missing = [s for s in requested_slugs if s not in raw_strategies]
        selected = [s for s in requested_slugs if s in raw_strategies]
        if not selected:
            return StrategyPromotionReadinessAnswer(
                status="NEEDS_DATA",
                trade_date=str(shadow_eval.get("trade_date") or ""),
                outputs_root=str(outputs_root),
                shadow_root=str(resolved_shadow_root),
                requested_strategies=tuple(requested_names),
                available_strategies=available,
                strategy_panels={},
                closest_to_promotion=None,
                ranking_by_recommendation=[],
                has_phase_c_sidecar=bool(phase_c_payload.get("strategies")),
                missing_strategies=missing,
                warnings=[
                    f"missing strategy slug: {slug}" for slug in missing
                ] + [
                    f"available strategies: {list(available)}"
                ],
                source_paths=[str(eval_path)],
            )
    else:
        requested_slugs = list(available)
        missing = []
        selected = list(available)

    phase_c_strategies = phase_c_payload.get("strategies") or {}
    has_phase_c = bool(phase_c_strategies)

    # Compute the three-tier observation-evidence block. This is purely
    # informational — it does NOT alter the recommendation tier. The
    # strict Phase C `valid_observation_windows` count carried by each
    # panel remains the bar for capital allocation. We derive the NAV
    # and stable_window paths from the resolved shadow_root /
    # outputs_root so tests with custom fixtures don't accidentally
    # read the repo-relative real artifacts.
    derived_nav_path = resolved_shadow_root / "performance" / "shadow_nav_series.csv"
    derived_stable_window_root = outputs_root / "research" / "stable_window_evaluation"
    observation_evidence = _compute_observation_evidence(
        outputs_root=outputs_root,
        shadow_root=resolved_shadow_root,
        available_strategies=available,
        nav_path=derived_nav_path,
        stable_window_root=derived_stable_window_root,
    )

    panels: dict[str, dict[str, Any]] = {}
    for slug in selected:
        eval_raw = raw_strategies.get(slug) or {}
        phase_c_entry = phase_c_strategies.get(slug)
        stability_raw = _load_per_strategy_stability(latest, slug)
        panel = _strategy_panel(
            slug=slug,
            eval_raw=eval_raw,
            phase_c_entry=phase_c_entry,
            stability_raw=stability_raw,
        )
        # Decorate the panel with the additive evidence counts. Capital
        # governance still keys on `valid_observation_windows`; these
        # new fields are for operator transparency.
        panel["valid_shadow_observation_days"] = int(
            observation_evidence["valid_shadow_observation_days"].get(slug, 0)
        )
        panel["valid_live_execution_days"] = int(
            observation_evidence["valid_live_execution_days"].get(slug, 0)
        )
        panels[slug] = panel

    # Rank: by recommendation tier, then by excess vs SPY (descending),
    # then by slug (alphabetical for determinism).
    def _rank_key(slug: str) -> tuple[int, float, str]:
        panel = panels[slug]
        rec_rank = -RECOMMENDATION_RANK.get(panel["recommendation"], 0)
        excess = panel["metrics"].get("excess_return_vs_spy")
        excess_key = -float(excess) if excess is not None else math.inf
        return (rec_rank, excess_key, slug)

    ranked = sorted(panels.keys(), key=_rank_key)
    closest = ranked[0] if ranked else None

    warnings: list[str] = []
    if not has_phase_c:
        warnings.append(
            "no_promotion_readiness_sidecar: recommendation derived from "
            "shadow_evaluation metrics + per-strategy stability_analysis only"
        )
    if missing:
        warnings.extend(f"missing strategy slug: {slug}" for slug in missing)
    no_data_slugs = [
        slug for slug, panel in panels.items()
        if panel["metrics"].get("data_status") not in (None, "OK")
    ]
    if no_data_slugs:
        warnings.append(
            f"strategies with non-OK data_status: {sorted(no_data_slugs)}"
        )

    source_paths = [str(eval_path)]
    if phase_c_path.exists():
        source_paths.append(str(phase_c_path))
    else:
        source_paths.append(f"{phase_c_path} (missing)")
    # Include per-strategy stability files that exist
    for slug in selected:
        stab = latest / _name_to_short_slug(slug) / "stability_analysis.json"
        if stab.exists():
            source_paths.append(str(stab))

    return StrategyPromotionReadinessAnswer(
        status="OK",
        trade_date=str(shadow_eval.get("trade_date") or latest.name),
        outputs_root=str(outputs_root),
        shadow_root=str(resolved_shadow_root),
        requested_strategies=tuple(requested_names) if requested_names else available,
        available_strategies=available,
        strategy_panels=panels,
        closest_to_promotion=closest,
        ranking_by_recommendation=ranked,
        has_phase_c_sidecar=has_phase_c,
        observation_evidence=observation_evidence,
        missing_strategies=missing,
        warnings=warnings,
        source_paths=source_paths,
    )
