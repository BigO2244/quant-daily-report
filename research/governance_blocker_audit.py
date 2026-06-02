"""Governance blocker audit — Workstream 1.

Reads the Tier 2 / Tier 3 governance artifacts and classifies every
blocker into one of:

  REAL                — strategy-level evidence is real (e.g. measured
                        weak differentiation; measured hit-rate
                        deterioration). The blocker reflects an actual
                        finding about the strategy.
  DATA_QUALITY        — the blocker exists because an upstream artifact
                        is missing or stale (e.g. security master not
                        bootstrapped). Once the artifact is refreshed
                        the blocker disappears without any strategy
                        change.
  CONFIGURATION       — the strategy is operating as designed but the
                        governance gate threshold conflicts with the
                        design. Resolved by gate-threshold review, not
                        strategy change.
  OBSERVATION_WINDOW  — the blocker would resolve with more observation
                        history; nothing wrong with the strategy or
                        the data.

Research-only. Emits two artifacts under
``outputs/research/governance_blocker_audit/<date>/``.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "caerus_governance_blocker_audit_v1"

CLASSIFY_REAL = "REAL"
CLASSIFY_DATA_QUALITY = "DATA_QUALITY"
CLASSIFY_CONFIGURATION = "CONFIGURATION"
CLASSIFY_OBSERVATION_WINDOW = "OBSERVATION_WINDOW"

SEVERITY_HIGH = "HIGH"
SEVERITY_MEDIUM = "MEDIUM"
SEVERITY_LOW = "LOW"

# Concentration "designed-by-construction" thresholds: a strategy
# holding N equal-weight names forces max_single_name_weight >= 1/N
# and top3 >= 3/N. When a measured value matches the mathematical
# floor we attribute the breach to CONFIGURATION rather than REAL.
DESIGN_TOLERANCE = 0.02


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


def _classify_security_master_missing(repo: Path) -> dict[str, Any]:
    sm_root = repo / "data" / "security_master"
    latest_pointer = sm_root / "latest.json"
    ticker_universe_latest = sm_root / "ticker_universe_latest.json"
    aliases_present = (sm_root / "manual_aliases.json").exists()
    if not latest_pointer.exists() and not ticker_universe_latest.exists():
        root_cause = (
            "security_master_root_present_but_latest_pointer_missing"
            if sm_root.exists()
            else "security_master_root_missing"
        )
        return {
            "blocker": "security_master_missing",
            "classification": CLASSIFY_DATA_QUALITY,
            "root_cause": root_cause,
            "confidence": "HIGH",
            "remediation": "Run scripts/build_security_master.py (or equivalent bootstrap) to populate data/security_master/{latest.json, ticker_universe_latest.json}.",
            "severity": SEVERITY_HIGH,
            "supporting_facts": {
                "security_master_root_exists": sm_root.exists(),
                "latest_pointer_exists": latest_pointer.exists(),
                "ticker_universe_latest_exists": ticker_universe_latest.exists(),
                "manual_aliases_exists": aliases_present,
            },
        }
    return {
        "blocker": "security_master_missing",
        "classification": CLASSIFY_REAL,
        "root_cause": "security_master_artifacts_present_so_blocker_should_clear",
        "confidence": "HIGH",
        "remediation": "Rebuild universe_governance after the security master is refreshed; if blocker persists, audit universe_governance path discovery.",
        "severity": SEVERITY_MEDIUM,
        "supporting_facts": {
            "security_master_root_exists": sm_root.exists(),
            "latest_pointer_exists": latest_pointer.exists(),
            "ticker_universe_latest_exists": ticker_universe_latest.exists(),
            "manual_aliases_exists": aliases_present,
        },
    }


def _classify_planned_execution_payload_missing(repo: Path, trade_date: str) -> dict[str, Any]:
    precompute_root = repo / "outputs" / "precompute"
    payload_path = precompute_root / trade_date / "planned_execution_payload.json"
    target_present = payload_path.exists()
    available_dates: list[str] = []
    if precompute_root.exists():
        for child in sorted(precompute_root.iterdir()):
            if child.is_dir() and (child / "planned_execution_payload.json").exists():
                available_dates.append(child.name)
    most_recent = available_dates[-1] if available_dates else None
    if target_present:
        return {
            "blocker": "planned_execution_payload_missing",
            "classification": CLASSIFY_REAL,
            "root_cause": "payload_present_for_target_date_so_blocker_should_clear",
            "confidence": "HIGH",
            "remediation": "Rebuild universe_governance / execution_timing artifacts for the target date.",
            "severity": SEVERITY_LOW,
            "supporting_facts": {
                "payload_path": str(payload_path),
                "available_dates": available_dates,
                "most_recent_payload_date": most_recent,
            },
        }
    if available_dates:
        return {
            "blocker": "planned_execution_payload_missing",
            "classification": CLASSIFY_DATA_QUALITY,
            "root_cause": f"no_payload_for_target_date_{trade_date}_most_recent_payload_{most_recent}",
            "confidence": "HIGH",
            "remediation": "Run the precompute pipeline for the target date, or rebuild research against the most recent date that has a payload.",
            "severity": SEVERITY_HIGH,
            "supporting_facts": {
                "payload_path": str(payload_path),
                "available_dates": available_dates,
                "most_recent_payload_date": most_recent,
            },
        }
    return {
        "blocker": "planned_execution_payload_missing",
        "classification": CLASSIFY_DATA_QUALITY,
        "root_cause": "no_precompute_artifacts_on_disk",
        "confidence": "HIGH",
        "remediation": "Bootstrap precompute pipeline; no historical planned_execution_payload.json artifacts found.",
        "severity": SEVERITY_HIGH,
        "supporting_facts": {
            "payload_path": str(payload_path),
            "available_dates": available_dates,
            "most_recent_payload_date": None,
        },
    }


def _classify_no_planned_orders(
    repo: Path, trade_date: str, payload_classification: dict[str, Any]
) -> dict[str, Any]:
    # If the payload itself is missing the no_planned_orders signal is a
    # downstream symptom — cascade the classification.
    if payload_classification["classification"] == CLASSIFY_DATA_QUALITY:
        return {
            "blocker": "no_planned_orders",
            "classification": CLASSIFY_DATA_QUALITY,
            "root_cause": "cascades_from_planned_execution_payload_missing",
            "confidence": "HIGH",
            "remediation": "Same as planned_execution_payload_missing — restore the payload artifact.",
            "severity": SEVERITY_MEDIUM,
            "supporting_facts": {
                "depends_on": "planned_execution_payload_missing",
            },
        }
    payload_path = repo / "outputs" / "precompute" / trade_date / "planned_execution_payload.json"
    payload = _read_json(payload_path) or {}
    order_count = 0
    if isinstance(payload.get("orders"), list):
        order_count = len(payload["orders"])
    if isinstance(payload.get("trades"), list):
        order_count = max(order_count, len(payload["trades"]))
    if order_count > 0:
        return {
            "blocker": "no_planned_orders",
            "classification": CLASSIFY_REAL,
            "root_cause": f"payload_present_with_{order_count}_orders_so_blocker_should_clear",
            "confidence": "HIGH",
            "remediation": "Rebuild execution_timing/universe_governance against this payload.",
            "severity": SEVERITY_LOW,
            "supporting_facts": {"order_count": order_count},
        }
    return {
        "blocker": "no_planned_orders",
        "classification": CLASSIFY_REAL,
        "root_cause": "payload_present_but_empty_orders_list",
        "confidence": "HIGH",
        "remediation": "Verify the precompute pipeline produced orders (or that the day was intentionally a no-trade day).",
        "severity": SEVERITY_MEDIUM,
        "supporting_facts": {"order_count": order_count},
    }


def _classify_missing_timing_coverage(
    repo: Path,
    trade_date: str,
    payload_classification: dict[str, Any],
) -> dict[str, Any]:
    timing_path = repo / "outputs" / "research" / "execution_timing" / trade_date / "execution_timing_summary.json"
    timing = _read_json(timing_path)
    if timing is None:
        return {
            "blocker": "missing_timing_coverage",
            "classification": CLASSIFY_DATA_QUALITY,
            "root_cause": "execution_timing_summary_artifact_missing",
            "confidence": "HIGH",
            "remediation": "Run scripts/build_execution_timing_counterfactual.py --date <date>.",
            "severity": SEVERITY_HIGH,
            "supporting_facts": {"path": str(timing_path)},
        }
    if payload_classification["classification"] == CLASSIFY_DATA_QUALITY:
        return {
            "blocker": "missing_timing_coverage",
            "classification": CLASSIFY_DATA_QUALITY,
            "root_cause": "cascades_from_planned_execution_payload_missing",
            "confidence": "HIGH",
            "remediation": "Restore the planned execution payload; timing coverage will follow.",
            "severity": SEVERITY_HIGH,
            "supporting_facts": {
                "depends_on": "planned_execution_payload_missing",
                "coverage_ratio": _safe_float(timing.get("coverage_ratio")),
            },
        }
    coverage = _safe_float(timing.get("coverage_ratio"))
    if coverage is not None and coverage >= 0.5:
        return {
            "blocker": "missing_timing_coverage",
            "classification": CLASSIFY_REAL,
            "root_cause": "timing_coverage_above_floor_so_blocker_should_clear",
            "confidence": "HIGH",
            "remediation": "Re-run promotion_governance to clear this blocker.",
            "severity": SEVERITY_LOW,
            "supporting_facts": {"coverage_ratio": coverage},
        }
    return {
        "blocker": "missing_timing_coverage",
        "classification": CLASSIFY_REAL,
        "root_cause": "timing_coverage_below_floor",
        "confidence": "MEDIUM",
        "remediation": "Increase symbol coverage in execution_timing_cache builder, or relax the timing coverage floor in promotion governance.",
        "severity": SEVERITY_MEDIUM,
        "supporting_facts": {"coverage_ratio": coverage},
    }


def _classify_universe_governance_incomplete(
    universe_payload: dict[str, Any] | None,
    security_master_classification: dict[str, Any],
) -> dict[str, Any]:
    if universe_payload is None:
        return {
            "blocker": "universe_governance_incomplete",
            "classification": CLASSIFY_DATA_QUALITY,
            "root_cause": "universe_governance_artifact_missing",
            "confidence": "HIGH",
            "remediation": "Run scripts/build_universe_governance.py --date <date>.",
            "severity": SEVERITY_HIGH,
            "supporting_facts": {},
        }
    blockers = universe_payload.get("blockers") or []
    reason_codes = universe_payload.get("reason_codes") or []
    if security_master_classification["classification"] == CLASSIFY_DATA_QUALITY:
        return {
            "blocker": "universe_governance_incomplete",
            "classification": CLASSIFY_DATA_QUALITY,
            "root_cause": "cascades_from_security_master_missing",
            "confidence": "HIGH",
            "remediation": "Restore security master; universe governance will recompute cleanly.",
            "severity": SEVERITY_HIGH,
            "supporting_facts": {
                "blockers": [str(b) for b in blockers],
                "reason_codes": [str(c) for c in reason_codes],
            },
        }
    if not bool(universe_payload.get("available")):
        return {
            "blocker": "universe_governance_incomplete",
            "classification": CLASSIFY_REAL,
            "root_cause": "universe_governance_marked_unavailable",
            "confidence": "MEDIUM",
            "remediation": "Address each universe blocker explicitly.",
            "severity": SEVERITY_MEDIUM,
            "supporting_facts": {
                "blockers": [str(b) for b in blockers],
                "reason_codes": [str(c) for c in reason_codes],
            },
        }
    return {
        "blocker": "universe_governance_incomplete",
        "classification": CLASSIFY_REAL,
        "root_cause": "universe_governance_clean_so_blocker_should_clear",
        "confidence": "HIGH",
        "remediation": "Re-run promotion_governance.",
        "severity": SEVERITY_LOW,
        "supporting_facts": {
            "blockers": [str(b) for b in blockers],
            "reason_codes": [str(c) for c in reason_codes],
        },
    }


def _classify_weak_differentiation(
    differentiation_payload: dict[str, Any] | None,
    promotion_windows_payload: dict[str, Any] | None,
) -> dict[str, Any]:
    if differentiation_payload is None:
        return {
            "blocker": "weak_differentiation",
            "classification": CLASSIFY_DATA_QUALITY,
            "root_cause": "strategy_differentiation_artifact_missing",
            "confidence": "HIGH",
            "remediation": "Run scripts/build_strategy_differentiation.py --date <date>.",
            "severity": SEVERITY_HIGH,
            "supporting_facts": {},
        }
    pairs = differentiation_payload.get("pairs") or []
    weak_pairs = [
        pair for pair in pairs if str((pair or {}).get("differentiation_readiness_flag") or "").upper() == "WEAK"
    ]
    max_obs = 0
    if promotion_windows_payload is not None:
        for sname in ("caerus_lyra", "caerus_orion", "caerus_polaris"):
            row = (promotion_windows_payload.get("strategies") or {}).get(sname) or {}
            for window in (row.get("windows") or {}).values():
                obs = _safe_float((window or {}).get("observation_count"))
                if obs is not None:
                    max_obs = max(max_obs, int(obs))
    if not weak_pairs:
        return {
            "blocker": "weak_differentiation",
            "classification": CLASSIFY_REAL,
            "root_cause": "no_weak_pairs_present_so_blocker_should_clear",
            "confidence": "HIGH",
            "remediation": "Re-run promotion_governance.",
            "severity": SEVERITY_LOW,
            "supporting_facts": {"pair_count": len(pairs), "weak_pair_count": 0, "max_observation_count": max_obs},
        }
    if max_obs < 40:
        return {
            "blocker": "weak_differentiation",
            "classification": CLASSIFY_OBSERVATION_WINDOW,
            "root_cause": f"weak_pairs_with_only_{max_obs}_day_max_window",
            "confidence": "MEDIUM",
            "remediation": "Continue accumulating observations before treating weak differentiation as durable.",
            "severity": SEVERITY_MEDIUM,
            "supporting_facts": {"pair_count": len(pairs), "weak_pair_count": len(weak_pairs), "max_observation_count": max_obs},
        }
    return {
        "blocker": "weak_differentiation",
        "classification": CLASSIFY_REAL,
        "root_cause": f"{len(weak_pairs)}_weak_pairs_with_{max_obs}_day_history",
        "confidence": "HIGH",
        "remediation": "Strategy-level work required: diversify holdings or change selection logic to lower correlation/overlap with incumbent.",
        "severity": SEVERITY_HIGH,
        "supporting_facts": {
            "pair_count": len(pairs),
            "weak_pair_count": len(weak_pairs),
            "max_observation_count": max_obs,
            "weak_pair_strategies": sorted({(p.get("left_strategy"), p.get("right_strategy")) for p in weak_pairs}, key=str),
        },
    }


def _classify_hit_rate_deteriorated(promotion_windows_payload: dict[str, Any] | None) -> dict[str, Any]:
    if promotion_windows_payload is None:
        return {
            "blocker": "hit_rate_deteriorated",
            "classification": CLASSIFY_DATA_QUALITY,
            "root_cause": "promotion_readiness_windows_artifact_missing",
            "confidence": "HIGH",
            "remediation": "Run scripts/build_promotion_readiness_windows.py --date <date>.",
            "severity": SEVERITY_HIGH,
            "supporting_facts": {},
        }
    deteriorated_strategies: list[dict[str, Any]] = []
    max_obs = 0
    strategies = promotion_windows_payload.get("strategies") or {}
    for strategy_name, strategy_row in strategies.items():
        windows = (strategy_row or {}).get("windows") or {}
        if not windows:
            continue
        # Sort by window size ascending; compare smallest vs largest.
        sized: list[tuple[int, dict[str, Any]]] = []
        for label, row in windows.items():
            try:
                size = int(label)
            except Exception:
                continue
            sized.append((size, row or {}))
            obs = _safe_float((row or {}).get("observation_count"))
            if obs is not None:
                max_obs = max(max_obs, int(obs))
        sized.sort()
        if len(sized) < 2:
            continue
        small_hr = _safe_float(sized[0][1].get("hit_rate"))
        large_hr = _safe_float(sized[-1][1].get("hit_rate"))
        if small_hr is None or large_hr is None:
            continue
        delta = small_hr - large_hr
        if delta > 0.05:
            deteriorated_strategies.append(
                {"strategy": strategy_name, "delta": round(delta, 4), "small_window_hit_rate": small_hr, "large_window_hit_rate": large_hr}
            )
    if not deteriorated_strategies:
        return {
            "blocker": "hit_rate_deteriorated",
            "classification": CLASSIFY_REAL,
            "root_cause": "no_strategy_shows_>5pp_hit_rate_drop",
            "confidence": "HIGH",
            "remediation": "Re-run promotion_governance.",
            "severity": SEVERITY_LOW,
            "supporting_facts": {"deteriorated_strategies": [], "max_observation_count": max_obs},
        }
    if max_obs < 40:
        return {
            "blocker": "hit_rate_deteriorated",
            "classification": CLASSIFY_OBSERVATION_WINDOW,
            "root_cause": f"deterioration_observed_with_only_{max_obs}_day_max_window",
            "confidence": "MEDIUM",
            "remediation": "Continue accumulating observations; short windows amplify hit-rate volatility.",
            "severity": SEVERITY_MEDIUM,
            "supporting_facts": {"deteriorated_strategies": deteriorated_strategies, "max_observation_count": max_obs},
        }
    return {
        "blocker": "hit_rate_deteriorated",
        "classification": CLASSIFY_REAL,
        "root_cause": "measurable_hit_rate_drop_between_short_and_long_window",
        "confidence": "MEDIUM",
        "remediation": "Strategy-level review: is signal decaying, or is small-window sample over-fitting?",
        "severity": SEVERITY_MEDIUM,
        "supporting_facts": {"deteriorated_strategies": deteriorated_strategies, "max_observation_count": max_obs},
    }


def _classify_concentration_above_caps(risk_payload: dict[str, Any] | None) -> dict[str, Any]:
    if risk_payload is None:
        return {
            "blocker": "concentration_above_caps",
            "classification": CLASSIFY_DATA_QUALITY,
            "root_cause": "risk_coverage_artifact_missing",
            "confidence": "HIGH",
            "remediation": "Run scripts/build_risk_coverage.py --date <date>.",
            "severity": SEVERITY_HIGH,
            "supporting_facts": {},
        }
    strategies = risk_payload.get("strategies") or {}
    findings: list[dict[str, Any]] = []
    any_real = False
    any_configuration = False
    for strategy_name, row in strategies.items():
        if not isinstance(row, dict) or not row.get("available"):
            continue
        max_name = _safe_float(row.get("max_single_name_weight"))
        top3 = _safe_float(row.get("top3_concentration"))
        position_count = int(_safe_float(row.get("position_count")) or 0)
        # Equal-weight floor when n positions are held equal.
        equal_weight_max_name = 1.0 / position_count if position_count > 0 else None
        equal_weight_top3 = 3.0 / position_count if position_count >= 3 else None
        violations: list[str] = []
        designed: list[str] = []
        if max_name is not None and max_name > 0.10:
            if (
                equal_weight_max_name is not None
                and abs(max_name - equal_weight_max_name) <= DESIGN_TOLERANCE
            ):
                designed.append("max_single_name_weight_matches_equal_weight_floor")
            else:
                violations.append(f"max_single_name_weight={max_name}_above_0.10")
        if top3 is not None and top3 > 0.40:
            if (
                equal_weight_top3 is not None
                and abs(top3 - equal_weight_top3) <= DESIGN_TOLERANCE
            ):
                designed.append("top3_concentration_matches_equal_weight_floor")
            else:
                violations.append(f"top3_concentration={top3}_above_0.40")
        findings.append(
            {
                "strategy": strategy_name,
                "position_count": position_count,
                "max_single_name_weight": max_name,
                "top3_concentration": top3,
                "equal_weight_max_name_floor": equal_weight_max_name,
                "equal_weight_top3_floor": equal_weight_top3,
                "true_violations": violations,
                "designed_by_construction": designed,
            }
        )
        if violations:
            any_real = True
        if designed and not violations:
            any_configuration = True
    if not findings:
        return {
            "blocker": "concentration_above_caps",
            "classification": CLASSIFY_DATA_QUALITY,
            "root_cause": "no_per_strategy_risk_rows_available",
            "confidence": "HIGH",
            "remediation": "Rebuild risk_coverage with strategy-level holdings.",
            "severity": SEVERITY_HIGH,
            "supporting_facts": {},
        }
    if any_real:
        return {
            "blocker": "concentration_above_caps",
            "classification": CLASSIFY_REAL,
            "root_cause": "at_least_one_strategy_exceeds_cap_beyond_equal_weight_floor",
            "confidence": "HIGH",
            "remediation": "Either raise position count or rebalance away from the violating name.",
            "severity": SEVERITY_HIGH,
            "supporting_facts": {"strategies": findings},
        }
    if any_configuration:
        return {
            "blocker": "concentration_above_caps",
            "classification": CLASSIFY_CONFIGURATION,
            "root_cause": "concentration_matches_equal_weight_floor_for_designed_position_count",
            "confidence": "HIGH",
            "remediation": "Either raise the strategy's position count or relax the governance concentration cap to reflect the designed N.",
            "severity": SEVERITY_MEDIUM,
            "supporting_facts": {"strategies": findings},
        }
    return {
        "blocker": "concentration_above_caps",
        "classification": CLASSIFY_REAL,
        "root_cause": "no_strategy_exceeds_cap_so_blocker_should_clear",
        "confidence": "HIGH",
        "remediation": "Re-run promotion_governance.",
        "severity": SEVERITY_LOW,
        "supporting_facts": {"strategies": findings},
    }


def build_governance_blocker_audit(
    *,
    trade_date: str,
    repo_root: Path | str = Path("."),
    output_root: Path | str | None = None,
) -> dict[str, Any]:
    repo = Path(repo_root)
    universe_payload = _read_json(repo / "outputs" / "research" / "universe_governance" / trade_date / "universe_governance.json")
    timing_payload = _read_json(repo / "outputs" / "research" / "execution_timing" / trade_date / "execution_timing_summary.json")
    differentiation_payload = _read_json(repo / "outputs" / "research" / "strategy_differentiation" / trade_date / "strategy_differentiation.json")
    risk_payload = _read_json(repo / "outputs" / "research" / "risk_coverage" / trade_date / "risk_coverage.json")
    promotion_payload = _read_json(repo / "outputs" / "research" / "promotion_readiness" / trade_date / "promotion_readiness_windows.json")

    sm_class = _classify_security_master_missing(repo)
    payload_class = _classify_planned_execution_payload_missing(repo, trade_date)
    no_orders_class = _classify_no_planned_orders(repo, trade_date, payload_class)
    timing_class = _classify_missing_timing_coverage(repo, trade_date, payload_class)
    universe_class = _classify_universe_governance_incomplete(universe_payload, sm_class)
    diff_class = _classify_weak_differentiation(differentiation_payload, promotion_payload)
    hit_class = _classify_hit_rate_deteriorated(promotion_payload)
    conc_class = _classify_concentration_above_caps(risk_payload)

    classifications = [sm_class, payload_class, no_orders_class, timing_class, universe_class, diff_class, hit_class, conc_class]

    counts: dict[str, int] = {
        CLASSIFY_REAL: 0,
        CLASSIFY_DATA_QUALITY: 0,
        CLASSIFY_CONFIGURATION: 0,
        CLASSIFY_OBSERVATION_WINDOW: 0,
    }
    for row in classifications:
        counts[row["classification"]] = counts.get(row["classification"], 0) + 1

    available = all(p is not None for p in (universe_payload, timing_payload, differentiation_payload, risk_payload, promotion_payload))
    source_artifacts: list[str] = []
    for path, present in [
        ("outputs/research/universe_governance", universe_payload is not None),
        ("outputs/research/execution_timing", timing_payload is not None),
        ("outputs/research/strategy_differentiation", differentiation_payload is not None),
        ("outputs/research/risk_coverage", risk_payload is not None),
        ("outputs/research/promotion_readiness", promotion_payload is not None),
    ]:
        if present:
            source_artifacts.append(f"{path}/{trade_date}/")

    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "date": trade_date,
        "available": available,
        "confidence": "HIGH" if available else "MEDIUM",
        "classification_counts": counts,
        "classifications": classifications,
        "reason_codes": sorted({row["classification"] for row in classifications}) or ["ok"],
        "source_artifacts": source_artifacts,
    }

    out_dir = (Path(output_root) if output_root is not None else repo / "outputs" / "research" / "governance_blocker_audit") / trade_date
    _write_json(out_dir / "governance_blocker_audit.json", payload)
    _write_text(out_dir / "governance_blocker_audit.md", render_markdown(payload))
    return payload


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        f"# Governance Blocker Audit - {payload.get('date')}",
        "",
        f"- Available: {payload.get('available')}",
        f"- Confidence: {payload.get('confidence')}",
        "",
        "## Classification Counts",
        "",
        "| Classification | Count |",
        "|---|---:|",
    ]
    for cls, count in (payload.get("classification_counts") or {}).items():
        lines.append(f"| {cls} | {count} |")
    lines += [
        "",
        "## Per-Blocker Audit",
        "",
        "| Blocker | Classification | Severity | Root Cause | Remediation |",
        "|---|---|---|---|---|",
    ]
    for row in payload.get("classifications") or []:
        lines.append(
            f"| {row.get('blocker')} | {row.get('classification')} | {row.get('severity')} | {row.get('root_cause')} | {row.get('remediation')} |"
        )
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit governance blockers and classify each by root cause.")
    parser.add_argument("--date", required=True)
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--output-root", default=None)
    args = parser.parse_args(argv)
    payload = build_governance_blocker_audit(
        trade_date=args.date,
        repo_root=Path(args.repo_root),
        output_root=Path(args.output_root) if args.output_root else None,
    )
    print(
        json.dumps(
            {
                "date": args.date,
                "available": payload["available"],
                "confidence": payload["confidence"],
                "classification_counts": payload["classification_counts"],
                "reason_codes": payload["reason_codes"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
