from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from core.strategy_identity import LIVE_STRATEGY_ID
from core.strategy_registry import load_strategy_registry_for_repo
from research_registry.research.model_quality_common import (
    dated_source,
    md_join,
    model_quality_dir,
    normalize_date,
    read_json,
    round_or_none,
    safe_float,
    source_status,
    write_json,
    write_text,
)

SCHEMA_VERSION = "caerus_model_tournament_v1"
MIN_DECISION_GRADE_DAYS = 252


def _latest_payload(repo: Path, trade_date: str, relative_root: str, filename: str) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    directory, source_date, reasons = dated_source(repo, relative_root, trade_date, filename)
    status = source_status(
        name=filename.removesuffix(".json"),
        path=(directory / filename) if directory else None,
        source_date=source_date,
        target_date=trade_date,
        reason_codes=reasons,
    )
    return (read_json(directory / filename) if directory else None), status


def _risk_row(risk_payload: dict[str, Any] | None, strategy: str) -> dict[str, Any]:
    row = ((risk_payload or {}).get("strategies") or {}).get(strategy)
    return dict(row) if isinstance(row, dict) else {}


def _regime_returns(regime_payload: dict[str, Any] | None, strategy: str) -> dict[str, Any]:
    regimes = (((regime_payload or {}).get("strategies") or {}).get(strategy) or {}).get("regimes") or {}
    if not isinstance(regimes, dict):
        return {}
    return {
        str(regime): {
            "total_return": round_or_none((metrics or {}).get("total_return")),
            "observation_count": int(safe_float((metrics or {}).get("observation_count")) or 0),
            "confidence": (metrics or {}).get("confidence") or "LOW",
        }
        for regime, metrics in sorted(regimes.items())
        if isinstance(metrics, dict)
    }


def _readiness_state(readiness_payload: dict[str, Any] | None, strategy: str) -> dict[str, Any]:
    windows = (((readiness_payload or {}).get("strategies") or {}).get(strategy) or {}).get("windows") or {}
    if not isinstance(windows, dict) or not windows:
        return {"state": "INSUFFICIENT_HISTORY", "max_observation_count": 0, "reason_codes": ["PROMOTION_READINESS_MISSING"]}
    max_obs = 0
    states = []
    reasons = set()
    for _, row in sorted(windows.items()):
        if not isinstance(row, dict):
            continue
        max_obs = max(max_obs, int(safe_float(row.get("observation_count")) or 0))
        if row.get("readiness_state"):
            states.append(str(row["readiness_state"]))
        for code in row.get("reason_codes") or []:
            if code != "ok":
                reasons.add(str(code))
    return {
        "state": states[0] if states else "UNKNOWN",
        "max_observation_count": max_obs,
        "reason_codes": sorted(reasons) or ["ok"],
    }


def _decision_grade(
    *,
    coverage_days: int,
    readiness: dict[str, Any],
    governance_payload: dict[str, Any] | None,
    strategy: str,
) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if coverage_days < MIN_DECISION_GRADE_DAYS:
        reasons.append(f"INSUFFICIENT_COVERAGE_DAYS:{coverage_days}/{MIN_DECISION_GRADE_DAYS}")
    governance = ((governance_payload or {}).get("strategies") or {}).get(strategy)
    if isinstance(governance, dict):
        decision = str(governance.get("decision") or "").upper()
        if decision not in {"PASS", "WATCH", "PROMOTION_CANDIDATE"}:
            reasons.append(f"PROMOTION_GOVERNANCE_{decision or 'UNKNOWN'}")
        for code in governance.get("reason_codes") or []:
            if code != "ok":
                reasons.append(str(code))
    else:
        reasons.append("PROMOTION_GOVERNANCE_MISSING")
    state = str(readiness.get("state") or "")
    if state in {"NOT_READY", "INSUFFICIENT_HISTORY", "UNKNOWN"}:
        reasons.append(f"READINESS_{state}")
    return not reasons, sorted(set(reasons)) or ["ok"]


def build_model_tournament(
    *,
    trade_date: str,
    repo_root: Path | str = Path("."),
    output_root: Path | str | None = None,
    write: bool = True,
) -> dict[str, Any]:
    target = normalize_date(trade_date)
    repo = Path(repo_root)
    registry = load_strategy_registry_for_repo(repo)
    summary_path = repo / "outputs" / "shadow_candidates" / "performance" / "shadow_summary.json"
    summary_payload = read_json(summary_path)
    strategies_payload = (summary_payload or {}).get("strategies") or {}
    risk_payload, risk_status = _latest_payload(repo, target, "outputs/research/risk_coverage", "risk_coverage.json")
    regime_payload, regime_status = _latest_payload(repo, target, "outputs/research/regime_attribution", "regime_attribution.json")
    readiness_payload, readiness_status = _latest_payload(repo, target, "outputs/research/promotion_readiness", "promotion_readiness_windows.json")
    governance_payload, governance_status = _latest_payload(repo, target, "outputs/research/promotion_governance", "promotion_governance.json")
    _model_quality_dir = repo / "outputs" / "model_quality" / target
    argo_payload = read_json(_model_quality_dir / "argo_regime_selection.json")
    if argo_payload is None:
        argo_payload = read_json(_model_quality_dir / "cassiopeia_model_selection.json")  # bounded legacy fallback

    records: list[dict[str, Any]] = []
    for entry in registry.entries:
        if entry.strategy_id == "spy_benchmark":
            continue
        if entry.is_meta_model:
            records.append(
                {
                    "strategy": entry.strategy_id,
                    "display_name": entry.display_name,
                    "strategy_type": entry.strategy_type,
                    "status": "META_MODEL_RECOMMENDATION_ONLY",
                    "rankable": False,
                    "metrics": {},
                    "decision_grade": False,
                    "data_quality_status": "RECOMMENDATION_LAYER",
                    "learning_readiness": "META_MODEL",
                    "reason_codes": ["META_MODEL_RECOMMENDATION_ONLY"],
                    "argo_recommendation": (argo_payload or {}).get("recommended_strategy"),
                }
            )
            continue
        if not entry.is_security_selection:
            records.append(
                {
                    "strategy": entry.strategy_id,
                    "display_name": entry.display_name,
                    "strategy_type": entry.strategy_type,
                    "status": "NOT_DIRECTLY_RANKED",
                    "rankable": False,
                    "metrics": {},
                    "decision_grade": False,
                    "data_quality_status": "NOT_SECURITY_SELECTION",
                    "learning_readiness": "NOT_APPLICABLE",
                    "reason_codes": ["NOT_SECURITY_SELECTION"],
                }
            )
            continue
        perf = strategies_payload.get(entry.strategy_id) if isinstance(strategies_payload, dict) else None
        summary = (perf or {}).get("summary") if isinstance(perf, dict) else None
        if not isinstance(summary, dict):
            records.append(
                {
                    "strategy": entry.strategy_id,
                    "display_name": entry.display_name,
                    "strategy_type": entry.strategy_type,
                    "status": "REGISTERED_INSUFFICIENT_HISTORY",
                    "rankable": False,
                    "metrics": {},
                    "decision_grade": False,
                    "data_quality_status": "NO_STRATEGY_HISTORY",
                    "learning_readiness": "INSUFFICIENT_HISTORY",
                    "reason_codes": ["NO_STRATEGY_HISTORY"],
                }
            )
            continue
        risk = _risk_row(risk_payload, entry.strategy_id)
        readiness = _readiness_state(readiness_payload, entry.strategy_id)
        coverage_days = int(safe_float(summary.get("n_days")) or 0)
        decision_grade, decision_reasons = _decision_grade(
            coverage_days=coverage_days,
            readiness=readiness,
            governance_payload=governance_payload,
            strategy=entry.strategy_id,
        )
        records.append(
            {
                "strategy": entry.strategy_id,
                "display_name": entry.display_name,
                "strategy_type": entry.strategy_type,
                "status": entry.status,
                "rankable": True,
                "metrics": {
                    "total_return": round_or_none(summary.get("cumulative_return")),
                    "excess_return_vs_spy": round_or_none(summary.get("excess_return_vs_spy")),
                    "hit_rate": round_or_none(summary.get("hit_rate") or summary.get("win_rate")),
                    "max_drawdown": round_or_none(summary.get("max_drawdown")),
                    "volatility": round_or_none(summary.get("annualised_vol")),
                    "turnover": round_or_none(summary.get("avg_turnover")),
                    "top3_concentration": round_or_none(risk.get("top3_concentration") or summary.get("avg_top_3_concentration")),
                    "constituent_changes": None,
                    "coverage_days": coverage_days,
                    "coverage_years": round_or_none(summary.get("n_years")),
                    "regime_specific_return": _regime_returns(regime_payload, entry.strategy_id),
                },
                "decision_grade": decision_grade,
                "data_quality_status": "OK" if decision_reasons == ["ok"] else "PARTIAL",
                "learning_readiness": readiness.get("state"),
                "reason_codes": decision_reasons,
            }
        )

    if LIVE_STRATEGY_ID and all(row.get("strategy") != LIVE_STRATEGY_ID for row in records):
        records.append(
            {
                "strategy": LIVE_STRATEGY_ID,
                "display_name": LIVE_STRATEGY_ID,
                "strategy_type": "live_baseline",
                "status": "CURRENT_OPERATIONAL_STACK_REFERENCE",
                "rankable": False,
                "metrics": {},
                "decision_grade": False,
                "data_quality_status": "NO_COMPARABLE_TOURNAMENT_HISTORY",
                "learning_readiness": "REFERENCE_ONLY",
                "reason_codes": ["LIVE_BASELINE_REFERENCE_ONLY"],
            }
        )

    ranked = sorted(
        [row for row in records if row.get("rankable")],
        key=lambda row: (-(safe_float((row.get("metrics") or {}).get("excess_return_vs_spy")) or -10**9), row["strategy"]),
    )
    current_leader = ranked[0]["strategy"] if ranked else None
    decision_ranked = [row for row in ranked if row.get("decision_grade")]
    decision_grade_leader = decision_ranked[0]["strategy"] if decision_ranked else None
    needs_more_evidence = [row["strategy"] for row in records if not row.get("decision_grade") and row.get("status") != "NOT_DIRECTLY_RANKED"]
    reason_codes = sorted({code for row in records for code in (row.get("reason_codes") or []) if code != "ok"})
    if decision_grade_leader is None:
        reason_codes.append("NO_DECISION_GRADE_LEADER")
    payload = {
        "schema_version": SCHEMA_VERSION,
        "date": target,
        "governance_label": "RESEARCH_ONLY",
        "execution_impact": "NON_EXECUTIONAL",
        "current_leader": current_leader,
        "decision_grade_leader": decision_grade_leader,
        "strategies": sorted(records, key=lambda row: (not bool(row.get("rankable")), str(row.get("strategy") or ""))),
        "strategies_needing_more_evidence": sorted(set(needs_more_evidence)),
        "deprecation_candidates": [],
        "next_evidence_required": [
            "fresh security master and universe governance",
            "clean promotion governance gates",
            "more regime-specific observations for challengers",
            "explicit Phoenix history before ranking Phoenix",
        ],
        "promotion_recommendation": "NO_PROMOTION_RECOMMENDED" if decision_grade_leader is None else f"REVIEW_{decision_grade_leader}",
        "reason_codes": sorted(set(reason_codes)) or ["ok"],
        "source_statuses": [
            source_status(
                name="shadow_performance_summary",
                path=summary_path if summary_path.exists() else None,
                source_date=(summary_payload or {}).get("trade_date") if summary_payload else None,
                target_date=target,
                reason_codes=["ok"] if summary_payload else ["SHADOW_PERFORMANCE_SUMMARY_MISSING"],
            ),
            risk_status,
            regime_status,
            readiness_status,
            governance_status,
        ],
    }
    if write:
        out_dir = model_quality_dir(repo, target, output_root)
        write_json(out_dir / "model_tournament.json", payload)
        write_text(out_dir / "model_tournament.md", render_markdown(payload))
    return payload


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        f"# Model Tournament - {payload.get('date')}",
        "",
        f"- Current leader: {payload.get('current_leader')}",
        f"- Decision-grade leader: {payload.get('decision_grade_leader') or 'none'}",
        f"- Promotion recommendation: {payload.get('promotion_recommendation')}",
        f"- Reason codes: {md_join(payload.get('reason_codes') or [])}",
        "",
        "| Strategy | Status | Rankable | Excess vs SPY | Drawdown | Coverage | Decision grade | Reasons |",
        "|---|---|:---:|---:|---:|---:|:---:|---|",
    ]
    for row in payload.get("strategies") or []:
        metrics = row.get("metrics") or {}
        lines.append(
            f"| {row.get('strategy')} | {row.get('status')} | {row.get('rankable')} | "
            f"{metrics.get('excess_return_vs_spy')} | {metrics.get('max_drawdown')} | {metrics.get('coverage_days')} | "
            f"{row.get('decision_grade')} | {md_join(row.get('reason_codes') or [])} |"
        )
    lines.extend(["", "## Next Evidence Required", ""])
    for item in payload.get("next_evidence_required") or []:
        lines.append(f"- {item}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build research-only model tournament artifacts.")
    parser.add_argument("--date", required=True)
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--output-root", default=None)
    args = parser.parse_args(argv)
    payload = build_model_tournament(
        trade_date=args.date,
        repo_root=Path(args.repo_root),
        output_root=Path(args.output_root) if args.output_root else None,
    )
    print(json.dumps({"date": payload["date"], "current_leader": payload["current_leader"], "decision_grade_leader": payload["decision_grade_leader"], "reason_codes": payload["reason_codes"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
