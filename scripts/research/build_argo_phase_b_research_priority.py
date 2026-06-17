#!/usr/bin/env python3
"""Build Argo Phase B research-priority artifacts.

Argo Phase B ranks where Caerus should spend the next unit of research effort.
It is advisory only: no allocation, execution, broker, risk, promotion, or
runtime decision is made from this artifact.
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

SCHEMA_VERSION = "caerus_argo_phase_b_research_priority_v1"
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


def _priority_classification(score: int, blockers: list[str]) -> str:
    if any(blocker.startswith("external_dependency") for blocker in blockers):
        return "BLOCKED_EXTERNAL"
    if any(blocker.endswith("_missing") or blocker.endswith("_unavailable") for blocker in blockers):
        return "BLOCKED_DATA"
    if any("evidence" in blocker for blocker in blockers):
        return "BLOCKED_EVIDENCE"
    if score >= 75:
        return "RESEARCH_PRIORITY_HIGH"
    if score >= 50:
        return "RESEARCH_PRIORITY_MEDIUM"
    return "RESEARCH_PRIORITY_LOW"


def _priority_row(
    *,
    sleeve_id: str,
    differentiation: int,
    evidence_gap: int,
    uncertainty_reduction: int,
    dependency_impact: int,
    implementation_readiness: int,
    governance_readiness: int,
    external_blocker_penalty: int,
    blockers: list[str],
    next_action: str,
    stop_research: str,
    rationale: list[str],
    sources: list[dict[str, Any]],
) -> dict[str, Any]:
    raw_score = (
        differentiation
        + evidence_gap
        + uncertainty_reduction
        + dependency_impact
        + implementation_readiness
        + governance_readiness
        - external_blocker_penalty
    )
    score = max(0, min(100, int(raw_score)))
    return {
        "sleeve_id": sleeve_id,
        "research_priority_score": score,
        "priority_classification": _priority_classification(score, blockers),
        "inputs": {
            "differentiation": differentiation,
            "evidence_gap": evidence_gap,
            "uncertainty_reduction": uncertainty_reduction,
            "dependency_impact": dependency_impact,
            "implementation_readiness": implementation_readiness,
            "governance_readiness": governance_readiness,
            "external_blocker_penalty": external_blocker_penalty,
        },
        "blockers": sorted(set(blockers)),
        "next_research_action": next_action,
        "research_to_stop": stop_research,
        "rationale": rationale,
        "source_artifacts": sources,
    }


def build_argo_phase_b_research_priority(
    *,
    trade_date: str = DEFAULT_DATE,
    repo_root: Path | str = REPO_ROOT,
    write: bool = True,
) -> dict[str, Any]:
    repo = Path(repo_root)
    phase_a_path = repo / "outputs" / "research" / "argo" / f"argo_phase_a_evidence_framework_{trade_date}.json"
    pit_path = repo / "outputs" / "research" / "pit_rebaseline" / f"orion_lyra_matched_{trade_date}.json"
    phoenix_crisis_path = repo / "outputs" / "research" / "phoenix_evidence" / f"phoenix_crisis_recovery_{trade_date}.json"
    phoenix_b_path = repo / "outputs" / "research" / "phoenix_evidence" / f"phoenix_phase_b_risk_shaping_{trade_date}.json"
    phoenix_c_path = repo / "outputs" / "research" / "phoenix_evidence" / f"phoenix_phase_c_liquidity_capacity_{trade_date}.json"

    phase_a = _read_json(phase_a_path)
    pit = _read_json(pit_path)
    phoenix_phase_b = _read_json(phoenix_b_path)
    phoenix_phase_c = _read_json(phoenix_c_path)

    phoenix_c_classification = (
        _metric(phoenix_phase_c, "classification")
        or _metric(phoenix_phase_c, "decision", "classification")
        or "UNKNOWN"
    )
    phoenix_candidate = _metric(phoenix_phase_b, "best_research_candidate", "classification") or "UNKNOWN"
    orion_lyra_t_stat = _metric(pit, "paired_significance", "paired_t_stat")
    active_share = _metric(pit, "active_share", "average")

    rows = [
        _priority_row(
            sleeve_id="phoenix",
            differentiation=20,
            evidence_gap=18,
            uncertainty_reduction=20,
            dependency_impact=20,
            implementation_readiness=16,
            governance_readiness=12,
            external_blocker_penalty=3,
            blockers=[
                "external_dependency_blocked",
                "nasdaq_data_link_qelx06_temporary_disablement",
                "pit_liquidity_ohlcv_unavailable",
            ],
            next_action="Restore Sharadar SEP OHLCV access, rebuild PIT liquidity panel, rerun Phoenix Phase C, then reassess Shadow readiness.",
            stop_research="Stop additional Phoenix alpha tuning until liquidity and capacity evidence exists.",
            rationale=[
                "Phoenix is the most differentiated candidate versus the momentum family.",
                f"Phase B risk-shaping candidate is {phoenix_candidate}.",
                f"Phase C liquidity classification is {phoenix_c_classification}, so research effort should attack the data dependency first.",
            ],
            sources=[_source(phoenix_crisis_path), _source(phoenix_b_path), _source(phoenix_c_path)],
        ),
        _priority_row(
            sleeve_id="cassiopeia",
            differentiation=18,
            evidence_gap=20,
            uncertainty_reduction=16,
            dependency_impact=14,
            implementation_readiness=8,
            governance_readiness=8,
            external_blocker_penalty=0,
            blockers=["event_contract_missing", "event_tape_missing", "decision_grade_evidence_missing"],
            next_action="Build a PIT-safe event taxonomy and event-tape contract for analyst upgrades, index events, and activist 13D filings.",
            stop_research="Do not implement event signals before the event contract proves timestamp availability and source lineage.",
            rationale=[
                "Cassiopeia is unblocked by the OHLCV vendor issue and attacks a non-momentum return driver.",
                "The largest uncertainty is binary and platform-useful: can Caerus build a PIT-safe event tape?",
                "A minimal event contract would also support later Argo evidence comparisons.",
            ],
            sources=[_source(repo / "docs" / "governance" / "fr_active" / "fr_069_cassiopeia_onboarding_packet.md")],
        ),
        _priority_row(
            sleeve_id="argo",
            differentiation=14,
            evidence_gap=8,
            uncertainty_reduction=10,
            dependency_impact=14,
            implementation_readiness=8,
            governance_readiness=8,
            external_blocker_penalty=0,
            blockers=[],
            next_action="Accumulate Phase A/B scoring history across future evidence packets and add reviewer notes when rankings change.",
            stop_research="Do not turn Argo scores into allocation, promotion, or retirement rules.",
            rationale=[
                "Argo improves research throughput by ranking uncertainty, dependencies, and stop-work candidates.",
                "Phase B should remain advisory and non-executing.",
            ],
            sources=[_source(phase_a_path), _source(repo / "docs" / "governance" / "fr_active" / "fr_069_argo_phase_a_evidence_framework.md")],
        ),
        _priority_row(
            sleeve_id="orion",
            differentiation=6,
            evidence_gap=6,
            uncertainty_reduction=10,
            dependency_impact=12,
            implementation_readiness=15,
            governance_readiness=16,
            external_blocker_penalty=0,
            blockers=["merge_watch_owner_decision_missing"],
            next_action="Prepare an owner-facing Orion/Lyra merge-watch packet selecting the canonical retained implementation and rollback criteria.",
            stop_research="Stop open-ended Orion versus Lyra performance comparisons unless new holdout or sector/factor evidence is added.",
            rationale=[
                "Matched PIT evidence is already decision-grade for disposition triage.",
                f"Paired t-stat is {orion_lyra_t_stat}; active share is {active_share}.",
                "The next useful work is governance disposition, not another generic comparison.",
            ],
            sources=[_source(pit_path), _source(repo / "docs" / "governance" / "fr_active" / "fr_068_phase_c_disposition_analysis.md")],
        ),
        _priority_row(
            sleeve_id="polaris",
            differentiation=4,
            evidence_gap=5,
            uncertainty_reduction=6,
            dependency_impact=8,
            implementation_readiness=12,
            governance_readiness=12,
            external_blocker_penalty=0,
            blockers=["baseline_monitoring_only"],
            next_action="Use Polaris as the control sleeve for comparisons; spend new research effort elsewhere unless production monitoring fails.",
            stop_research="Stop feature expansion on Polaris while execution and target-attainment remain observe-first.",
            rationale=[
                "Polaris is the paper baseline and should remain stable.",
                "Research ROI is lower than differentiated or unresolved sleeves.",
            ],
            sources=[_source(repo / "outputs" / "research" / "pit_rebaseline" / "polaris_priced_2026-06-10.json")],
        ),
        _priority_row(
            sleeve_id="cygnus",
            differentiation=14,
            evidence_gap=18,
            uncertainty_reduction=12,
            dependency_impact=9,
            implementation_readiness=6,
            governance_readiness=8,
            external_blocker_penalty=15,
            blockers=["v0_shelved", "eps_surprise_consensus_vendor_missing", "decision_grade_evidence_missing"],
            next_action="Defer until a PIT consensus/EPS-surprise vendor path is selected; then draft a v1 holdout-preserving research plan.",
            stop_research="Stop v0 retuning; the holdout remains preserved and v0 is shelved.",
            rationale=[
                "Cygnus is differentiated but vendor-gated and less immediately actionable than Phoenix liquidity or Cassiopeia event contracts.",
                "Retuning v0 would violate the existing governance state.",
            ],
            sources=[_source(repo / "docs" / "governance" / "fr_active" / "fr_069_cygnus_onboarding_packet.md")],
        ),
        _priority_row(
            sleeve_id="lyra",
            differentiation=3,
            evidence_gap=4,
            uncertainty_reduction=4,
            dependency_impact=6,
            implementation_readiness=8,
            governance_readiness=8,
            external_blocker_penalty=0,
            blockers=["merge_watch_redundancy", "redeployment_watch"],
            next_action="Do not fund independent Lyra research; only include Lyra in the Orion/Lyra disposition packet or a future redeployment thesis.",
            stop_research="Stop treating Lyra as a separate promotion candidate.",
            rationale=[
                "Lyra has not shown statistically meaningful superiority versus Orion.",
                "The disposition packet places Lyra on redeployment/merge watch.",
            ],
            sources=[_source(pit_path), _source(repo / "docs" / "governance" / "fr_active" / "fr_068_phase_c_disposition_analysis.md")],
        ),
    ]

    rows = sorted(rows, key=lambda row: (-int(row["research_priority_score"]), str(row["sleeve_id"])))
    for idx, row in enumerate(rows, start=1):
        row["research_priority_rank"] = idx

    payload = {
        "schema_version": SCHEMA_VERSION,
        "trade_date": trade_date,
        "governance_label": "RESEARCH_ONLY",
        "execution_impact": "NON_EXECUTIONAL",
        "production_impact": "research_only",
        "behavior_change_allowed": False,
        "argo_role": "research_prioritization_engine_advisory_only",
        "explicit_non_goals": [
            "no allocation",
            "no capital routing",
            "no execution",
            "no broker interaction",
            "no promotion",
            "no retirement",
            "no runtime behavior change",
        ],
        "evidence_inventory": {
            "phase_a_available": phase_a is not None,
            "sources_inspected": [
                "Argo Phase A evidence framework",
                "FR-069 evidence envelope templates and onboarding packets",
                "Orion/Lyra PIT rebaseline and disposition analysis",
                "Phoenix crisis/recovery, Phase B risk-shaping, and Phase C liquidity/capacity artifacts",
                "Current research roadmap",
            ],
            "external_blockers": [
                "Nasdaq Data Link QELx06 disables Sharadar SEP OHLCV restoration for Phoenix liquidity/capacity.",
                "Cygnus v1 requires PIT consensus/EPS-surprise vendor data.",
            ],
            "ignored_by_design": [
                "live broker state",
                "execution artifacts",
                "allocation targets",
                "order lifecycle data",
                "promotion status as an automatic action",
            ],
        },
        "priority_methodology": {
            "score_name": "research_priority_score",
            "score_range": [0, 100],
            "inputs": [
                "differentiation",
                "evidence_gap",
                "uncertainty_reduction",
                "dependency_impact",
                "implementation_readiness",
                "governance_readiness",
                "external_blocker_penalty",
            ],
            "interpretation": "Higher score means higher expected value for the next unit of research effort; it does not authorize capital allocation or lifecycle promotion.",
            "classifications": [
                "RESEARCH_PRIORITY_HIGH",
                "RESEARCH_PRIORITY_MEDIUM",
                "RESEARCH_PRIORITY_LOW",
                "BLOCKED_EXTERNAL",
                "BLOCKED_EVIDENCE",
                "BLOCKED_DATA",
                "READY_FOR_NEXT_RESEARCH",
            ],
        },
        "research_priority_ranking": rows,
        "highest_roi_research_task": {
            "sleeve_id": "phoenix",
            "task": "Restore Sharadar SEP OHLCV access and rebuild PIT liquidity/capacity evidence.",
            "reason": "It resolves the single largest blocker on the most differentiated risk-shaped sleeve and unlocks a Shadow-readiness decision.",
        },
        "biggest_platform_blocker": {
            "blocker": "Sharadar SEP OHLCV access unavailable due to Nasdaq Data Link QELx06 temporary disablement.",
            "affected_work": ["Phoenix Phase C", "PIT liquidity infrastructure", "capacity analysis", "future Shadow readiness"],
        },
        "dependency_map": {
            "phoenix": ["Sharadar SEP OHLCV access", "OHLCV cache rebuild", "PIT liquidity panel", "Phase C rerun"],
            "cassiopeia": ["owner-approved event taxonomy", "PIT event tape", "availability timestamps"],
            "orion": ["owner disposition decision", "optional sector/factor overlap diagnostic"],
            "lyra": ["owner disposition decision", "redeployment thesis if retained under new purpose"],
            "cygnus": ["PIT consensus/EPS-surprise vendor", "v1 holdout-preserving plan"],
            "argo": ["future evidence packet history", "reviewer challenge log"],
            "polaris": ["baseline monitoring", "FR-070 observation"],
        },
        "reason_codes": [
            "research_only_no_runtime_change",
            "argo_advisory_only",
            "forced_research_priority_ranking",
            "phoenix_external_dependency_blocked",
            "stop_independent_lyra_research",
        ],
    }

    if write:
        out_dir = repo / "outputs" / "research" / "argo"
        out_dir.mkdir(parents=True, exist_ok=True)
        json_path = out_dir / f"argo_phase_b_research_priority_{trade_date}.json"
        md_path = out_dir / f"argo_phase_b_research_priority_{trade_date}.md"
        payload["artifact_paths"] = {"json": str(json_path), "markdown": str(md_path)}
        json_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        md_path.write_text(render_markdown(payload), encoding="utf-8")
    return payload


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        f"# Argo Phase B Research Priority - {payload.get('trade_date')}",
        "",
        "RESEARCH_ONLY",
        "NO_RUNTIME_CHANGE",
        "",
        "## Executive Summary",
        "",
        "Argo Phase B ranks where Caerus should spend future research effort. It is advisory only and does not allocate capital, select securities, submit orders, promote sleeves, retire sleeves, or change production behavior.",
        "",
        "## Research Priority Ranking",
        "",
        "| Rank | Sleeve | Classification | Score | Next action |",
        "|---:|---|---|---:|---|",
    ]
    for row in payload.get("research_priority_ranking") or []:
        lines.append(
            f"| {row.get('research_priority_rank')} | {row.get('sleeve_id')} | {row.get('priority_classification')} | {row.get('research_priority_score')} | {row.get('next_research_action')} |"
        )
    lines.extend(
        [
            "",
            "## Highest ROI Task",
            "",
            f"{payload.get('highest_roi_research_task', {}).get('task')}",
            "",
            "## Biggest Platform Blocker",
            "",
            f"{payload.get('biggest_platform_blocker', {}).get('blocker')}",
            "",
            "## Research To Stop",
            "",
        ]
    )
    for row in payload.get("research_priority_ranking") or []:
        lines.append(f"- `{row.get('sleeve_id')}`: {row.get('research_to_stop')}")
    lines.extend(
        [
            "",
            "## Governance Controls",
            "",
            "- Argo Phase B is advisory only.",
            "- The ranking is a research queue, not an allocation queue.",
            "- Owner approval and separate FRs are required for promotion, retirement, capital routing, or production behavior changes.",
            "- Execution, broker, risk, allocation, strategy-selection, and promotion code are out of scope.",
            "",
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build Argo Phase B research-only priority artifact.")
    parser.add_argument("--date", default=DEFAULT_DATE)
    parser.add_argument("--repo-root", default=str(REPO_ROOT))
    args = parser.parse_args(argv)
    payload = build_argo_phase_b_research_priority(trade_date=args.date, repo_root=Path(args.repo_root), write=True)
    print(json.dumps(payload.get("artifact_paths", {}), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
