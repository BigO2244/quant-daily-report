"""Governance threshold calibration — FR-040.

Replaces fixed concentration limits with design-aware limits so a 5-name
equal-weight strategy is not mathematically blocked by a 10% cap built
for diversified portfolios.

Design classes (by position_count):

    MICRO_PORTFOLIO   <= 5
    CONCENTRATED      6 - 10
    STANDARD          11 - 25
    DIVERSIFIED       > 25

For each class, the calibrated concentration ceilings are:

    MICRO_PORTFOLIO   max_single_name=0.25  top3=0.75  top5=1.00
    CONCENTRATED      max_single_name=0.15  top3=0.60  top5=0.85
    STANDARD          max_single_name=0.10  top3=0.45  top5=0.65
    DIVERSIFIED       max_single_name=0.07  top3=0.30  top5=0.50

Outputs (research-only, additive):

    outputs/research/governance_calibration/<date>/
        governance_calibration.json
        governance_calibration.md
        governance_reclassification.json
        governance_reclassification.md

The reclassification artifact shows what each strategy's verdict would
have been under the OLD fixed thresholds vs the NEW calibrated ones.
This is a research diagnostic; production allocation, strategy
construction, broker, cron and execution behavior are unchanged.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

SCHEMA_VERSION_CALIBRATION = "caerus_governance_calibration_v1"
SCHEMA_VERSION_RECLASSIFICATION = "caerus_governance_reclassification_v1"

STRATEGIES = ("caerus_polaris", "caerus_orion", "caerus_lyra")

DESIGN_MICRO = "MICRO_PORTFOLIO"
DESIGN_CONCENTRATED = "CONCENTRATED"
DESIGN_STANDARD = "STANDARD"
DESIGN_DIVERSIFIED = "DIVERSIFIED"
DESIGN_UNKNOWN = "UNKNOWN"

CALIBRATION_STATUS_CLEAN = "DESIGN_CONSISTENT_CONCENTRATION"
CALIBRATION_STATUS_RISK = "TRUE_CONCENTRATION_RISK"
CALIBRATION_STATUS_UNKNOWN = "UNKNOWN"

# Calibrated ceilings per design class.
DESIGN_AWARE_THRESHOLDS: dict[str, dict[str, float]] = {
    DESIGN_MICRO: {
        "max_single_name_allowed": 0.25,
        "top3_allowed": 0.75,
        "top5_allowed": 1.00,
    },
    DESIGN_CONCENTRATED: {
        "max_single_name_allowed": 0.15,
        "top3_allowed": 0.60,
        "top5_allowed": 0.85,
    },
    DESIGN_STANDARD: {
        "max_single_name_allowed": 0.10,
        "top3_allowed": 0.45,
        "top5_allowed": 0.65,
    },
    DESIGN_DIVERSIFIED: {
        "max_single_name_allowed": 0.07,
        "top3_allowed": 0.30,
        "top5_allowed": 0.50,
    },
}

# Legacy fixed thresholds (preserved so the reclassification artifact
# can replay the OLD verdict).
LEGACY_FIXED_THRESHOLDS: dict[str, float] = {
    "max_single_name_allowed": 0.10,
    "top3_allowed": 0.40,
    "top5_allowed": 0.60,
}


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


def _round(value: Any, digits: int = 6) -> float | None:
    f = _safe_float(value)
    return round(f, digits) if f is not None else None


def classify_design(position_count: int | None) -> str:
    """Map ``position_count`` to a design class."""
    if position_count is None or position_count <= 0:
        return DESIGN_UNKNOWN
    n = int(position_count)
    if n <= 5:
        return DESIGN_MICRO
    if n <= 10:
        return DESIGN_CONCENTRATED
    if n <= 25:
        return DESIGN_STANDARD
    return DESIGN_DIVERSIFIED


def calibrated_thresholds_for(position_count: int | None) -> dict[str, float]:
    """Return the calibrated concentration ceilings for the design class
    implied by ``position_count``. Falls back to DIVERSIFIED ceilings
    when the class is UNKNOWN so governance stays fail-closed."""
    design_class = classify_design(position_count)
    if design_class == DESIGN_UNKNOWN:
        return dict(DESIGN_AWARE_THRESHOLDS[DESIGN_DIVERSIFIED])
    return dict(DESIGN_AWARE_THRESHOLDS[design_class])


def _evaluate_strategy_calibration(strategy_row: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(strategy_row, dict) or not strategy_row.get("available"):
        return {
            "strategy": (strategy_row or {}).get("strategy"),
            "position_count": None,
            "design_class": DESIGN_UNKNOWN,
            "expected_equal_weight": None,
            "designed_concentration_profile": {},
            "actual_concentration_profile": {},
            "calibrated_thresholds": dict(DESIGN_AWARE_THRESHOLDS[DESIGN_DIVERSIFIED]),
            "calibration_status": CALIBRATION_STATUS_UNKNOWN,
            "reason_codes": ["risk_coverage_row_missing_or_unavailable"],
        }
    position_count = int(_safe_float(strategy_row.get("position_count")) or 0)
    design_class = classify_design(position_count)
    expected_equal_weight = _round(1.0 / position_count) if position_count > 0 else None
    designed_profile = {
        "expected_max_single_name_weight": expected_equal_weight,
        "expected_top3_concentration": _round(min(3.0, position_count) / position_count) if position_count > 0 else None,
        "expected_top5_concentration": _round(min(5.0, position_count) / position_count) if position_count > 0 else None,
    }
    actual_profile = {
        "max_single_name_weight": _round(strategy_row.get("max_single_name_weight")),
        "top3_concentration": _round(strategy_row.get("top3_concentration")),
        "top5_concentration": _round(strategy_row.get("top5_concentration")),
        "top10_concentration": _round(strategy_row.get("top10_concentration")),
    }
    thresholds = calibrated_thresholds_for(position_count)
    violations: list[dict[str, Any]] = []
    for metric, measured_key, threshold_key in (
        ("max_single_name_weight", "max_single_name_weight", "max_single_name_allowed"),
        ("top3_concentration", "top3_concentration", "top3_allowed"),
        ("top5_concentration", "top5_concentration", "top5_allowed"),
    ):
        measured = _safe_float(strategy_row.get(measured_key))
        cap = float(thresholds[threshold_key])
        if measured is None:
            continue
        if measured > cap + 1e-9:
            violations.append(
                {
                    "metric": metric,
                    "measured": _round(measured),
                    "calibrated_cap": _round(cap),
                    "excess": _round(measured - cap),
                }
            )
    if design_class == DESIGN_UNKNOWN:
        status = CALIBRATION_STATUS_UNKNOWN
        reasons = ["position_count_unknown"]
    elif violations:
        status = CALIBRATION_STATUS_RISK
        reasons = [f"{v['metric']}_above_calibrated_cap" for v in violations]
    else:
        status = CALIBRATION_STATUS_CLEAN
        reasons = ["design_consistent_concentration"]
    return {
        "strategy": strategy_row.get("strategy"),
        "position_count": position_count,
        "design_class": design_class,
        "expected_equal_weight": expected_equal_weight,
        "designed_concentration_profile": designed_profile,
        "actual_concentration_profile": actual_profile,
        "calibrated_thresholds": {k: _round(v) for k, v in thresholds.items()},
        "calibration_status": status,
        "violations": violations,
        "reason_codes": reasons,
    }


def build_governance_calibration(
    *,
    trade_date: str,
    repo_root: Path | str = Path("."),
    output_root: Path | str | None = None,
) -> dict[str, Any]:
    repo = Path(repo_root)
    risk_path = repo / "outputs" / "research" / "risk_coverage" / trade_date / "risk_coverage.json"
    risk = _read_json(risk_path)
    if not isinstance(risk, dict) or not risk.get("strategies"):
        payload = {
            "schema_version": SCHEMA_VERSION_CALIBRATION,
            "date": trade_date,
            "available": False,
            "confidence": "LOW",
            "design_aware_thresholds": {
                cls: {k: _round(v) for k, v in row.items()}
                for cls, row in DESIGN_AWARE_THRESHOLDS.items()
            },
            "strategies": [],
            "calibration_status_counts": {},
            "reason_codes": ["missing_risk_coverage"],
            "source_artifacts": [],
        }
        out_dir = (Path(output_root) if output_root is not None else repo / "outputs" / "research" / "governance_calibration") / trade_date
        _write_json(out_dir / "governance_calibration.json", payload)
        _write_text(out_dir / "governance_calibration.md", render_markdown_calibration(payload))
        return payload
    rows: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    for name, row in sorted((risk.get("strategies") or {}).items()):
        cal = _evaluate_strategy_calibration({**(row or {}), "strategy": name})
        rows.append(cal)
        counts[cal["calibration_status"]] = counts.get(cal["calibration_status"], 0) + 1
    aggregate_reasons = sorted({r for row in rows for r in row["reason_codes"]} - {"ok"}) or ["ok"]
    payload = {
        "schema_version": SCHEMA_VERSION_CALIBRATION,
        "date": trade_date,
        "available": True,
        "confidence": "HIGH" if all(r["calibration_status"] != CALIBRATION_STATUS_UNKNOWN for r in rows) else "MEDIUM",
        "design_aware_thresholds": {
            cls: {k: _round(v) for k, v in row.items()}
            for cls, row in DESIGN_AWARE_THRESHOLDS.items()
        },
        "legacy_fixed_thresholds": {k: _round(v) for k, v in LEGACY_FIXED_THRESHOLDS.items()},
        "strategies": rows,
        "calibration_status_counts": counts,
        "reason_codes": aggregate_reasons,
        "source_artifacts": [str(risk_path)],
    }
    out_dir = (Path(output_root) if output_root is not None else repo / "outputs" / "research" / "governance_calibration") / trade_date
    _write_json(out_dir / "governance_calibration.json", payload)
    _write_text(out_dir / "governance_calibration.md", render_markdown_calibration(payload))
    return payload


# ---------------------------------------------------------------------------
# Reclassification — OLD fixed vs NEW calibrated
# ---------------------------------------------------------------------------

def _evaluate_legacy_risk_gate(strategy_row: dict[str, Any]) -> list[str]:
    """Replay the pre-FR-040 risk-gate logic against the measurements
    stored on a risk_coverage strategy row. Returns the list of legacy
    reason codes (excluding 'ok')."""
    if not isinstance(strategy_row, dict) or not strategy_row.get("available"):
        return ["no_risk_row_for_strategy"]
    reasons: list[str] = []
    max_name = _safe_float(strategy_row.get("max_single_name_weight"))
    top3 = _safe_float(strategy_row.get("top3_concentration"))
    top5 = _safe_float(strategy_row.get("top5_concentration"))
    if max_name is not None and max_name > LEGACY_FIXED_THRESHOLDS["max_single_name_allowed"] + 1e-9:
        reasons.append("single_name_concentration_above_cap")
    if top3 is not None and top3 > LEGACY_FIXED_THRESHOLDS["top3_allowed"] + 1e-9:
        reasons.append("top3_concentration_above_cap")
    if top5 is not None and top5 > LEGACY_FIXED_THRESHOLDS["top5_allowed"] + 1e-9:
        reasons.append("top5_concentration_above_cap")
    return reasons


def _evaluate_calibrated_risk_gate(strategy_row: dict[str, Any]) -> list[str]:
    if not isinstance(strategy_row, dict) or not strategy_row.get("available"):
        return ["no_risk_row_for_strategy"]
    reasons: list[str] = []
    position_count = int(_safe_float(strategy_row.get("position_count")) or 0)
    thresholds = calibrated_thresholds_for(position_count)
    max_name = _safe_float(strategy_row.get("max_single_name_weight"))
    top3 = _safe_float(strategy_row.get("top3_concentration"))
    top5 = _safe_float(strategy_row.get("top5_concentration"))
    if max_name is not None and max_name > thresholds["max_single_name_allowed"] + 1e-9:
        reasons.append("single_name_concentration_above_calibrated_cap")
    if top3 is not None and top3 > thresholds["top3_allowed"] + 1e-9:
        reasons.append("top3_concentration_above_calibrated_cap")
    if top5 is not None and top5 > thresholds["top5_allowed"] + 1e-9:
        reasons.append("top5_concentration_above_calibrated_cap")
    return reasons


def _derive_decision_from_other_gates(
    promotion_strategies: dict[str, Any] | None,
    strategy: str,
    risk_gate_reasons: list[str],
) -> str:
    """Given the existing per-strategy gate output from
    promotion_governance.json, replace just the risk gate reasons with
    ``risk_gate_reasons`` and re-derive the final decision using the
    same logic as research/promotion_governance.py."""
    # Local import to avoid a circular dependency at module import time.
    from research.promotion_governance import (
        CONTROL_STRATEGY,
        DECISION_BLOCKED,
        DECISION_DEMOTE,
        DECISION_HOLD,
        DECISION_PROMOTE,
        DECISION_PROMOTION_CANDIDATE,
        DECISION_WATCH,
        GATE_BLOCKED,
        GATE_INSUFFICIENT_DATA,
        GATE_PASS,
        WINDOW_MIN_CANDIDATE,
        WINDOW_MIN_PROMOTE,
        WINDOW_MIN_WATCH,
    )

    if promotion_strategies is None or strategy not in promotion_strategies:
        return DECISION_BLOCKED
    row = promotion_strategies[strategy] or {}
    gates = dict((row.get("gates") or {}))

    # Substitute the risk gate with a synthesized version using the
    # provided risk_gate_reasons.
    original_risk = gates.get("risk") or {}
    synthesized_risk = dict(original_risk)
    if not risk_gate_reasons or risk_gate_reasons == ["no_risk_row_for_strategy"]:
        synthesized_risk["status"] = GATE_INSUFFICIENT_DATA
        synthesized_risk["reason_codes"] = ["no_risk_row_for_strategy"]
    elif any(r != "design_consistent_concentration" for r in risk_gate_reasons):
        synthesized_risk["status"] = GATE_BLOCKED
        synthesized_risk["reason_codes"] = sorted(set(risk_gate_reasons))
    else:
        synthesized_risk["status"] = GATE_PASS
        synthesized_risk["reason_codes"] = ["ok"]
    gates["risk"] = synthesized_risk

    blockers: list[str] = []
    insufficient: list[str] = []
    for gate_name, gate in gates.items():
        if not isinstance(gate, dict):
            continue
        status = gate.get("status")
        if status == GATE_BLOCKED:
            for code in gate.get("reason_codes") or []:
                if code and code != "ok":
                    blockers.append(f"{gate_name}:{code}")
        elif status == GATE_INSUFFICIENT_DATA:
            for code in gate.get("reason_codes") or []:
                if code and code != "ok":
                    insufficient.append(f"{gate_name}:{code}")

    if insufficient:
        return DECISION_BLOCKED

    obs_gate = gates.get("observation_window") or {}
    max_obs = int(obs_gate.get("max_observation_count") or 0)

    if strategy == CONTROL_STRATEGY:
        return DECISION_BLOCKED if blockers else DECISION_HOLD

    if blockers:
        data_blockers = [b for b in blockers if b.split(":", 1)[0] in ("observation_window", "universe", "execution_timing")]
        perf_or_risk = [b for b in blockers if b.split(":", 1)[0] in ("performance", "risk")]
        if data_blockers:
            return DECISION_BLOCKED
        if perf_or_risk:
            return DECISION_DEMOTE
        return DECISION_HOLD

    if max_obs >= WINDOW_MIN_PROMOTE:
        return DECISION_PROMOTE
    if max_obs >= WINDOW_MIN_CANDIDATE:
        return DECISION_PROMOTION_CANDIDATE
    if max_obs >= WINDOW_MIN_WATCH:
        return DECISION_WATCH
    return DECISION_BLOCKED


def build_governance_reclassification(
    *,
    trade_date: str,
    repo_root: Path | str = Path("."),
    output_root: Path | str | None = None,
) -> dict[str, Any]:
    repo = Path(repo_root)
    risk = _read_json(repo / "outputs" / "research" / "risk_coverage" / trade_date / "risk_coverage.json")
    promotion = _read_json(repo / "outputs" / "research" / "promotion_governance" / trade_date / "promotion_governance.json")
    if not isinstance(risk, dict):
        risk = {}
    risk_strategies = risk.get("strategies") if isinstance(risk.get("strategies"), dict) else {}
    promotion_strategies = (promotion or {}).get("strategies") if isinstance((promotion or {}).get("strategies"), dict) else {}

    comparisons: list[dict[str, Any]] = []
    change_counts: dict[str, int] = {}
    for strategy in STRATEGIES:
        strat_row = risk_strategies.get(strategy)
        old_risk_reasons = _evaluate_legacy_risk_gate(
            {**(strat_row or {}), "strategy": strategy}
        )
        new_risk_reasons = _evaluate_calibrated_risk_gate(
            {**(strat_row or {}), "strategy": strategy}
        )
        if not new_risk_reasons:
            new_risk_reasons = ["design_consistent_concentration"]
        old_decision = _derive_decision_from_other_gates(
            promotion_strategies, strategy, old_risk_reasons
        )
        new_decision = _derive_decision_from_other_gates(
            promotion_strategies, strategy, new_risk_reasons
        )
        change_key = f"{old_decision}->{new_decision}"
        change_counts[change_key] = change_counts.get(change_key, 0) + 1
        comparisons.append(
            {
                "strategy": strategy,
                "old_risk_reasons": sorted(set(old_risk_reasons)) or ["ok"],
                "new_risk_reasons": sorted(set(new_risk_reasons)) or ["ok"],
                "old_decision": old_decision,
                "new_decision": new_decision,
                "decision_changed": old_decision != new_decision,
            }
        )

    available = bool(risk_strategies and promotion_strategies)
    reason_codes: list[str] = []
    if not risk_strategies:
        reason_codes.append("missing_risk_coverage")
    if not promotion_strategies:
        reason_codes.append("missing_promotion_governance")
    if not reason_codes:
        reason_codes.append("ok")

    payload = {
        "schema_version": SCHEMA_VERSION_RECLASSIFICATION,
        "date": trade_date,
        "available": available,
        "confidence": "HIGH" if available else "LOW",
        "comparisons": comparisons,
        "change_counts": change_counts,
        "reason_codes": reason_codes,
        "source_artifacts": sorted(
            p for p, present in [
                (f"outputs/research/risk_coverage/{trade_date}/risk_coverage.json", bool(risk_strategies)),
                (f"outputs/research/promotion_governance/{trade_date}/promotion_governance.json", bool(promotion_strategies)),
            ] if present
        ),
    }

    out_dir = (Path(output_root) if output_root is not None else repo / "outputs" / "research" / "governance_calibration") / trade_date
    _write_json(out_dir / "governance_reclassification.json", payload)
    _write_text(out_dir / "governance_reclassification.md", render_markdown_reclassification(payload))
    return payload


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------

def render_markdown_calibration(payload: dict[str, Any]) -> str:
    lines = [
        f"# Governance Calibration - {payload.get('date')}",
        "",
        f"- Available: {payload.get('available')}",
        f"- Confidence: {payload.get('confidence')}",
        f"- Reason codes: {', '.join(payload.get('reason_codes') or [])}",
        f"- Calibration status counts: {payload.get('calibration_status_counts')}",
        "",
        "## Design-Aware Thresholds",
        "",
        "| Class | Max Single Name | Top 3 | Top 5 |",
        "|---|---:|---:|---:|",
    ]
    for cls, row in (payload.get("design_aware_thresholds") or {}).items():
        lines.append(
            f"| {cls} | {row.get('max_single_name_allowed')} | {row.get('top3_allowed')} | {row.get('top5_allowed')} |"
        )
    lines += [
        "",
        "## Per-Strategy Calibration",
        "",
        "| Strategy | Positions | Design | Expected EW | Actual Max | Top3 | Top5 | Calibrated Cap | Status | Reasons |",
        "|---|---:|---|---:|---:|---:|---:|---:|---|---|",
    ]
    for row in payload.get("strategies") or []:
        thresholds = row.get("calibrated_thresholds") or {}
        actual = row.get("actual_concentration_profile") or {}
        lines.append(
            f"| {row.get('strategy')} | {row.get('position_count')} | {row.get('design_class')} | "
            f"{row.get('expected_equal_weight')} | {actual.get('max_single_name_weight')} | "
            f"{actual.get('top3_concentration')} | {actual.get('top5_concentration')} | "
            f"{thresholds.get('max_single_name_allowed')} | {row.get('calibration_status')} | "
            f"{', '.join(row.get('reason_codes') or [])} |"
        )
    lines.append("")
    return "\n".join(lines)


def render_markdown_reclassification(payload: dict[str, Any]) -> str:
    lines = [
        f"# Governance Reclassification (OLD vs NEW) - {payload.get('date')}",
        "",
        f"- Available: {payload.get('available')}",
        f"- Reason codes: {', '.join(payload.get('reason_codes') or [])}",
        f"- Change counts: {payload.get('change_counts')}",
        "",
        "## Per-Strategy OLD → NEW",
        "",
        "| Strategy | OLD Decision | NEW Decision | Changed | OLD Risk Reasons | NEW Risk Reasons |",
        "|---|---|---|---|---|---|",
    ]
    for row in payload.get("comparisons") or []:
        lines.append(
            f"| {row.get('strategy')} | {row.get('old_decision')} | {row.get('new_decision')} | "
            f"{row.get('decision_changed')} | {', '.join(row.get('old_risk_reasons') or [])} | "
            f"{', '.join(row.get('new_risk_reasons') or [])} |"
        )
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build design-aware governance calibration and reclassification artifacts."
    )
    parser.add_argument("--date", required=True)
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--output-root", default=None)
    args = parser.parse_args(argv)
    calibration = build_governance_calibration(
        trade_date=args.date,
        repo_root=Path(args.repo_root),
        output_root=Path(args.output_root) if args.output_root else None,
    )
    reclassification = build_governance_reclassification(
        trade_date=args.date,
        repo_root=Path(args.repo_root),
        output_root=Path(args.output_root) if args.output_root else None,
    )
    print(
        json.dumps(
            {
                "date": args.date,
                "calibration_available": calibration["available"],
                "calibration_status_counts": calibration["calibration_status_counts"],
                "reclassification_change_counts": reclassification["change_counts"],
                "reason_codes": sorted(set(calibration["reason_codes"] + reclassification["reason_codes"])),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
