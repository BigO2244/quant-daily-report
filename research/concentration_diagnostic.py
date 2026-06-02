"""Concentration diagnostic — Workstream 5.

Reads risk_coverage.json and classifies each strategy's concentration
profile into one of:

  ACTUAL_VIOLATION    — measured weight exceeds the cap by an amount
                        that cannot be explained by equal-weighting the
                        designed N positions.
  TEMPORARY_VIOLATION — exceedance is small (<= TEMP_TOLERANCE) and
                        cannot be persistently true across history.
                        Indicates a rebalance-friction artifact.
  ARTIFACT_ISSUE      — risk_coverage row is incomplete (missing
                        position_count, missing holdings) so the
                        violation cannot be verified.
  CONFIGURATION       — measured weight equals the equal-weight floor
                        forced by the strategy's designed position
                        count (e.g. 5 positions equal-weight → max 0.20
                        forced by construction).
  CLEAN               — no caps breached.

Research-only.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "caerus_concentration_diagnostic_v1"

CLASSIFY_VIOLATION = "ACTUAL_VIOLATION"
CLASSIFY_TEMPORARY = "TEMPORARY_VIOLATION"
CLASSIFY_ARTIFACT = "ARTIFACT_ISSUE"
CLASSIFY_CONFIGURATION = "CONFIGURATION"
CLASSIFY_CLEAN = "CLEAN"

MAX_SINGLE_NAME_WEIGHT = 0.10
MAX_TOP3_CONCENTRATION = 0.40
MAX_TOP5_CONCENTRATION = 0.60
MAX_TOP10_CONCENTRATION = 0.90
MAX_SECTOR_CONCENTRATION = 0.50

DESIGN_TOLERANCE = 0.02
TEMP_TOLERANCE = 0.03


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


def _classify_one_strategy(row: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(row, dict) or not row.get("available"):
        return {
            "strategy": row.get("strategy") if isinstance(row, dict) else None,
            "classification": CLASSIFY_ARTIFACT,
            "violations": [],
            "designed_by_construction": [],
            "reason_codes": ["risk_coverage_row_missing_or_unavailable"],
        }
    position_count = int(_safe_float(row.get("position_count")) or 0)
    if position_count == 0:
        return {
            "strategy": row.get("strategy"),
            "classification": CLASSIFY_ARTIFACT,
            "violations": [],
            "designed_by_construction": [],
            "reason_codes": ["position_count_unknown"],
        }
    metrics = {
        "max_single_name_weight": (_safe_float(row.get("max_single_name_weight")), MAX_SINGLE_NAME_WEIGHT, 1.0 / position_count),
        "top3_concentration": (_safe_float(row.get("top3_concentration")), MAX_TOP3_CONCENTRATION, min(3.0, position_count) / position_count),
        "top5_concentration": (_safe_float(row.get("top5_concentration")), MAX_TOP5_CONCENTRATION, min(5.0, position_count) / position_count),
        "top10_concentration": (_safe_float(row.get("top10_concentration")), MAX_TOP10_CONCENTRATION, min(10.0, position_count) / position_count),
        "sector_concentration": (_safe_float(row.get("sector_concentration")), MAX_SECTOR_CONCENTRATION, None),
    }
    violations: list[dict[str, Any]] = []
    designed: list[dict[str, Any]] = []
    temporary: list[dict[str, Any]] = []
    for metric, (measured, cap, equal_floor) in metrics.items():
        if measured is None or measured <= cap:
            continue
        excess = measured - cap
        if equal_floor is not None and abs(measured - equal_floor) <= DESIGN_TOLERANCE and equal_floor > cap:
            designed.append(
                {"metric": metric, "measured": _round(measured), "cap": cap, "equal_weight_floor": _round(equal_floor), "excess": _round(excess)}
            )
        elif excess <= TEMP_TOLERANCE:
            temporary.append(
                {"metric": metric, "measured": _round(measured), "cap": cap, "excess": _round(excess)}
            )
        else:
            violations.append(
                {"metric": metric, "measured": _round(measured), "cap": cap, "excess": _round(excess)}
            )
    if violations:
        classification = CLASSIFY_VIOLATION
    elif temporary and not designed:
        classification = CLASSIFY_TEMPORARY
    elif designed:
        classification = CLASSIFY_CONFIGURATION
    else:
        classification = CLASSIFY_CLEAN
    reason_codes: list[str] = []
    if classification == CLASSIFY_VIOLATION:
        reason_codes.append("true_concentration_violation")
    if classification == CLASSIFY_TEMPORARY:
        reason_codes.append("small_excess_within_temp_tolerance")
    if classification == CLASSIFY_CONFIGURATION:
        reason_codes.append("equal_weight_design_forces_concentration")
    if classification == CLASSIFY_CLEAN:
        reason_codes.append("ok")
    return {
        "strategy": row.get("strategy"),
        "classification": classification,
        "position_count": position_count,
        "max_single_name_weight": _round(metrics["max_single_name_weight"][0]),
        "top3_concentration": _round(metrics["top3_concentration"][0]),
        "top5_concentration": _round(metrics["top5_concentration"][0]),
        "top10_concentration": _round(metrics["top10_concentration"][0]),
        "sector_concentration": _round(metrics["sector_concentration"][0]),
        "violations": violations,
        "temporary_violations": temporary,
        "designed_by_construction": designed,
        "reason_codes": reason_codes,
    }


def build_concentration_diagnostic(
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
            "schema_version": SCHEMA_VERSION,
            "date": trade_date,
            "available": False,
            "confidence": "LOW",
            "strategies": [],
            "classification_counts": {},
            "reason_codes": ["missing_risk_coverage"],
            "source_artifacts": [],
        }
        out_dir = (Path(output_root) if output_root is not None else repo / "outputs" / "research" / "concentration_diagnostic") / trade_date
        _write_json(out_dir / "concentration_diagnostic.json", payload)
        _write_text(out_dir / "concentration_diagnostic.md", render_markdown(payload))
        return payload
    rows: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    for strategy_name, strat_row in sorted((risk.get("strategies") or {}).items()):
        diag = _classify_one_strategy({**(strat_row or {}), "strategy": strategy_name})
        rows.append(diag)
        counts[diag["classification"]] = counts.get(diag["classification"], 0) + 1
    aggregate_reasons = sorted({code for row in rows for code in row["reason_codes"]} - {"ok"}) or ["ok"]
    payload = {
        "schema_version": SCHEMA_VERSION,
        "date": trade_date,
        "available": True,
        "confidence": "HIGH" if all(row["classification"] != CLASSIFY_ARTIFACT for row in rows) else "MEDIUM",
        "strategies": rows,
        "classification_counts": counts,
        "reason_codes": aggregate_reasons,
        "source_artifacts": [str(risk_path)],
    }
    out_dir = (Path(output_root) if output_root is not None else repo / "outputs" / "research" / "concentration_diagnostic") / trade_date
    _write_json(out_dir / "concentration_diagnostic.json", payload)
    _write_text(out_dir / "concentration_diagnostic.md", render_markdown(payload))
    return payload


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        f"# Concentration Diagnostic - {payload.get('date')}",
        "",
        f"- Available: {payload.get('available')}",
        f"- Confidence: {payload.get('confidence')}",
        f"- Reason codes: {', '.join(payload.get('reason_codes') or [])}",
        "",
        "## Per-Strategy Classifications",
        "",
        "| Strategy | Classification | Positions | Max Name | Top3 | Top5 | Top10 | Sector | Violations | Designed |",
        "|---|---|---:|---:|---:|---:|---:|---:|---|---|",
    ]
    for row in payload.get("strategies") or []:
        lines.append(
            f"| {row.get('strategy')} | {row.get('classification')} | {row.get('position_count')} | "
            f"{row.get('max_single_name_weight')} | {row.get('top3_concentration')} | "
            f"{row.get('top5_concentration')} | {row.get('top10_concentration')} | "
            f"{row.get('sector_concentration')} | {len(row.get('violations') or [])} | "
            f"{len(row.get('designed_by_construction') or [])} |"
        )
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Classify concentration blockers as actual vs configuration vs artifact.")
    parser.add_argument("--date", required=True)
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--output-root", default=None)
    args = parser.parse_args(argv)
    payload = build_concentration_diagnostic(
        trade_date=args.date,
        repo_root=Path(args.repo_root),
        output_root=Path(args.output_root) if args.output_root else None,
    )
    print(
        json.dumps(
            {
                "date": args.date,
                "available": payload["available"],
                "classification_counts": payload["classification_counts"],
                "reason_codes": payload["reason_codes"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
