#!/usr/bin/env python3
"""FR-028 Phase B governance interpretation review.

Reads Phase A timing-surface artifacts and produces research-only governance
interpretation outputs. This command does not migrate accounting semantics,
rewrite NAV chains, modify dashboards, or change promotion logic.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


STRATEGY_SLUGS = ("caerus_polaris", "caerus_orion", "caerus_lyra")
MODEL_SLUGS = (*STRATEGY_SLUGS, "spy_benchmark")
DISPLAY_NAMES = {
    "caerus_polaris": "Polaris",
    "caerus_orion": "Orion",
    "caerus_lyra": "Lyra",
    "spy_benchmark": "SPY",
}
PROVENANCE = {
    "nav_surface_type": "FR028_GOVERNANCE_INTERPRETATION_REVIEW",
    "timing_semantics": "current_vs_proposed_timing_surface_interpretation",
    "confidence_classification": "RESEARCH_ONLY_NOT_GOVERNANCE_APPROVED",
    "provenance_status": "PHASE_B_INTERPRETATION_ONLY",
    "governance_scope": "NO_RULE_CHANGE_NO_MIGRATION",
    "migration_status": "NOT_MIGRATED",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Review FR-028 Phase A timing surfaces for governance impact.")
    parser.add_argument("--repo-root", default=str(_REPO_ROOT))
    parser.add_argument("--surface-root", default="outputs/fr028_research_surface")
    parser.add_argument("--attribution-root", default="outputs/attribution")
    parser.add_argument("--as-of-date", default=None)
    return parser


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _write_json(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _as_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _round(value: float | None, digits: int = 10) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    return round(float(value), digits)


def _fmt_pct(value: float | None) -> str:
    return "N/A" if value is None else f"{value:+.2%}"


def _latest_surface_date(root: Path) -> str | None:
    dates = []
    for child in root.iterdir() if root.exists() else []:
        if not child.is_dir():
            continue
        try:
            pd.Timestamp(child.name)
        except Exception:
            continue
        dates.append(child.name)
    return max(dates) if dates else None


def _load_phase_a(surface_root: Path, as_of_date: str) -> dict[str, Any]:
    root = surface_root / as_of_date
    required = {
        "current": "current_semantics_nav.json",
        "proposed": "proposed_semantics_nav.json",
        "divergence": "nav_divergence_analysis.json",
        "ranking": "strategy_ranking_delta.json",
        "drawdown": "drawdown_delta_analysis.json",
        "attribution_delta": "attribution_delta_analysis.json",
        "regime_delta": "regime_delta_analysis.json",
        "governance": "governance_impact_review.json",
    }
    payloads = {key: _read_json(root / filename) for key, filename in required.items()}
    missing = [filename for key, filename in required.items() if not payloads[key]]
    if missing:
        raise SystemExit(f"Missing Phase A artifacts for {as_of_date}: {missing}")
    return payloads


def _point_pairs(current: dict[str, Any], proposed: dict[str, Any], slug: str) -> list[dict[str, Any]]:
    current_by_source = {row["source_signal_date"]: row for row in current["strategies"][slug].get("points") or []}
    proposed_by_source = {row["source_signal_date"]: row for row in proposed["strategies"][slug].get("points") or []}
    rows = []
    for source_date in sorted(set(current_by_source) & set(proposed_by_source)):
        left = current_by_source[source_date]
        right = proposed_by_source[source_date]
        rows.append(
            {
                "source_signal_date": source_date,
                "current_date": left.get("date"),
                "proposed_date": right.get("date"),
                "current_return": left.get("daily_return"),
                "proposed_return": right.get("daily_return"),
                "return_delta": _round((_as_float(right.get("daily_return")) or 0.0) - (_as_float(left.get("daily_return")) or 0.0)),
            }
        )
    return rows


def build_rolling_divergence(payloads: dict[str, Any]) -> dict[str, Any]:
    current = payloads["current"]
    proposed = payloads["proposed"]
    strategies = {}
    for slug in STRATEGY_SLUGS:
        pairs = _point_pairs(current, proposed, slug)
        cumulative = []
        acc = 0.0
        for row in pairs:
            acc += _as_float(row.get("return_delta")) or 0.0
            cumulative.append({**row, "cumulative_return_delta": _round(acc)})
        strategies[slug] = {
            "strategy_name": DISPLAY_NAMES[slug],
            "observation_count": len(pairs),
            "rolling_rows": cumulative,
            "status": "LIMITED_SAMPLE" if len(pairs) < 20 else "OK",
        }
    return {"schema_version": "fr028_rolling_divergence_analysis_v1", **PROVENANCE, "strategies": strategies}


def build_long_horizon_review(payloads: dict[str, Any]) -> dict[str, Any]:
    divergence = payloads["divergence"]
    strategies = {}
    for slug in STRATEGY_SLUGS:
        item = divergence["strategies"][slug]
        obs = int(item.get("comparison_observation_count") or 0)
        sensitivity = _as_float(item.get("timing_sensitivity_abs"))
        strategies[slug] = {
            "strategy_name": DISPLAY_NAMES[slug],
            "comparison_observation_count": obs,
            "timing_sensitivity_abs": sensitivity,
            "long_horizon_confidence": "INSUFFICIENT_HISTORY" if obs < 60 else "REVIEWABLE",
            "performance_claim_trust": "NOT_DECISION_GRADE" if obs < 60 else "COMPARISON_READY",
            "survives_timing_correction": sensitivity is not None and sensitivity < 0.05,
        }
    return {"schema_version": "fr028_long_horizon_timing_review_v1", **PROVENANCE, "strategies": strategies}


def build_ranking_stability(payloads: dict[str, Any]) -> dict[str, Any]:
    ranking = payloads["ranking"]
    changes = ranking.get("rank_changes") or {}
    changed = [slug for slug, row in changes.items() if row.get("delta") not in (None, 0)]
    return {
        "schema_version": "fr028_ranking_stability_review_v1",
        **PROVENANCE,
        "current_ranking": ranking.get("current_ranking"),
        "proposed_ranking": ranking.get("proposed_ranking"),
        "rank_changes": changes,
        "ranking_stability": "STABLE" if not changed else "UNSTABLE",
        "changed_strategies": changed,
    }


def build_persistence_report(rolling: dict[str, Any]) -> dict[str, Any]:
    strategies = {}
    for slug in STRATEGY_SLUGS:
        rows = ((rolling.get("strategies") or {}).get(slug) or {}).get("rolling_rows") or []
        signs = [1 if (_as_float(row.get("return_delta")) or 0.0) > 0 else -1 if (_as_float(row.get("return_delta")) or 0.0) < 0 else 0 for row in rows]
        nonzero = [sign for sign in signs if sign != 0]
        persistence = abs(sum(nonzero)) / len(nonzero) if nonzero else None
        strategies[slug] = {
            "strategy_name": DISPLAY_NAMES[slug],
            "observation_count": len(rows),
            "same_direction_persistence": _round(persistence),
            "classification": "INSUFFICIENT_HISTORY" if len(rows) < 20 else "PERSISTENT" if persistence and persistence >= 0.7 else "MIXED",
        }
    return {"schema_version": "fr028_divergence_persistence_report_v1", **PROVENANCE, "strategies": strategies}


def build_regime_specific_reviews(payloads: dict[str, Any], attribution_root: Path, as_of_date: str) -> dict[str, dict[str, Any]]:
    regime_delta = payloads["regime_delta"]
    attribution_delta = payloads["attribution_delta"]
    factor_exposure = _read_json(attribution_root / as_of_date / "factor_exposure.json") or {}
    concentration = _read_json(attribution_root / as_of_date / "concentration_analysis.json") or {}
    risk_flags = _read_json(attribution_root / as_of_date / "factor_risk_flags.json") or {}
    regime_specific = {"schema_version": "fr028_regime_specific_timing_sensitivity_v1", **PROVENANCE, "strategies": {}}
    fragility = {"schema_version": "fr028_timing_fragility_by_regime_v1", **PROVENANCE, "strategies": {}}
    beta_review = {"schema_version": "fr028_beta_amplification_review_v1", **PROVENANCE, "strategies": {}}
    concentration_review = {"schema_version": "fr028_concentration_amplification_review_v1", **PROVENANCE, "strategies": {}}
    for slug in STRATEGY_SLUGS:
        by_regime = ((regime_delta.get("strategies") or {}).get(slug) or {}).get("by_regime") or {}
        attribution = ((attribution_delta.get("strategies") or {}).get(slug) or {})
        factor = ((factor_exposure.get("strategies") or {}).get(slug) or {})
        conc = ((concentration.get("strategies") or {}).get(slug) or {})
        flags = ((risk_flags.get("strategies") or {}).get(slug) or {}).get("flags") or []
        regime_specific["strategies"][slug] = {"strategy_name": DISPLAY_NAMES[slug], "by_regime": by_regime}
        max_regime_delta = max((abs(_as_float(row.get("avg_return_delta")) or 0.0) for row in by_regime.values()), default=0.0)
        fragility["strategies"][slug] = {
            "strategy_name": DISPLAY_NAMES[slug],
            "max_abs_regime_delta": _round(max_regime_delta),
            "classification": "INSUFFICIENT_HISTORY" if sum((row.get("count") or 0) for row in by_regime.values()) < 20 else "FRAGILE" if max_regime_delta >= 0.01 else "STABLE",
        }
        beta = _as_float(factor.get("market_beta"))
        beta_review["strategies"][slug] = {
            "strategy_name": DISPLAY_NAMES[slug],
            "market_beta": beta,
            "timing_sensitivity_abs": attribution.get("timing_sensitivity_abs"),
            "beta_amplification_changes_materially": attribution.get("beta_amplification_changes_materially"),
            "flags": [flag for flag in flags if "beta" in flag or "market" in flag],
        }
        top3 = _as_float(conc.get("top3_contribution_share_21d"))
        concentration_review["strategies"][slug] = {
            "strategy_name": DISPLAY_NAMES[slug],
            "top3_contribution_share_21d": top3,
            "timing_sensitivity_abs": attribution.get("timing_sensitivity_abs"),
            "concentration_amplifies_timing_risk": attribution.get("concentration_amplifies_timing_risk"),
            "flags": [flag for flag in flags if "concentration" in flag],
        }
    return {
        "regime_specific_timing_sensitivity.json": regime_specific,
        "timing_fragility_by_regime.json": fragility,
        "beta_amplification_review.json": beta_review,
        "concentration_amplification_review.json": concentration_review,
    }


def build_promotion_reviews(payloads: dict[str, Any], long_horizon: dict[str, Any], ranking_stability: dict[str, Any]) -> dict[str, dict[str, Any]]:
    divergence = payloads["divergence"]
    strategies = {}
    threshold_rows = {}
    confidence_rows = {}
    for slug in STRATEGY_SLUGS:
        div = divergence["strategies"][slug]
        sensitivity = _as_float(div.get("timing_sensitivity_abs"))
        obs = ((long_horizon.get("strategies") or {}).get(slug) or {}).get("comparison_observation_count")
        strategies[slug] = {
            "strategy_name": DISPLAY_NAMES[slug],
            "promotion_metric_materially_changes": sensitivity is not None and sensitivity >= 0.01,
            "timing_sensitivity_abs": sensitivity,
            "review_status": "AMBIGUOUS_INSUFFICIENT_HISTORY" if (obs or 0) < 60 else "REVIEWABLE",
        }
        threshold_rows[slug] = {
            "strategy_name": DISPLAY_NAMES[slug],
            "existing_thresholds_remain_appropriate": False if sensitivity is not None and sensitivity >= 0.01 else None,
            "reason": "Thresholds cannot be validated until longer timing-corrected history exists." if (obs or 0) < 60 else "Review threshold deltas before migration.",
        }
        confidence_rows[slug] = {
            "strategy_name": DISPLAY_NAMES[slug],
            "current_confidence": "LOW_CONFIDENCE_TIMING_REVIEW_REQUIRED",
            "recommended_phase_b_confidence": "LOW_CONFIDENCE_RESEARCH_ONLY",
            "propagate_low_confidence": True,
        }
    promotion = {
        "schema_version": "fr028_promotion_governance_impact_review_v1",
        **PROVENANCE,
        "status": "NO_PROMOTION_LOGIC_CHANGE",
        "ranking_stability": ranking_stability.get("ranking_stability"),
        "strategies": strategies,
    }
    threshold = {"schema_version": "fr028_threshold_stability_review_v1", **PROVENANCE, "strategies": threshold_rows}
    confidence = {"schema_version": "fr028_confidence_propagation_review_v1", **PROVENANCE, "strategies": confidence_rows}
    return {
        "promotion_governance_impact_review.json": promotion,
        "threshold_stability_review.json": threshold,
        "confidence_propagation_review.json": confidence,
    }


def build_surface_reviews(long_horizon: dict[str, Any]) -> dict[str, Any]:
    authoritative = {
        "schema_version": "fr028_authoritative_surface_review_v1",
        **PROVENANCE,
        "recommendations": {
            "operational_reporting": "LIVE_BROKER_PAPER_NAV for live paper; proposed timing-corrected shadow only after future approval.",
            "cio_dashboards": "Display CURRENT_OPERATIONAL_SHADOW as legacy/low confidence and PROPOSED_TIMING_CORRECTED_RESEARCH as research-only.",
            "promotion_governance": "Do not use proposed surface for gates until longer history and FR approval.",
            "exposure_intelligence": "Exposure metrics can remain additive but must show timing surface labels when tied to performance.",
            "attribution_reporting": "Use proposed surface only for sensitivity analysis until daily holdings history is sufficient.",
            "regime_evaluation": "Require longer timing-corrected sample before authoritative regime conclusions.",
        },
    }
    legacy = {
        "schema_version": "fr028_legacy_surface_policy_review_v1",
        **PROVENANCE,
        "current_operational_shadow_policy": "Remain visible as legacy current semantics with LOW confidence.",
        "deprecation_recommendation": "Do not deprecate until FR-028 later phases produce longer comparison evidence.",
        "legacy_labeling_required": True,
        "reports_requiring_future_disclaimers": ["shadow scorecards", "CIO report", "dashboard performance panels", "promotion readiness reports"],
    }
    return {"authoritative_surface_review.json": authoritative, "legacy_surface_policy_review.json": legacy}


def build_markdown_review(
    *,
    as_of_date: str,
    long_horizon: dict[str, Any],
    ranking: dict[str, Any],
    promotion: dict[str, Any],
    surface: dict[str, Any],
) -> str:
    lines = [
        "# FR-028 Phase B Governance Surface Recommendations",
        "",
        "## Executive Summary",
        "- Status: `RESEARCH_ONLY_GOVERNANCE_INTERPRETATION`",
        f"- As of date: `{as_of_date}`",
        "- No production semantics, historical chains, dashboards, promotion logic, or execution behavior were changed.",
        "",
        "## Long-Horizon Confidence",
        "| Strategy | Observations | Timing Sensitivity | Confidence |",
        "|---|---:|---:|---|",
    ]
    for slug in STRATEGY_SLUGS:
        item = long_horizon["strategies"][slug]
        lines.append(
            f"| {DISPLAY_NAMES[slug]} | {item['comparison_observation_count']} | {_fmt_pct(item['timing_sensitivity_abs'])} | {item['long_horizon_confidence']} |"
        )
    lines.extend(
        [
            "",
            "## Ranking Stability",
            f"- Ranking stability: `{ranking['ranking_stability']}`",
            f"- Changed strategies: `{ranking['changed_strategies']}`",
            "",
            "## Governance Recommendation",
            "- Do not migrate accounting semantics yet.",
            "- Keep operational shadow NAV low confidence.",
            "- Continue building longer parallel history before any Phase C migration proposal.",
            "",
            "## Authoritative Surface Recommendation",
            f"- Operational reporting: {surface['recommendations']['operational_reporting']}",
            f"- Promotion governance: {surface['recommendations']['promotion_governance']}",
        ]
    )
    return "\n".join(lines) + "\n"


def _fmt_pct(value: float | None) -> str:
    return "N/A" if value is None else f"{value:+.2%}"


def run_phase_b(
    *,
    repo_root: Path,
    surface_root: Path,
    attribution_root: Path,
    as_of_date: str | None,
) -> tuple[dict[str, Any], list[Path]]:
    effective_date = as_of_date or _latest_surface_date(surface_root)
    if not effective_date:
        raise SystemExit("No FR-028 Phase A surface found.")
    payloads = _load_phase_a(surface_root, effective_date)
    rolling = build_rolling_divergence(payloads)
    long_horizon = build_long_horizon_review(payloads)
    ranking_stability = build_ranking_stability(payloads)
    persistence = build_persistence_report(rolling)
    regime_reviews = build_regime_specific_reviews(payloads, attribution_root, effective_date)
    promotion_reviews = build_promotion_reviews(payloads, long_horizon, ranking_stability)
    surface_reviews = build_surface_reviews(long_horizon)
    markdown = build_markdown_review(
        as_of_date=effective_date,
        long_horizon=long_horizon,
        ranking=ranking_stability,
        promotion=promotion_reviews["promotion_governance_impact_review.json"],
        surface=surface_reviews["authoritative_surface_review.json"],
    )
    out_dir = surface_root / effective_date / "phase_b_governance_review"
    artifacts = {
        "rolling_divergence_analysis.json": rolling,
        "long_horizon_timing_review.json": long_horizon,
        "ranking_stability_review.json": ranking_stability,
        "divergence_persistence_report.json": persistence,
        **regime_reviews,
        **promotion_reviews,
        **surface_reviews,
    }
    written = [_write_json(out_dir / name, payload) for name, payload in artifacts.items()]
    md_path = out_dir / "governance_surface_recommendations.md"
    md_path.write_text(markdown, encoding="utf-8")
    written.append(md_path)
    summary = {
        "as_of_date": effective_date,
        "artifact_dir": str(out_dir.relative_to(repo_root)),
        "status": "RESEARCH_ONLY_GOVERNANCE_INTERPRETATION",
        "long_horizon_confidence": {
            slug: payload["long_horizon_confidence"]
            for slug, payload in long_horizon["strategies"].items()
        },
        "ranking_stability": ranking_stability["ranking_stability"],
        "migration_recommendation": "DO_NOT_MIGRATE_YET_BUILD_LONGER_PARALLEL_HISTORY",
    }
    return summary, written


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo_root = Path(args.repo_root).resolve()
    surface_root = (repo_root / args.surface_root).resolve() if not Path(args.surface_root).is_absolute() else Path(args.surface_root)
    attribution_root = (repo_root / args.attribution_root).resolve() if not Path(args.attribution_root).is_absolute() else Path(args.attribution_root)
    summary, written = run_phase_b(
        repo_root=repo_root,
        surface_root=surface_root,
        attribution_root=attribution_root,
        as_of_date=args.as_of_date,
    )
    print(f"[FR-028] phase=B status={summary['status']} as_of={summary['as_of_date']}")
    for path in written:
        print(f"[FR-028] wrote {path.relative_to(repo_root)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
