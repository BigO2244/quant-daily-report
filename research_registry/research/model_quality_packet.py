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
    "argo_regime_selection": "argo_regime_selection.json",
    "universe_quality": "universe_quality.json",
    "model_tournament": "model_tournament.json",
}
LEGACY_SECTION_FILES = {
    "argo_regime_selection": "cassiopeia_model_selection.json",
}
OPTIONAL_SECTION_FILES = {
    "portfolio_history_freshness": "portfolio_history_freshness.json",
    "lyra_orion_differentiation": "lyra_orion_differentiation.json",
    "phoenix_evidence_tracker": "phoenix_evidence_tracker.json",
    "phoenix_phase_b_review": "phoenix_phase_b_review.json",
    "strategy_differentiation_deep_dive": "strategy_differentiation_deep_dive.json",
    "argo_phase_b_validation": "argo_phase_b_validation.json",
    "multi_asset_research_framework": "multi_asset_research_framework.json",
}
OPTIONAL_EXTERNAL_SECTION_FILES = {
    "security_master_diagnostics": Path("outputs/research/security_master_diagnostics") / "{date}" / "security_master_diagnostics.json",
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
        if payload is None and name in LEGACY_SECTION_FILES:
            legacy_payload = read_json(out_dir / LEGACY_SECTION_FILES[name])
            if legacy_payload is not None:
                existing = [code for code in (legacy_payload.get("reason_codes") or []) if code != "ok"]
                legacy_payload["reason_codes"] = sorted(set(existing + ["LEGACY_ARTIFACT_NAME"]))
                payload = legacy_payload
        if payload is None:
            sections[name] = {"available": False, "reason_codes": [f"{name.upper()}_MISSING"]}
            missing.append(name)
        else:
            sections[name] = payload
    for name, filename in OPTIONAL_SECTION_FILES.items():
        payload = read_json(out_dir / filename)
        if payload is None:
            sections[name] = {"available": False, "optional": True, "reason_codes": [f"{name.upper()}_MISSING"]}
        else:
            sections[name] = payload
    for name, rel_template in OPTIONAL_EXTERNAL_SECTION_FILES.items():
        rel = Path(str(rel_template).format(date=target))
        payload = read_json(repo / rel)
        if payload is None:
            sections[name] = {"available": False, "optional": True, "reason_codes": [f"{name.upper()}_MISSING"]}
        else:
            sections[name] = payload
    sections["dashboard_decision_grade"] = _dashboard_decision_grade(repo)
    argo = sections.get("argo_regime_selection") or {}
    tournament = sections.get("model_tournament") or {}
    attribution = sections.get("attribution_quality") or {}
    universe = sections.get("universe_quality") or {}
    phoenix = sections.get("phoenix_research") or {}
    portfolio = sections.get("portfolio_history_freshness") or {}
    security = sections.get("security_master_diagnostics") or {}
    differentiation = sections.get("lyra_orion_differentiation") or {}
    phoenix_evidence = sections.get("phoenix_evidence_tracker") or {}
    phoenix_phase_b = sections.get("phoenix_phase_b_review") or {}
    strategy_deep_dive = sections.get("strategy_differentiation_deep_dive") or {}
    argo_phase_b = sections.get("argo_phase_b_validation") or {}
    multi_asset = sections.get("multi_asset_research_framework") or {}
    dashboard_decision_grade = sections.get("dashboard_decision_grade") or {}
    reason_codes = []
    if missing:
        reason_codes.extend(f"MISSING_SECTION:{name}" for name in missing)
    if not (argo or {}).get("decision_grade_recommendation"):
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
            "portfolio_history_freshness": portfolio.get("freshness_status"),
            "security_master_status": (security.get("security_master_artifact") or {}).get("status"),
            "lyra_orion_decision_grade": bool(differentiation.get("decision_grade_flag")),
            "phoenix_evidence_confidence": phoenix_evidence.get("confidence"),
            "phoenix_phase_b_confidence": phoenix_phase_b.get("confidence"),
            "strategy_differentiation_watchlist_count": len(strategy_deep_dive.get("retirement_watchlist") or []),
            "argo_phase_b_decision_grade": bool(argo_phase_b.get("decision_grade_recommendation")),
            "multi_asset_framework_status": multi_asset.get("status"),
            "dashboard_decision_grade_status": dashboard_decision_grade.get("status"),
            "cygnus_design_status": "DESIGN_ONLY_DOCS",
            "best_current_research_insight": _best_insight(
                argo=argo,
                phoenix=phoenix,
                universe=universe,
                tournament=tournament,
                differentiation=differentiation,
            ),
            "strategy_change_decision_grade": bool((argo or {}).get("decision_grade_recommendation")) and bool((tournament or {}).get("decision_grade_leader")),
            "next_recommended_research_action": _next_action(argo=argo, universe=universe, tournament=tournament, portfolio=portfolio, security=security),
        },
        "sections": sections,
        "reason_codes": sorted(set(reason_codes)) or ["ok"],
    }
    if write:
        write_json(out_dir / "model_quality_packet.json", payload)
        write_text(out_dir / "model_quality_packet.md", render_markdown(payload))
    return payload


def _dashboard_decision_grade(repo: Path) -> dict[str, Any]:
    path = repo / "web" / "dashboard" / "dashboard_data.json"
    payload = read_json(path)
    section = ((payload or {}).get("sections") or {}).get("decision_grade") if isinstance(payload, dict) else None
    if not isinstance(section, dict):
        return {"available": False, "optional": True, "source_path": str(path), "reason_codes": ["DASHBOARD_DECISION_GRADE_MISSING"]}
    out = dict(section)
    out.setdefault("available", True)
    out["optional"] = True
    out["source_path"] = str(path)
    out.setdefault("reason_codes", ["ok"])
    return out


def _best_insight(
    *,
    argo: dict[str, Any],
    phoenix: dict[str, Any],
    universe: dict[str, Any],
    tournament: dict[str, Any],
    differentiation: dict[str, Any] | None = None,
) -> str:
    diff_summary = (differentiation or {}).get("executive_summary") or {}
    if diff_summary.get("lyra_only_symbols") or diff_summary.get("orion_only_symbols"):
        return (
            "Lyra and Orion share the same momentum core; current mechanical difference is replacement-name "
            f"selection ({md_join(diff_summary.get('lyra_only_symbols') or [])} vs {md_join(diff_summary.get('orion_only_symbols') or [])})."
        )
    if tournament.get("current_leader"):
        return f"Leaderboard leader is {tournament.get('current_leader')}, but decision-grade leader is {tournament.get('decision_grade_leader') or 'none'}."
    if phoenix.get("active"):
        return "Phoenix has active crisis-reversal candidates, pending history."
    if universe.get("status") == "PARTIAL":
        return "Universe/data quality remains a gating constraint."
    if argo.get("recommended_strategy"):
        return f"Argo recommends {argo.get('recommended_strategy')}."
    return "No single model-quality insight is decision-grade yet."


def _next_action(
    *,
    argo: dict[str, Any],
    universe: dict[str, Any],
    tournament: dict[str, Any],
    portfolio: dict[str, Any] | None = None,
    security: dict[str, Any] | None = None,
) -> str:
    if (portfolio or {}).get("freshness_status") == "STALE":
        return "Refresh canonical portfolio history or document broker history unavailability before promotion-readiness use."
    refresh = (security or {}).get("refresh_diagnostic") or {}
    if refresh.get("auth_status") in {"MISSING_CREDENTIALS", "UNAUTHORIZED", "NETWORK_UNAVAILABLE"}:
        return "Resolve security-master refresh/auth diagnostics before universe expansion or alias migration."
    if "SECURITY_MASTER_MISSING" in (universe.get("reason_codes") or []):
        return "Refresh security master and rerun universe quality plus tournament."
    if not argo.get("decision_grade_recommendation"):
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
