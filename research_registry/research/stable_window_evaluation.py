"""Stable-window / random-window evaluation summariser.

Backs the ``stable_window_evaluation`` MCP tool. Reads the existing
alpha-lab random-window backtest CSVs under ``outputs/research/`` and
the promotion-grade stable-window JSON artifacts under
``outputs/research/stable_window_evaluation/``, and emits a per-policy
dispersion + consistency panel with deterministic confidence caveats.

Data sources (best-effort; each is optional)
--------------------------------------------
1. ``outputs/research/random_windows_3y_{policy}.csv`` — one row per
   randomly-sampled backtest window: start_date, end_date, policy,
   total_return, cagr, sharpe, max_drawdown, ulcer_index, avg_turnover,
   n_days, trade_count. This is the *primary* signal for dispersion.
2. ``outputs/research/random_windows_summary_{policy}.csv`` — one
   summary row per policy. Used to cross-check our derived stats.
3. ``outputs/research/stable_window_evaluation/latest_{mode}.json``
   (mode in ``{loose, strict}``) — promotion-grade window validity
   counts: how many days qualify for promotion math under each
   mode. Surfaced as a promotion-validity block.

Output shape
------------
* ``status``: ``OK`` | ``NO_WINDOW_DATA`` | ``NEEDS_DATA``.
* ``policy_panels``: list of per-policy dispersion + consistency
  panels. Empty list when no random-window CSV is on disk.
* ``promotion_validity``: optional block describing the promotion-
  grade evaluation windows (when the JSON artifact is present).
* ``confidence_caveats``: deterministic list of ``insufficient_sample``,
  ``mixed_policy_sample``, ``no_promotion_validity``, etc.
* ``narrative``: deterministic template-driven summary.

Fail-closed contract
--------------------
* The tool never invents window counts. If the CSV is malformed or
  missing, that policy is absent from the response.
* ``status="NO_WINDOW_DATA"`` only when **all** sources are absent.
* When the random-window CSV is present but has < 30 rows, the
  per-policy panel includes ``insufficient_sample: True`` and the
  narrative refuses to make a directional claim.
"""

from __future__ import annotations

import csv
import json
import re
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

DEFAULT_RESEARCH_ROOT = Path("outputs/research")
DEFAULT_STABLE_WINDOW_ROOT = Path("outputs/research/stable_window_evaluation")

INSUFFICIENT_SAMPLE_THRESHOLD = 30
"""Below this many random-window rows for a policy we refuse to make
directional claims (we still emit the panel; ``insufficient_sample``
is flagged)."""

_WINDOW_CSV_RE = re.compile(r"^random_windows_(?P<years>\d+)y_(?P<policy>[a-z0-9_]+)\.csv$")
_SUMMARY_CSV_RE = re.compile(r"^random_windows_summary_(?P<policy>[a-z0-9_]+)\.csv$")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _coerce_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    try:
        return list(csv.DictReader(path.read_text(encoding="utf-8").splitlines()))
    except (OSError, csv.Error):
        return []


def _safe_json(path: Path) -> Optional[dict[str, Any]]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _percentile(values: list[float], q: float) -> Optional[float]:
    """Linear-interpolation percentile (q in [0, 1])."""
    if not values:
        return None
    sorted_values = sorted(values)
    if len(sorted_values) == 1:
        return sorted_values[0]
    rank = q * (len(sorted_values) - 1)
    lo = int(rank)
    hi = min(lo + 1, len(sorted_values) - 1)
    frac = rank - lo
    return sorted_values[lo] * (1 - frac) + sorted_values[hi] * frac


def _summary_stats(values: list[float]) -> dict[str, Optional[float]]:
    if not values:
        return {"n": 0, "p10": None, "median": None, "p90": None, "range": None}
    p10 = _percentile(values, 0.10)
    median = _percentile(values, 0.50)
    p90 = _percentile(values, 0.90)
    return {
        "n": len(values),
        "p10": round(p10, 6) if p10 is not None else None,
        "median": round(median, 6) if median is not None else None,
        "p90": round(p90, 6) if p90 is not None else None,
        "range": round(max(values) - min(values), 6),
    }


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def discover_random_window_files(
    research_root: Path = DEFAULT_RESEARCH_ROOT,
) -> list[tuple[str, int, Path]]:
    """Return ``[(policy, years, path), ...]`` for each random-window CSV found.

    Skips files whose row count is zero. Sorted by policy then years.
    """
    if not research_root.exists() or not research_root.is_dir():
        return []
    found: list[tuple[str, int, Path]] = []
    for path in research_root.iterdir():
        if not path.is_file():
            continue
        match = _WINDOW_CSV_RE.match(path.name)
        if not match:
            continue
        try:
            years = int(match.group("years"))
        except (TypeError, ValueError):
            continue
        found.append((match.group("policy").upper(), years, path))
    return sorted(found, key=lambda item: (item[0], item[1]))


def _select_summary_path(
    policy: str,
    research_root: Path,
) -> Optional[Path]:
    candidate = research_root / f"random_windows_summary_{policy.lower()}.csv"
    return candidate if candidate.exists() else None


# ---------------------------------------------------------------------------
# Per-policy panel
# ---------------------------------------------------------------------------


def _build_policy_panel(
    *,
    policy: str,
    years: int,
    rows_path: Path,
    summary_path: Optional[Path],
) -> Optional[dict[str, Any]]:
    rows = _read_csv_rows(rows_path)
    if not rows:
        return None

    cagrs: list[float] = []
    drawdowns: list[float] = []
    sharpes: list[float] = []
    ulcers: list[float] = []
    total_returns: list[float] = []
    parsed_rows: list[dict[str, Any]] = []
    for raw in rows:
        cagr = _coerce_float(raw.get("cagr"))
        dd = _coerce_float(raw.get("max_drawdown"))
        sharpe = _coerce_float(raw.get("sharpe"))
        ulcer = _coerce_float(raw.get("ulcer_index"))
        total = _coerce_float(raw.get("total_return"))
        if cagr is not None:
            cagrs.append(cagr)
        if dd is not None:
            drawdowns.append(dd)
        if sharpe is not None:
            sharpes.append(sharpe)
        if ulcer is not None:
            ulcers.append(ulcer)
        if total is not None:
            total_returns.append(total)
        parsed_rows.append(
            {
                "start_date": raw.get("start_date"),
                "end_date": raw.get("end_date"),
                "policy": raw.get("policy"),
                "cagr": cagr,
                "max_drawdown": dd,
                "sharpe": sharpe,
                "ulcer_index": ulcer,
                "total_return": total,
            }
        )

    n = len(parsed_rows)
    consistency = {
        "fraction_positive_return": (
            round(sum(1 for v in total_returns if v > 0) / len(total_returns), 6)
            if total_returns else None
        ),
        "fraction_positive_sharpe": (
            round(sum(1 for v in sharpes if v > 0) / len(sharpes), 6)
            if sharpes else None
        ),
    }

    dispersion = {
        "cagr": _summary_stats(cagrs),
        "max_drawdown": _summary_stats(drawdowns),
        "sharpe": _summary_stats(sharpes),
        "ulcer_index": _summary_stats(ulcers),
    }

    best_by_cagr = _argextreme(parsed_rows, "cagr", direction="max")
    worst_by_cagr = _argextreme(parsed_rows, "cagr", direction="min")
    worst_by_drawdown = _argextreme(parsed_rows, "max_drawdown", direction="min")

    # Start-date sensitivity: range of CAGR over absolute median CAGR.
    cagr_median = dispersion["cagr"]["median"]
    cagr_range = dispersion["cagr"]["range"]
    if cagr_range is None or cagr_median is None or cagr_median == 0:
        sensitivity_ratio = None
        sensitivity_label = "unavailable"
    else:
        sensitivity_ratio = abs(cagr_range / cagr_median)
        if sensitivity_ratio < 0.5:
            sensitivity_label = "low"
        elif sensitivity_ratio < 1.5:
            sensitivity_label = "moderate"
        else:
            sensitivity_label = "high"

    summary_payload = _read_summary_row(summary_path) if summary_path else None

    insufficient = n < INSUFFICIENT_SAMPLE_THRESHOLD

    return {
        "policy": policy,
        "years": years,
        "n_windows": n,
        "insufficient_sample": insufficient,
        "consistency": consistency,
        "dispersion": dispersion,
        "best_window_by_cagr": best_by_cagr,
        "worst_window_by_cagr": worst_by_cagr,
        "worst_window_by_drawdown": worst_by_drawdown,
        "start_date_sensitivity": {
            "cagr_range_over_median_abs": (
                round(sensitivity_ratio, 6) if sensitivity_ratio is not None else None
            ),
            "interpretation": sensitivity_label,
        },
        "source_summary": summary_payload,
        "source_paths": [str(rows_path)] + (
            [str(summary_path)] if summary_path else []
        ),
    }


def _argextreme(
    rows: list[dict[str, Any]],
    field_name: str,
    *,
    direction: str,
) -> Optional[dict[str, Any]]:
    candidates = [r for r in rows if r.get(field_name) is not None]
    if not candidates:
        return None
    chooser = max if direction == "max" else min
    winner = chooser(candidates, key=lambda r: r[field_name])
    return {
        "start_date": winner.get("start_date"),
        "end_date": winner.get("end_date"),
        "cagr": winner.get("cagr"),
        "max_drawdown": winner.get("max_drawdown"),
        "sharpe": winner.get("sharpe"),
        "total_return": winner.get("total_return"),
    }


def _read_summary_row(path: Path) -> Optional[dict[str, Any]]:
    rows = _read_csv_rows(path)
    if not rows:
        return None
    raw = rows[-1]  # one-row file in practice; take last for stability
    return {
        "policy": raw.get("policy"),
        "years": _coerce_float(raw.get("years")),
        "n_windows": _coerce_float(raw.get("n_windows")),
        "selection_metric": raw.get("selection_metric"),
        "worst_start_date": raw.get("worst_start_date"),
        "worst_end_date": raw.get("worst_end_date"),
        "worst_cagr": _coerce_float(raw.get("worst_cagr")),
        "worst_max_drawdown": _coerce_float(raw.get("worst_max_drawdown")),
        "worst_ulcer_index": _coerce_float(raw.get("worst_ulcer_index")),
        "median_cagr": _coerce_float(raw.get("median_cagr")),
        "median_max_drawdown": _coerce_float(raw.get("median_max_drawdown")),
        "median_ulcer_index": _coerce_float(raw.get("median_ulcer_index")),
    }


# ---------------------------------------------------------------------------
# Promotion-validity (separate signal)
# ---------------------------------------------------------------------------


def _read_promotion_validity(
    stable_window_root: Path = DEFAULT_STABLE_WINDOW_ROOT,
) -> Optional[dict[str, Any]]:
    """Read the latest_loose.json + latest_strict.json (or fall back to the
    most recent dated artifact) and return a compact validity block.

    Returns ``None`` if no usable artifact exists.
    """
    if not stable_window_root.exists():
        return None
    out: dict[str, Any] = {}
    for mode in ("loose", "strict"):
        latest = stable_window_root / f"latest_{mode}.json"
        payload = _safe_json(latest)
        if payload is None:
            # Try to find the newest dated artifact for this mode.
            candidates = sorted(
                p for p in stable_window_root.glob(f"stable_window_eval_{mode}_*.json")
            )
            if candidates:
                payload = _safe_json(candidates[-1])
        if not isinstance(payload, Mapping):
            continue
        out[mode] = {
            "schema_version": payload.get("schema_version"),
            "generated_at": payload.get("generated_at"),
            "valid_days_since_inception": len(payload.get("valid_days_since_inception") or []),
            "valid_days_stable_window": len(payload.get("valid_days_stable_window") or []),
            "shadow_only_days_since_inception": len(payload.get("shadow_only_days_since_inception") or []),
            "shadow_only_days_stable_window": len(payload.get("shadow_only_days_stable_window") or []),
            "diagnostic_excluded_count": len(payload.get("diagnostic_excluded_since") or []),
            "windows": list((payload.get("windows") or {}).keys()),
            "strategies": list((payload.get("strategies") or {}).keys()),
        }
    return out or None


# ---------------------------------------------------------------------------
# Top-level orchestrator
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StableWindowAnswer:
    status: str
    research_root: str
    stable_window_root: str
    policy_panels: list[dict[str, Any]]
    promotion_validity: Optional[dict[str, Any]]
    confidence_caveats: list[str]
    narrative: str
    source_paths: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def stable_window_to_dict(answer: StableWindowAnswer) -> dict[str, Any]:
    return {
        "status": answer.status,
        "research_root": answer.research_root,
        "stable_window_root": answer.stable_window_root,
        "policy_panels": answer.policy_panels,
        "promotion_validity": answer.promotion_validity,
        "confidence_caveats": answer.confidence_caveats,
        "narrative": answer.narrative,
        "source_paths": list(answer.source_paths),
        "warnings": list(answer.warnings),
    }


def evaluate_stable_windows(
    *,
    research_root: Path = DEFAULT_RESEARCH_ROOT,
    stable_window_root: Path = DEFAULT_STABLE_WINDOW_ROOT,
) -> StableWindowAnswer:
    panels: list[dict[str, Any]] = []
    source_paths: list[str] = []
    for policy, years, rows_path in discover_random_window_files(research_root):
        summary_path = _select_summary_path(policy, research_root)
        panel = _build_policy_panel(
            policy=policy,
            years=years,
            rows_path=rows_path,
            summary_path=summary_path,
        )
        if panel is not None:
            panels.append(panel)
            source_paths.extend(panel["source_paths"])

    promotion = _read_promotion_validity(stable_window_root)

    if not panels and promotion is None:
        return StableWindowAnswer(
            status="NO_WINDOW_DATA",
            research_root=str(research_root),
            stable_window_root=str(stable_window_root),
            policy_panels=[],
            promotion_validity=None,
            confidence_caveats=["no_random_window_csv", "no_promotion_validity_artifact"],
            narrative=(
                f"No random-window backtest CSVs under {research_root} and no "
                f"promotion-validity artifacts under {stable_window_root}. "
                "Run the random-window backtest pipeline first."
            ),
            warnings=[
                f"no random_windows_*.csv found under {research_root}",
                f"no stable_window_evaluation artifacts under {stable_window_root}",
            ],
        )

    caveats: list[str] = []
    if any(p.get("insufficient_sample") for p in panels):
        caveats.append("insufficient_sample")
    if len({p["policy"] for p in panels}) > 1:
        caveats.append("mixed_policy_sample")
    if promotion is None:
        caveats.append("no_promotion_validity")
    elif promotion and all(
        m.get("valid_days_since_inception", 0) == 0
        for m in promotion.values()
        if isinstance(m, Mapping)
    ):
        caveats.append("zero_valid_days_for_promotion_math")

    narrative = _render_narrative(panels, promotion, caveats)
    warnings: list[str] = []
    for cav in caveats:
        warnings.append(f"caveat: {cav}")

    return StableWindowAnswer(
        status="OK" if panels or promotion else "NEEDS_DATA",
        research_root=str(research_root),
        stable_window_root=str(stable_window_root),
        policy_panels=panels,
        promotion_validity=promotion,
        confidence_caveats=caveats,
        narrative=narrative,
        source_paths=source_paths,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# Deterministic narrative
# ---------------------------------------------------------------------------


def _fmt_pct(value: Any) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value) * 100:+.2f}%"
    except (TypeError, ValueError):
        return "n/a"


def _fmt_ratio(value: Any) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value):.3f}"
    except (TypeError, ValueError):
        return "n/a"


def _render_narrative(
    panels: list[dict[str, Any]],
    promotion: Optional[Mapping[str, Any]],
    caveats: list[str],
) -> str:
    lines: list[str] = []
    if not panels:
        lines.append("No random-window backtest panels available.")
    else:
        for panel in panels:
            policy = panel["policy"]
            years = panel["years"]
            n = panel["n_windows"]
            cagr = panel["dispersion"]["cagr"]
            drawdown = panel["dispersion"]["max_drawdown"]
            sharpe = panel["dispersion"]["sharpe"]
            consist = panel["consistency"]
            fraction_pos = consist.get("fraction_positive_return")
            sensitivity = panel["start_date_sensitivity"]["interpretation"]
            line = (
                f"Policy {policy} ({years}-year windows, n={n}): "
                f"median CAGR {_fmt_pct(cagr['median'])} "
                f"(p10 {_fmt_pct(cagr['p10'])}, p90 {_fmt_pct(cagr['p90'])}); "
                f"median max drawdown {_fmt_pct(drawdown['median'])}; "
                f"median Sharpe {_fmt_ratio(sharpe['median'])}; "
                f"positive-return windows: "
                f"{_fmt_pct(fraction_pos)} of {n}. "
                f"Start-date sensitivity: {sensitivity}."
            )
            lines.append(line)
            worst_dd = panel.get("worst_window_by_drawdown") or {}
            if worst_dd.get("start_date"):
                lines.append(
                    f"  Worst drawdown window: {worst_dd['start_date']} → "
                    f"{worst_dd.get('end_date', '?')}, max_dd {_fmt_pct(worst_dd.get('max_drawdown'))} "
                    f"(CAGR {_fmt_pct(worst_dd.get('cagr'))})."
                )
            if panel.get("insufficient_sample"):
                lines.append(
                    f"  ⚠ insufficient_sample ({n} < {INSUFFICIENT_SAMPLE_THRESHOLD} windows); "
                    "do not draw directional claims from this policy."
                )
    if promotion:
        lines.append("")
        for mode, block in promotion.items():
            if not isinstance(block, Mapping):
                continue
            lines.append(
                f"Promotion-validity ({mode}): "
                f"{block.get('valid_days_since_inception', 0)} valid days since inception, "
                f"{block.get('shadow_only_days_since_inception', 0)} shadow-only, "
                f"{block.get('diagnostic_excluded_count', 0)} diagnostic-excluded."
            )
    if caveats:
        lines.append("")
        lines.append(f"Confidence caveats: {caveats}")
    return "\n".join(lines)
