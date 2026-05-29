"""Aggregate execution-timing analysis (not regime-stratified).

This module backs the ``execution_timing_summary`` MCP tool. It reads the
already-aggregated ``outputs/research/execution_timing/<RUN_DATE>/timing_summary.json``
produced by the timing-replay engine and emits a compact, deterministic
summary suitable for the question family

* "Is 9:35 better than 9:30?"
* "What is the best execution time?"
* "Compare 9:30 and 9:35 execution timing."

Unlike :mod:`research_registry.research.timing_regime`, this module does
NOT stratify by VIX regime — it just summarises the cross-day per-offset
opportunity numbers already inside ``timing_summary.json`` and adds a
deterministic recommendation.

Recommendation rule (deterministic, no randomness)
--------------------------------------------------
1. If ``days_replayed < MIN_DAYS_FOR_RECOMMENDATION`` →
   ``insufficient_evidence``.
2. Else find the non-baseline offset with the highest mean opportunity
   USD vs the baseline.
   * If its **mean AND median** opportunity are both strictly positive
     AND its ``n_days_with_opportunity`` ≥ ``MIN_DAYS_FOR_RECOMMENDATION``
     AND the offset's minutes are **earlier** than the baseline →
     ``earlier_timing_appears_better``.
   * Else → ``retain_9_35_baseline``.

The verdict is intentionally conservative: a positive mean alone is not
enough; we also require a positive median (so the signal isn't a few
outlier days carrying the average) and adequate per-offset sample size.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

DEFAULT_TIMING_ROOT = Path("outputs/research/execution_timing")

MIN_DAYS_FOR_RECOMMENDATION = 5
"""Below this, the tool refuses to make a directional claim and returns
``insufficient_evidence`` regardless of the sign of the mean opportunity.
Set conservatively because the timing-replay sample size is the binding
constraint per the study spec (§2.2)."""


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def select_latest_run(timing_root: Path = DEFAULT_TIMING_ROOT) -> Optional[Path]:
    """Return the lexicographically-latest ``<RUN_DATE>/timing_summary.json`` dir."""
    if not timing_root.exists() or not timing_root.is_dir():
        return None
    candidates = [
        p for p in timing_root.iterdir()
        if p.is_dir() and (p / "timing_summary.json").exists()
    ]
    if not candidates:
        return None
    return sorted(candidates, key=lambda p: p.name)[-1]


# ---------------------------------------------------------------------------
# Offset helpers
# ---------------------------------------------------------------------------


_OFFSET_LABEL_RE = re.compile(r"^T\+(\d+)m$", re.IGNORECASE)
_CLOCK_RE = re.compile(r"\b9:?(\d{2})\b")


def offset_minutes_from_label(label: str) -> Optional[int]:
    """``T+5m`` → ``5``. Returns ``None`` if the label is malformed."""
    match = _OFFSET_LABEL_RE.match(str(label or ""))
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def parse_offset_highlights(question: str) -> tuple[str, ...]:
    """Return offset labels mentioned in a free-text question.

    Supported phrasings:

    * ``9:30`` / ``9:35`` / ``9:40`` etc. → minute past the regular session
      open, mapped to ``T+<minute-30>m``. Only minutes in ``[30, 60)``
      produce a valid offset.
    * ``T+0m`` / ``T+5m`` literal forms.

    Duplicates are deduplicated; order of first appearance is preserved
    so the renderer can show the user's columns in the order asked.
    """
    text = question or ""
    seen: list[str] = []
    seen_set: set[str] = set()

    for raw_label in re.findall(r"T\+\d+m", text, flags=re.IGNORECASE):
        normalized = raw_label.upper().replace("T+", "T+").lower().replace("t+", "T+").replace("M", "m")
        if normalized.startswith("T+") and normalized.endswith("m") and normalized not in seen_set:
            seen.append(normalized)
            seen_set.add(normalized)

    for clock_minute in _CLOCK_RE.findall(text):
        try:
            minute = int(clock_minute)
        except ValueError:
            continue
        if not 30 <= minute < 60:
            continue
        label = f"T+{minute - 30}m"
        if label not in seen_set:
            seen.append(label)
            seen_set.add(label)

    return tuple(seen)


# ---------------------------------------------------------------------------
# Core summariser
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TimingSummaryAnswer:
    status: str
    run_date: Optional[str]
    baseline_offset: Optional[str]
    days_replayed: int
    by_offset: dict[str, dict[str, Optional[float]]]
    best_offset: Optional[str]
    highlighted_offsets: tuple[str, ...]
    recommendation: str
    recommendation_reason: str
    timing_root: str
    warnings: list[str] = field(default_factory=list)


def load_timing_summary_payload(timing_run_dir: Path) -> Mapping[str, Any]:
    summary_path = timing_run_dir / "timing_summary.json"
    if not summary_path.exists():
        raise FileNotFoundError(f"timing_summary_missing: {summary_path}")
    return json.loads(summary_path.read_text(encoding="utf-8"))


def _flatten_offset_metrics(by_offset_raw: Mapping[str, Any]) -> dict[str, dict[str, Optional[float]]]:
    out: dict[str, dict[str, Optional[float]]] = {}
    for offset_label, entry in (by_offset_raw or {}).items():
        if not isinstance(entry, Mapping):
            continue
        usd = entry.get("opportunity_usd") if isinstance(entry.get("opportunity_usd"), Mapping) else {}
        bps = entry.get("opportunity_bps") if isinstance(entry.get("opportunity_bps"), Mapping) else {}
        out[str(offset_label)] = {
            "n_days_with_opportunity": _coerce_int(usd.get("n")),
            "mean_opportunity_usd": _coerce_float(usd.get("mean")),
            "median_opportunity_usd": _coerce_float(usd.get("median")),
            "p10_opportunity_usd": _coerce_float(usd.get("p10")),
            "p90_opportunity_usd": _coerce_float(usd.get("p90")),
            "sum_opportunity_usd": _coerce_float(usd.get("sum")),
            "mean_opportunity_bps": _coerce_float(bps.get("mean")),
            "median_opportunity_bps": _coerce_float(bps.get("median")),
        }
    return out


def _coerce_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _select_best_offset(
    by_offset: Mapping[str, Mapping[str, Optional[float]]],
    baseline_offset: Optional[str],
) -> Optional[str]:
    best_label: Optional[str] = None
    best_mean: Optional[float] = None
    for label, metrics in by_offset.items():
        if label == baseline_offset:
            continue
        mean = metrics.get("mean_opportunity_usd")
        if mean is None:
            continue
        if best_mean is None or mean > best_mean:
            best_mean = mean
            best_label = label
    return best_label


def _build_recommendation(
    *,
    days_replayed: int,
    baseline_offset: Optional[str],
    by_offset: Mapping[str, Mapping[str, Optional[float]]],
    best_offset: Optional[str],
) -> tuple[str, str]:
    if days_replayed < MIN_DAYS_FOR_RECOMMENDATION:
        return (
            "insufficient_evidence",
            f"{days_replayed} replayed day(s) is below the threshold of "
            f"{MIN_DAYS_FOR_RECOMMENDATION}; refusing to make a directional claim.",
        )
    if not best_offset or not baseline_offset:
        return (
            "retain_9_35_baseline",
            "No non-baseline offset produced a comparable opportunity number.",
        )

    best_metrics = by_offset.get(best_offset) or {}
    mean = best_metrics.get("mean_opportunity_usd")
    median = best_metrics.get("median_opportunity_usd")
    n = best_metrics.get("n_days_with_opportunity") or 0
    baseline_minutes = offset_minutes_from_label(baseline_offset)
    best_minutes = offset_minutes_from_label(best_offset)

    if mean is None or median is None:
        return (
            "retain_9_35_baseline",
            f"Best offset {best_offset} lacks mean/median opportunity data.",
        )
    if not (mean > 0 and median > 0):
        return (
            "retain_9_35_baseline",
            f"Best non-baseline offset is {best_offset} but its mean "
            f"({mean:.4f}) and median ({median:.4f}) opportunity vs baseline "
            "are not both strictly positive.",
        )
    if n < MIN_DAYS_FOR_RECOMMENDATION:
        return (
            "retain_9_35_baseline",
            f"Best offset {best_offset} has positive mean+median opportunity "
            f"but only {n} day(s) of data — below the threshold of "
            f"{MIN_DAYS_FOR_RECOMMENDATION}.",
        )
    if baseline_minutes is not None and best_minutes is not None and best_minutes < baseline_minutes:
        return (
            "earlier_timing_appears_better",
            f"{best_offset} (minute {best_minutes} of the session) beats the "
            f"{baseline_offset} baseline on both mean and median opportunity "
            f"across {n} days; consider an FR proposal to move execution earlier.",
        )
    return (
        "retain_9_35_baseline",
        f"Best non-baseline offset is {best_offset} but it is not earlier than "
        f"the {baseline_offset} baseline; later offsets do not justify a change.",
    )


def summarise_timing(
    *,
    timing_root: Path = DEFAULT_TIMING_ROOT,
    question: str | None = None,
    highlighted_offsets: Iterable[str] | None = None,
) -> TimingSummaryAnswer:
    """End-to-end: discover latest run, load summary, compute recommendation."""
    run_dir = select_latest_run(timing_root)
    if run_dir is None:
        return TimingSummaryAnswer(
            status="NO_TIMING_DATA",
            run_date=None,
            baseline_offset=None,
            days_replayed=0,
            by_offset={},
            best_offset=None,
            highlighted_offsets=tuple(highlighted_offsets or parse_offset_highlights(question or "")),
            recommendation="insufficient_evidence",
            recommendation_reason=(
                "No execution-timing replay run found on disk under "
                f"{timing_root}; run scripts.research.execution_timing_replay first."
            ),
            timing_root=str(timing_root),
            warnings=[
                f"no timing-replay run found under {timing_root}",
            ],
        )

    payload = load_timing_summary_payload(run_dir)
    by_offset = _flatten_offset_metrics(payload.get("by_offset") or {})
    coverage = payload.get("coverage_summary") or {}
    days_replayed = int(coverage.get("days_replayed") or 0)
    baseline_offset = payload.get("baseline_offset")

    requested = tuple(highlighted_offsets) if highlighted_offsets else parse_offset_highlights(question or "")
    # Filter the requested list to offsets that actually exist in the summary
    # so the renderer doesn't claim numbers for offsets the replay didn't compute.
    requested_present = tuple(label for label in requested if label in by_offset)
    unknown_highlights = tuple(label for label in requested if label not in by_offset)

    best_offset = _select_best_offset(by_offset, baseline_offset)
    recommendation, recommendation_reason = _build_recommendation(
        days_replayed=days_replayed,
        baseline_offset=baseline_offset,
        by_offset=by_offset,
        best_offset=best_offset,
    )

    warnings: list[str] = []
    if unknown_highlights:
        warnings.append(
            f"requested offsets not present in this run's summary: {list(unknown_highlights)}"
        )

    return TimingSummaryAnswer(
        status="OK",
        run_date=str(payload.get("run_date") or run_dir.name),
        baseline_offset=str(baseline_offset) if baseline_offset else None,
        days_replayed=days_replayed,
        by_offset=by_offset,
        best_offset=best_offset,
        highlighted_offsets=requested_present,
        recommendation=recommendation,
        recommendation_reason=recommendation_reason,
        timing_root=str(timing_root),
        warnings=warnings,
    )


def timing_summary_to_dict(answer: TimingSummaryAnswer) -> dict[str, Any]:
    return {
        "status": answer.status,
        "run_date": answer.run_date,
        "baseline_offset": answer.baseline_offset,
        "days_replayed": answer.days_replayed,
        "by_offset": answer.by_offset,
        "best_offset": answer.best_offset,
        "highlighted_offsets": list(answer.highlighted_offsets),
        "recommendation": answer.recommendation,
        "recommendation_reason": answer.recommendation_reason,
        "timing_root": answer.timing_root,
        "warnings": list(answer.warnings),
    }
