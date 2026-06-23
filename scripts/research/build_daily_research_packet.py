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

from core.strategy_registry import active_shadow_security_selection_ids, load_strategy_registry

LOW_CONFIDENCE_REASON = "FR-028 timing semantics remain unresolved for operational shadow NAV."
INCOMPLETE_EXPOSURE_MESSAGE = (
    "Required exposure fields are absent despite source artifacts being present, likely because upstream shadow artifacts were generated from a stale/no-data price source."
)
INCOMPLETE_NEXT_ACTION = (
    "Wait for post-close hydration and shadow artifact refresh, then rerun Orion.command. Do not force an incomplete packet unless diagnosing source readiness."
)
_REGISTRY = load_strategy_registry()
STRATEGY_ORDER = active_shadow_security_selection_ids()
OPTIONAL_PRE_INCEPTION_STRATEGIES = {
    entry.strategy_id
    for entry in _REGISTRY.active_shadow_security_selection_entries()
    if (entry.shadow_tracking or {}).get("baseline_strategy_id")
}
PROMOTION_CANDIDATES = set(_REGISTRY.promotion_candidate_ids())
EXPOSURE_SOURCE_ARTIFACTS = (
    "exposures_snapshot.json",
    "exposure_summary.json",
    "concentration_monitor.json",
    "regime_exposure_matrix.json",
)
EXPOSURE_FIELD_SOURCES = {
    "top3_concentration": ("exposures_snapshot.json", "exposure_summary.json", "concentration_monitor.json", "regime_exposure_matrix.json"),
    "max_position_weight": ("exposures_snapshot.json", "exposure_summary.json", "concentration_monitor.json", "regime_exposure_matrix.json"),
    "max_sector_exposure": ("exposures_snapshot.json", "exposure_summary.json", "concentration_monitor.json", "regime_exposure_matrix.json"),
}


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


def _short_pct(value: Any) -> str:
    if value is None:
        return "missing"
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return "missing"


def _num(value: Any, digits: int = 2) -> str:
    if value is None:
        return "unavailable"
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return "unavailable"


def _plain_num(value: Any, digits: int = 2) -> str | None:
    if value is None:
        return None
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return None


def _label(value: str) -> str:
    return value.replace("_", " ").title()


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
        "price_hydration_status": _read_json(repo_root / "outputs" / "price_hydration" / trade_date / "status.json"),
        "vix_regime": _read_json(repo_root / "outputs" / "vix_regime" / "regime_current.json"),
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
        "prior_artifacts": _load_prior_research_artifacts(repo_root, artifacts),
        "freshness": freshness,
        "source_diagnostics": source_diagnostics,
        "source_readiness": _source_readiness(artifacts, trade_date),
    }


def _load_prior_research_artifacts(repo_root: Path, artifacts: dict[str, Any]) -> dict[str, Any]:
    performance = artifacts.get("shadow_performance") if isinstance(artifacts.get("shadow_performance"), dict) else {}
    comparison = artifacts.get("comparison") if isinstance(artifacts.get("comparison"), dict) else {}
    previous_date = performance.get("previous_trade_date") or comparison.get("delta", {}).get("previous_date")
    if not previous_date:
        return {"previous_trade_date": None, "status": "MISSING"}
    prior_dir = repo_root / "outputs" / "research_clarity" / str(previous_date)
    if not prior_dir.exists():
        return {"previous_trade_date": str(previous_date), "status": "MISSING", "path": str(prior_dir)}
    return {
        "previous_trade_date": str(previous_date),
        "status": "FOUND",
        "path": str(prior_dir),
        "weights_snapshot": _read_json(prior_dir / "weights_snapshot.json"),
        "exposures_snapshot": _read_json(prior_dir / "exposures_snapshot.json"),
        "concentration_monitor": _read_json(prior_dir / "concentration_monitor.json"),
        "exposure_summary": _read_json(prior_dir / "exposure_summary.json"),
    }


def _source_readiness(artifacts: dict[str, Any], trade_date: str) -> dict[str, Any]:
    performance = artifacts.get("shadow_performance") if isinstance(artifacts.get("shadow_performance"), dict) else {}
    comparison = artifacts.get("comparison") if isinstance(artifacts.get("comparison"), dict) else {}
    hydration = artifacts.get("price_hydration_status") if isinstance(artifacts.get("price_hydration_status"), dict) else {}
    strategies = comparison.get("strategies") if isinstance(comparison.get("strategies"), dict) else {}

    shadow_data_status = performance.get("data_status")
    shadow_data_reason = performance.get("data_reason")
    comparison_status = comparison.get("status", "OK" if strategies else "UNKNOWN")
    strategy_count = len(strategies)
    hydration_status_path = f"outputs/price_hydration/{trade_date}/status.json"
    price_hydration_status = hydration.get("status") if hydration else "MISSING"
    max_cache_date = hydration.get("max_cache_date") or hydration.get("as_of_date")
    hydration_covers_trade_date = bool(max_cache_date and str(max_cache_date) >= trade_date)

    failures = []
    if shadow_data_status != "OK":
        failures.append("shadow_performance.data_status is not OK")
    if shadow_data_reason not in (None, "", "OK"):
        failures.append("shadow_performance.data_reason is present")
    if comparison_status != "OK":
        failures.append("comparison.status is not OK")
    if strategy_count == 0:
        failures.append("comparison.strategies is empty")
    if not hydration:
        failures.append("price hydration status is missing")
    elif price_hydration_status != "OK":
        failures.append("price hydration status is not OK")
    elif not hydration_covers_trade_date:
        failures.append("price hydration max cache date does not cover trade date")

    return {
        "status": "READY" if not failures else "INCOMPLETE",
        "failures": failures,
        "shadow_data_status": shadow_data_status,
        "shadow_data_reason": shadow_data_reason,
        "comparison_status": comparison_status,
        "strategy_count": strategy_count,
        "hydration_status_path": hydration_status_path,
        "price_hydration_status": price_hydration_status,
        "price_hydration_max_cache_date": max_cache_date,
        "hydration_covers_trade_date": hydration_covers_trade_date,
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
    status = "PARTIAL" if missing or inputs["source_readiness"]["status"] != "READY" else "FRESH" if not stale_or_unknown else "PARTIAL"
    return {
        "status": status,
        "source_readiness": inputs["source_readiness"],
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
        optional_pre_inception = (
            strategy_id in OPTIONAL_PRE_INCEPTION_STRATEGIES
            and not perf
            and not exposure
            and not exposure_from_summary
            and not concentration_row
            and not regime_row
        )
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
        if top3 is None and not optional_pre_inception:
            missing_sources.append("top3_concentration from exposures_snapshot/concentration_monitor")
        if max_position is None and not optional_pre_inception:
            missing_sources.append("max_position_weight from exposures_snapshot/concentration_monitor")
        if max_sector is None and not optional_pre_inception:
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
        return INCOMPLETE_EXPOSURE_MESSAGE
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
        return f"{name}: exposure-adjusted read unavailable; use ranking only as context."
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
    source_ready = inputs["source_readiness"]["status"] == "READY"
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
    field_diagnostics = _field_diagnostics(inputs, comparison)
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
        "field_diagnostics": field_diagnostics,
        "missing_artifacts": missing_artifacts,
        "strategies_missing_fields": strategies_missing_fields,
        "impacted_sections": impacted_sections,
        "operator_consequence": (
            "Exposure-adjusted interpretation is available."
            if status == "complete"
            else INCOMPLETE_EXPOSURE_MESSAGE if not source_ready else "Performance ranking is available, but exposure-adjusted interpretation is not yet available."
        ),
        "next_action": (
            "Use exposure and concentration flags in normal review."
            if status == "complete"
            else INCOMPLETE_NEXT_ACTION if not source_ready else "Regenerate or inspect FR-026/FR-027 research clarity artifacts. Do not compare strategy quality until exposure data is present."
        ),
    }


def _field_diagnostics(inputs: dict[str, Any], comparison: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for field_name, sources in EXPOSURE_FIELD_SOURCES.items():
        strategy_values = {
            row["strategy_id"]: row.get(field_name)
            for row in comparison
        }
        field_exists = any(value is not None for value in strategy_values.values())
        source_status = {
            source: inputs["source_diagnostics"].get(source, {}).get("status", "MISSING")
            for source in sources
        }
        rows.append(
            {
                "field": field_name,
                "expected_sources": list(sources),
                "artifact_status": source_status,
                "field_exists": field_exists,
                "operator_consequence": (
                    "Available for exposure-adjusted review."
                    if field_exists
                    else INCOMPLETE_EXPOSURE_MESSAGE
                ),
            }
        )
    return rows


def _regime_review(inputs: dict[str, Any]) -> dict[str, Any]:
    breakdown = inputs["artifacts"]["regime_performance_breakdown"]
    fragility = inputs["artifacts"]["regime_fragility_report"]
    matrix = inputs["artifacts"]["regime_exposure_matrix"]
    regime = breakdown.get("regime", {})
    if _regime_missing(regime):
        fallback = _vix_regime_fallback(inputs["artifacts"].get("vix_regime"))
        if fallback:
            regime = fallback
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


def _regime_missing(regime: Any) -> bool:
    if not isinstance(regime, dict) or not regime:
        return True
    if regime.get("regime_source") == "not_present_in_shadow_artifact":
        return True
    return not any(regime.get(key) and regime.get(key) != "UNKNOWN" for key in ("risk", "volatility", "trend", "breadth"))


def _vix_regime_fallback(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict) or not payload:
        return None
    regime = payload.get("regime")
    if not regime:
        return None
    return {
        "risk": "UNKNOWN",
        "volatility": str(regime).lower(),
        "trend": "UNKNOWN",
        "breadth": "UNKNOWN",
        "vix": payload.get("vix"),
        "regime_source": "vix_regime_current_fallback",
        "fallback_confidence": "LOW",
    }


def _regime_sentence(regime: Any) -> str:
    if not isinstance(regime, dict) or not regime:
        return "Regime metadata was not present in the shadow artifact; interpretation remains LOW confidence."
    if regime.get("regime_source") == "not_present_in_shadow_artifact":
        return "Regime metadata was not present in the shadow artifact; interpretation remains LOW confidence."
    if regime.get("regime_source") == "vix_regime_current_fallback":
        vix_value = _plain_num(regime.get("vix"), 2)
        vix_text = f" with VIX {vix_value}" if vix_value is not None else ""
        return f"Shadow artifact lacked regime metadata; VIX fallback indicates volatility is {str(regime.get('volatility')).replace('_', ' ')}{vix_text}. Interpretation remains LOW confidence."
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


def _research_intelligence(inputs: dict[str, Any], comparison: list[dict[str, Any]], data_completeness: dict[str, Any]) -> dict[str, Any]:
    if inputs["source_readiness"]["status"] != "READY":
        return {
            "status": "NOT_ASSESSABLE",
            "previous_trade_date": inputs["artifacts"]["shadow_performance"].get("previous_trade_date"),
            "interpretation": "Research intelligence is context-only until post-close hydration and shadow artifacts are complete.",
            "material_vs_noise": "not_assessable",
            "strategy_change_summaries": [],
            "attention_flags": [
                {
                    "severity": "HIGH",
                    "flag": "MISSING_ATTRIBUTION_EVIDENCE",
                    "scope": "packet",
                    "interpretation": "Source readiness is incomplete; do not infer composition drift or strategy stability.",
                }
            ],
        }
    artifacts = inputs["artifacts"]
    prior = inputs.get("prior_artifacts", {})
    prior_status = prior.get("status")
    if prior_status != "FOUND":
        return {
            "status": "BASELINE_ONLY",
            "previous_trade_date": prior.get("previous_trade_date"),
            "interpretation": "Current composition is available, but prior research clarity artifacts are missing; drift is not assessable.",
            "material_vs_noise": "baseline_only",
            "strategy_change_summaries": [_baseline_strategy_summary(row) for row in comparison],
            "attention_flags": [
                {
                    "severity": "MEDIUM",
                    "flag": "MISSING_PRIOR_RESEARCH_CLARITY",
                    "scope": "packet",
                    "interpretation": "Prior immutable research clarity artifacts are required for turnover, drift, and stability comparison.",
                }
            ],
        }

    current_weights = artifacts["weights_snapshot"].get("strategies", {})
    prior_weights = prior.get("weights_snapshot", {}).get("strategies", {})
    current_exposures = artifacts["exposures_snapshot"].get("strategies", {})
    prior_exposures = prior.get("exposures_snapshot", {}).get("strategies", {})
    summaries = []
    flags = []
    for row in comparison:
        strategy_id = row["strategy_id"]
        current_weight_map = _target_weights(current_weights.get(strategy_id, {}))
        prior_weight_map = _target_weights(prior_weights.get(strategy_id, {}))
        current_exposure = current_exposures.get(strategy_id, {}) if isinstance(current_exposures, dict) else {}
        prior_exposure = prior_exposures.get(strategy_id, {}) if isinstance(prior_exposures, dict) else {}
        summary = _strategy_research_change_summary(
            strategy_id=strategy_id,
            strategy_name=row["strategy_name"],
            current_weights=current_weight_map,
            prior_weights=prior_weight_map,
            current_exposure=current_exposure,
            prior_exposure=prior_exposure,
            fallback_turnover=row.get("turnover_proxy"),
            data_completeness=data_completeness,
        )
        summaries.append(summary)
        flags.extend(summary["attention_flags"])
    material_count = sum(1 for item in summaries if item["material_vs_noise"] == "material")
    if material_count >= 2:
        packet_materiality = "material"
        interpretation = "Multiple strategies show material composition or exposure movement; operator should review whether change reflects rotation or fragility."
    elif material_count == 1:
        packet_materiality = "mixed"
        interpretation = "One strategy shows material movement; most changes appear incremental."
    else:
        packet_materiality = "incremental"
        interpretation = "Most changes appear incremental rather than structural."
    return {
        "status": "ASSESSABLE",
        "previous_trade_date": prior.get("previous_trade_date"),
        "interpretation": interpretation,
        "material_vs_noise": packet_materiality,
        "strategy_change_summaries": summaries,
        "attention_flags": flags,
    }


def _baseline_strategy_summary(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "strategy_id": row["strategy_id"],
        "strategy_name": row["strategy_name"],
        "turnover": row.get("turnover_proxy"),
        "turnover_interpretation": _turnover_interpretation(row.get("turnover_proxy")),
        "largest_additions": [],
        "largest_removals": [],
        "largest_weight_increases": [],
        "largest_weight_decreases": [],
        "concentration_change": None,
        "sector_exposure_drift": [],
        "stability_interpretation": "Baseline only; prior composition evidence is missing.",
        "material_vs_noise": "baseline_only",
        "attention_flags": [],
    }


def _target_weights(payload: dict[str, Any]) -> dict[str, float]:
    weights = payload.get("target_weights") if isinstance(payload, dict) else {}
    if not isinstance(weights, dict):
        return {}
    result: dict[str, float] = {}
    for ticker, weight in weights.items():
        try:
            result[str(ticker).upper()] = float(weight)
        except (TypeError, ValueError):
            continue
    return result


def _strategy_research_change_summary(
    *,
    strategy_id: str,
    strategy_name: str,
    current_weights: dict[str, float],
    prior_weights: dict[str, float],
    current_exposure: dict[str, Any],
    prior_exposure: dict[str, Any],
    fallback_turnover: Any,
    data_completeness: dict[str, Any],
) -> dict[str, Any]:
    all_tickers = sorted(set(current_weights) | set(prior_weights))
    deltas = {
        ticker: round(current_weights.get(ticker, 0.0) - prior_weights.get(ticker, 0.0), 10)
        for ticker in all_tickers
    }
    additions = _top_weight_changes(
        [
            {"ticker": ticker, "weight": current_weights[ticker], "change": current_weights[ticker]}
            for ticker in current_weights.keys() - prior_weights.keys()
        ]
    )
    removals = _top_weight_changes(
        [
            {"ticker": ticker, "weight": prior_weights[ticker], "change": -prior_weights[ticker]}
            for ticker in prior_weights.keys() - current_weights.keys()
        ]
    )
    increases = _top_weight_changes(
        [
            {"ticker": ticker, "weight": current_weights.get(ticker, 0.0), "change": change}
            for ticker, change in deltas.items()
            if change > 1e-9 and ticker in prior_weights and ticker in current_weights
        ]
    )
    decreases = _top_weight_changes(
        [
            {"ticker": ticker, "weight": current_weights.get(ticker, 0.0), "change": change}
            for ticker, change in deltas.items()
            if change < -1e-9 and ticker in prior_weights and ticker in current_weights
        ],
        reverse=False,
    )
    computed_turnover = round(sum(abs(change) for change in deltas.values()) / 2.0, 10) if deltas else None
    turnover = _first_present(computed_turnover, fallback_turnover)
    top3_delta = _delta_metric(current_exposure, prior_exposure, "top3_concentration")
    max_sector_delta = _delta_metric(current_exposure, prior_exposure, "max_sector_exposure")
    sector_drift = _sector_drift(current_exposure.get("sector_exposure"), prior_exposure.get("sector_exposure"))
    flags = _research_attention_flags(
        strategy_id=strategy_id,
        turnover=turnover,
        additions=additions,
        removals=removals,
        increases=increases,
        decreases=decreases,
        top3_delta=top3_delta,
        max_sector_delta=max_sector_delta,
        sector_drift=sector_drift,
        data_completeness=data_completeness,
    )
    material = _is_material_change(turnover, additions, removals, increases, decreases, top3_delta, max_sector_delta, sector_drift)
    return {
        "strategy_id": strategy_id,
        "strategy_name": strategy_name,
        "turnover": turnover,
        "computed_turnover": computed_turnover,
        "turnover_interpretation": _turnover_interpretation(turnover),
        "largest_additions": additions[:3],
        "largest_removals": removals[:3],
        "largest_weight_increases": increases[:3],
        "largest_weight_decreases": decreases[:3],
        "concentration_change": {
            "top3_concentration_delta": top3_delta,
            "max_sector_exposure_delta": max_sector_delta,
            "interpretation": _concentration_change_interpretation(top3_delta, max_sector_delta),
        },
        "sector_exposure_drift": sector_drift[:3],
        "stability_interpretation": _stability_interpretation(turnover, additions, removals, top3_delta, sector_drift),
        "material_vs_noise": "material" if material else "incremental",
        "attention_flags": flags,
    }


def _top_weight_changes(rows: list[dict[str, Any]], reverse: bool = True) -> list[dict[str, Any]]:
    return sorted(rows, key=lambda item: abs(float(item.get("change") or 0.0)), reverse=reverse)


def _delta_metric(current: dict[str, Any], prior: dict[str, Any], key: str) -> float | None:
    if current.get(key) is None or prior.get(key) is None:
        return None
    try:
        return round(float(current[key]) - float(prior[key]), 10)
    except (TypeError, ValueError):
        return None


def _sector_drift(current: Any, prior: Any) -> list[dict[str, Any]]:
    if not isinstance(current, dict) or not isinstance(prior, dict):
        return []
    rows = []
    for sector in sorted(set(current) | set(prior)):
        try:
            change = round(float(current.get(sector, 0.0)) - float(prior.get(sector, 0.0)), 10)
        except (TypeError, ValueError):
            continue
        if abs(change) > 1e-9:
            rows.append({"sector": sector, "change": change, "current": current.get(sector), "previous": prior.get(sector)})
    return sorted(rows, key=lambda item: abs(float(item["change"])), reverse=True)


def _research_attention_flags(
    *,
    strategy_id: str,
    turnover: Any,
    additions: list[dict[str, Any]],
    removals: list[dict[str, Any]],
    increases: list[dict[str, Any]],
    decreases: list[dict[str, Any]],
    top3_delta: float | None,
    max_sector_delta: float | None,
    sector_drift: list[dict[str, Any]],
    data_completeness: dict[str, Any],
) -> list[dict[str, Any]]:
    flags = []
    turnover_value = _as_float(turnover)
    if turnover_value is not None and turnover_value >= 0.25:
        flags.append(_attention_flag(strategy_id, "HIGH", "UNUSUALLY_HIGH_TURNOVER", {"turnover": turnover_value}, "Turnover increased materially vs prior composition."))
    elif turnover_value is not None and turnover_value >= 0.10:
        flags.append(_attention_flag(strategy_id, "MEDIUM", "ELEVATED_TURNOVER", {"turnover": turnover_value}, "Turnover is elevated enough to review composition changes."))
    if top3_delta is not None and top3_delta >= 0.10:
        flags.append(_attention_flag(strategy_id, "MEDIUM", "CONCENTRATION_WIDENING", {"top3_delta": top3_delta}, "Top-3 concentration widened materially."))
    if max_sector_delta is not None and max_sector_delta >= 0.15:
        flags.append(_attention_flag(strategy_id, "MEDIUM", "MAJOR_SECTOR_DRIFT", {"max_sector_delta": max_sector_delta}, "Dominant sector exposure increased materially."))
    if sector_drift and abs(float(sector_drift[0]["change"])) >= 0.20:
        flags.append(_attention_flag(strategy_id, "MEDIUM", "MAJOR_SECTOR_DRIFT", {"sector": sector_drift[0]["sector"], "change": sector_drift[0]["change"]}, "Sector exposure drift is large enough to review."))
    max_rotation = max([abs(float(item["change"])) for item in [*additions, *removals, *increases, *decreases]] or [0.0])
    if max_rotation >= 0.20 or len(additions) + len(removals) >= 3:
        flags.append(_attention_flag(strategy_id, "HIGH", "SUDDEN_COMPOSITION_ROTATION", {"largest_weight_change": max_rotation}, "Composition rotation appears structural rather than routine."))
    if strategy_id in PROMOTION_CANDIDATES and flags:
        flags.append(_attention_flag(strategy_id, "MEDIUM", "CHALLENGER_INSTABILITY", {"flag_count": len(flags)}, "Challenger stability should be reviewed before interpreting outperformance as durable."))
    if data_completeness["exposure_data_status"] != "complete":
        flags.append(_attention_flag(strategy_id, "MEDIUM", "MISSING_ATTRIBUTION_EVIDENCE", {"exposure_data_status": data_completeness["exposure_data_status"]}, "Missing exposure evidence limits attribution and drift interpretation."))
    return flags


def _attention_flag(strategy_id: str, severity: str, flag: str, evidence: dict[str, Any], interpretation: str) -> dict[str, Any]:
    return {
        "strategy_id": strategy_id,
        "severity": severity,
        "flag": flag,
        "evidence": evidence,
        "interpretation": interpretation,
    }


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _turnover_interpretation(turnover: Any) -> str:
    value = _as_float(turnover)
    if value is None:
        return "Turnover evidence is unavailable."
    if value >= 0.25:
        return "Turnover increased materially vs prior day."
    if value >= 0.10:
        return "Turnover is elevated but not extreme."
    if value >= 0.03:
        return "Turnover appears moderate and likely incremental."
    return "Strategy composition was stable."


def _concentration_change_interpretation(top3_delta: float | None, max_sector_delta: float | None) -> str:
    if top3_delta is None and max_sector_delta is None:
        return "Concentration change is not assessable from available evidence."
    if top3_delta is not None and top3_delta >= 0.10:
        return "Top-3 concentration widened."
    if max_sector_delta is not None and max_sector_delta >= 0.15:
        return "Sector exposure increased materially."
    if (top3_delta is not None and top3_delta <= -0.10) or (max_sector_delta is not None and max_sector_delta <= -0.15):
        return "Concentration or sector exposure narrowed."
    return "Concentration changes appear incremental."


def _stability_interpretation(
    turnover: Any,
    additions: list[dict[str, Any]],
    removals: list[dict[str, Any]],
    top3_delta: float | None,
    sector_drift: list[dict[str, Any]],
) -> str:
    value = _as_float(turnover)
    large_constituent_change = any(abs(float(item.get("change") or 0.0)) >= 0.20 for item in [*additions, *removals])
    large_sector_drift = bool(sector_drift and abs(float(sector_drift[0]["change"])) >= 0.20)
    if (value is not None and value >= 0.25) or large_constituent_change:
        return "Composition rotation is material; review why this changed before treating the signal as stable."
    if large_sector_drift:
        return "Observed drift likely reflects sector rotation rather than a clean selection signal."
    if top3_delta is not None and top3_delta >= 0.10:
        return "Strategy remains investable as context, but concentration widened."
    return "Strategy composition was stable; most changes appear incremental rather than structural."


def _is_material_change(
    turnover: Any,
    additions: list[dict[str, Any]],
    removals: list[dict[str, Any]],
    increases: list[dict[str, Any]],
    decreases: list[dict[str, Any]],
    top3_delta: float | None,
    max_sector_delta: float | None,
    sector_drift: list[dict[str, Any]],
) -> bool:
    value = _as_float(turnover)
    if value is not None and value >= 0.10:
        return True
    all_weight_changes = [*additions, *removals, *increases, *decreases]
    if any(abs(float(item.get("change") or 0.0)) >= 0.15 for item in all_weight_changes):
        return True
    if top3_delta is not None and abs(top3_delta) >= 0.10:
        return True
    if max_sector_delta is not None and abs(max_sector_delta) >= 0.15:
        return True
    if sector_drift and abs(float(sector_drift[0]["change"])) >= 0.15:
        return True
    return False


def _ranking_change_note(comparison: list[dict[str, Any]]) -> str:
    if not comparison:
        return "Strategy ranking is unavailable because packet inputs are incomplete."
    basis = comparison[0].get("ranking_basis")
    if basis == "daily_return":
        return "Strategy ordering is based on available same-day shadow daily return."
    if basis == "cumulative_nav":
        return "No meaningful daily return ranking available; cumulative NAV shown for context."
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


def _research_intelligence_followups(intelligence: dict[str, Any]) -> list[str]:
    flags = intelligence.get("attention_flags", [])
    followups = []
    if any(flag.get("flag") == "UNUSUALLY_HIGH_TURNOVER" for flag in flags):
        followups.append("Review high-turnover strategies for avoidable churn before interpreting outperformance.")
    if any(flag.get("flag") == "MAJOR_SECTOR_DRIFT" for flag in flags):
        followups.append("Check whether sector exposure drift explains challenger leadership.")
    if any(flag.get("flag") == "CHALLENGER_INSTABILITY" for flag in flags):
        followups.append("Track Orion/Lyra stability over repeated packets before treating the move as durable.")
    if intelligence.get("status") in {"BASELINE_ONLY", "NOT_ASSESSABLE"}:
        followups.append("Preserve prior-day research clarity artifacts so composition drift and turnover can be compared.")
    return followups


def _packet_payload(repo_root: Path, trade_date: str, shadow_dir: Path | None, clarity_dir: Path | None) -> dict[str, Any]:
    inputs = _load_inputs(repo_root, trade_date, shadow_dir, clarity_dir)
    trust = _operational_trust(inputs)
    comparison = _strategy_comparison(inputs)
    data_completeness = _data_completeness(inputs, comparison)
    exposure = _exposure_review(inputs, data_completeness)
    regime = _regime_review(inputs)
    intelligence = _research_intelligence(inputs, comparison, data_completeness)
    key_risks = _key_risks(trust, exposure, regime)
    followups = [*_research_followups(exposure, regime), *_research_intelligence_followups(intelligence)]
    operator_takeaway = _operator_takeaway(comparison, exposure, regime, trust)
    dashboard = _dashboard_summary(trust, comparison, exposure, regime, data_completeness, operator_takeaway)
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
        "source_state": inputs["source_readiness"],
        "source_readiness": inputs["source_readiness"]["status"],
        "shadow_data_status": inputs["source_readiness"]["shadow_data_status"],
        "shadow_data_reason": inputs["source_readiness"]["shadow_data_reason"],
        "comparison_status": inputs["source_readiness"]["comparison_status"],
        "strategy_count": inputs["source_readiness"]["strategy_count"],
        "price_hydration_status": inputs["source_readiness"]["price_hydration_status"],
        "dashboard": dashboard,
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
        "research_intelligence": intelligence,
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


def _dashboard_summary(
    trust: dict[str, Any],
    comparison: list[dict[str, Any]],
    exposure: dict[str, Any],
    regime: dict[str, Any],
    data_completeness: dict[str, Any],
    operator_takeaway: list[str],
) -> dict[str, Any]:
    ranking_basis = comparison[0].get("ranking_basis", "unavailable") if comparison else "unavailable"
    regime_status = "missing" if "not present" in regime["regime_summary"] else "available"
    source_status = trust.get("source_readiness", {}).get("status", "UNKNOWN")
    source_ready = source_status == "READY"
    main_takeaway = operator_takeaway[0] if operator_takeaway else "Review packet evidence before drawing conclusions."
    if not source_ready:
        main_takeaway = "Do not use this packet for strategy interpretation until post-close hydration and shadow artifacts are complete."
    return {
        "packet_status": trust["status"],
        "source_readiness": source_status,
        "confidence_floor": trust["shadow_confidence_floor"],
        "ranking_basis": ranking_basis,
        "ranking_basis_label": _ranking_basis_label(ranking_basis),
        "exposure_data_status": data_completeness["exposure_data_status"],
        "regime_data_status": regime_status,
        "main_operator_takeaway": main_takeaway,
        "can_use": {
            "execution_promotion_use": "NO",
            "research_review_use": "YES" if source_ready else "LIMITED",
            "exposure_adjusted_conclusions": "YES" if source_ready and exposure.get("risk_assessable", False) else "NO until fields are present",
            "strategy_quality_comparison": "LIMITED" if (not source_ready or data_completeness["exposure_data_status"] != "complete") else "YES with caveats",
        },
    }


def _ranking_basis_label(ranking_basis: str) -> str:
    if ranking_basis == "daily_return":
        return "Daily return"
    if ranking_basis == "cumulative_nav":
        return "Cumulative NAV context"
    if ranking_basis == "fallback_ordering":
        return "Fallback ordering"
    return "Unavailable"


def _operator_takeaway(
    comparison: list[dict[str, Any]],
    exposure: dict[str, Any],
    regime: dict[str, Any],
    trust: dict[str, Any],
) -> list[str]:
    if not comparison:
        return ["Packet inputs are insufficient to rank strategies today."]
    source_readiness = trust.get("source_readiness", {})
    if source_readiness.get("status") != "READY":
        return [
            "Do not use this packet for strategy interpretation until post-close hydration and shadow artifacts are complete.",
            f"Source readiness is {source_readiness.get('status', 'UNKNOWN')}; shadow_data_status={source_readiness.get('shadow_data_status')}, shadow_data_reason={source_readiness.get('shadow_data_reason')}, comparison_status={source_readiness.get('comparison_status')}.",
            f"Confidence floor is {trust['shadow_confidence_floor']} because operational shadow NAV remains governed by unresolved FR-028 timing semantics.",
            regime["regime_summary"],
        ]
    leader = comparison[0]
    ranking_basis = leader.get("ranking_basis")
    if ranking_basis == "cumulative_nav":
        lead_text = f"{leader['strategy_name']} has the highest cumulative NAV in this packet; no meaningful daily return ranking is available."
    elif ranking_basis == "daily_return":
        lead_text = f"{leader['strategy_name']} leads today's available daily shadow return ranking, but the evidence remains advisory."
    else:
        lead_text = "Strategy ordering is a fallback because daily return and NAV evidence are incomplete."
    takeaways = [
        lead_text,
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
    dashboard = packet["dashboard"]
    lines = [
        f"# Daily Research Packet - {packet['trade_date']}",
        "",
        "## Top Dashboard",
        "",
        "| Field | Status |",
        "|---|---|",
        f"| Packet status | {dashboard['packet_status']} |",
        f"| Source readiness | {dashboard['source_readiness']} |",
        f"| Confidence floor | {dashboard['confidence_floor']} |",
        f"| Ranking basis | {dashboard['ranking_basis_label']} |",
        f"| Exposure data | {dashboard['exposure_data_status']} |",
        f"| Regime data | {dashboard['regime_data_status']} |",
        f"| Main takeaway | {dashboard['main_operator_takeaway']} |",
        "",
        "## Can I Use This Today?",
        "",
        "| Use | Answer |",
        "|---|---|",
        f"| Execution/promotion use | {dashboard['can_use']['execution_promotion_use']} |",
        f"| Research review use | {dashboard['can_use']['research_review_use']} |",
        f"| Exposure-adjusted conclusions | {dashboard['can_use']['exposure_adjusted_conclusions']} |",
        f"| Strategy quality comparison | {dashboard['can_use']['strategy_quality_comparison']} |",
        "",
        "## Executive Notes",
        "",
    ]
    lines.extend(f"- {item}" for item in packet["executive_summary"])
    lines.extend(["", "## Operator Takeaway", ""])
    lines.extend(f"- {item}" for item in packet["operator_takeaway"])
    if packet["source_readiness"] != "READY":
        lines.extend(["", "## Why This Is Incomplete", ""])
        source_state = packet["source_state"]
        lines.extend(
            [
                f"- Shadow data status: `{source_state['shadow_data_status']}`",
                f"- Shadow data reason: `{source_state['shadow_data_reason']}`",
                f"- Comparison status: `{source_state['comparison_status']}`",
                f"- Price hydration status: `{source_state['price_hydration_status']}`",
                f"- Strategy count: `{source_state['strategy_count']}`",
            ]
        )
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
    lines.append("")
    lines.append("### Field Diagnostics")
    for diagnostic in completeness["field_diagnostics"]:
        source_text = ", ".join(
            f"{source}={status}"
            for source, status in sorted(diagnostic["artifact_status"].items())
        )
        field_status = "present" if diagnostic["field_exists"] else "missing"
        lines.append(
            f"  - {diagnostic['field']}: field {field_status}; sources {source_text}; {diagnostic['operator_consequence']}"
        )
    lines.extend(["", "## Operational Trust Summary", ""])
    trust = packet["operational_trust_summary"]
    source_state = packet["source_state"]
    lines.extend(
        [
            f"- Status: `{trust['status']}`",
            f"- Source readiness: `{source_state['status']}`",
            f"- Shadow data status: `{source_state['shadow_data_status']}`",
            f"- Shadow data reason: `{source_state['shadow_data_reason']}`",
            f"- Comparison status: `{source_state['comparison_status']}`",
            f"- Strategy count: `{source_state['strategy_count']}`",
            f"- Price hydration status: `{source_state['price_hydration_status']}`",
            f"- Shadow confidence floor: `{trust['shadow_confidence_floor']}`",
            f"- Confidence reason: {trust['confidence_reason']}",
            f"- Missing inputs: {', '.join(trust['missing_artifacts']) if trust['missing_artifacts'] else 'none'}",
            f"- Stale or unknown inputs: {', '.join(trust['stale_or_unknown_artifacts']) if trust['stale_or_unknown_artifacts'] else 'none'}",
            f"- Interpretation: {trust['interpretation']}",
        ]
    )
    strategy_heading = "## Strategy Briefs - Context Only" if packet["source_readiness"] != "READY" else "## Strategy Briefs"
    lines.extend(["", strategy_heading, ""])
    if packet["source_readiness"] != "READY":
        lines.append("- Strategy ordering is not analytically meaningful while source readiness is INCOMPLETE; NAV values are context only.")
    lines.extend(
        [
            "| Rank | Strategy | NAV | Daily Return | Concentration | Exposure | Interpretation |",
            "|---:|---|---:|---:|---|---|---|",
        ]
    )
    for row in packet["strategy_comparison"]:
        concentration_status = _compact_concentration_status(row)
        exposure_status = _compact_exposure_status(row)
        lines.append(
            f"| {row['daily_rank']} | {row['strategy_name']} | {_num(row['nav'], 4)} | {_pct(row['daily_return'])} | "
            f"{concentration_status} | {exposure_status} | {row['operator_interpretation']} |"
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
    lines.extend(["", "## Research Intelligence", ""])
    intelligence = packet["research_intelligence"]
    lines.extend(
        [
            f"- Status: `{intelligence['status']}`",
            f"- Prior comparison date: `{intelligence.get('previous_trade_date') or 'unavailable'}`",
            f"- Material vs noise: `{intelligence['material_vs_noise']}`",
            f"- Interpretation: {intelligence['interpretation']}",
        ]
    )
    if intelligence["attention_flags"]:
        lines.append("- Research attention flags:")
        for flag in intelligence["attention_flags"]:
            scope = flag.get("strategy_id") or flag.get("scope", "packet")
            lines.append(f"  - `{scope}`: `{flag['flag']}` severity `{flag['severity']}` - {flag['interpretation']}")
    else:
        lines.append("- Research attention flags: none")
    for summary in intelligence["strategy_change_summaries"]:
        lines.extend(
            [
                f"",
                f"### {_strategy_name_for_heading(summary['strategy_name'])}",
                f"- Turnover: {_short_pct(summary.get('turnover'))} - {summary['turnover_interpretation']}",
                f"- Material vs noise: `{summary['material_vs_noise']}`",
                f"- Concentration: {summary['concentration_change']['interpretation'] if isinstance(summary.get('concentration_change'), dict) else 'not assessable'}",
                f"- Stability: {summary['stability_interpretation']}",
                f"- Largest additions: {_format_weight_change_list(summary['largest_additions'])}",
                f"- Largest removals: {_format_weight_change_list(summary['largest_removals'])}",
                f"- Largest increases: {_format_weight_change_list(summary['largest_weight_increases'])}",
                f"- Largest decreases: {_format_weight_change_list(summary['largest_weight_decreases'])}",
                f"- Sector drift: {_format_sector_drift(summary['sector_exposure_drift'])}",
            ]
        )
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


def _strategy_name_for_heading(value: str) -> str:
    return value.replace("|", "").strip()


def _format_weight_change_list(items: list[dict[str, Any]]) -> str:
    if not items:
        return "none"
    parts = []
    for item in items[:3]:
        ticker = item.get("ticker", "UNKNOWN")
        change = _short_pct(item.get("change"))
        weight = _short_pct(item.get("weight"))
        parts.append(f"{ticker} change {change} current/prior weight {weight}")
    return "; ".join(parts)


def _format_sector_drift(items: list[dict[str, Any]]) -> str:
    if not items:
        return "none"
    return "; ".join(f"{item.get('sector', 'UNKNOWN')} { _short_pct(item.get('change')) }" for item in items[:3])


def _compact_concentration_status(row: dict[str, Any]) -> str:
    if row["top3_concentration"] is None and row["max_position_weight"] is None:
        return "not assessable"
    return f"top-3 {_short_pct(row['top3_concentration'])}; max {_short_pct(row['max_position_weight'])}"


def _compact_exposure_status(row: dict[str, Any]) -> str:
    if row["max_sector_exposure"] is None:
        return "not assessable"
    return f"max sector {_short_pct(row['max_sector_exposure'])}"


def _html(packet: dict[str, Any], markdown: str) -> str:
    del markdown
    dashboard = packet["dashboard"]
    completeness = packet["data_completeness"]
    trust = packet["operational_trust_summary"]
    exposure = packet["exposure_concentration_review"]
    regime = packet["regime_interpretation"]
    intelligence = packet["research_intelligence"]
    source_state = packet["source_state"]
    why_incomplete = []
    if packet["source_readiness"] != "READY":
        why_incomplete = [
            "<section class=\"card warning\"><h2>Why This Is Incomplete</h2>",
            "<table><tbody>",
            f"<tr><th>Shadow data status</th><td>{html.escape(str(source_state['shadow_data_status']))}</td></tr>",
            f"<tr><th>Shadow data reason</th><td>{html.escape(str(source_state['shadow_data_reason']))}</td></tr>",
            f"<tr><th>Comparison status</th><td>{html.escape(str(source_state['comparison_status']))}</td></tr>",
            f"<tr><th>Price hydration status</th><td>{html.escape(str(source_state['price_hydration_status']))}</td></tr>",
            f"<tr><th>Strategy count</th><td>{html.escape(str(source_state['strategy_count']))}</td></tr>",
            "</tbody></table></section>",
        ]
    strategy_title = "Strategy Briefs - Context Only" if packet["source_readiness"] != "READY" else "Strategy Briefs"
    strategy_context_note = []
    if packet["source_readiness"] != "READY":
        strategy_context_note = [
            "<p><strong>Context only:</strong> Strategy ordering is not analytically meaningful while source readiness is INCOMPLETE.</p>"
        ]
    body = [
        f"<h1>Daily Research Packet - {html.escape(packet['trade_date'])}</h1>",
        "<section class=\"card wide\"><h2>Top Dashboard</h2>",
        "<section class=\"grid cards\">",
        _metric_card("Packet Status", dashboard["packet_status"]),
        _metric_card("Source Readiness", dashboard["source_readiness"]),
        _metric_card("Confidence Floor", dashboard["confidence_floor"]),
        _metric_card("Ranking Basis", dashboard["ranking_basis_label"]),
        _metric_card("Exposure Data", dashboard["exposure_data_status"]),
        _metric_card("Regime Data", dashboard["regime_data_status"]),
        "</section></section>",
        "<section class=\"card wide\"><h2>Operator Takeaway</h2>",
        f"<p>{html.escape(dashboard['main_operator_takeaway'])}</p>",
        "<ul>",
        *(f"<li>{html.escape(item)}</li>" for item in packet["operator_takeaway"][1:]),
        "</ul></section>",
        *why_incomplete,
        "<section class=\"card\"><h2>Can I Use This Today?</h2>",
        "<table><thead><tr><th>Use</th><th>Answer</th></tr></thead><tbody>",
        *(f"<tr><td>{html.escape(_label(key))}</td><td>{html.escape(value)}</td></tr>" for key, value in dashboard["can_use"].items()),
        "</tbody></table></section>",
        "<section class=\"card\"><h2>Data Completeness</h2>",
        f"<p><strong>Status:</strong> {html.escape(completeness['exposure_data_status'])}</p>",
        f"<p><strong>Operator consequence:</strong> {html.escape(completeness['operator_consequence'])}</p>",
        f"<p><strong>Next action:</strong> {html.escape(completeness['next_action'])}</p>",
        "<h3>Field Diagnostics</h3>",
        "<table><thead><tr><th>Field</th><th>Expected Sources</th><th>Field Exists</th><th>Operator Consequence</th></tr></thead><tbody>",
        *(
            "<tr>"
            f"<td>{html.escape(diagnostic['field'])}</td>"
            f"<td>{html.escape(', '.join(diagnostic['expected_sources']))}</td>"
            f"<td>{'YES' if diagnostic['field_exists'] else 'NO'}</td>"
            f"<td>{html.escape(diagnostic['operator_consequence'])}</td>"
            "</tr>"
            for diagnostic in completeness["field_diagnostics"]
        ),
        "</tbody></table></section>",
        f"<section class=\"card muted\"><h2>{html.escape(strategy_title)}</h2>",
        *strategy_context_note,
        "<table><thead><tr><th>Rank</th><th>Strategy</th><th>NAV</th><th>Daily Return</th><th>Concentration</th><th>Exposure</th><th>Interpretation</th></tr></thead><tbody>",
        *(
            "<tr>"
            f"<td>{row['daily_rank']}</td>"
            f"<td>{html.escape(row['strategy_name'])}</td>"
            f"<td>{html.escape(_num(row['nav'], 4))}</td>"
            f"<td>{html.escape(_pct(row['daily_return']))}</td>"
            f"<td>{html.escape(_compact_concentration_status(row))}</td>"
            f"<td>{html.escape(_compact_exposure_status(row))}</td>"
            f"<td>{html.escape(row['operator_interpretation'])}</td>"
            "</tr>"
            for row in packet["strategy_comparison"]
        ),
        "</tbody></table></section>",
        "<section class=\"card\"><h2>Trust And Caveats</h2>",
        f"<p><strong>Shadow confidence floor:</strong> {html.escape(trust['shadow_confidence_floor'])}</p>",
        f"<p>{html.escape(trust['confidence_reason'])}</p>",
        f"<p><strong>Exposure review:</strong> {html.escape(exposure['interpretation'])}</p>",
        f"<p><strong>Regime:</strong> {html.escape(regime['regime_summary'])}</p>",
        "</section>",
        "<section class=\"card\"><h2>Research Intelligence</h2>",
        "<table><tbody>",
        f"<tr><th>Status</th><td>{html.escape(str(intelligence['status']))}</td></tr>",
        f"<tr><th>Prior comparison date</th><td>{html.escape(str(intelligence.get('previous_trade_date') or 'unavailable'))}</td></tr>",
        f"<tr><th>Material vs noise</th><td>{html.escape(str(intelligence['material_vs_noise']))}</td></tr>",
        f"<tr><th>Interpretation</th><td>{html.escape(str(intelligence['interpretation']))}</td></tr>",
        "</tbody></table>",
        "<h3>Research Attention Flags</h3>",
        "<ul>",
        *_html_attention_flags(intelligence["attention_flags"]),
        "</ul>",
        "<h3>Strategy Change Summary</h3>",
        "<table><thead><tr><th>Strategy</th><th>Turnover</th><th>Largest Additions</th><th>Largest Removals</th><th>Sector Drift</th><th>Stability</th></tr></thead><tbody>",
        *(
            "<tr>"
            f"<td>{html.escape(summary['strategy_name'])}</td>"
            f"<td>{html.escape(_short_pct(summary.get('turnover')))} - {html.escape(summary['turnover_interpretation'])}</td>"
            f"<td>{html.escape(_format_weight_change_list(summary['largest_additions']))}</td>"
            f"<td>{html.escape(_format_weight_change_list(summary['largest_removals']))}</td>"
            f"<td>{html.escape(_format_sector_drift(summary['sector_exposure_drift']))}</td>"
            f"<td>{html.escape(summary['stability_interpretation'])}</td>"
            "</tr>"
            for summary in intelligence["strategy_change_summaries"]
        ),
        "</tbody></table></section>",
        "<section class=\"card\"><h2>What Changed Today</h2><ul>",
        *(f"<li>{html.escape(item)}</li>" for item in packet["what_changed_today"]),
        "</ul></section>",
        "<section class=\"card\"><h2>Research Follow-Ups</h2><ul>",
        *(f"<li>{html.escape(item)}</li>" for item in packet["research_followups"]),
        "</ul></section>",
    ]
    return "\n".join(
        [
            "<!doctype html>",
            "<html>",
            "<head><meta charset=\"utf-8\"><title>Daily Research Packet</title>",
            "<style>",
            "body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;margin:24px;background:#f6f7f9;color:#1f2933;}",
            "h1{margin-bottom:16px;} h2{margin:0 0 12px 0;} h3{margin:16px 0 8px 0;}",
            ".grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin-bottom:14px;}",
            ".card{background:#fff;border:1px solid #d9dee7;border-radius:8px;padding:16px;margin:14px 0;box-shadow:0 1px 2px rgba(0,0,0,.04);}",
            ".warning{border-color:#f59e0b;background:#fffbeb;} .muted{background:#f9fafb;}",
            ".metric .label{font-size:12px;text-transform:uppercase;color:#667085;} .metric .value{font-size:20px;font-weight:700;margin-top:4px;}",
            "table{width:100%;border-collapse:collapse;font-size:14px;} th,td{border-bottom:1px solid #e5e7eb;text-align:left;padding:8px;vertical-align:top;} th{background:#f3f4f6;}",
            "ul{margin:8px 0 0 20px;} p{line-height:1.45;}",
            "</style></head>",
            "<body>",
            *body,
            "</body>",
            "</html>",
            "",
        ]
    )


def _metric_card(label: str, value: Any) -> str:
    return (
        "<div class=\"card metric\">"
        f"<div class=\"label\">{html.escape(str(label))}</div>"
        f"<div class=\"value\">{html.escape(str(value))}</div>"
        "</div>"
    )


def _html_attention_flags(flags: list[dict[str, Any]]) -> list[str]:
    if not flags:
        return ["<li>none</li>"]
    return [
        f"<li><strong>{html.escape(str(flag.get('severity')))}</strong> {html.escape(str(flag.get('flag')))} "
        f"({html.escape(str(flag.get('strategy_id') or flag.get('scope', 'packet')))}): {html.escape(str(flag.get('interpretation')))}</li>"
        for flag in flags
    ]


def _summary(packet: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "daily_research_packet_summary_v1",
        "trade_date": packet["trade_date"],
        "status": packet["operational_trust_summary"]["status"],
        "source_readiness": packet["source_readiness"],
        "shadow_data_status": packet["shadow_data_status"],
        "shadow_data_reason": packet["shadow_data_reason"],
        "comparison_status": packet["comparison_status"],
        "strategy_count": packet["strategy_count"],
        "price_hydration_status": packet["price_hydration_status"],
        "confidence_floor": packet["confidence_freshness_caveats"]["confidence_floor"],
        "leader": packet["strategy_comparison"][0] if packet["strategy_comparison"] else None,
        "risk_flag_count": len(packet["exposure_concentration_review"]["risk_flags"]),
        "exposure_data_status": packet["data_completeness"]["exposure_data_status"],
        "exposure_risk_assessable": packet["exposure_concentration_review"]["risk_assessable"],
        "fragility_indicator_count": len(packet["regime_interpretation"]["fragility_indicators"]),
        "research_attention_flag_count": len(packet["research_intelligence"]["attention_flags"]),
        "research_intelligence_status": packet["research_intelligence"]["status"],
        "research_material_vs_noise": packet["research_intelligence"]["material_vs_noise"],
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
