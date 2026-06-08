from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from core.strategy_registry import load_strategy_registry_for_repo
from research_registry.research.model_quality_common import (
    collect_reason_codes,
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

SCHEMA_VERSION = "caerus_argo_regime_selection_v1"
STRATEGY_ID = "caerus_argo"
MIN_COVERAGE_DAYS = 252
MIN_REGIME_OBSERVATIONS = 30


def _current_regime(repo: Path, trade_date: str) -> tuple[dict[str, Any], list[str]]:
    path = repo / "outputs" / "vix_regime" / "regime_current.json"
    payload = read_json(path)
    if payload is None:
        return {
            "regime": "UNKNOWN",
            "evidence_regime": "neutral",
            "source_artifact": None,
            "reason_codes": ["CURRENT_REGIME_MISSING"],
        }, ["CURRENT_REGIME_MISSING"]
    raw_date = payload.get("date") or payload.get("as_of")
    reasons: list[str] = []
    if raw_date:
        try:
            source_date = normalize_date(str(raw_date))
            if source_date > trade_date:
                reasons.append("CURRENT_REGIME_AFTER_TARGET_IGNORED")
            elif source_date != trade_date:
                reasons.append("CURRENT_REGIME_DATE_DIFFERS_FROM_TARGET")
        except Exception:
            reasons.append("CURRENT_REGIME_DATE_INVALID")
    else:
        reasons.append("CURRENT_REGIME_DATE_MISSING")
    regime = str(payload.get("regime") or "UNKNOWN").upper()
    return {
        "regime": regime,
        "vix": round_or_none(payload.get("vix")),
        "source_date": raw_date,
        "source_artifact": str(path),
        "evidence_regime": _map_regime_to_evidence_bucket(regime),
        "reason_codes": sorted(set(reasons)) or ["ok"],
    }, sorted(set(reasons)) or ["ok"]


def _map_regime_to_evidence_bucket(regime: str) -> str:
    value = str(regime or "").upper()
    if value in {"CRISIS", "HIGH"}:
        return "high_vol"
    if value == "LOW":
        return "low_vol"
    return "neutral"


def _latest_payload(
    repo: Path,
    trade_date: str,
    relative_root: str,
    filename: str,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    directory, source_date, reasons = dated_source(repo, relative_root, trade_date, filename)
    status = source_status(
        name=filename.removesuffix(".json"),
        path=(directory / filename) if directory else None,
        source_date=source_date,
        target_date=trade_date,
        reason_codes=reasons,
    )
    return (read_json(directory / filename) if directory else None), status


def _regime_metrics(regime_payload: dict[str, Any] | None, strategy: str, evidence_regime: str) -> dict[str, Any] | None:
    block = (((regime_payload or {}).get("strategies") or {}).get(strategy) or {}).get("regimes") or {}
    row = block.get(evidence_regime)
    return dict(row) if isinstance(row, dict) else None


def _promotion_blockers(governance_payload: dict[str, Any] | None, strategy: str) -> list[str]:
    strategy_payload = ((governance_payload or {}).get("strategies") or {}).get(strategy)
    if not isinstance(strategy_payload, dict):
        return ["PROMOTION_GOVERNANCE_MISSING"]
    decision = str(strategy_payload.get("decision") or "").upper()
    if decision in {"PASS", "WATCH", "PROMOTION_CANDIDATE"}:
        return []
    reasons = [f"PROMOTION_GOVERNANCE_{decision or 'UNKNOWN'}"]
    reasons.extend(str(code) for code in strategy_payload.get("reason_codes") or [] if code != "ok")
    return sorted(set(reasons))


def _readiness_summary(readiness_payload: dict[str, Any] | None, strategy: str) -> dict[str, Any]:
    windows = (((readiness_payload or {}).get("strategies") or {}).get(strategy) or {}).get("windows") or {}
    if not isinstance(windows, dict):
        return {"available": False, "best_state": None, "max_observations": 0, "reason_codes": ["PROMOTION_READINESS_MISSING"]}
    best_state = None
    max_obs = 0
    reason_codes: set[str] = set()
    for _, row in sorted(windows.items()):
        if not isinstance(row, dict):
            continue
        state = row.get("readiness_state")
        if best_state is None and state:
            best_state = str(state)
        obs = int(safe_float(row.get("observation_count")) or 0)
        max_obs = max(max_obs, obs)
        for code in row.get("reason_codes") or []:
            if code != "ok":
                reason_codes.add(str(code))
    return {
        "available": bool(windows),
        "best_state": best_state,
        "max_observations": max_obs,
        "reason_codes": sorted(reason_codes) or ["ok"],
    }


def build_argo_regime_selection(
    *,
    trade_date: str,
    repo_root: Path | str = Path("."),
    output_root: Path | str | None = None,
    write: bool = True,
) -> dict[str, Any]:
    target = normalize_date(trade_date)
    repo = Path(repo_root)
    registry = load_strategy_registry_for_repo(repo)
    current_regime, current_regime_reasons = _current_regime(repo, target)
    performance_path = repo / "outputs" / "shadow_candidates" / "performance" / "shadow_summary.json"
    performance_payload = read_json(performance_path)
    regime_payload, regime_status = _latest_payload(repo, target, "outputs/research/regime_attribution", "regime_attribution.json")
    governance_payload, governance_status = _latest_payload(repo, target, "outputs/research/promotion_governance", "promotion_governance.json")
    readiness_payload, readiness_status = _latest_payload(repo, target, "outputs/research/promotion_readiness", "promotion_readiness_windows.json")

    source_statuses = [
        source_status(
            name="shadow_performance_summary",
            path=performance_path if performance_path.exists() else None,
            source_date=(performance_payload or {}).get("trade_date") if performance_payload else None,
            target_date=target,
            reason_codes=["ok"] if performance_payload else ["SHADOW_PERFORMANCE_SUMMARY_MISSING"],
        ),
        regime_status,
        governance_status,
        readiness_status,
    ]
    strategies_payload = (performance_payload or {}).get("strategies") or {}
    eligible: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []

    for entry in registry.entries:
        if entry.strategy_id in {STRATEGY_ID, "spy_benchmark"} or entry.is_meta_model:
            excluded.append(
                {
                    "strategy": entry.strategy_id,
                    "display_name": entry.display_name,
                    "reason_codes": ["META_MODEL_RECOMMENDATION_ONLY"] if entry.strategy_id == STRATEGY_ID else ["NOT_A_DIRECT_PORTFOLIO"],
                }
            )
            continue
        if not entry.is_security_selection:
            excluded.append({"strategy": entry.strategy_id, "display_name": entry.display_name, "reason_codes": ["NOT_SECURITY_SELECTION"]})
            continue
        perf = strategies_payload.get(entry.strategy_id) if isinstance(strategies_payload, dict) else None
        summary = (perf or {}).get("summary") if isinstance(perf, dict) else None
        if not isinstance(summary, dict):
            excluded.append({"strategy": entry.strategy_id, "display_name": entry.display_name, "reason_codes": ["NO_STRATEGY_HISTORY"]})
            continue
        evidence = _regime_metrics(regime_payload, entry.strategy_id, str(current_regime.get("evidence_regime")))
        readiness = _readiness_summary(readiness_payload, entry.strategy_id)
        coverage_days = int(safe_float(summary.get("n_days")) or 0)
        regime_obs = int(safe_float((evidence or {}).get("observation_count")) or 0)
        blockers: list[str] = []
        if coverage_days < MIN_COVERAGE_DAYS:
            blockers.append(f"INSUFFICIENT_COVERAGE_DAYS:{coverage_days}/{MIN_COVERAGE_DAYS}")
        if evidence is None:
            blockers.append(f"NO_REGIME_EVIDENCE:{current_regime.get('evidence_regime')}")
        elif regime_obs < MIN_REGIME_OBSERVATIONS:
            blockers.append(f"INSUFFICIENT_REGIME_OBSERVATIONS:{regime_obs}/{MIN_REGIME_OBSERVATIONS}")
        blockers.extend(_promotion_blockers(governance_payload, entry.strategy_id))
        if readiness.get("available") is False:
            blockers.extend(readiness.get("reason_codes") or [])
        score = safe_float((evidence or {}).get("total_return"))
        if score is None:
            score = safe_float(summary.get("excess_return_vs_spy") or summary.get("cagr")) or 0.0
        row = {
            "strategy": entry.strategy_id,
            "display_name": entry.display_name,
            "status": entry.status,
            "coverage_days": coverage_days,
            "coverage_years": round_or_none(summary.get("n_years")),
            "leaderboard_score": round_or_none(score),
            "cagr": round_or_none(summary.get("cagr")),
            "sharpe": round_or_none(summary.get("sharpe")),
            "max_drawdown": round_or_none(summary.get("max_drawdown")),
            "hit_rate": round_or_none(summary.get("hit_rate") or summary.get("win_rate")),
            "turnover": round_or_none(summary.get("avg_turnover")),
            "regime_evidence": {
                "regime": current_regime.get("evidence_regime"),
                "observation_count": regime_obs,
                "total_return": round_or_none((evidence or {}).get("total_return")),
                "hit_rate": round_or_none((evidence or {}).get("hit_rate")),
                "max_drawdown": round_or_none((evidence or {}).get("max_drawdown")),
                "confidence": (evidence or {}).get("confidence") or "LOW",
                "reason_codes": list((evidence or {}).get("reason_codes") or (["NO_REGIME_EVIDENCE"] if evidence is None else ["ok"])),
            },
            "readiness": readiness,
            "decision_grade": not blockers,
            "reason_codes": sorted(set(blockers)) or ["ok"],
        }
        eligible.append(row)
        if blockers:
            excluded.append({"strategy": entry.strategy_id, "display_name": entry.display_name, "reason_codes": sorted(set(blockers))})

    ranked = sorted(eligible, key=lambda row: (-(safe_float(row.get("leaderboard_score")) or 0.0), row["strategy"]))
    leaderboard_winner = ranked[0]["strategy"] if ranked else None
    decision_grade = [row for row in ranked if row.get("decision_grade")]
    recommended = decision_grade[0]["strategy"] if decision_grade else None
    reason_codes = collect_reason_codes(
        current_regime_reasons,
        *(row.get("reason_codes") or [] for row in eligible),
        ["NO_DECISION_GRADE_EVIDENCE"] if recommended is None else [],
    )
    payload = {
        "schema_version": SCHEMA_VERSION,
        "date": target,
        "strategy_id": STRATEGY_ID,
        "governance_label": "RESEARCH_ONLY",
        "execution_impact": "NON_EXECUTIONAL",
        "current_regime": current_regime,
        "eligible_strategies": ranked,
        "excluded_strategies": sorted(excluded, key=lambda row: str(row.get("strategy") or "")),
        "leaderboard_winner": leaderboard_winner,
        "recommended_strategy": recommended,
        "confidence": "MEDIUM" if recommended else "LOW",
        "decision_grade_recommendation": recommended is not None,
        "reason_codes": reason_codes,
        "source_statuses": source_statuses,
        "decision_policy": {
            "min_coverage_days": MIN_COVERAGE_DAYS,
            "min_regime_observations": MIN_REGIME_OBSERVATIONS,
            "leaderboard_winner_is_not_promotion_recommendation": True,
        },
    }
    if write:
        out_dir = model_quality_dir(repo, target, output_root)
        write_json(out_dir / "argo_regime_selection.json", payload)
        write_text(out_dir / "argo_regime_selection.md", render_markdown(payload))
    return payload


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        f"# Argo Regime Selection - {payload.get('date')}",
        "",
        f"- Current regime: {(payload.get('current_regime') or {}).get('regime')} / evidence bucket {(payload.get('current_regime') or {}).get('evidence_regime')}",
        f"- Leaderboard winner: {payload.get('leaderboard_winner')}",
        f"- Decision-grade recommendation: {payload.get('recommended_strategy') or 'none'}",
        f"- Confidence: {payload.get('confidence')}",
        f"- Reason codes: {md_join(payload.get('reason_codes') or [])}",
        "",
        "## Strategy Evidence",
        "",
        "| Strategy | Score | Coverage days | Regime obs | Decision grade | Reasons |",
        "|---|---:|---:|---:|:---:|---|",
    ]
    for row in payload.get("eligible_strategies") or []:
        regime = row.get("regime_evidence") or {}
        lines.append(
            f"| {row.get('strategy')} | {row.get('leaderboard_score')} | {row.get('coverage_days')} | "
            f"{regime.get('observation_count')} | {row.get('decision_grade')} | {md_join(row.get('reason_codes') or [])} |"
        )
    lines.extend(["", "## Exclusions", "", "| Strategy | Reasons |", "|---|---|"])
    for row in payload.get("excluded_strategies") or []:
        lines.append(f"| {row.get('strategy')} | {md_join(row.get('reason_codes') or [])} |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build Argo research-only regime/model-selection artifacts.")
    parser.add_argument("--date", required=True)
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--output-root", default=None)
    args = parser.parse_args(argv)
    payload = build_argo_regime_selection(
        trade_date=args.date,
        repo_root=Path(args.repo_root),
        output_root=Path(args.output_root) if args.output_root else None,
    )
    print(json.dumps({"date": payload["date"], "recommended_strategy": payload["recommended_strategy"], "reason_codes": payload["reason_codes"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
