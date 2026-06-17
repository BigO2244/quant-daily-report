#!/usr/bin/env python3
"""Build Argo Phase A research-only sleeve evidence framework artifacts.

Argo Phase A consumes existing sleeve evidence and governance packets. It does
not allocate capital, select securities, submit orders, or alter production
state.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

SCHEMA_VERSION = "caerus_argo_phase_a_evidence_framework_v1"
DEFAULT_DATE = "2026-06-17"


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _source(path: Path) -> dict[str, Any]:
    return {"path": str(path), "exists": path.exists()}


def _metric(payload: dict[str, Any] | None, *path: str) -> Any:
    cur: Any = payload
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def _classification(score: int, blockers: list[str]) -> str:
    if "external_dependency_blocked" in blockers:
        return "EXTERNAL_DEPENDENCY_BLOCKED"
    if blockers:
        return "NOT_READY"
    if score >= 80:
        return "EVIDENCE_READY"
    if score >= 60:
        return "RESEARCH_READY"
    return "NOT_READY"


def _score_sleeve(
    *,
    sleeve_id: str,
    evidence_quality: int,
    pit_ready: bool,
    differentiated: bool,
    blockers: list[str],
    notes: list[str],
    sources: list[dict[str, Any]],
    readiness_hint: str | None = None,
) -> dict[str, Any]:
    score = int(evidence_quality)
    if pit_ready:
        score += 15
    if differentiated:
        score += 10
    if blockers:
        score -= min(35, 10 * len(blockers))
    score = max(0, min(100, score))
    return {
        "sleeve_id": sleeve_id,
        "score": score,
        "classification": readiness_hint or _classification(score, blockers),
        "pit_ready": bool(pit_ready),
        "differentiated": bool(differentiated),
        "blockers": sorted(set(blockers)),
        "notes": notes,
        "source_artifacts": sources,
    }


def build_argo_phase_a_evidence_framework(
    *,
    trade_date: str = DEFAULT_DATE,
    repo_root: Path | str = REPO_ROOT,
    write: bool = True,
) -> dict[str, Any]:
    repo = Path(repo_root)
    pit = _read_json(repo / "outputs" / "research" / "pit_rebaseline" / f"orion_lyra_matched_{trade_date}.json")
    phoenix_crisis = _read_json(repo / "outputs" / "research" / "phoenix_evidence" / f"phoenix_crisis_recovery_{trade_date}.json")
    phoenix_phase_b = _read_json(repo / "outputs" / "research" / "phoenix_evidence" / f"phoenix_phase_b_risk_shaping_{trade_date}.json")
    phoenix_phase_c = _read_json(repo / "outputs" / "research" / "phoenix_evidence" / f"phoenix_phase_c_liquidity_capacity_{trade_date}.json")

    pit_exists = pit is not None
    raw_phoenix_c_status = _metric(phoenix_phase_c, "decision", "classification") or _metric(phoenix_phase_c, "classification")
    if isinstance(raw_phoenix_c_status, dict):
        raw_phoenix_c_status = raw_phoenix_c_status.get("classification")
    phoenix_c_status = str(raw_phoenix_c_status or "")
    phoenix_blockers = [
        "external_dependency_blocked",
        "nasdaq_data_link_qelx06_temporary_disablement",
        "pit_liquidity_ohlcv_unavailable",
    ]
    if phoenix_c_status and phoenix_c_status != "PENDING_LIQUIDITY":
        phoenix_blockers.append(f"unexpected_phase_c_status:{phoenix_c_status}")

    sleeves = [
        _score_sleeve(
            sleeve_id="polaris",
            evidence_quality=65,
            pit_ready=True,
            differentiated=True,
            blockers=[],
            notes=[
                "Current paper baseline.",
                "FR-068 PIT priced rebaseline exists and is material; use as baseline evidence input.",
            ],
            sources=[
                _source(repo / "outputs" / "research" / "pit_rebaseline" / "polaris_priced_2026-06-10.json"),
                _source(repo / "docs" / "governance" / "fr_active" / "fr_069_phase_c_readiness.md"),
            ],
        ),
        _score_sleeve(
            sleeve_id="orion",
            evidence_quality=70 if pit_exists else 35,
            pit_ready=pit_exists,
            differentiated=False,
            blockers=[] if pit_exists else ["orion_lyra_pit_artifact_missing"],
            notes=[
                "Matched PIT evidence exists.",
                "Disposition analysis treats Orion/Lyra as variations of one sleeve family; Orion has lower turnover and lower drawdown in the PIT artifact.",
            ],
            sources=[
                _source(repo / "outputs" / "research" / "pit_rebaseline" / f"orion_lyra_matched_{trade_date}.json"),
                _source(repo / "docs" / "governance" / "fr_active" / "fr_068_phase_c_disposition_analysis.md"),
            ],
        ),
        _score_sleeve(
            sleeve_id="lyra",
            evidence_quality=68 if pit_exists else 35,
            pit_ready=pit_exists,
            differentiated=False,
            blockers=["merge_watch_redundancy"] if pit_exists else ["orion_lyra_pit_artifact_missing"],
            notes=[
                "Matched PIT evidence exists, but paired t-stat does not support a statistically meaningful Lyra lead.",
                "Disposition packet places Lyra on redeployment/merge-watch rather than promotion.",
            ],
            sources=[
                _source(repo / "outputs" / "research" / "pit_rebaseline" / f"orion_lyra_matched_{trade_date}.json"),
                _source(repo / "docs" / "governance" / "fr_active" / "fr_068_phase_c_disposition_analysis.md"),
            ],
        ),
        _score_sleeve(
            sleeve_id="phoenix",
            evidence_quality=62 if phoenix_crisis and phoenix_phase_b else 30,
            pit_ready=False,
            differentiated=True,
            blockers=phoenix_blockers,
            readiness_hint="EXTERNAL_DEPENDENCY_BLOCKED",
            notes=[
                "Phoenix is differentiated and risk-shaped, but Phase C liquidity/capacity remains blocked.",
                "Blocker owner: Brett.",
                "Unblock condition: vendor confirms Sharadar SEP OHLCV access restored after Nasdaq Data Link QELx06 temporary disablement.",
            ],
            sources=[
                _source(repo / "outputs" / "research" / "phoenix_evidence" / f"phoenix_crisis_recovery_{trade_date}.json"),
                _source(repo / "outputs" / "research" / "phoenix_evidence" / f"phoenix_phase_b_risk_shaping_{trade_date}.json"),
                _source(repo / "outputs" / "research" / "phoenix_evidence" / f"phoenix_phase_c_liquidity_capacity_{trade_date}.json"),
            ],
        ),
        _score_sleeve(
            sleeve_id="cassiopeia",
            evidence_quality=20,
            pit_ready=False,
            differentiated=True,
            blockers=["research_spec_only", "event_contract_missing", "decision_grade_evidence_missing"],
            notes=["Governed Research-stage onboarding exists; no implementation evidence yet."],
            sources=[_source(repo / "docs" / "governance" / "fr_active" / "fr_069_cassiopeia_onboarding_packet.md")],
        ),
        _score_sleeve(
            sleeve_id="cygnus",
            evidence_quality=25,
            pit_ready=False,
            differentiated=True,
            blockers=["v0_shelved", "eps_surprise_consensus_vendor_missing", "decision_grade_evidence_missing"],
            notes=["V0 is shelved; v1 remains vendor-gated on PIT consensus/surprise data."],
            sources=[_source(repo / "docs" / "governance" / "fr_active" / "fr_069_cygnus_onboarding_packet.md")],
        ),
        _score_sleeve(
            sleeve_id="argo",
            evidence_quality=55,
            pit_ready=True,
            differentiated=True,
            blockers=[],
            readiness_hint="RESEARCH_READY",
            notes=[
                "Argo Phase A consumes sleeve evidence and emits research classifications only.",
                "No allocation, promotion, order, or runtime decision is authorized.",
            ],
            sources=[
                _source(repo / "docs" / "governance" / "fr_active" / "fr_069_argo_onboarding_packet.md"),
                _source(repo / "docs" / "governance" / "fr_active" / "fr_069_argo_evidence_envelope_template.json"),
            ],
        ),
    ]

    payload = {
        "schema_version": SCHEMA_VERSION,
        "trade_date": trade_date,
        "governance_label": "RESEARCH_ONLY",
        "execution_impact": "NON_EXECUTIONAL",
        "production_impact": "research_only",
        "behavior_change_allowed": False,
        "argo_role": "evidence_consumer_only",
        "explicit_non_goals": [
            "no allocation",
            "no capital routing",
            "no execution",
            "no broker interaction",
            "no promotion",
            "no runtime behavior change",
        ],
        "evidence_inventory": {
            "sources_inspected": [
                "FR-069 sleeve manifest and onboarding packets",
                "Orion/Lyra PIT rebaseline and disposition packet",
                "Phoenix crisis/recovery, Phase B risk-shaping, and Phase C liquidity/capacity artifacts",
                "FR-069 evidence envelope templates",
            ],
            "consumable_now": [
                "orion_lyra_matched_pit",
                "orion_lyra_disposition",
                "phoenix_crisis_recovery",
                "phoenix_phase_b_risk_shaping",
                "phoenix_phase_c_blocker",
                "fr069_sleeve_manifest",
            ],
            "ignored_by_design": [
                "live broker state",
                "execution artifacts",
                "allocation targets",
                "order lifecycle data",
                "post-hoc non-PIT evidence",
            ],
        },
        "scoring_framework": {
            "inputs": [
                "evidence_quality",
                "pit_readiness",
                "differentiation",
                "drawdown_or_risk_evidence",
                "turnover_or_cost_sensitivity",
                "readiness_blockers",
            ],
            "classifications": [
                "NOT_READY",
                "RESEARCH_READY",
                "EVIDENCE_READY",
                "SHADOW_CANDIDATE",
                "PROMOTION_CANDIDATE",
                "EXTERNAL_DEPENDENCY_BLOCKED",
            ],
            "governance_control": "SHADOW_CANDIDATE and PROMOTION_CANDIDATE are descriptive research labels only; owner approval is required before any lifecycle transition.",
        },
        "sleeve_scores": sleeves,
        "recommendations": [
            {
                "item": "phoenix",
                "classification": "EXTERNAL_DEPENDENCY_BLOCKED",
                "action": "hold until Sharadar SEP OHLCV access is restored, then rebuild liquidity evidence.",
            },
            {
                "item": "orion_lyra",
                "classification": "EVIDENCE_READY_FOR_GOVERNANCE_REVIEW",
                "action": "treat as one redundant core-momentum family pending any owner-approved merge/redeploy decision.",
            },
            {
                "item": "argo",
                "classification": "RESEARCH_READY",
                "action": "use Phase A as an evidence inventory/scoring surface only.",
            },
        ],
        "reason_codes": [
            "research_only_no_runtime_change",
            "argo_evidence_consumer_only",
            "phoenix_external_dependency_blocked",
            "nasdaq_data_link_qelx06_temporary_disablement",
        ],
    }
    if write:
        out_dir = repo / "outputs" / "research" / "argo"
        out_dir.mkdir(parents=True, exist_ok=True)
        json_path = out_dir / f"argo_phase_a_evidence_framework_{trade_date}.json"
        md_path = out_dir / f"argo_phase_a_evidence_framework_{trade_date}.md"
        json_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        md_path.write_text(render_markdown(payload), encoding="utf-8")
        payload["artifact_paths"] = {"json": str(json_path), "markdown": str(md_path)}
        json_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        f"# Argo Phase A Evidence Framework - {payload.get('trade_date')}",
        "",
        "RESEARCH_ONLY",
        "NO_RUNTIME_CHANGE",
        "",
        "## Executive Summary",
        "",
        "Argo Phase A consumes existing sleeve evidence and emits research-only readiness classifications. It does not allocate capital, select securities, submit orders, or change production behavior.",
        "",
        "## Sleeve Scores",
        "",
        "| Sleeve | Classification | Score | Blockers |",
        "|---|---:|---:|---|",
    ]
    for row in payload.get("sleeve_scores") or []:
        lines.append(
            f"| {row.get('sleeve_id')} | {row.get('classification')} | {row.get('score')} | {', '.join(row.get('blockers') or []) or 'none'} |"
        )
    lines.extend(
        [
            "",
            "## Governance Controls",
            "",
            "- Argo is an observer, not a decision maker.",
            "- SHADOW_CANDIDATE and PROMOTION_CANDIDATE labels are descriptive only.",
            "- Owner approval is required for any lifecycle, allocation, or production change.",
            "- Execution, broker, risk, allocation, strategy-selection, and promotion code are out of scope.",
            "",
            "## Recommendations",
            "",
        ]
    )
    for row in payload.get("recommendations") or []:
        lines.append(f"- `{row.get('item')}`: `{row.get('classification')}` - {row.get('action')}")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build Argo Phase A research-only evidence framework artifact.")
    parser.add_argument("--date", default=DEFAULT_DATE)
    parser.add_argument("--repo-root", default=str(REPO_ROOT))
    args = parser.parse_args(argv)
    payload = build_argo_phase_a_evidence_framework(trade_date=args.date, repo_root=Path(args.repo_root), write=True)
    print(json.dumps(payload.get("artifact_paths", {}), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
