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
EXPOSURE_SOURCE_ARTIFACTS = (
    "exposures_snapshot.json",
    "exposure_summary.json",
    "concentration_monitor.json",
    "regime_exposure_matrix.json",
)


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
        return "unavailable"
    try:
        return f"{float(value) * 100:.2f}%"
    except (TypeError, ValueError):
        return "unavailable"


def _num(value: Any, digits: int = 2) -> str:
    if value is None:
        return "unavailable"
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return "unavailable"


def _first_present(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def _by_strategy(rows: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(rows, list):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        strategy_id = row.get("strategy_id")
        if strategy_id:
            result[str(strategy_id)] = row
    return result


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
    source_diagnostics = {
        name: {
            "artifact": name,
            "status": "FOUND" if _artifact_has_payload(artifacts[_artifact_key(name)]) else "MISSING",
        }
        for name in EXPOSURE_SOURCE_ARTIFACTS
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
        "source_diagnostics": source_diagnostics,
    }


def _artifact_key(artifact_name: str) -> str:
    return artifact_name.removesuffix(".json")


def _artifact_has_payload(payload: Any) -> bool:
    return isinstance(payload, dict) and bool(payload)


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
    surfaces = registry.get("surfaces", {}) if isinstance(registry.get("surfaces"), dict) else {}
    if not shadow_confidences and "OPERATIONAL_SHADOW_NAV" in surfaces:
        shadow_confidences.append(str(surfaces["OPERATIONAL_SHADOW_NAV"].get("confidence", "LOW")))
    confidence_floor = "LOW" if "LOW" in shadow_confidences or any("LOW" in value for value in shadow_confidences) else "LOW"
    status = "PARTIAL" if missing else "FRESH" if not stale_or_unknown else "PARTIAL"
    return {
        "status": status,
        "research_only": True,
        "advisory_only": True,
        "source_surface_count": len(registry.get("surfaces", {})),
        "missing_artifacts": missing,
        "stale_or_unknown_artifacts": stale_or_unknown,
        "shadow_confidence_floor": confidence_floor,
        "confidence_reason": LOW_CONFIDENCE_REASON,
        "interpretation": "Packet is suitable for operator review, not execution or promotion decisions.",
    }


def _strategy_comparison(inputs: dict[str, Any]) -> list[dict[str, Any]]:
    artifacts = inputs["artifacts"]
    shadow_performance = artifacts["shadow_performance"].get("strategies", {})
    exposures = artifacts["exposures_snapshot"].get("strategies", {})
    exposure_summary = artifacts["exposure_summary"].get("strategies", {})
    concentration = _by_strategy(artifacts["concentration_monitor"].get("strategies", []))
    regime_matrix = artifacts["regime_exposure_matrix"].get("strategies", {})
    source_diagnostics = inputs["source_diagnostics"]
    flags_by_strategy: dict[str, list[dict[str, Any]]] = {strategy_id: [] for strategy_id in STRATEGY_ORDER}
    for flag in artifacts["factor_risk_flags"].get("flags", []):
        if isinstance(flag, dict) and flag.get("strategy_id") in flags_by_strategy:
            flags_by_strategy[str(flag["strategy_id"])].append(flag)
    rows = []
    for strategy_id in STRATEGY_ORDER:
        perf = shadow_performance.get(strategy_id, {}) if isinstance(shadow_performance, dict) else {}
        exposure = exposures.get(strategy_id, {}) if isinstance(exposures, dict) else {}
        exposure_from_summary = exposure_summary.get(strategy_id, {}) if isinstance(exposure_summary, dict) else {}
        concentration_row = concentration.get(strategy_id, {})
        regime_row = regime_matrix.get(strategy_id, {}) if isinstance(regime_matrix, dict) else {}
        top3 = _first_present(
            exposure.get("top3_concentration"),
            exposure_from_summary.get("top3_concentration"),
            concentration_row.get("top3_concentration"),
            regime_row.get("top3_concentration"),
        )
        max_position = _first_present(
            exposure.get("max_position_weight"),
            exposure_from_summary.get("max_position_weight"),
            concentration_row.get("max_position_weight"),
            regime_row.get("max_position_weight"),
        )
        max_sector = _first_present(
            exposure.get("max_sector_exposure"),
            exposure_from_summary.get("max_sector_exposure"),
            concentration_row.get("max_sector_exposure"),
            regime_row.get("max_sector_exposure"),
        )
        missing_sources = []
        if top3 is None:
            missing_sources.append("top3_concentration from exposures_snapshot/concentration_monitor")
        if max_position is None:
            missing_sources.append("max_position_weight from exposures_snapshot/concentration_monitor")
        if max_sector is None:
            missing_sources.append("max_sector_exposure from exposures_snapshot/concentration_monitor/regime_exposure_matrix")
        risk_flags = flags_by_strategy.get(strategy_id, [])
        main_risk = _main_risk(strategy_id, top3, max_position, max_sector, risk_flags, missing_sources)
        per_strategy_sources = {
            name: payload["status"]
            for name, payload in source_diagnostics.items()
        }
        rows.append(
            {
                "strategy_id": strategy_id,
                "strategy_name": _strategy_name(strategy_id, exposures),
                "daily_return": perf.get("daily_return"),
                "nav": perf.get("nav"),
                "weights_count": perf.get("weights_count"),
                "top3_concentration": top3,
                "max_position_weight": max_position,
                "max_sector_exposure": max_sector,
                "turnover_proxy": _first_present(exposure.get("turnover_proxy"), exposure_from_summary.get("turnover_proxy")),
                "risk_flags": risk_flags,
                "main_risk_caveat": main_risk,
                "missing_exposure_sources": missing_sources,
                "source_diagnostics": per_strategy_sources,
                "operator_interpretation": _strategy_interpretation(strategy_id, perf.get("daily_return"), top3, max_position, max_sector, risk_flags, missing_sources),
                "confidence_classification": exposure.get("confidence_classification", "LOW"),
            }
        )
    ranking_basis = _ranking_basis(rows)
    if ranking_basis == "cumulative_nav":
        rows.sort(key=lambda row: (row["nav"] is None, -(row["nav"] or 0.0), row["strategy_id"]))
    else:
        rows.sort(key=lambda row: (row["daily_return"] is None, -(row["daily_return"] or 0.0), row["strategy_id"]))
    for index, row in enumerate(rows, start=1):
        row["daily_rank"] = index
        row["ranking_basis"] = ranking_basis
    return rows


def _ranking_basis(rows: list[dict[str, Any]]) -> str:
    daily_returns = [row.get("daily_return") for row in rows if row.get("daily_return") is not None]
    if daily_returns and any(abs(float(value)) > 1e-12 for value in daily_returns):
        return "daily_return"
    if any(row.get("nav") is not None for row in rows):
        return "cumulative_nav"
    return "fallback_ordering"


def _main_risk(
    strategy_id: str,
    top3: Any,
    max_position: Any,
    max_sector: Any,
    risk_flags: list[dict[str, Any]],
    missing_sources: list[str],
) -> str:
    if missing_sources:
        return f"Exposure data incomplete: missing {', '.join(missing_sources)}."
    flag_names = [str(flag.get("flag")) for flag in risk_flags if flag.get("flag")]
    if "POSITION_CONCENTRATION" in flag_names:
        return "High position concentration may amplify outperformance and drawdown."
    if "SECTOR_CONCENTRATION" in flag_names:
        return "Sector concentration may dominate selection effects."
    if "MOMENTUM_FACTOR_SENSITIVITY" in flag_names:
        return "Momentum sensitivity may explain part of the move."
    if max_sector is not None and float(max_sector) >= 0.5:
        return "Sector exposure is elevated."
    if top3 is not None and float(top3) >= 0.6:
        return "Top-three concentration is elevated."
    return "No threshold concentration flag fired; continue normal evidence review."


def _strategy_interpretation(
    strategy_id: str,
    daily_return: Any,
    top3: Any,
    max_position: Any,
    max_sector: Any,
    risk_flags: list[dict[str, Any]],
    missing_sources: list[str],
) -> str:
    name = strategy_id.replace("caerus_", "").title()
    if missing_sources:
        return (
            f"{name} has incomplete exposure evidence today. Performance ranking is available, "
            "but exposure-adjusted interpretation is not yet available. Do not compare strategy "
            "quality until exposure data is present."
        )
    concentrated = bool(risk_flags) or any(
        value is not None and float(value) >= threshold
        for value, threshold in ((top3, 0.6), (max_position, 0.2), (max_sector, 0.5))
    )
    if concentrated:
        return f"{name} performance may be concentration- or exposure-amplified; review risk flags before treating it as durable edge."
    if daily_return is not None and float(daily_return) > 0:
        return f"{name} was positive today without a high concentration flag in the available evidence."
    return f"{name} requires normal follow-up; current packet evidence does not support a stronger conclusion."


def _exposure_review(inputs: dict[str, Any], data_completeness: dict[str, Any]) -> dict[str, Any]:
    concentration = inputs["artifacts"]["concentration_monitor"].get("strategies", [])
    flags = inputs["artifacts"]["factor_risk_flags"].get("flags", [])
    high_concentration = [
        row for row in concentration
        if isinstance(row, dict) and row.get("concentration_score") == "HIGH"
    ]
    assessable = data_completeness["exposure_data_status"] == "complete"
    return {
        "concentration_rows": concentration,
        "risk_flags": flags,
        "risk_assessable": assessable,
        "high_concentration_strategy_count": len(high_concentration),
        "interpretation": (
            "High concentration means outperformance may be amplified by position or sector exposure."
            if assessable
            else "Exposure risk not assessable because required exposure artifacts are incomplete."
        ),
    }


def _data_completeness(inputs: dict[str, Any], comparison: list[dict[str, Any]]) -> dict[str, Any]:
    missing_artifacts = [
        name for name, payload in inputs["source_diagnostics"].items()
        if payload["status"] != "FOUND"
    ]
    strategies_missing_fields = {
        row["strategy_id"]: row["missing_exposure_sources"]
        for row in comparison
        if row["missing_exposure_sources"]
    }
    if len(missing_artifacts) == len(EXPOSURE_SOURCE_ARTIFACTS):
        status = "missing"
    elif missing_artifacts or strategies_missing_fields:
        status = "partial"
    else:
        status = "complete"
    impacted_sections = []
    if status != "complete":
        impacted_sections = [
            "Strategy Briefs",
            "Exposure + Concentration Review",
            "Operator Takeaway",
            "Research Follow-Ups",
        ]
    return {
        "exposure_data_status": status,
        "expected_sources": inputs["source_diagnostics"],
        "missing_artifacts": missing_artifacts,
        "strategies_missing_fields": strategies_missing_fields,
        "impacted_sections": impacted_sections,
        "operator_consequence": (
            "Exposure-adjusted interpretation is available."
            if status == "complete"
            else "Performance ranking is available, but exposure-adjusted interpretation is not yet available."
        ),
        "next_action": (
            "Use exposure and concentration flags in normal review."
            if status == "complete"
            else "Regenerate or inspect FR-026/FR-027 research clarity artifacts before comparing strategy quality."
        ),
    }


def _regime_review(inputs: dict[str, Any]) -> dict[str, Any]:
    breakdown = inputs["artifacts"]["regime_performance_breakdown"]
    fragility = inputs["artifacts"]["regime_fragility_report"]
    matrix = inputs["artifacts"]["regime_exposure_matrix"]
    regime = breakdown.get("regime", {})
    regime_sentence = _regime_sentence(regime)
    return {
        "regime": regime,
        "regime_summary": regime_sentence,
        "performance_by_strategy": breakdown.get("strategies", {}),
        "fragility_indicators": fragility.get("fragility_indicators", []),
        "exposure_matrix": matrix.get("strategies", {}),
        "confidence_classification": breakdown.get("confidence_classification", "LOW"),
        "interpretation": "Regime interpretation is advisory and inherits source artifact confidence.",
    }


def _regime_sentence(regime: Any) -> str:
    if not isinstance(regime, dict) or not regime:
        return "Regime metadata was not present in the shadow artifact; interpretation remains LOW confidence."
    if regime.get("regime_source") == "not_present_in_shadow_artifact":
        return "Regime metadata was not present in the shadow artifact; interpretation remains LOW confidence."
    parts = []
    labels = (
        ("risk", "risk"),
        ("volatility", "volatility"),
        ("trend", "trend"),
        ("breadth", "breadth"),
    )
    for key, label in labels:
        value = regime.get(key)
        if value and value != "UNKNOWN":
            parts.append(f"{label} is {str(value).replace('_', ' ')}")
    if not parts:
        return "Regime metadata was not present in the shadow artifact; interpretation remains LOW confidence."
    return "Available regime evidence indicates " + ", ".join(parts) + "."


def _what_changed(inputs: dict[str, Any]) -> list[str]:
    delta = inputs["artifacts"]["rebalance_delta"]
    delta_basis = delta.get("delta_basis", "UNKNOWN")
    if delta_basis == "NO_PRIOR":
        return ["No prior immutable snapshot was available; today's bundle establishes a baseline."]
    if delta_basis == "OK":
        return ["Shadow source delta status is OK; review turnover proxies for composition changes."]
    return [f"Rebalance delta status is {delta_basis}; review source artifacts before drawing change conclusions."]


def _ranking_change_note(comparison: list[dict[str, Any]]) -> str:
    if not comparison:
        return "Strategy ranking is unavailable because packet inputs are incomplete."
    basis = comparison[0].get("ranking_basis")
    if basis == "daily_return":
        return "Strategy ordering is based on available same-day shadow daily return."
    if basis == "cumulative_nav":
        return "All available daily returns are 0.00%; strategy ordering is based on cumulative NAV instead of daily return."
    return "Strategy ordering uses fallback strategy order because daily return and NAV evidence are unavailable."


def _key_risks(trust: dict[str, Any], exposure: dict[str, Any], regime: dict[str, Any]) -> list[str]:
    risks = [LOW_CONFIDENCE_REASON]
    if trust["missing_artifacts"]:
        risks.append("One or more expected packet inputs are missing; packet confidence is partial.")
    if exposure["risk_flags"]:
        risks.append("Exposure risk flags are present; inspect concentration and factor sensitivity before interpreting returns.")
    if not exposure.get("risk_assessable", True):
        risks.append("Exposure risk could not be evaluated because required FR-026/FR-027 artifacts are incomplete.")
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
    if not exposure.get("risk_assessable", True):
        followups.append("Restore complete FR-026/FR-027 exposure artifacts before comparing strategy quality.")
    if regime["fragility_indicators"]:
        followups.append("Track whether fragility indicators persist across regime transitions.")
    return followups


def _packet_payload(repo_root: Path, trade_date: str, shadow_dir: Path | None, clarity_dir: Path | None) -> dict[str, Any]:
    inputs = _load_inputs(repo_root, trade_date, shadow_dir, clarity_dir)
    trust = _operational_trust(inputs)
    comparison = _strategy_comparison(inputs)
    data_completeness = _data_completeness(inputs, comparison)
    exposure = _exposure_review(inputs, data_completeness)
    regime = _regime_review(inputs)
    key_risks = _key_risks(trust, exposure, regime)
    followups = _research_followups(exposure, regime)
    operator_takeaway = _operator_takeaway(comparison, exposure, regime, trust)
    executive_summary = [
        f"Packet status: {trust['status']}; advisory research-only interpretation.",
        f"Strategy rank leader: {comparison[0]['strategy_name'] if comparison else 'unavailable'} based on {comparison[0]['ranking_basis'].replace('_', ' ') if comparison else 'unavailable'}.",
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
        "operator_takeaway": operator_takeaway,
        "how_to_read": [
            "Start with confidence and freshness before interpreting returns.",
            "Treat shadow outperformance as advisory until FR-028 timing semantics are governed.",
            "Use concentration and sector exposure to separate possible selection edge from exposure amplification.",
            "Use regime evidence as context, not as promotion or execution guidance.",
        ],
        "data_completeness": data_completeness,
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
        "what_changed_today": [_ranking_change_note(comparison), *_what_changed(inputs)],
        "key_risks": key_risks,
        "research_followups": followups,
        "delivery_preparation": {
            "email_ready_html": "packet.html",
            "dashboard_summary": "summary.json",
            "mcp_readiness": "read_only_artifact_retrieval_ready",
        },
    }


def _operator_takeaway(
    comparison: list[dict[str, Any]],
    exposure: dict[str, Any],
    regime: dict[str, Any],
    trust: dict[str, Any],
) -> list[str]:
    if not comparison:
        return ["Packet inputs are insufficient to rank strategies today."]
    leader = comparison[0]
    takeaways = [
        f"{leader['strategy_name']} leads today's available shadow return ranking, but the evidence remains advisory.",
        f"Confidence floor is {trust['shadow_confidence_floor']} because operational shadow NAV remains governed by unresolved FR-028 timing semantics.",
    ]
    if not exposure.get("risk_assessable", True):
        takeaways.append("Exposure risk is not assessable because required FR-026/FR-027 artifacts are incomplete.")
    elif exposure["risk_flags"]:
        takeaways.append("Risk flags are present; review concentration and exposure before treating outperformance as durable.")
    else:
        takeaways.append("No exposure risk flags fired from available FR-026 artifacts.")
    takeaways.append(regime["regime_summary"])
    return takeaways


def _markdown(packet: dict[str, Any]) -> str:
    lines = [
        f"# Daily Research Packet - {packet['trade_date']}",
        "",
        "## Executive Summary",
        "",
    ]
    lines.extend(f"- {item}" for item in packet["executive_summary"])
    lines.extend(["", "## Operator Takeaway", ""])
    lines.extend(f"- {item}" for item in packet["operator_takeaway"])
    lines.extend(["", "## How To Read This Packet", ""])
    lines.extend(f"- {item}" for item in packet["how_to_read"])
    lines.extend(["", "## Data Completeness", ""])
    completeness = packet["data_completeness"]
    lines.extend(
        [
            f"- Exposure data status: `{completeness['exposure_data_status']}`",
            f"- Impacted sections: {', '.join(completeness['impacted_sections']) if completeness['impacted_sections'] else 'none'}",
            f"- Operator consequence: {completeness['operator_consequence']}",
            f"- Next action: {completeness['next_action']}",
        ]
    )
    lines.append("- Source diagnostics:")
    for name, payload in sorted(completeness["expected_sources"].items()):
        lines.append(f"  - `{name}`: `{payload['status']}`")
    lines.extend(["", "## Operational Trust Summary", ""])
    trust = packet["operational_trust_summary"]
    lines.extend(
        [
            f"- Status: `{trust['status']}`",
            f"- Shadow confidence floor: `{trust['shadow_confidence_floor']}`",
            f"- Confidence reason: {trust['confidence_reason']}",
            f"- Missing inputs: {', '.join(trust['missing_artifacts']) if trust['missing_artifacts'] else 'none'}",
            f"- Stale or unknown inputs: {', '.join(trust['stale_or_unknown_artifacts']) if trust['stale_or_unknown_artifacts'] else 'none'}",
            f"- Interpretation: {trust['interpretation']}",
        ]
    )
    lines.extend(["", "## Strategy Briefs", ""])
    for row in packet["strategy_comparison"]:
        lines.extend(
            [
                f"### #{row['daily_rank']} {row['strategy_name']}",
                "",
                f"- Daily return: {_pct(row['daily_return'])}",
                f"- NAV: {_num(row['nav'], 4)}",
                f"- Concentration: top-3 {_pct(row['top3_concentration'])}; max position {_pct(row['max_position_weight'])}",
                f"- Sector exposure: max sector {_pct(row['max_sector_exposure'])}",
                f"- Source diagnostics: {_source_diagnostics_sentence(row['source_diagnostics'])}",
                f"- Main risk caveat: {row['main_risk_caveat']}",
                f"- Interpretation: {row['operator_interpretation']}",
                "",
            ]
        )
    lines.extend(["", "## Exposure + Concentration Review", ""])
    exposure = packet["exposure_concentration_review"]
    if not exposure.get("risk_assessable", True):
        lines.append(f"- {exposure['interpretation']}")
    elif exposure["risk_flags"]:
        lines.append(f"- {exposure['interpretation']}")
        for flag in exposure["risk_flags"]:
            lines.append(f"- `{flag['strategy_id']}`: `{flag['flag']}` severity `{flag['severity']}`.")
    else:
        lines.append(f"- {exposure['interpretation']}")
        lines.append("- No exposure risk flags fired.")
    lines.extend(["", "## Regime Interpretation", ""])
    regime = packet["regime_interpretation"]
    lines.append(f"- {regime['regime_summary']}")
    lines.append(f"- Confidence: `{regime['confidence_classification']}`")
    lines.append(f"- Interpretation: {regime['interpretation']}")
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


def _source_diagnostics_sentence(source_diagnostics: dict[str, str]) -> str:
    found = [name for name, status in sorted(source_diagnostics.items()) if status == "FOUND"]
    missing = [name for name, status in sorted(source_diagnostics.items()) if status != "FOUND"]
    found_text = ", ".join(found) if found else "none"
    missing_text = ", ".join(missing) if missing else "none"
    return f"found {found_text}; missing {missing_text}."


def _html(packet: dict[str, Any], markdown: str) -> str:
    body = []
    for line in markdown.splitlines():
        escaped = html.escape(line)
        if line.startswith("# "):
            body.append(f"<h1>{html.escape(line[2:])}</h1>")
        elif line.startswith("## "):
            body.append(f"<h2>{html.escape(line[3:])}</h2>")
        elif line.startswith("### "):
            body.append(f"<h3>{html.escape(line[4:])}</h3>")
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
        "exposure_data_status": packet["data_completeness"]["exposure_data_status"],
        "exposure_risk_assessable": packet["exposure_concentration_review"]["risk_assessable"],
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
    source_diagnostics = inputs["source_diagnostics"]
