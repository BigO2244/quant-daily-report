"""Build additive FR-023 through FR-027 research clarity artifacts.

This module is intentionally read-only with respect to source artifacts. It
hydrates existing shadow candidate evidence and writes a dated, immutable
research clarity bundle for operator interpretation. It does not alter strategy,
execution, accounting, cron, broker, dashboard, or promotion behavior.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any


STRATEGY_ORDER = ("caerus_polaris", "caerus_orion", "caerus_lyra")
BENCHMARK_ID = "spy_benchmark"
LOW_CONFIDENCE_REASON = "FR-028 timing semantics remain unresolved for operational shadow NAV."


class ImmutableArtifactError(RuntimeError):
    """Raised when an existing research artifact would be silently rewritten."""


@dataclass(frozen=True)
class SourceBundle:
    trade_date: str
    source_dir: Path
    strategies: dict[str, dict[str, Any]]
    comparison: dict[str, Any]
    shadow_performance: dict[str, Any]
    source_hashes: dict[str, str]


def _canonical_json(data: Any) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _hash_json(data: Any) -> str:
    return _hash_text(_canonical_json(data))


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return data


def _write_immutable(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        existing = path.read_text(encoding="utf-8")
        if existing != content:
            raise ImmutableArtifactError(f"Refusing to rewrite immutable artifact: {path}")
        return
    path.write_text(content, encoding="utf-8")


def _write_json_immutable(path: Path, payload: dict[str, Any]) -> None:
    _write_immutable(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _load_sector_map(repo_root: Path) -> dict[str, str]:
    universe_path = repo_root / "data" / "universe.csv"
    if not universe_path.exists():
        return {}
    with universe_path.open("r", encoding="utf-8") as handle:
        reader = csv.DictReader(line for line in handle if line.strip())
        return {
            (row.get("ticker") or "").strip().upper(): (row.get("sector") or "UNKNOWN").strip()
            for row in reader
            if row.get("ticker")
        }


def _strategy_display(strategy_id: str, payload: dict[str, Any]) -> str:
    return str(payload.get("strategy_name") or strategy_id.replace("_", " ").title())


def _weight_from_holding(holding: dict[str, Any]) -> float:
    value = holding.get("target_weight", holding.get("weight", 0.0))
    try:
        return round(float(value), 10)
    except (TypeError, ValueError):
        return 0.0


def _normalize_holdings(payload: dict[str, Any], sector_map: dict[str, str]) -> list[dict[str, Any]]:
    holdings = payload.get("holdings") or []
    normalized: list[dict[str, Any]] = []
    for raw in holdings:
        if not isinstance(raw, dict):
            continue
        ticker = str(raw.get("ticker") or raw.get("symbol") or "").upper().strip()
        if not ticker:
            continue
        weight = _weight_from_holding(raw)
        normalized.append(
            {
                "ticker": ticker,
                "target_weight": weight,
                "sector": str(raw.get("sector") or sector_map.get(ticker, "UNKNOWN")),
                "momentum_rank": raw.get("momentum_rank"),
                "momentum_score": raw.get("momentum_score"),
                "estimated_holding_period_days": raw.get("estimated_holding_period_days"),
            }
        )
    normalized.sort(key=lambda item: (item["ticker"], item["target_weight"]))
    return normalized


def _load_sources(repo_root: Path, trade_date: str, source_dir: Path | None = None) -> SourceBundle:
    actual_source_dir = source_dir or repo_root / "outputs" / "shadow_candidates" / trade_date
    comparison = _read_json(actual_source_dir / "comparison.json")
    shadow_performance = _read_json(actual_source_dir / "shadow_performance.json")

    strategies: dict[str, dict[str, Any]] = {}
    comparison_strategies = comparison.get("strategies") if isinstance(comparison.get("strategies"), dict) else {}
    for strategy_id in STRATEGY_ORDER:
        file_payload = _read_json(actual_source_dir / f"{strategy_id}.json")
        comparison_payload = comparison_strategies.get(strategy_id, {})
        if file_payload and comparison_payload:
            merged = {**comparison_payload, **file_payload}
        else:
            merged = file_payload or comparison_payload
        if merged:
            strategies[strategy_id] = merged

    source_hashes = {}
    for path in sorted(actual_source_dir.glob("*.json")):
        source_hashes[path.name] = _hash_text(path.read_text(encoding="utf-8"))

    return SourceBundle(
        trade_date=trade_date,
        source_dir=actual_source_dir,
        strategies=strategies,
        comparison=comparison,
        shadow_performance=shadow_performance,
        source_hashes=source_hashes,
    )


def _surface_registry(bundle: SourceBundle) -> dict[str, Any]:
    return {
        "schema_version": "research_clarity_nav_surface_registry_v1",
        "trade_date": bundle.trade_date,
        "provenance_status": "ADDITIVE_RESEARCH_OVERLAY",
        "accounting_semantics_changed": False,
        "execution_behavior_changed": False,
        "historical_chains_rewritten": False,
        "surfaces": {
            "LIVE_BROKER_PAPER_NAV": {
                "classification": "authoritative_broker_surface",
                "confidence": "HIGH_WHEN_BROKER_RECONCILED_ELSE_UNKNOWN",
                "execution_realism": "paper_broker_fills_and_account_state",
                "point_in_time_validity": "broker_timestamp_dependent",
                "allowed_use": ["operator NAV truth", "broker reconciliation"],
                "not_allowed_use": ["shadow promotion without comparison caveats"],
            },
            "OPERATIONAL_SHADOW_NAV": {
                "classification": "shadow_interpretation_surface",
                "confidence": "LOW",
                "confidence_reason": LOW_CONFIDENCE_REASON,
                "execution_realism": "model_portfolio_no_broker_fills",
                "point_in_time_validity": "PARTIAL_PENDING_FR_028",
                "allowed_use": ["research comparison", "challenger telemetry"],
                "not_allowed_use": ["broker truth", "governance-approved timing-corrected performance"],
            },
            "RESEARCH_BACKTEST_NAV": {
                "classification": "research_backtest_surface",
                "confidence": "MEDIUM_WHEN_REPRODUCIBLE_ELSE_LOW",
                "execution_realism": "synthetic_assumptions",
                "point_in_time_validity": "depends_on_source_data_controls",
                "allowed_use": ["hypothesis evaluation", "historical sensitivity review"],
                "not_allowed_use": ["live broker truth"],
            },
            "CONVENIENCE_LATEST_PUBLICATION": {
                "classification": "convenience_publication",
                "confidence": "DERIVED_FROM_CANONICAL_SOURCE_AND_FRESHNESS",
                "execution_realism": "not_a_nav_truth_surface",
                "point_in_time_validity": "publication_timestamp_dependent",
                "allowed_use": ["operator navigation", "latest pointer"],
                "not_allowed_use": ["canonical evidence without source verification"],
            },
        },
        "source_hashes": bundle.source_hashes,
    }


def _surface_metadata(bundle: SourceBundle) -> dict[str, Any]:
    strategy_entries = {}
    performance = bundle.shadow_performance.get("strategies", {})
    for strategy_id in sorted(bundle.strategies):
        perf = performance.get(strategy_id, {}) if isinstance(performance, dict) else {}
        strategy_entries[strategy_id] = {
            "strategy_name": _strategy_display(strategy_id, bundle.strategies[strategy_id]),
            "nav_surface_type": "OPERATIONAL_SHADOW_NAV",
            "confidence_classification": "LOW",
            "execution_realism": "model_portfolio_no_broker_fills",
            "point_in_time_validity": "PARTIAL_PENDING_FR_028",
            "timing_semantics": bundle.shadow_performance.get("return_convention", "UNKNOWN"),
            "nav": perf.get("nav"),
            "daily_return": perf.get("daily_return"),
            "weights_count": perf.get("weights_count"),
        }
    strategy_entries[BENCHMARK_ID] = {
        "strategy_name": "SPY",
        "nav_surface_type": "BENCHMARK_COMPARISON_SURFACE",
        "confidence_classification": "MEDIUM",
        "execution_realism": "benchmark_close_series",
        "point_in_time_validity": "depends_on_price_hydration",
        "timing_semantics": bundle.shadow_performance.get("return_convention", "UNKNOWN"),
    }
    return {
        "schema_version": "research_clarity_surface_metadata_v1",
        "trade_date": bundle.trade_date,
        "metadata_scope": "FR_024_NAV_SURFACE_PROVENANCE",
        "strategies": strategy_entries,
        "confidence_downgrade_rules": [
            "Operational shadow NAV remains LOW confidence until FR-028 timing semantics are governed.",
            "Convenience latest publications inherit confidence from their source and freshness state.",
            "Research backtest NAV must not be blended with broker NAV in one headline claim.",
        ],
    }


def _holdings_snapshot(bundle: SourceBundle, sector_map: dict[str, str]) -> dict[str, Any]:
    strategies = {}
    for strategy_id in sorted(bundle.strategies):
        payload = bundle.strategies[strategy_id]
        holdings = _normalize_holdings(payload, sector_map)
        strategies[strategy_id] = {
            "strategy_name": _strategy_display(strategy_id, payload),
            "nav_surface_type": "OPERATIONAL_SHADOW_NAV",
            "confidence_classification": "LOW",
            "immutable_snapshot": True,
            "holdings": holdings,
            "holdings_count": len(holdings),
        }
    return {
        "schema_version": "immutable_shadow_holdings_snapshot_v1",
        "trade_date": bundle.trade_date,
        "snapshot_status": "IMMUTABLE_ON_WRITE",
        "source_surface": "OPERATIONAL_SHADOW_NAV",
        "strategies": strategies,
    }


def _weights_snapshot(holdings_snapshot: dict[str, Any]) -> dict[str, Any]:
    strategies = {}
    for strategy_id, payload in sorted(holdings_snapshot["strategies"].items()):
        weights = {
            holding["ticker"]: holding["target_weight"]
            for holding in payload["holdings"]
        }
        gross = round(sum(weights.values()), 10)
        strategies[strategy_id] = {
            "strategy_name": payload["strategy_name"],
            "nav_surface_type": payload["nav_surface_type"],
            "confidence_classification": payload["confidence_classification"],
            "target_weights": dict(sorted(weights.items())),
            "gross_weight": gross,
            "cash_weight": round(max(0.0, 1.0 - gross), 10),
        }
    return {
        "schema_version": "immutable_shadow_weights_snapshot_v1",
        "trade_date": holdings_snapshot["trade_date"],
        "snapshot_status": "IMMUTABLE_ON_WRITE",
        "source_surface": "OPERATIONAL_SHADOW_NAV",
        "strategies": strategies,
    }


def _strategy_exposure(holdings: list[dict[str, Any]], turnover: float | None) -> dict[str, Any]:
    weights = [float(item["target_weight"]) for item in holdings]
    sector_exposure: dict[str, float] = {}
    weighted_momentum = 0.0
    momentum_weight = 0.0
    weighted_holding_period = 0.0
    holding_period_weight = 0.0
    for item in holdings:
        weight = float(item["target_weight"])
        sector = str(item.get("sector") or "UNKNOWN")
        sector_exposure[sector] = sector_exposure.get(sector, 0.0) + weight
        if item.get("momentum_score") is not None:
            weighted_momentum += weight * float(item["momentum_score"])
            momentum_weight += weight
        if item.get("estimated_holding_period_days") is not None:
            weighted_holding_period += weight * float(item["estimated_holding_period_days"])
            holding_period_weight += weight
    sector_exposure = {sector: round(value, 10) for sector, value in sorted(sector_exposure.items())}
    known_sector_values = [
        value for sector, value in sector_exposure.items() if sector not in {"UNKNOWN", ""}
    ]
    max_sector = max(known_sector_values, default=0.0)
    max_weight = max(weights, default=0.0)
    top3 = round(sum(sorted(weights, reverse=True)[:3]), 10)
    hhi = round(sum(weight * weight for weight in weights), 10)
    concentration_score = "HIGH" if max_weight >= 0.2 or top3 >= 0.6 else "MEDIUM" if top3 >= 0.4 else "LOW"
    return {
        "holdings_count": len(holdings),
        "max_position_weight": round(max_weight, 10),
        "top3_concentration": top3,
        "weight_hhi": hhi,
        "sector_exposure": sector_exposure,
        "max_sector_exposure": round(max_sector, 10),
        "unknown_sector_exposure": round(sector_exposure.get("UNKNOWN", 0.0), 10),
        "momentum_sensitivity_proxy": round(weighted_momentum / momentum_weight, 10) if momentum_weight else None,
        "holding_period_proxy_days": round(weighted_holding_period / holding_period_weight, 2) if holding_period_weight else None,
        "turnover_proxy": turnover,
        "liquidity_proxy": "UNKNOWN_NO_ADV_SOURCE",
        "volatility_proxy": "UNKNOWN_NO_VOL_SOURCE",
        "concentration_score": concentration_score,
    }


def _exposures_snapshot(bundle: SourceBundle, holdings_snapshot: dict[str, Any]) -> dict[str, Any]:
    strategies = {}
    for strategy_id, payload in sorted(holdings_snapshot["strategies"].items()):
        raw = bundle.strategies.get(strategy_id, {})
        turnover = raw.get("expected_turnover")
        if turnover is not None:
            turnover = float(turnover)
        strategies[strategy_id] = {
            "strategy_name": payload["strategy_name"],
            "nav_surface_type": "OPERATIONAL_SHADOW_NAV",
            "confidence_classification": "LOW",
            **_strategy_exposure(payload["holdings"], turnover),
        }
    return {
        "schema_version": "shadow_exposures_snapshot_v1",
        "trade_date": bundle.trade_date,
        "source_surface": "OPERATIONAL_SHADOW_NAV",
        "strategies": strategies,
    }


def _rebalance_delta(bundle: SourceBundle, weights_snapshot: dict[str, Any]) -> dict[str, Any]:
    previous_date = bundle.shadow_performance.get("previous_trade_date") or bundle.comparison.get("delta", {}).get("previous_date")
    source_status = bundle.shadow_performance.get("status") or bundle.comparison.get("delta", {}).get("status") or "UNKNOWN"
    strategies = {}
    for strategy_id, payload in sorted(weights_snapshot["strategies"].items()):
        strategies[strategy_id] = {
            "strategy_name": payload["strategy_name"],
            "delta_status": source_status,
            "previous_trade_date": previous_date,
            "turnover_proxy": bundle.strategies.get(strategy_id, {}).get("expected_turnover"),
            "interpretation": "No prior immutable snapshot in this bundle; deltas are advisory unless prior evidence is supplied."
            if source_status == "NO_PRIOR"
            else "Delta status inherited from shadow source artifact.",
        }
    return {
        "schema_version": "shadow_rebalance_delta_v1",
        "trade_date": bundle.trade_date,
        "source_surface": "OPERATIONAL_SHADOW_NAV",
        "delta_basis": source_status,
        "strategies": strategies,
    }


def _risk_flags(exposures: dict[str, Any]) -> dict[str, Any]:
    flags = []
    for strategy_id, payload in sorted(exposures["strategies"].items()):
        if payload["concentration_score"] == "HIGH":
            flags.append(
                {
                    "strategy_id": strategy_id,
                    "severity": "HIGH",
                    "flag": "POSITION_CONCENTRATION",
                    "evidence": {
                        "max_position_weight": payload["max_position_weight"],
                        "top3_concentration": payload["top3_concentration"],
                    },
                }
            )
        if payload["max_sector_exposure"] >= 0.5:
            flags.append(
                {
                    "strategy_id": strategy_id,
                    "severity": "MEDIUM",
                    "flag": "SECTOR_CONCENTRATION",
                    "evidence": {"max_sector_exposure": payload["max_sector_exposure"]},
                }
            )
        if payload.get("momentum_sensitivity_proxy") is not None and payload["momentum_sensitivity_proxy"] > 1.5:
            flags.append(
                {
                    "strategy_id": strategy_id,
                    "severity": "MEDIUM",
                    "flag": "MOMENTUM_FACTOR_SENSITIVITY",
                    "evidence": {"momentum_sensitivity_proxy": payload["momentum_sensitivity_proxy"]},
                }
            )
    return {
        "schema_version": "factor_risk_flags_v1",
        "trade_date": exposures["trade_date"],
        "source_surface": "OPERATIONAL_SHADOW_NAV",
        "confidence_classification": "LOW",
        "flags": flags,
    }


def _exposure_summary(exposures: dict[str, Any], flags: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "exposure_summary_v1",
        "trade_date": exposures["trade_date"],
        "source_surface": "OPERATIONAL_SHADOW_NAV",
        "confidence_classification": "LOW",
        "strategies": exposures["strategies"],
        "risk_flag_count": len(flags["flags"]),
        "interpretation": "Exposure telemetry is advisory and supports operator interpretation; it does not change execution or promotion logic.",
    }


def _concentration_monitor(exposures: dict[str, Any]) -> dict[str, Any]:
    rows = []
    for strategy_id, payload in sorted(exposures["strategies"].items()):
        rows.append(
            {
                "strategy_id": strategy_id,
                "strategy_name": payload["strategy_name"],
                "holdings_count": payload["holdings_count"],
                "max_position_weight": payload["max_position_weight"],
                "top3_concentration": payload["top3_concentration"],
                "max_sector_exposure": payload["max_sector_exposure"],
                "concentration_score": payload["concentration_score"],
            }
        )
    return {
        "schema_version": "concentration_monitor_v1",
        "trade_date": exposures["trade_date"],
        "source_surface": "OPERATIONAL_SHADOW_NAV",
        "confidence_classification": "LOW",
        "strategies": rows,
    }


def _exposure_drift_summary(exposures: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "exposure_drift_summary_v1",
        "trade_date": exposures["trade_date"],
        "source_surface": "OPERATIONAL_SHADOW_NAV",
        "confidence_classification": "LOW",
        "drift_status": "BASELINE_ONLY",
        "interpretation": "Single-date bundle establishes a baseline; multi-day drift requires future immutable snapshots.",
        "strategy_count": len(exposures["strategies"]),
    }


def _infer_regime(bundle: SourceBundle) -> dict[str, Any]:
    explicit = bundle.comparison.get("regime")
    if isinstance(explicit, dict):
        return explicit
    return {
        "risk": "UNKNOWN",
        "volatility": "UNKNOWN",
        "trend": "UNKNOWN",
        "breadth": "UNKNOWN",
        "regime_source": "not_present_in_shadow_artifact",
    }


def _regime_artifacts(bundle: SourceBundle, exposures: dict[str, Any]) -> dict[str, dict[str, Any]]:
    regime = _infer_regime(bundle)
    performance = bundle.shadow_performance.get("strategies", {})
    breakdown = {
        "schema_version": "regime_performance_breakdown_v1",
        "trade_date": bundle.trade_date,
        "source_surface": "OPERATIONAL_SHADOW_NAV",
        "confidence_classification": "LOW",
        "regime": regime,
        "strategies": {},
    }
    fragility = {
        "schema_version": "regime_fragility_report_v1",
        "trade_date": bundle.trade_date,
        "source_surface": "OPERATIONAL_SHADOW_NAV",
        "confidence_classification": "LOW",
        "fragility_indicators": [],
    }
    matrix = {
        "schema_version": "regime_exposure_matrix_v1",
        "trade_date": bundle.trade_date,
        "source_surface": "OPERATIONAL_SHADOW_NAV",
        "confidence_classification": "LOW",
        "regime": regime,
        "strategies": {},
    }
    attribution = {
        "schema_version": "attribution_by_regime_v1",
        "trade_date": bundle.trade_date,
        "source_surface": "OPERATIONAL_SHADOW_NAV",
        "confidence_classification": "LOW",
        "regime": regime,
        "strategies": {},
    }
    for strategy_id, exposure in sorted(exposures["strategies"].items()):
        perf = performance.get(strategy_id, {}) if isinstance(performance, dict) else {}
        breakdown["strategies"][strategy_id] = {
            "strategy_name": exposure["strategy_name"],
            "daily_return": perf.get("daily_return"),
            "nav": perf.get("nav"),
            "turnover_proxy": exposure.get("turnover_proxy"),
            "regime_confidence": "LOW_IF_REGIME_SOURCE_UNKNOWN",
        }
        matrix["strategies"][strategy_id] = {
            "max_position_weight": exposure["max_position_weight"],
            "top3_concentration": exposure["top3_concentration"],
            "max_sector_exposure": exposure["max_sector_exposure"],
            "momentum_sensitivity_proxy": exposure["momentum_sensitivity_proxy"],
        }
        attribution["strategies"][strategy_id] = {
            "contribution_basis": "target_weight_x_shadow_daily_return_proxy",
            "confidence_classification": "LOW",
            "caveat": "Position-level daily returns are not present; this is not realized Brinson attribution.",
        }
        if exposure["concentration_score"] == "HIGH":
            fragility["fragility_indicators"].append(
                {
                    "strategy_id": strategy_id,
                    "indicator": "CONCENTRATION_AMPLIFIES_REGIME_DEPENDENCY",
                    "severity": "HIGH",
                    "evidence": {
                        "top3_concentration": exposure["top3_concentration"],
                        "max_position_weight": exposure["max_position_weight"],
                    },
                }
            )
    return {
        "regime_performance_breakdown.json": breakdown,
        "regime_fragility_report.json": fragility,
        "regime_exposure_matrix.json": matrix,
        "attribution_by_regime.json": attribution,
    }


def _summary_markdown(trade_date: str, artifacts: dict[str, dict[str, Any]]) -> str:
    flags = artifacts["factor_risk_flags.json"]["flags"]
    surfaces = artifacts["nav_surface_registry.json"]["surfaces"]
    concentration_rows = artifacts["concentration_monitor.json"]["strategies"]
    lines = [
        f"# Research Clarity Wave Summary - {trade_date}",
        "",
        "## Executive Summary",
        "",
        "This additive bundle separates truth surfaces, persists immutable shadow portfolio snapshots, and surfaces exposure/regime caveats for operator interpretation.",
        "",
        "No accounting semantics, execution behavior, broker behavior, cron behavior, dashboard behavior, or promotion logic changed.",
        "",
        "## NAV Surface Interpretation",
        "",
    ]
    for name in sorted(surfaces):
        surface = surfaces[name]
        lines.append(f"- `{name}`: {surface['classification']} / confidence `{surface['confidence']}`.")
    lines.extend(["", "## Concentration Overview", ""])
    for row in concentration_rows:
        lines.append(
            f"- `{row['strategy_id']}`: max weight {row['max_position_weight']:.2f}, "
            f"top-3 {row['top3_concentration']:.2f}, score `{row['concentration_score']}`."
        )
    lines.extend(["", "## Risk Flags", ""])
    if flags:
        for flag in flags:
            lines.append(f"- `{flag['strategy_id']}`: `{flag['flag']}` severity `{flag['severity']}`.")
    else:
        lines.append("- No threshold risk flags fired in this bundle.")
    lines.extend(
        [
            "",
            "## Confidence Caveats",
            "",
            f"- {LOW_CONFIDENCE_REASON}",
            "- Regime and attribution outputs are advisory research surfaces unless source artifacts contain complete point-in-time evidence.",
            "- Latest/convenience publications must be verified against canonical dated source artifacts before use as governance evidence.",
            "",
        ]
    )
    return "\n".join(lines)


def _manifest(trade_date: str, output_dir: Path, artifact_names: list[str]) -> dict[str, Any]:
    entries = []
    for name in sorted(artifact_names):
        path = output_dir / name
        entries.append(
            {
                "artifact": name,
                "sha256": _hash_text(path.read_text(encoding="utf-8")),
                "immutable": True,
            }
        )
    return {
        "schema_version": "research_clarity_manifest_v1",
        "trade_date": trade_date,
        "manifest_status": "IMMUTABLE_CONTENT_HASHED",
        "artifact_count": len(entries),
        "artifacts": entries,
    }


def build_research_clarity_wave(
    repo_root: Path,
    trade_date: str | None = None,
    source_dir: Path | None = None,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    """Hydrate shadow artifacts into additive research clarity outputs."""

    actual_trade_date = trade_date or date.today().isoformat()
    bundle = _load_sources(repo_root, actual_trade_date, source_dir)
    sector_map = _load_sector_map(repo_root)
    actual_output_dir = output_dir or repo_root / "outputs" / "research_clarity" / actual_trade_date

    holdings = _holdings_snapshot(bundle, sector_map)
    weights = _weights_snapshot(holdings)
    exposures = _exposures_snapshot(bundle, holdings)
    flags = _risk_flags(exposures)

    artifacts: dict[str, dict[str, Any]] = {
        "nav_surface_registry.json": _surface_registry(bundle),
        "surface_metadata.json": _surface_metadata(bundle),
        "holdings_snapshot.json": holdings,
        "weights_snapshot.json": weights,
        "exposures_snapshot.json": exposures,
        "rebalance_delta.json": _rebalance_delta(bundle, weights),
        "exposure_summary.json": _exposure_summary(exposures, flags),
        "factor_risk_flags.json": flags,
        "concentration_monitor.json": _concentration_monitor(exposures),
        "exposure_drift_summary.json": _exposure_drift_summary(exposures),
    }
    artifacts.update(_regime_artifacts(bundle, exposures))

    written_names: list[str] = []
    for name, payload in sorted(artifacts.items()):
        _write_json_immutable(actual_output_dir / name, payload)
        written_names.append(name)

    summary = _summary_markdown(actual_trade_date, artifacts)
    _write_immutable(actual_output_dir / "research_clarity_summary.md", summary)
    written_names.append("research_clarity_summary.md")

    manifest = _manifest(actual_trade_date, actual_output_dir, written_names)
    _write_json_immutable(actual_output_dir / "manifest.json", manifest)

    return {
        "trade_date": actual_trade_date,
        "output_dir": str(actual_output_dir),
        "artifact_count": len(written_names) + 1,
        "artifacts": sorted(written_names + ["manifest.json"]),
        "source_dir": str(bundle.source_dir),
        "source_count": len(bundle.source_hashes),
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build additive research clarity artifacts.")
    parser.add_argument("--trade-date", default=date.today().isoformat())
    parser.add_argument("--source-dir", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    result = build_research_clarity_wave(
        repo_root=args.repo_root,
        trade_date=args.trade_date,
        source_dir=args.source_dir,
        output_dir=args.output_dir,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
