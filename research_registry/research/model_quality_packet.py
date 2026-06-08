from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from research_registry.research.model_quality_common import (
    md_join,
    model_quality_dir,
    normalize_date,
    read_json,
    write_json,
    write_text,
)

SCHEMA_VERSION = "caerus_model_quality_packet_v1"
SECTION_FILES = {
    "attribution_quality": "attribution_quality.json",
    "phoenix_research": "phoenix_research.json",
    "cassiopeia_model_selection": "cassiopeia_model_selection.json",
    "universe_quality": "universe_quality.json",
    "model_tournament": "model_tournament.json",
}


def build_model_quality_packet(
    *,
    trade_date: str,
    repo_root: Path | str = Path("."),
    output_root: Path | str | None = None,
    write: bool = True,
) -> dict[str, Any]:
    target = normalize_date(trade_date)
    repo = Path(repo_root)
    out_dir = model_quality_dir(repo, target, output_root)
    sections: dict[str, Any] = {}
    missing: list[str] = []
    for name, filename in SECTION_FILES.items():
        payload = read_json(out_dir / filename)
        if payload is None:
            sections[name] = {"available": False, "reason_codes": [f"{name.upper()}_MISSING"]}
            missing.append(name)
        else:
            sections[name] = payload
    cassiopeia = sections.get("cassiopeia_model_selection") or {}
    tournament = sections.get("model_tournament") or {}
    attribution = sections.get("attribution_quality") or {}
    universe = sections.get("universe_quality") or {}
    phoenix = sections.get("phoenix_research") or {}
    reason_codes = []
    if missing:
        reason_codes.extend(f"MISSING_SECTION:{name}" for name in missing)
    if not (cassiopeia or {}).get("decision_grade_recommendation"):
        reason_codes.append("NO_DECISION_GRADE_STRATEGY_CHANGE")
    payload = {
        "schema_version": SCHEMA_VERSION,
        "date": target,
        "governance_label": "RESEARCH_ONLY",
        "execution_impact": "NON_EXECUTIONAL",
        "available": not missing,
        "status": "OK" if not reason_codes else "PARTIAL",
        "executive_summary": {
            "alpha_quality_status": "RESEARCH_IMPROVEMENT_PACKET_BUILT" if not missing else "INCOMPLETE_PACKET",
            "attribution_status": attribution.get("status"),
            "best_current_research_insight": _best_insight(cassiopeia=cassiopeia, phoenix=phoenix, universe=universe, tournament=tournament),
            "strategy_change_decision_grade": bool((cassiopeia or {}).get("decision_grade_recommendation")) and bool((tournament or {}).get("decision_grade_leader")),
            "next_recommended_research_action": _next_action(cassiopeia=cassiopeia, universe=universe, tournament=tournament),
        },
        "sections": sections,
        "reason_codes": sorted(set(reason_codes)) or ["ok"],
    }
    if write:
        write_json(out_dir / "model_quality_packet.json", payload)
        write_text(out_dir / "model_quality_packet.md", render_markdown(payload))
    return payload


def _best_insight(*, cassiopeia: dict[str, Any], phoenix: dict[str, Any], universe: dict[str, Any], tournament: dict[str, Any]) -> str:
    if tournament.get("current_leader"):
        return f"Leaderboard leader is {tournament.get('current_leader')}, but decision-grade leader is {tournament.get('decision_grade_leader') or 'none'}."
    if phoenix.get("active"):
        return "Phoenix has active crisis-reversal candidates, pending history."
    if universe.get("status") == "PARTIAL":
        return "Universe/data quality remains a gating constraint."
    if cassiopeia.get("recommended_strategy"):
        return f"Cassiopeia recommends {cassiopeia.get('recommended_strategy')}."
    return "No single model-quality insight is decision-grade yet."


def _next_action(*, cassiopeia: dict[str, Any], universe: dict[str, Any], tournament: dict[str, Any]) -> str:
    if "SECURITY_MASTER_MISSING" in (universe.get("reason_codes") or []):
        return "Refresh security master and rerun universe quality plus tournament."
    if not cassiopeia.get("decision_grade_recommendation"):
        return "Accumulate cleaner promotion-governance evidence before any strategy change."
    if not tournament.get("decision_grade_leader"):
        return "Resolve tournament decision-grade blockers."
    return "Review decision-grade recommendation with governance checklist."


def render_markdown(payload: dict[str, Any]) -> str:
    summary = payload.get("executive_summary") or {}
    lines = [
        f"# Model Quality Packet - {payload.get('date')}",
        "",
        f"- Status: {payload.get('status')}",
        f"- Alpha quality status: {summary.get('alpha_quality_status')}",
        f"- Attribution status: {summary.get('attribution_status')}",
        f"- Strategy change decision-grade: {summary.get('strategy_change_decision_grade')}",
        f"- Best insight: {summary.get('best_current_research_insight')}",
        f"- Next action: {summary.get('next_recommended_research_action')}",
        f"- Reason codes: {md_join(payload.get('reason_codes') or [])}",
        "",
        "## Sections",
        "",
        "| Section | Available | Status | Reasons |",
        "|---|:---:|---:|---|",
    ]
    for name, section in sorted((payload.get("sections") or {}).items()):
        lines.append(f"| {name} | {section.get('available', True)} | {section.get('status')} | {md_join(section.get('reason_codes') or [])} |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build aggregate research-only model quality packet.")
    parser.add_argument("--date", required=True)
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--output-root", default=None)
    args = parser.parse_args(argv)
    payload = build_model_quality_packet(
        trade_date=args.date,
        repo_root=Path(args.repo_root),
        output_root=Path(args.output_root) if args.output_root else None,
    )
    print(json.dumps({"date": payload["date"], "status": payload["status"], "reason_codes": payload["reason_codes"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
