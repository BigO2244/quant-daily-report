"""Build the FR-030 daily research interpretation packet.

The packet is an additive, advisory consumption layer over existing shadow and
research clarity artifacts. It does not execute trades, alter accounting or
timing semantics, mutate source artifacts, gate workflows, or send email.
"""

from __future__ import annotations

import argparse
import html
import json
from datetime import date
from pathlib import Path
from typing import Any


LOW_CONFIDENCE_REASON = "FR-028 timing semantics remain unresolved for operational shadow NAV."
STRATEGY_ORDER = ("caerus_polaris", "caerus_orion", "caerus_lyra")


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return data


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    _write_text(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _pct(value: Any) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value) * 100:.2f}%"
    except (TypeError, ValueError):
        return "n/a"


def _num(value: Any, digits: int = 2) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return "n/a"


def _strategy_name(strategy_id: str, payloads: dict[str, dict[str, Any]]) -> str:
    payload = payloads.get(strategy_id, {})
    return str(payload.get("strategy_name") or strategy_id.replace("_", " ").title())


def _artifact_state(path: Path, trade_date: str) -> dict[str, Any]:
    payload = _read_json(path)
    if not payload:
        return {
            "artifact": path.name,
            "path": str(path),
            "status": "MISSING",
            "freshness_confidence": "LOW",
            "trade_date": None,
        }
    artifact_trade_date = payload.get("trade_date")
    status = "FRESH" if artifact_trade_date == trade_date else "UNKNOWN"
    confidence = "HIGH" if status == "FRESH" else "UNKNOWN"
    return {
        "artifact": path.name,
        "path": str(path),
        "status": status,
        "freshness_confidence": confidence,
        "trade_date": artifact_trade_date,
    }


def _load_inputs(repo_root: Path, trade_date: str, shadow_dir: Path | None, clarity_dir: Path | None) -> dict[str, Any]:
    actual_shadow_dir = shadow_dir or repo_root / "outputs" / "shadow_candidates" / trade_date
    actual_clarity_dir = clarity_dir or repo_root / "outputs" / "research_clarity" / trade_date
    artifacts = {
        "nav_surface_registry": _read_json(actual_clarity_dir / "nav_surface_registry.json"),
        "surface_metadata": _read_json(actual_clarity_dir / "surface_metadata.json"),
        "holdings_snapshot": _read_json(actual_clarity_dir / "holdings_snapshot.json"),
        "weights_snapshot": _read_json(actual_clarity_dir / "weights_snapshot.json"),
        "exposures_snapshot": _read_json(actual_clarity_dir / "exposures_snapshot.json"),
        "rebalance_delta": _read_json(actual_clarity_dir / "rebalance_delta.json"),
        "exposure_summary": _read_json(actual_clarity_dir / "exposure_summary.json"),
        "factor_risk_flags": _read_json(actual_clarity_dir / "factor_risk_flags.json"),
        "concentration_monitor": _read_json(actual_clarity_dir / "concentration_monitor.json"),
        "regime_performance_breakdown": _read_json(actual_clarity_dir / "regime_performance_breakdown.json"),
        "regime_fragility_report": _read_json(actual_clarity_dir / "regime_fragility_report.json"),
        "regime_exposure_matrix": _read_json(actual_clarity_dir / "regime_exposure_matrix.json"),
        "attribution_by_regime": _read_json(actual_clarity_dir / "attribution_by_regime.json"),
        "manifest": _read_json(actual_clarity_dir / "manifest.json"),
        "shadow_performance": _read_json(actual_shadow_dir / "shadow_performance.json"),
        "comparison": _read_json(actual_shadow_dir / "comparison.json"),
    }
    freshness = [
        _artifact_state(actual_clarity_dir / name, trade_date)
        for name in (
            "nav_surface_registry.json",
            "holdings_snapshot.json",
            "exposure_summary.json",
            "factor_risk_flags.json",
            "regime_fragility_report.json",
            "manifest.json",
        )
    ]
    freshness.extend(
        [
            _artifact_state(actual_shadow_dir / "shadow_performance.json", trade_date),
            _artifact_state(actual_shadow_dir / "comparison.json", trade_date),
        ]
    )
    return {
        "shadow_dir": actual_shadow_dir,
        "clarity_dir": actual_clarity_dir,
        "artifacts": artifacts,
        "freshness": freshness,
    }


def _operational_trust(inputs: dict[str, Any]) -> dict[str, Any]:
    artifacts = inputs["artifacts"]
    registry = artifacts["nav_surface_registry"]
    surface_metadata = artifacts["surface_metadata"]
    missing = [item["artifact"] for item in inputs["freshness"] if item["status"] == "MISSING"]
    stale_or_unknown = [item["artifact"] for item in inputs["freshness"] if item["status"] == "UNKNOWN"]
    shadow_confidences = []
    for payload in surface_metadata.get("strategies", {}).values():
        if isinstance(payload, dict):
            confidence = payload.get("confidence_classification")
            if confidence:
                shadow_confidences.append(confidence)
    status = "PARTIAL" if missing else "FRESH" if not stale_or_unknown else "UNKNOWN"
    return {
        "status": status,
        "research_only": True,
        "advisory_only": True,
        "source_surface_count": len(registry.get("surfaces", {})),
        "missing_artifacts": missing,
        "stale_or_unknown_artifacts": stale_or_unknown,
        "shadow_confidence_floor": "LOW" if "LOW" in shadow_confidences else "UNKNOWN",
        "interpretation": "Packet is suitable for operator review, not execution or promotion decisions.",
    }


def _strategy_comparison(inputs: dict[str, Any]) -> list[dict[str, Any]]:
    artifacts = inputs["artifacts"]
    shadow_performance = artifacts["shadow_performance"].get("strategies", {})
    exposures = artifacts["exposures_snapshot"].get("strategies", {})
    rows = []
    for strategy_id in STRATEGY_ORDER:
        perf = shadow_performance.get(strategy_id, {}) if isinstance(shadow_performance, dict) else {}
        exposure = exposures.get(strategy_id, {}) if isinstance(exposures, dict) else {}
        rows.append(
            {
                "strategy_id": strategy_id,
                "strategy_name": _strategy_name(strategy_id, exposures),
                "daily_return": perf.get("daily_return"),
                "nav": perf.get("nav"),
                "weights_count": perf.get("weights_count"),
                "top3_concentration": exposure.get("top3_concentration"),
                "max_position_weight": exposure.get("max_position_weight"),
                "max_sector_exposure": exposure.get("max_sector_exposure"),
                "turnover_proxy": exposure.get("turnover_proxy"),
                "confidence_classification": exposure.get("confidence_classification", "LOW"),
            }
        )
    rows.sort(key=lambda row: (row["daily_return"] is None, -(row["daily_return"] or 0.0), row["strategy_id"]))
    for index, row in enumerate(rows, start=1):
        row["daily_rank"] = index
    return rows


def _exposure_review(inputs: dict[str, Any]) -> dict[str, Any]:
    concentration = inputs["artifacts"]["concentration_monitor"].get("strategies", [])
    flags = inputs["artifacts"]["factor_risk_flags"].get("flags", [])
    high_concentration = [
        row for row in concentration
        if isinstance(row, dict) and row.get("concentration_score") == "HIGH"
    ]
    return {
        "concentration_rows": concentration,
        "risk_flags": flags,
        "high_concentration_strategy_count": len(high_concentration),
        "interpretation": "High concentration means outperformance may be amplified by position or sector exposure.",
    }


def _regime_review(inputs: dict[str, Any]) -> dict[str, Any]:
    breakdown = inputs["artifacts"]["regime_performance_breakdown"]
    fragility = inputs["artifacts"]["regime_fragility_report"]
    matrix = inputs["artifacts"]["regime_exposure_matrix"]
    return {
        "regime": breakdown.get("regime", {}),
        "performance_by_strategy": breakdown.get("strategies", {}),
        "fragility_indicators": fragility.get("fragility_indicators", []),
        "exposure_matrix": matrix.get("strategies", {}),
        "confidence_classification": breakdown.get("confidence_classification", "LOW"),
        "interpretation": "Regime interpretation is advisory and inherits source artifact confidence.",
    }


def _what_changed(inputs: dict[str, Any]) -> list[str]:
    delta = inputs["artifacts"]["rebalance_delta"]
    delta_basis = delta.get("delta_basis", "UNKNOWN")
    if delta_basis == "NO_PRIOR":
        return ["No prior immutable snapshot was available; today's bundle establishes a baseline."]
    if delta_basis == "OK":
        return ["Shadow source delta status is OK; review turnover proxies for composition changes."]
    return [f"Rebalance delta status is {delta_basis}; review source artifacts before drawing change conclusions."]


def _key_risks(trust: dict[str, Any], exposure: dict[str, Any], regime: dict[str, Any]) -> list[str]:
    risks = [LOW_CONFIDENCE_REASON]
    if trust["missing_artifacts"]:
        risks.append("One or more expected packet inputs are missing; packet confidence is partial.")
    if exposure["risk_flags"]:
        risks.append("Exposure risk flags are present; inspect concentration and factor sensitivity before interpreting returns.")
    if regime["fragility_indicators"]:
        risks.append("Regime fragility indicators are present; challenger outperformance may be regime-dependent.")
    return risks


def _research_followups(exposure: dict[str, Any], regime: dict[str, Any]) -> list[str]:
    followups = [
        "Continue preserving operational shadow NAV as LOW confidence until FR-028 is governed.",
        "Compare repeated packets over multiple dates before treating drift as persistent.",
    ]
    if exposure["high_concentration_strategy_count"]:
        followups.append("Review whether Orion/Lyra outperformance is concentration-amplified rather than selection-only.")
    if regime["fragility_indicators"]:
        followups.append("Track whether fragility indicators persist across regime transitions.")
    return followups


def _packet_payload(repo_root: Path, trade_date: str, shadow_dir: Path | None, clarity_dir: Path | None) -> dict[str, Any]:
    inputs = _load_inputs(repo_root, trade_date, shadow_dir, clarity_dir)
    trust = _operational_trust(inputs)
    comparison = _strategy_comparison(inputs)
    exposure = _exposure_review(inputs)
    regime = _regime_review(inputs)
    key_risks = _key_risks(trust, exposure, regime)
    followups = _research_followups(exposure, regime)
    executive_summary = [
        f"Packet status: {trust['status']}; advisory research-only interpretation.",
        f"Strategy rank leader: {comparison[0]['strategy_name'] if comparison else 'n/a'} based on available daily shadow return.",
        f"Exposure flags: {len(exposure['risk_flags'])}; regime fragility indicators: {len(regime['fragility_indicators'])}.",
        "Operational shadow NAV confidence remains LOW pending FR-028.",
    ]
    return {
        "schema_version": "daily_research_packet_v1",
        "trade_date": trade_date,
        "packet_scope": "FR_030_RESEARCH_ONLY",
        "advisory_only": True,
        "execution_behavior_changed": False,
        "accounting_semantics_changed": False,
        "timing_semantics_changed": False,
        "promotion_logic_changed": False,
        "source_paths": {
            "shadow_dir": str(inputs["shadow_dir"]),
            "research_clarity_dir": str(inputs["clarity_dir"]),
        },
        "executive_summary": executive_summary,
        "operational_trust_summary": trust,
        "strategy_comparison": comparison,
        "exposure_concentration_review": exposure,
        "regime_interpretation": regime,
        "confidence_freshness_caveats": {
            "freshness": inputs["freshness"],
            "confidence_floor": "LOW",
            "caveats": [
                LOW_CONFIDENCE_REASON,
                "Latest/convenience publications are not canonical evidence without dated source verification.",
                "Packet does not assert promotion readiness or timing-corrected performance.",
            ],
        },
        "what_changed_today": _what_changed(inputs),
        "key_risks": key_risks,
        "research_followups": followups,
        "delivery_preparation": {
            "email_ready_html": "packet.html",
            "dashboard_summary": "summary.json",
            "mcp_readiness": "read_only_artifact_retrieval_ready",
        },
    }


def _markdown(packet: dict[str, Any]) -> str:
    lines = [
        f"# Daily Research Packet - {packet['trade_date']}",
        "",
        "## Executive Summary",
        "",
    ]
    lines.extend(f"- {item}" for item in packet["executive_summary"])
    lines.extend(["", "## Operational Trust Summary", ""])
    trust = packet["operational_trust_summary"]
    lines.extend(
        [
            f"- Status: `{trust['status']}`",
            f"- Shadow confidence floor: `{trust['shadow_confidence_floor']}`",
            f"- Missing inputs: {', '.join(trust['missing_artifacts']) if trust['missing_artifacts'] else 'none'}",
            f"- Interpretation: {trust['interpretation']}",
        ]
    )
    lines.extend(["", "## Strategy Comparison Summary", ""])
    for row in packet["strategy_comparison"]:
        lines.append(
            f"- #{row['daily_rank']} `{row['strategy_id']}`: return {_pct(row['daily_return'])}, "
            f"NAV {_num(row['nav'], 4)}, top-3 {_pct(row['top3_concentration'])}, "
            f"max sector {_pct(row['max_sector_exposure'])}."
        )
    lines.extend(["", "## Exposure + Concentration Review", ""])
    exposure = packet["exposure_concentration_review"]
    if exposure["risk_flags"]:
        for flag in exposure["risk_flags"]:
            lines.append(f"- `{flag['strategy_id']}`: `{flag['flag']}` severity `{flag['severity']}`.")
    else:
        lines.append("- No exposure risk flags fired.")
    lines.extend(["", "## Regime Interpretation", ""])
    regime = packet["regime_interpretation"]
    lines.append(f"- Regime evidence: `{json.dumps(regime['regime'], sort_keys=True)}`")
    lines.append(f"- Confidence: `{regime['confidence_classification']}`")
    lines.extend(["", "## Fragility Observations", ""])
    if regime["fragility_indicators"]:
        for item in regime["fragility_indicators"]:
            lines.append(f"- `{item['strategy_id']}`: `{item['indicator']}` severity `{item['severity']}`.")
    else:
        lines.append("- No regime fragility indicators fired.")
    lines.extend(["", "## Confidence + Freshness Caveats", ""])
    for caveat in packet["confidence_freshness_caveats"]["caveats"]:
        lines.append(f"- {caveat}")
    lines.extend(["", "## What Changed Today", ""])
    lines.extend(f"- {item}" for item in packet["what_changed_today"])
    lines.extend(["", "## Key Risks", ""])
    lines.extend(f"- {item}" for item in packet["key_risks"])
    lines.extend(["", "## Research Follow-Ups", ""])
    lines.extend(f"- {item}" for item in packet["research_followups"])
    lines.append("")
    return "\n".join(lines)


def _html(packet: dict[str, Any], markdown: str) -> str:
    body = []
    for line in markdown.splitlines():
        escaped = html.escape(line)
        if line.startswith("# "):
            body.append(f"<h1>{html.escape(line[2:])}</h1>")
        elif line.startswith("## "):
            body.append(f"<h2>{html.escape(line[3:])}</h2>")
        elif line.startswith("- "):
            body.append(f"<li>{html.escape(line[2:])}</li>")
        elif not line.strip():
            body.append("")
        else:
            body.append(f"<p>{escaped}</p>")
    return "\n".join(
        [
            "<!doctype html>",
            "<html>",
            "<head><meta charset=\"utf-8\"><title>Daily Research Packet</title></head>",
            "<body>",
            *body,
            "</body>",
            "</html>",
            "",
        ]
    )


def _summary(packet: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "daily_research_packet_summary_v1",
        "trade_date": packet["trade_date"],
        "status": packet["operational_trust_summary"]["status"],
        "confidence_floor": packet["confidence_freshness_caveats"]["confidence_floor"],
        "leader": packet["strategy_comparison"][0] if packet["strategy_comparison"] else None,
        "risk_flag_count": len(packet["exposure_concentration_review"]["risk_flags"]),
        "fragility_indicator_count": len(packet["regime_interpretation"]["fragility_indicators"]),
        "advisory_only": True,
        "execution_behavior_changed": False,
    }


def build_daily_research_packet(
    repo_root: Path,
    trade_date: str | None = None,
    shadow_dir: Path | None = None,
    clarity_dir: Path | None = None,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    actual_trade_date = trade_date or date.today().isoformat()
    actual_output_dir = output_dir or repo_root / "outputs" / "research_packets" / actual_trade_date
    packet = _packet_payload(repo_root, actual_trade_date, shadow_dir, clarity_dir)
    markdown = _markdown(packet)
    html_output = _html(packet, markdown)
    summary = _summary(packet)

    _write_json(actual_output_dir / "packet.json", packet)
    _write_text(actual_output_dir / "packet.md", markdown)
    _write_text(actual_output_dir / "packet.html", html_output)
    _write_json(actual_output_dir / "summary.json", summary)

    return {
        "trade_date": actual_trade_date,
        "output_dir": str(actual_output_dir),
        "artifacts": ["packet.html", "packet.json", "packet.md", "summary.json"],
        "status": summary["status"],
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build FR-030 daily research packet.")
    parser.add_argument("--trade-date", default=date.today().isoformat())
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--shadow-dir", type=Path, default=None)
    parser.add_argument("--clarity-dir", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=None)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    result = build_daily_research_packet(
        repo_root=args.repo_root,
        trade_date=args.trade_date,
        shadow_dir=args.shadow_dir,
        clarity_dir=args.clarity_dir,
        output_dir=args.output_dir,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
