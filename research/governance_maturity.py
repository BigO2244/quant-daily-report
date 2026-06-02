"""Governance maturity score — Workstream 6.

Replaces subjective "evidence maturity" judgments with a deterministic
0-1 score per component, rolled up into a single maturity tier:

  IMMATURE         total_score <  0.30
  EMERGING         total_score <  0.50
  DEVELOPING       total_score <  0.70
  MATURE           total_score <  0.90
  PROMOTION_READY  total_score >= 0.90

Components (each 0-1):

  execution_coverage           — execution_timing_summary.coverage_ratio
  risk_coverage                — risk_coverage.available + confidence
  universe_coverage            — universe_governance.available +
                                  symbol_check pass rate
  attribution_coverage         — position_attribution available +
                                  decision_attribution available
  timing_coverage              — execution_timing_summary.available +
                                  symbols_evaluated > 0
  differentiation_confidence   — strategy_differentiation.confidence +
                                  factor_exposure_available
  observation_window_maturity  — promotion_readiness_windows.max
                                  observation_count / 60

Research-only.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "caerus_governance_maturity_v1"

TIER_IMMATURE = "IMMATURE"
TIER_EMERGING = "EMERGING"
TIER_DEVELOPING = "DEVELOPING"
TIER_MATURE = "MATURE"
TIER_PROMOTION_READY = "PROMOTION_READY"

CONFIDENCE_SCORE = {"LOW": 0.33, "MEDIUM": 0.67, "HIGH": 1.0}


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    except Exception:
        return None


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        f = float(value)
    except Exception:
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


def _round(value: Any, digits: int = 6) -> float:
    f = _safe_float(value)
    return round(f if f is not None else 0.0, digits)


def _score_execution_coverage(timing: dict[str, Any] | None) -> tuple[float, str]:
    if not isinstance(timing, dict):
        return 0.0, "execution_timing_artifact_missing"
    if not timing.get("available"):
        return 0.0, "execution_timing_unavailable"
    coverage = _safe_float(timing.get("coverage_ratio"))
    if coverage is None:
        return 0.0, "coverage_ratio_missing"
    return _clamp(coverage), "ok"


def _score_risk_coverage(risk: dict[str, Any] | None) -> tuple[float, str]:
    if not isinstance(risk, dict):
        return 0.0, "risk_coverage_artifact_missing"
    if not risk.get("available"):
        return 0.0, "risk_coverage_unavailable"
    base = CONFIDENCE_SCORE.get(str(risk.get("confidence") or "LOW").upper(), 0.33)
    strategies = risk.get("strategies") or {}
    if strategies:
        covered = sum(1 for row in strategies.values() if isinstance(row, dict) and row.get("available"))
        coverage_ratio = covered / len(strategies) if strategies else 0.0
        return _clamp(0.5 * base + 0.5 * coverage_ratio), "ok"
    return _clamp(base), "no_per_strategy_rows"


def _score_universe_coverage(universe: dict[str, Any] | None) -> tuple[float, str]:
    if not isinstance(universe, dict):
        return 0.0, "universe_governance_artifact_missing"
    if not universe.get("available"):
        return 0.0, "universe_governance_unavailable"
    checks = universe.get("symbol_checks") or []
    if not checks:
        return _clamp(CONFIDENCE_SCORE.get(str(universe.get("confidence") or "LOW").upper(), 0.33)), "no_symbol_checks"
    ok = sum(1 for row in checks if isinstance(row, dict) and str(row.get("status") or "").lower() == "ok")
    pass_rate = ok / len(checks) if checks else 0.0
    return _clamp(pass_rate), "ok"


def _score_attribution_coverage(repo: Path, trade_date: str) -> tuple[float, str]:
    position = _read_json(repo / "outputs" / "attribution" / trade_date / "position_attribution.json")
    decision = _read_json(repo / "outputs" / "decision_attribution" / trade_date / "strategy_decision_summary.json")
    if position is None and decision is None:
        return 0.0, "no_attribution_artifacts"
    score = 0.0
    reason: list[str] = []
    if position is not None:
        score += 0.5
    else:
        reason.append("position_attribution_missing")
    if decision is not None:
        score += 0.5
    else:
        reason.append("decision_attribution_missing")
    return _clamp(score), ", ".join(reason) or "ok"


def _score_timing_coverage(timing: dict[str, Any] | None) -> tuple[float, str]:
    if not isinstance(timing, dict):
        return 0.0, "execution_timing_artifact_missing"
    if not timing.get("available"):
        return 0.0, "execution_timing_unavailable"
    symbols = _safe_float(timing.get("symbols_evaluated"))
    if symbols is None or symbols <= 0:
        return 0.0, "zero_symbols_evaluated"
    # Cap at 50 symbols → 1.0 score
    return _clamp(symbols / 50.0), "ok"


def _score_differentiation_confidence(diff: dict[str, Any] | None) -> tuple[float, str]:
    if not isinstance(diff, dict):
        return 0.0, "strategy_differentiation_artifact_missing"
    base = CONFIDENCE_SCORE.get(str(diff.get("confidence") or "LOW").upper(), 0.33)
    factor_ok = 1.0 if diff.get("factor_exposure_available") else 0.0
    contrib_ok = 1.0 if diff.get("position_contributions_available") else 0.0
    return _clamp(0.5 * base + 0.25 * factor_ok + 0.25 * contrib_ok), "ok"


def _score_observation_window(promotion: dict[str, Any] | None) -> tuple[float, str]:
    if not isinstance(promotion, dict):
        return 0.0, "promotion_readiness_windows_artifact_missing"
    strategies = promotion.get("strategies") or {}
    max_obs = 0
    for row in strategies.values():
        windows = (row or {}).get("windows") or {}
        for window in windows.values():
            v = _safe_float((window or {}).get("observation_count"))
            if v is not None:
                max_obs = max(max_obs, int(v))
    if max_obs == 0:
        return 0.0, "no_observation_windows_present"
    return _clamp(max_obs / 60.0), "ok"


def _summarize_blockers(audit: dict[str, Any] | None) -> dict[str, int]:
    counts = {"REAL": 0, "DATA_QUALITY": 0, "CONFIGURATION": 0, "OBSERVATION_WINDOW": 0}
    if not isinstance(audit, dict):
        return counts
    for row in audit.get("classifications") or []:
        cls = str((row or {}).get("classification") or "").upper()
        if cls in counts:
            # A blocker is only "live" if its root_cause does not say
            # blocker_should_clear.
            if "blocker_should_clear" in str((row or {}).get("root_cause") or ""):
                continue
            counts[cls] += 1
    return counts


def _score_blocker_quality(audit: dict[str, Any] | None) -> tuple[float, str, dict[str, int]]:
    """Blocker quality score rewards low blocker counts overall and is
    especially sensitive to REAL and DATA_QUALITY blockers. CONFIGURATION
    blockers are weighted lighter because they reflect governance/threshold
    decisions rather than strategy or hygiene problems.

    Returns ``(score, reason, breakdown_counts)``."""
    counts = _summarize_blockers(audit)
    if not isinstance(audit, dict):
        return 0.5, "governance_blocker_audit_artifact_missing", counts
    weights = {"REAL": 1.0, "DATA_QUALITY": 0.7, "OBSERVATION_WINDOW": 0.5, "CONFIGURATION": 0.3}
    total_weighted = sum(weights[k] * counts.get(k, 0) for k in weights)
    # Max plausible weighted load: 8 blockers × max-weight (1.0) = 8.
    score = _clamp(1.0 - total_weighted / 8.0)
    return score, "ok", counts


def _tier_for_score(score: float) -> str:
    if score >= 0.90:
        return TIER_PROMOTION_READY
    if score >= 0.70:
        return TIER_MATURE
    if score >= 0.50:
        return TIER_DEVELOPING
    if score >= 0.30:
        return TIER_EMERGING
    return TIER_IMMATURE


def build_governance_maturity(
    *,
    trade_date: str,
    repo_root: Path | str = Path("."),
    output_root: Path | str | None = None,
) -> dict[str, Any]:
    repo = Path(repo_root)
    timing = _read_json(repo / "outputs" / "research" / "execution_timing" / trade_date / "execution_timing_summary.json")
    risk = _read_json(repo / "outputs" / "research" / "risk_coverage" / trade_date / "risk_coverage.json")
    universe = _read_json(repo / "outputs" / "research" / "universe_governance" / trade_date / "universe_governance.json")
    differentiation = _read_json(repo / "outputs" / "research" / "strategy_differentiation" / trade_date / "strategy_differentiation.json")
    promotion = _read_json(repo / "outputs" / "research" / "promotion_readiness" / trade_date / "promotion_readiness_windows.json")
    blocker_audit = _read_json(repo / "outputs" / "research" / "governance_blocker_audit" / trade_date / "governance_blocker_audit.json")

    blocker_quality_score, blocker_quality_reason, blocker_counts = _score_blocker_quality(blocker_audit)

    components = [
        ("execution_coverage", _score_execution_coverage(timing)),
        ("risk_coverage", _score_risk_coverage(risk)),
        ("universe_coverage", _score_universe_coverage(universe)),
        ("attribution_coverage", _score_attribution_coverage(repo, trade_date)),
        ("timing_coverage", _score_timing_coverage(timing)),
        ("differentiation_confidence", _score_differentiation_confidence(differentiation)),
        ("observation_window_maturity", _score_observation_window(promotion)),
        ("blocker_quality", (blocker_quality_score, blocker_quality_reason)),
    ]
    component_rows = [
        {"component": name, "score": _round(score), "reason": reason}
        for name, (score, reason) in components
    ]
    total = sum(score for _, (score, _) in components) / len(components) if components else 0.0
    tier = _tier_for_score(total)

    reason_codes = sorted({row["reason"] for row in component_rows if row["reason"] not in ("ok", "")} or ["ok"])

    payload = {
        "schema_version": SCHEMA_VERSION,
        "date": trade_date,
        "available": True,
        "confidence": "HIGH" if all(_safe_float(r["score"]) is not None and _safe_float(r["score"]) > 0 for r in component_rows) else "MEDIUM",
        "total_score": _round(total),
        "tier": tier,
        "components": component_rows,
        "blockers_real": int(blocker_counts.get("REAL", 0)),
        "blockers_configuration": int(blocker_counts.get("CONFIGURATION", 0)),
        "blockers_data_quality": int(blocker_counts.get("DATA_QUALITY", 0)),
        "blockers_observation_window": int(blocker_counts.get("OBSERVATION_WINDOW", 0)),
        "reason_codes": reason_codes,
        "source_artifacts": sorted(
            p for p, present in [
                (f"outputs/research/execution_timing/{trade_date}/execution_timing_summary.json", timing is not None),
                (f"outputs/research/risk_coverage/{trade_date}/risk_coverage.json", risk is not None),
                (f"outputs/research/universe_governance/{trade_date}/universe_governance.json", universe is not None),
                (f"outputs/research/strategy_differentiation/{trade_date}/strategy_differentiation.json", differentiation is not None),
                (f"outputs/research/promotion_readiness/{trade_date}/promotion_readiness_windows.json", promotion is not None),
                (f"outputs/research/governance_blocker_audit/{trade_date}/governance_blocker_audit.json", blocker_audit is not None),
            ] if present
        ),
    }

    out_dir = (Path(output_root) if output_root is not None else repo / "outputs" / "research" / "governance_maturity") / trade_date
    _write_json(out_dir / "governance_maturity.json", payload)
    _write_text(out_dir / "governance_maturity.md", render_markdown(payload))
    return payload


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        f"# Governance Maturity - {payload.get('date')}",
        "",
        f"- Total score: {payload.get('total_score')}",
        f"- Tier: {payload.get('tier')}",
        f"- Confidence: {payload.get('confidence')}",
        f"- Reason codes: {', '.join(payload.get('reason_codes') or [])}",
        "",
        "## Components",
        "",
        "| Component | Score | Reason |",
        "|---|---:|---|",
    ]
    for row in payload.get("components") or []:
        lines.append(f"| {row.get('component')} | {row.get('score')} | {row.get('reason')} |")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Score governance maturity deterministically.")
    parser.add_argument("--date", required=True)
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--output-root", default=None)
    args = parser.parse_args(argv)
    payload = build_governance_maturity(
        trade_date=args.date,
        repo_root=Path(args.repo_root),
        output_root=Path(args.output_root) if args.output_root else None,
    )
    print(
        json.dumps(
            {
                "date": args.date,
                "tier": payload["tier"],
                "total_score": payload["total_score"],
                "reason_codes": payload["reason_codes"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
