"""Pairwise / multi-strategy shadow-portfolio comparison.

Backs the ``shadow_comparison`` MCP tool. Reads
``outputs/shadow_candidates/<DATE>/shadow_evaluation.json`` (rich per-
strategy panel) and the sibling ``comparison.json`` (pairwise overlap)
and emits a deterministic side-by-side comparison.

Available metrics per strategy (only fields we surface verbatim; we
**never** invent or interpolate missing values):

* ``nav``
* ``daily_return``
* ``cumulative_return``
* ``excess_return_vs_spy``
* ``avg_turnover``
* ``avg_top_3_concentration``
* ``max_drawdown``
* ``realized_volatility_ann``
* ``data_status`` / ``data_reason``

Any of those that is ``null`` in the source is reported as ``null`` —
the response also lists the unavailable metric names per strategy so
the operator knows what's missing.

Question handling
-----------------
* Strategy names parsed from the question against the fixed list
  ``polaris | orion | lyra | leda``. Mapped to the artifact's
  ``caerus_<name>`` slug.
* If no strategy name is mentioned, all available strategies are
  returned (the "which strategy is performing best?" case).
* ``spy_benchmark`` is excluded from the leader ranking but included
  as a reference column.

Fail-closed contract
--------------------
* ``status="NO_SHADOW_DATA"`` if no ``outputs/shadow_candidates/<DATE>/``
  exists or the latest one has no ``shadow_evaluation.json``.
* ``status="NEEDS_DATA"`` if a strategy named in the question is
  absent from the artifact (typo, or not yet running). The response
  carries ``missing_strategies`` for the operator.
* ``status="OK"`` otherwise; per-strategy ``unavailable_metrics`` is
  populated if any field is null.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

DEFAULT_SHADOW_ROOT = Path("outputs/shadow_candidates")
KNOWN_STRATEGY_NAMES = ("polaris", "orion", "lyra", "leda")
"""Closed set of recognised strategy names; the tool refuses unknown
names deterministically (no inference, no fuzzy-matching). Adding a new
strategy is a one-line tuple extension."""

BENCHMARK_SLUG = "spy_benchmark"

METRIC_FIELDS = (
    "nav",
    "daily_return",
    "cumulative_return",
    "excess_return_vs_spy",
    "avg_turnover",
    "avg_top_3_concentration",
    "max_drawdown",
    "realized_volatility_ann",
)


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def _looks_like_date_dir(name: str) -> bool:
    """Match ``YYYY-MM-DD`` — excludes ``latest``, ``performance``, etc."""
    return bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", name))


def select_latest_shadow_date(shadow_root: Path = DEFAULT_SHADOW_ROOT) -> Optional[Path]:
    """Return the lexicographically-latest ``<DATE>`` directory.

    Prefers an explicit ``latest`` symlink/alias **if** it contains a
    ``shadow_evaluation.json``; otherwise picks the highest ISO date
    directory. Returns ``None`` when no usable directory exists.
    """
    if not shadow_root.exists() or not shadow_root.is_dir():
        return None

    latest_alias = shadow_root / "latest"
    if latest_alias.exists() and (latest_alias / "shadow_evaluation.json").exists():
        return latest_alias

    dated = [
        p for p in shadow_root.iterdir()
        if p.is_dir() and _looks_like_date_dir(p.name)
        and (p / "shadow_evaluation.json").exists()
    ]
    if not dated:
        return None
    return sorted(dated, key=lambda p: p.name)[-1]


# ---------------------------------------------------------------------------
# Question parsing
# ---------------------------------------------------------------------------


def parse_strategy_names(question: str) -> list[str]:
    """Return strategy names (lowercase, deduped, in first-mention order)
    that appear in the question. Restricted to ``KNOWN_STRATEGY_NAMES``.
    """
    text = (question or "").lower()
    seen: list[str] = []
    seen_set: set[str] = set()
    for token in re.findall(r"[a-z]+", text):
        if token in KNOWN_STRATEGY_NAMES and token not in seen_set:
            seen.append(token)
            seen_set.add(token)
    return seen


def strategy_slug(name: str) -> str:
    """``polaris`` → ``caerus_polaris``. Pass-through for already-slugged input."""
    name = (name or "").lower().strip()
    if name.startswith("caerus_"):
        return name
    return f"caerus_{name}"


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------


def _safe_json(path: Path) -> Optional[dict[str, Any]]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _strategy_panel(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Extract the standardised metric panel for one strategy entry."""
    panel: dict[str, Any] = {
        "strategy_name": raw.get("strategy_name"),
        "data_status": raw.get("data_status"),
        "data_reason": raw.get("data_reason"),
    }
    unavailable: list[str] = []
    for field_name in METRIC_FIELDS:
        value = raw.get(field_name)
        panel[field_name] = value
        if value is None:
            unavailable.append(field_name)
    panel["unavailable_metrics"] = unavailable
    return panel


def _pairwise_overlap(
    pairwise_raw: Iterable[Mapping[str, Any]],
    requested_slugs: set[str],
) -> list[dict[str, Any]]:
    """Return pairwise-overlap entries that involve the requested strategies."""
    out: list[dict[str, Any]] = []
    for entry in pairwise_raw or ():
        if not isinstance(entry, Mapping):
            continue
        left = str(entry.get("left_slug") or "")
        right = str(entry.get("right_slug") or "")
        if requested_slugs and not (left in requested_slugs and right in requested_slugs):
            continue
        out.append(
            {
                "left_slug": left,
                "right_slug": right,
                "left_strategy": entry.get("left_strategy"),
                "right_strategy": entry.get("right_strategy"),
                "overlap_weight_pct": entry.get("overlap_weight_pct"),
                "shared_names": list(entry.get("shared_names") or []),
                "left_unique_names": list(entry.get("left_unique_names") or []),
                "right_unique_names": list(entry.get("right_unique_names") or []),
            }
        )
    return out


# ---------------------------------------------------------------------------
# Leader selection
# ---------------------------------------------------------------------------


def _pick_leader(
    panels: Mapping[str, dict[str, Any]],
    metric: str,
) -> Optional[str]:
    best_slug: Optional[str] = None
    best_value: Optional[float] = None
    for slug, panel in panels.items():
        if slug == BENCHMARK_SLUG:
            continue
        value = panel.get(metric)
        if value is None:
            continue
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            continue
        if best_value is None or numeric > best_value:
            best_value = numeric
            best_slug = slug
    return best_slug


@dataclass(frozen=True)
class ShadowComparisonAnswer:
    status: str
    trade_date: Optional[str]
    benchmark_symbol: Optional[str]
    available_strategies: tuple[str, ...]
    requested_strategies: tuple[str, ...]
    panels: dict[str, dict[str, Any]]
    pairwise_overlap: list[dict[str, Any]]
    leader_by_cumulative_return: Optional[str]
    leader_by_excess_vs_spy: Optional[str]
    leader_summary: str
    shadow_root: str
    source_paths: list[str]
    missing_strategies: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def compare_shadow_strategies(
    *,
    shadow_root: Path = DEFAULT_SHADOW_ROOT,
    question: str | None = None,
    strategies: Iterable[str] | None = None,
) -> ShadowComparisonAnswer:
    """End-to-end shadow comparison. See module docstring for the contract."""
    latest = select_latest_shadow_date(shadow_root)
    if latest is None:
        return ShadowComparisonAnswer(
            status="NO_SHADOW_DATA",
            trade_date=None,
            benchmark_symbol=None,
            available_strategies=(),
            requested_strategies=tuple(strategies or parse_strategy_names(question or "")),
            panels={},
            pairwise_overlap=[],
            leader_by_cumulative_return=None,
            leader_by_excess_vs_spy=None,
            leader_summary=(
                "No shadow candidate directory on disk with a "
                "shadow_evaluation.json yet."
            ),
            shadow_root=str(shadow_root),
            source_paths=[],
            warnings=[f"no shadow_evaluation.json found under {shadow_root}"],
        )

    evaluation_path = latest / "shadow_evaluation.json"
    comparison_path = latest / "comparison.json"
    evaluation = _safe_json(evaluation_path) or {}
    comparison_payload = _safe_json(comparison_path) or {}

    raw_strategies = evaluation.get("strategies") or {}
    available_slugs = tuple(sorted(raw_strategies.keys()))
    if not available_slugs:
        return ShadowComparisonAnswer(
            status="NEEDS_DATA",
            trade_date=str(evaluation.get("trade_date") or ""),
            benchmark_symbol=str(evaluation.get("benchmark_symbol") or "") or None,
            available_strategies=(),
            requested_strategies=tuple(strategies or parse_strategy_names(question or "")),
            panels={},
            pairwise_overlap=[],
            leader_by_cumulative_return=None,
            leader_by_excess_vs_spy=None,
            leader_summary="shadow_evaluation.json has no strategies block",
            shadow_root=str(shadow_root),
            source_paths=[str(evaluation_path)],
            warnings=["shadow_evaluation.json has no strategies"],
        )

    requested_names = list(strategies) if strategies else parse_strategy_names(question or "")
    if requested_names:
        requested_slugs = [strategy_slug(name) for name in requested_names]
        missing_slugs = [slug for slug in requested_slugs if slug not in raw_strategies]
        selected_slugs = [slug for slug in requested_slugs if slug in raw_strategies]
        if not selected_slugs and missing_slugs:
            return ShadowComparisonAnswer(
                status="NEEDS_DATA",
                trade_date=str(evaluation.get("trade_date") or ""),
                benchmark_symbol=str(evaluation.get("benchmark_symbol") or "") or None,
                available_strategies=available_slugs,
                requested_strategies=tuple(requested_names),
                panels={},
                pairwise_overlap=[],
                leader_by_cumulative_return=None,
                leader_by_excess_vs_spy=None,
                leader_summary=(
                    f"None of the requested strategies ({requested_names}) are "
                    f"present in shadow_evaluation.json. Available: "
                    f"{list(available_slugs)}."
                ),
                shadow_root=str(shadow_root),
                source_paths=[str(evaluation_path)],
                missing_strategies=missing_slugs,
                warnings=[f"missing strategy slug: {slug}" for slug in missing_slugs],
            )
    else:
        requested_slugs = list(available_slugs)
        missing_slugs = []
        selected_slugs = list(available_slugs)

    panels: dict[str, dict[str, Any]] = {}
    for slug in selected_slugs:
        raw = raw_strategies.get(slug)
        if isinstance(raw, Mapping):
            panels[slug] = _strategy_panel(raw)
    # Always include the benchmark for context if available.
    if BENCHMARK_SLUG in raw_strategies and BENCHMARK_SLUG not in panels:
        bench_raw = raw_strategies[BENCHMARK_SLUG]
        if isinstance(bench_raw, Mapping):
            panels[BENCHMARK_SLUG] = _strategy_panel(bench_raw)

    leader_cumret = _pick_leader(panels, "cumulative_return")
    leader_excess = _pick_leader(panels, "excess_return_vs_spy")
    leader_summary = _format_leader_summary(leader_cumret, leader_excess, panels)

    pairwise_raw = comparison_payload.get("pairwise_overlap") or []
    pairwise = _pairwise_overlap(
        pairwise_raw,
        requested_slugs={s for s in selected_slugs if s != BENCHMARK_SLUG},
    )

    warnings: list[str] = []
    if missing_slugs:
        warnings.extend(f"missing strategy slug: {slug}" for slug in missing_slugs)
    stale_strategies = [
        slug for slug, panel in panels.items()
        if panel.get("data_status") == "NO_DATA"
    ]
    if stale_strategies:
        warnings.append(
            f"data_status=NO_DATA on: {sorted(stale_strategies)} — metrics "
            "are last-valid-snapshot values, not fresh observations."
        )

    return ShadowComparisonAnswer(
        status="OK",
        trade_date=str(evaluation.get("trade_date") or ""),
        benchmark_symbol=str(evaluation.get("benchmark_symbol") or "") or None,
        available_strategies=available_slugs,
        requested_strategies=tuple(requested_names) if requested_names else available_slugs,
        panels=panels,
        pairwise_overlap=pairwise,
        leader_by_cumulative_return=leader_cumret,
        leader_by_excess_vs_spy=leader_excess,
        leader_summary=leader_summary,
        shadow_root=str(shadow_root),
        source_paths=[
            str(evaluation_path),
            str(comparison_path) if comparison_path.exists() else f"{comparison_path} (missing)",
        ],
        missing_strategies=missing_slugs,
        warnings=warnings,
    )


def _format_leader_summary(
    leader_cumret: Optional[str],
    leader_excess: Optional[str],
    panels: Mapping[str, Mapping[str, Any]],
) -> str:
    if not leader_cumret and not leader_excess:
        return "No strategy has a numeric cumulative_return or excess_return_vs_spy."
    if leader_cumret == leader_excess and leader_cumret:
        return (
            f"{leader_cumret} leads on both cumulative return "
            f"({panels[leader_cumret].get('cumulative_return')}) and excess vs SPY "
            f"({panels[leader_cumret].get('excess_return_vs_spy')})."
        )
    parts = []
    if leader_cumret:
        parts.append(
            f"{leader_cumret} leads cumulative return "
            f"({panels[leader_cumret].get('cumulative_return')})"
        )
    if leader_excess:
        parts.append(
            f"{leader_excess} leads excess vs SPY "
            f"({panels[leader_excess].get('excess_return_vs_spy')})"
        )
    return "; ".join(parts) + "."


def shadow_comparison_to_dict(answer: ShadowComparisonAnswer) -> dict[str, Any]:
    return {
        "status": answer.status,
        "trade_date": answer.trade_date,
        "benchmark_symbol": answer.benchmark_symbol,
        "available_strategies": list(answer.available_strategies),
        "requested_strategies": list(answer.requested_strategies),
        "panels": answer.panels,
        "pairwise_overlap": answer.pairwise_overlap,
        "leader_by_cumulative_return": answer.leader_by_cumulative_return,
        "leader_by_excess_vs_spy": answer.leader_by_excess_vs_spy,
        "leader_summary": answer.leader_summary,
        "shadow_root": answer.shadow_root,
        "source_paths": list(answer.source_paths),
        "missing_strategies": list(answer.missing_strategies),
        "warnings": list(answer.warnings),
    }
