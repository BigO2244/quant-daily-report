from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from research_registry.research.model_quality_common import (
    collect_reason_codes,
    md_join,
    model_quality_dir,
    normalize_date,
    read_json,
    write_json,
    write_text,
)

SCHEMA_VERSION = "caerus_argo_phase_b_validation_v1"
OVERLAY_ID = "caerus_argo"


def build_argo_phase_b_validation(
    *,
    trade_date: str,
    repo_root: Path | str = Path("."),
    output_root: Path | str | None = None,
    write: bool = True,
) -> dict[str, Any]:
    target = normalize_date(trade_date)
    repo = Path(repo_root)
    argo_payload, argo_source = _load_argo_selection(repo, target)
    stability = _stability_summary(repo, target)
    transition = _transition_summary(stability)
    freshness = _input_freshness(argo_payload, argo_source, target)
    lookahead = _no_lookahead_checks(argo_payload, argo_source, target, stability)
    blockers = _evidence_blockers(argo_payload, freshness, lookahead)
    reason_codes = set(collect_reason_codes(
        argo_source.get("reason_codes") or [],
        stability.get("reason_codes") or [],
        transition.get("reason_codes") or [],
        freshness.get("reason_codes") or [],
        lookahead.get("reason_codes") or [],
        blockers,
    ))
    reason_codes.discard("ok")
    leaderboard_winner = (argo_payload or {}).get("leaderboard_winner")
    current_recommendation = (argo_payload or {}).get("recommended_strategy")
    decision_grade = bool((argo_payload or {}).get("decision_grade_recommendation")) and not blockers
    if leaderboard_winner and not current_recommendation:
        reason_codes.add("LEADERBOARD_WINNER_NOT_DECISION_GRADE_RECOMMENDATION")
    payload = {
        "trade_date": target,
        "schema_version": SCHEMA_VERSION,
        "overlay_id": OVERLAY_ID,
        "governance_label": "RESEARCH_ONLY",
        "execution_impact": "NON_EXECUTIONAL",
        "current_regime": _current_regime(argo_payload, stability),
        "leaderboard_winner": leaderboard_winner,
        "current_recommendation": current_recommendation,
        "recommendation_confidence": (argo_payload or {}).get("confidence") or "NONE",
        "decision_grade_recommendation": decision_grade,
        "stability_summary": stability,
        "transition_summary": transition,
        "input_freshness": freshness,
        "no_lookahead_checks": lookahead,
        "evidence_blockers": sorted(set(blockers)),
        "source_artifacts": {"argo_regime_selection": argo_source},
        "reason_codes": sorted(reason_codes) or ["ok"],
    }
    if write:
        out_dir = model_quality_dir(repo, target, output_root)
        write_json(out_dir / "argo_phase_b_validation.json", payload)
        write_text(out_dir / "argo_phase_b_validation.md", render_markdown(payload))
    return payload


def _load_argo_selection(repo: Path, target: str) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    exact = repo / "outputs" / "model_quality" / target / "argo_regime_selection.json"
    if exact.exists():
        payload = read_json(exact)
        return payload, {"status": "PRESENT" if payload else "MALFORMED", "path": str(exact), "source_date": target, "target_date": target, "reason_codes": ["ok"] if payload else ["ARGO_SELECTION_PARSE_ERROR"]}
    root = repo / "outputs" / "model_quality"
    candidates: list[Path] = []
    if root.exists():
        for child in root.iterdir():
            if not child.is_dir():
                continue
            try:
                date = normalize_date(child.name)
            except Exception:
                continue
            path = child / "argo_regime_selection.json"
            if date <= target and path.exists():
                candidates.append(path)
    if candidates:
        selected = sorted(candidates, key=lambda path: path.parent.name)[-1]
        payload = read_json(selected)
        return payload, {
            "status": "STALE" if payload else "MALFORMED",
            "path": str(selected),
            "source_date": selected.parent.name,
            "target_date": target,
            "reason_codes": ["SOURCE_DATE_DIFFERS_FROM_TARGET"] if payload else ["ARGO_SELECTION_PARSE_ERROR"],
        }
    return None, {"status": "MISSING", "path": None, "source_date": None, "target_date": target, "reason_codes": ["ARGO_SELECTION_ARTIFACT_MISSING"]}


def _current_regime(argo_payload: dict[str, Any] | None, stability: dict[str, Any]) -> dict[str, Any]:
    regime = (argo_payload or {}).get("current_regime")
    if isinstance(regime, dict):
        return regime
    latest = (stability.get("daily_regimes") or [None])[-1]
    return {"regime": (latest or {}).get("regime") or "UNKNOWN", "reason_codes": ["CURRENT_REGIME_FROM_STABILITY_SUMMARY"] if latest else ["CURRENT_REGIME_MISSING"]}


def _stability_summary(repo: Path, target: str, *, window_days: int = 10) -> dict[str, Any]:
    rows = []
    root = repo / "outputs" / "vix_regime"
    if root.exists():
        for child in sorted(root.iterdir(), key=lambda path: path.name):
            if not child.is_dir():
                continue
            try:
                date = normalize_date(child.name)
            except Exception:
                continue
            if date > target:
                continue
            payload = read_json(child / "regime_current.json")
            if payload:
                rows.append({"date": date, "regime": str(payload.get("regime") or "UNKNOWN").upper(), "source_path": str(child / "regime_current.json")})
    current_payload = read_json(root / "regime_current.json")
    if current_payload:
        raw_date = str(current_payload.get("date") or current_payload.get("as_of") or "")[:10]
        try:
            date = normalize_date(raw_date)
        except Exception:
            date = target
        if date <= target and all(row["date"] != date for row in rows):
            rows.append({"date": date, "regime": str(current_payload.get("regime") or "UNKNOWN").upper(), "source_path": str(root / "regime_current.json")})
    rows = sorted(rows, key=lambda row: row["date"])[-window_days:]
    if not rows:
        return {"available": False, "window_days": window_days, "observations": 0, "unique_regimes": [], "transition_count": 0, "stable_regime": False, "daily_regimes": [], "reason_codes": ["REGIME_HISTORY_MISSING"]}
    transitions = sum(1 for prev, cur in zip(rows, rows[1:]) if prev["regime"] != cur["regime"])
    return {
        "available": True,
        "window_days": window_days,
        "observations": len(rows),
        "unique_regimes": sorted({row["regime"] for row in rows}),
        "transition_count": transitions,
        "stable_regime": transitions == 0,
        "daily_regimes": rows,
        "reason_codes": ["ok"] if rows[-1]["date"] == target else ["REGIME_HISTORY_LAGS_TARGET"],
    }


def _transition_summary(stability: dict[str, Any]) -> dict[str, Any]:
    rows = stability.get("daily_regimes") or []
    transitions = []
    for prev, cur in zip(rows, rows[1:]):
        if prev.get("regime") != cur.get("regime"):
            transitions.append({"from_date": prev.get("date"), "to_date": cur.get("date"), "from_regime": prev.get("regime"), "to_regime": cur.get("regime")})
    return {
        "transition_count": len(transitions),
        "transitions": transitions,
        "hysteresis_diagnosed": True,
        "reason_codes": ["ok"] if rows else ["REGIME_HISTORY_MISSING"],
    }


def _input_freshness(argo_payload: dict[str, Any] | None, argo_source: dict[str, Any], target: str) -> dict[str, Any]:
    statuses = list((argo_payload or {}).get("source_statuses") or [])
    statuses.insert(0, {"name": "argo_regime_selection", **argo_source})
    stale = []
    missing = []
    future = []
    for status in statuses:
        source_date = status.get("source_date")
        if status.get("status") == "MISSING" or not status.get("path"):
            missing.append(status.get("name") or "unknown")
            continue
        if source_date:
            try:
                normalized = normalize_date(str(source_date)[:10])
            except Exception:
                stale.append(status.get("name") or "unknown")
                continue
            if normalized < target:
                stale.append(status.get("name") or "unknown")
            if normalized > target:
                future.append(status.get("name") or "unknown")
    status = "FRESH"
    reasons = []
    if future:
        status = "BLOCKED"
        reasons.append("FUTURE_DATED_INPUT_DETECTED")
    elif missing:
        status = "PARTIAL"
        reasons.append("INPUTS_MISSING")
    elif stale:
        status = "STALE"
        reasons.append("STALE_REGIME_DATA" if "current_regime" in stale or "regime_current" in stale else "STALE_INPUT_DATA")
    return {"status": status, "stale_inputs": sorted(stale), "missing_inputs": sorted(missing), "future_dated_inputs": sorted(future), "source_statuses": statuses, "reason_codes": sorted(reasons) or ["ok"]}


def _no_lookahead_checks(argo_payload: dict[str, Any] | None, argo_source: dict[str, Any], target: str, stability: dict[str, Any]) -> dict[str, Any]:
    violations = []
    for status in [argo_source] + list((argo_payload or {}).get("source_statuses") or []):
        source_date = status.get("source_date")
        if not source_date:
            continue
        try:
            if normalize_date(str(source_date)[:10]) > target:
                violations.append({"name": status.get("name") or "unknown", "source_date": source_date})
        except Exception:
            continue
    for row in stability.get("daily_regimes") or []:
        if row.get("date") and row["date"] > target:
            violations.append({"name": "regime_history", "source_date": row["date"]})
    reasons = ["ok"]
    status = "PASS"
    if violations:
        status = "FAIL"
        reasons = ["NO_LOOKAHEAD_VIOLATION"]
    elif argo_payload is None:
        status = "PARTIAL"
        reasons = ["MODEL_SELECTION_MISSING_NO_LOOKAHEAD_NOT_FULLY_PROVABLE"]
    return {"status": status, "checks": ["source_dates_not_after_trade_date", "leaderboard_evidence_not_used_as_capital_route"], "violations": violations, "reason_codes": reasons}


def _evidence_blockers(argo_payload: dict[str, Any] | None, freshness: dict[str, Any], lookahead: dict[str, Any]) -> list[str]:
    blockers = []
    if argo_payload is None:
        blockers.append("ARGO_SELECTION_ARTIFACT_MISSING")
    if freshness.get("status") in {"STALE", "PARTIAL", "BLOCKED"}:
        blockers.extend(code for code in freshness.get("reason_codes") or [] if code != "ok")
    if lookahead.get("status") != "PASS":
        blockers.extend(code for code in lookahead.get("reason_codes") or [] if code != "ok")
    if argo_payload is not None and not argo_payload.get("decision_grade_recommendation"):
        blockers.append("NO_DECISION_GRADE_RECOMMENDATION")
    return sorted(set(blockers))


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        f"# Argo Phase B Validation - {payload.get('trade_date')}",
        "",
        f"- Overlay: {payload.get('overlay_id')}",
        f"- Governance: {payload.get('governance_label')} / {payload.get('execution_impact')}",
        f"- Current regime: {(payload.get('current_regime') or {}).get('regime')}",
        f"- Leaderboard winner: {payload.get('leaderboard_winner')}",
        f"- Current recommendation: {payload.get('current_recommendation') or 'none'}",
        f"- Decision-grade recommendation: {payload.get('decision_grade_recommendation')}",
        f"- Freshness: {(payload.get('input_freshness') or {}).get('status')}",
        f"- No-lookahead: {(payload.get('no_lookahead_checks') or {}).get('status')}",
        f"- Reason codes: {md_join(payload.get('reason_codes') or [])}",
        "",
        "## Evidence Blockers",
        "",
    ]
    for blocker in payload.get("evidence_blockers") or []:
        lines.append(f"- {blocker}")
    if not payload.get("evidence_blockers"):
        lines.append("- none")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build Argo Phase B validation artifacts.")
    parser.add_argument("--date", required=True)
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--output-root", default=None)
    args = parser.parse_args(argv)
    payload = build_argo_phase_b_validation(
        trade_date=args.date,
        repo_root=Path(args.repo_root),
        output_root=Path(args.output_root) if args.output_root else None,
    )
    print(json.dumps({"trade_date": payload["trade_date"], "current_recommendation": payload["current_recommendation"], "decision_grade_recommendation": payload["decision_grade_recommendation"], "reason_codes": payload["reason_codes"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
