"""Build Phoenix Phase C liquidity/capacity validation artifact.

Research-only. This script validates whether the Phoenix Phase B risk-shaped
candidate can be evaluated against realistic liquidity constraints using
repo-local PIT inputs. It does not activate Phoenix or alter live signals,
allocation, execution, broker behavior, cron, or promotion thresholds.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.research.build_phoenix_crisis_recovery_evidence import _round

SCHEMA_VERSION = "caerus_phoenix_phase_c_liquidity_capacity_v1"
REQUIRED_LIQUIDITY_FIELDS = ("volume", "dollar_volume", "ADV_20", "ADV_60", "dollar_ADV_20", "dollar_ADV_60")
DEFAULT_PHASE_B_PATTERN = "outputs/research/phoenix_evidence/phoenix_phase_b_risk_shaping_{date}.json"
DEFAULT_LIQUIDITY_PANEL = "outputs/research/pit_liquidity/pit_liquidity_panel.csv"
DEFAULT_LIQUIDITY_MANIFEST = "outputs/research/pit_liquidity/manifest.json"
DEFAULT_CAPITAL = 1_000_000.0


def _sha256(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _source_column_inventory(panel_path: Path) -> dict[str, Any]:
    if not panel_path.exists():
        return {
            "panel_path": str(panel_path),
            "observed_columns": [],
            "required_liquidity_fields": list(REQUIRED_LIQUIDITY_FIELDS),
            "missing_required_liquidity_fields": list(REQUIRED_LIQUIDITY_FIELDS),
            "decision_grade_liquidity_available": False,
            "row_count": 0,
        }
    with panel_path.open(newline="", encoding="utf-8") as fh:
        observed = next(csv.reader(fh), [])
    missing = [field for field in REQUIRED_LIQUIDITY_FIELDS if field not in observed]
    return {
        "panel_path": str(panel_path),
        "observed_columns": sorted(observed),
        "required_liquidity_fields": list(REQUIRED_LIQUIDITY_FIELDS),
        "missing_required_liquidity_fields": missing,
        "decision_grade_liquidity_available": not missing,
        "row_count": sum(1 for _ in panel_path.open("r", encoding="utf-8")) - 1,
    }


def _load_phase_b(repo: Path, date: str) -> tuple[Path, dict[str, Any]]:
    path = repo / DEFAULT_PHASE_B_PATTERN.format(date=date)
    if not path.exists():
        raise FileNotFoundError(f"Phoenix Phase B risk-shaping artifact not found: {path}")
    return path, json.loads(path.read_text(encoding="utf-8"))


def _candidate_events(phase_b: dict[str, Any]) -> tuple[str | None, list[dict[str, Any]]]:
    best = phase_b.get("best_research_candidate") or {}
    variant_id = best.get("variant_id")
    events = (phase_b.get("event_diagnostics_by_variant") or {}).get(str(variant_id), []) if variant_id else []
    return variant_id, [event for event in events if event.get("status") == "OK"]


def _candidate_inventory(events: list[dict[str, Any]], panel: pd.DataFrame | None = None, *, capital: float = DEFAULT_CAPITAL) -> dict[str, Any]:
    panel_lookup: dict[tuple[str, str], dict[str, Any]] = {}
    if panel is not None and len(panel):
        for row in panel.to_dict("records"):
            panel_lookup[(str(row.get("ticker")), str(row.get("date"))[:10])] = row
    rows: list[dict[str, Any]] = []
    tickers: set[str] = set()
    gross_exposures: list[float] = []
    turnovers: list[float] = []
    vols: list[float] = []
    adv_participations: list[float] = []
    dollar_adv_participations: list[float] = []
    capacities_5: list[float] = []
    capacities_10: list[float] = []
    degradation: list[float] = []
    shortfalls: list[float] = []
    measured = 0
    for event in events:
        gross = event.get("gross_exposure")
        turnover = event.get("turnover")
        if gross is not None:
            gross_exposures.append(float(gross))
        if turnover is not None:
            turnovers.append(float(turnover))
        for selected in event.get("selected") or []:
            ticker = str(selected.get("ticker") or "")
            if ticker:
                tickers.add(ticker)
            if selected.get("vol_20d_ann_at_entry") is not None:
                vols.append(float(selected["vol_20d_ann_at_entry"]))
            entry_date = str(event.get("entry_date") or "")[:10]
            weight = abs(float(selected.get("target_weight") or 0.0))
            liq = panel_lookup.get((ticker, entry_date))
            reason_codes: list[str] = []
            adv_participation = None
            dollar_adv_participation = None
            position_dollar_capacity_5 = None
            position_dollar_capacity_10 = None
            position_liquidity = None
            degradation_ratio = None
            implementation_shortfall_bps = None
            if not liq:
                reason_codes.append("liquidity_row_missing")
            else:
                closeadj = _finite(liq.get("closeadj"))
                volume = _finite(liq.get("volume"))
                adv_20 = _finite(liq.get("ADV_20"))
                dollar_adv_20 = _finite(liq.get("dollar_ADV_20"))
                dollar_adv_60 = _finite(liq.get("dollar_ADV_60"))
                position_dollars = capital * weight
                if closeadj and adv_20 and dollar_adv_20 and weight > 0:
                    shares = position_dollars / closeadj
                    adv_participation = shares / adv_20
                    dollar_adv_participation = position_dollars / dollar_adv_20
                    position_dollar_capacity_5 = (0.05 * dollar_adv_20) / weight
                    position_dollar_capacity_10 = (0.10 * dollar_adv_20) / weight
                    position_liquidity = dollar_adv_20 / position_dollars if position_dollars else None
                    implementation_shortfall_bps = 10.0 + (50.0 * math.sqrt(max(dollar_adv_participation, 0.0)))
                    adv_participations.append(adv_participation)
                    dollar_adv_participations.append(dollar_adv_participation)
                    capacities_5.append(position_dollar_capacity_5)
                    capacities_10.append(position_dollar_capacity_10)
                    shortfalls.append(implementation_shortfall_bps)
                    measured += 1
                else:
                    reason_codes.append("liquidity_values_incomplete")
                if dollar_adv_20 and dollar_adv_60:
                    degradation_ratio = (dollar_adv_20 / dollar_adv_60) - 1.0
                    degradation.append(degradation_ratio)
                if volume is None:
                    reason_codes.append("volume_null")
            if not reason_codes:
                reason_codes = ["ok"]
            rows.append(
                {
                    "window_id": event.get("window_id"),
                    "entry_date": entry_date,
                    "ticker": ticker,
                    "target_weight": selected.get("target_weight"),
                    "vol_20d_ann_at_entry": selected.get("vol_20d_ann_at_entry"),
                    "liquidity_fields_available": reason_codes == ["ok"],
                    "adv_participation": _round(adv_participation) if adv_participation is not None else None,
                    "dollar_adv_participation": _round(dollar_adv_participation) if dollar_adv_participation is not None else None,
                    "position_liquidity": _round(position_liquidity) if position_liquidity is not None else None,
                    "capacity_at_5pct_adv": _round(position_dollar_capacity_5) if position_dollar_capacity_5 is not None else None,
                    "capacity_at_10pct_adv": _round(position_dollar_capacity_10) if position_dollar_capacity_10 is not None else None,
                    "crisis_liquidity_degradation": _round(degradation_ratio) if degradation_ratio is not None else None,
                    "implementation_shortfall_bps": _round(implementation_shortfall_bps) if implementation_shortfall_bps is not None else None,
                    "reason_codes": reason_codes,
                }
            )
    row_count = len(rows)
    return {
        "event_count": len(events),
        "unique_ticker_count": len(tickers),
        "unique_tickers_sample": sorted(tickers)[:50],
        "candidate_row_count": row_count,
        "measured_candidate_row_count": measured,
        "measurement_coverage": _round(measured / row_count) if row_count else None,
        "average_gross_exposure": _mean(gross_exposures),
        "average_event_turnover": _mean(turnovers),
        "average_candidate_vol_20d_ann": _mean(vols),
        "max_candidate_vol_20d_ann": max(vols) if vols else None,
        "adv_participation": _distribution(adv_participations),
        "dollar_adv_participation": _distribution(dollar_adv_participations),
        "capacity_at_5pct_adv": _distribution(capacities_5),
        "capacity_at_10pct_adv": _distribution(capacities_10),
        "crisis_liquidity_degradation": _distribution(degradation),
        "implementation_shortfall_bps": _distribution(shortfalls),
        "candidate_rows_sample": rows[:100],
    }


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return _round(sum(values) / len(values))


def _finite(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(out) or out <= 0:
        return None
    return out


def _distribution(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"count": 0, "min": None, "median": None, "mean": None, "max": None}
    series = pd.Series(values)
    return {
        "count": int(len(values)),
        "min": _round(float(series.min())),
        "median": _round(float(series.median())),
        "mean": _round(float(series.mean())),
        "p95": _round(float(series.quantile(0.95))),
        "max": _round(float(series.max())),
    }


def _blocked_metric(metric: str, *, detail: str = "PIT volume/dollar-volume source missing") -> dict[str, Any]:
    return {
        "metric": metric,
        "status": "BLOCKED",
        "value": None,
        "reason_codes": ["pit_volume_source_missing"],
        "detail": detail,
    }


def _classification(source_inventory: dict[str, Any], phase_b: dict[str, Any], candidate_inventory: dict[str, Any] | None = None) -> dict[str, Any]:
    phase_b_conclusion = phase_b.get("readiness_conclusion") or {}
    if not source_inventory.get("decision_grade_liquidity_available"):
        return {
            "classification": "PENDING_LIQUIDITY",
            "is_shadow_ready": False,
            "is_not_viable": False,
            "reason_codes": ["pit_volume_source_missing", "capacity_not_decision_grade"],
            "rationale": (
                "Phase B found risk-shaped Phoenix candidates, but ADV participation, "
                "capacity, crisis liquidity degradation, slippage, and implementation "
                "shortfall cannot be measured from the repo-local close-only PIT cache."
            ),
            "phase_b_context": phase_b_conclusion,
        }
    candidate_inventory = candidate_inventory or {}
    coverage = candidate_inventory.get("measurement_coverage") or 0.0
    cap5 = ((candidate_inventory.get("capacity_at_5pct_adv") or {}).get("min"))
    max_participation = ((candidate_inventory.get("dollar_adv_participation") or {}).get("max"))
    max_shortfall = ((candidate_inventory.get("implementation_shortfall_bps") or {}).get("max"))
    if coverage < 0.95:
        return {
            "classification": "PENDING_LIQUIDITY",
            "is_shadow_ready": False,
            "is_not_viable": False,
            "reason_codes": ["liquidity_measurement_coverage_incomplete"],
            "rationale": "OHLCV source exists, but not enough selected Phoenix candidate rows have PIT liquidity measurements.",
            "phase_b_context": phase_b_conclusion,
        }
    if cap5 is not None and cap5 < DEFAULT_CAPITAL:
        return {
            "classification": "NOT_VIABLE",
            "is_shadow_ready": False,
            "is_not_viable": True,
            "reason_codes": ["capacity_below_5pct_adv_policy"],
            "rationale": "Measured PIT liquidity indicates at least one selected Phoenix position cannot support the reference capital at 5% ADV.",
            "phase_b_context": phase_b_conclusion,
        }
    return {
        "classification": "SHADOW_READY",
        "is_shadow_ready": True,
        "is_not_viable": False,
        "reason_codes": ["decision_grade_liquidity_measured", "shadow_readiness_review_candidate"],
        "rationale": (
            "Decision-grade PIT liquidity was measured for the Phoenix Phase B candidate. "
            f"At reference capital ${DEFAULT_CAPITAL:,.0f}, max dollar ADV participation is "
            f"{_round(max_participation) if max_participation is not None else None}, "
            f"minimum 5% ADV capacity is ${_round(cap5) if cap5 is not None else None}, "
            f"and max shortfall proxy is {_round(max_shortfall) if max_shortfall is not None else None} bps."
        ),
        "phase_b_context": phase_b_conclusion,
    }


def build_artifact(*, repo: Path, output_date: str, liquidity_panel_path: Path | None = None, capital: float = DEFAULT_CAPITAL) -> dict[str, Any]:
    phase_b_path, phase_b = _load_phase_b(repo, output_date)
    variant_id, events = _candidate_events(phase_b)
    panel_path = liquidity_panel_path or repo / DEFAULT_LIQUIDITY_PANEL
    if not panel_path.is_absolute():
        panel_path = repo / panel_path
    panel = pd.read_csv(panel_path) if panel_path.exists() else pd.DataFrame()
    source_inventory = _source_column_inventory(panel_path)
    candidate_inventory = _candidate_inventory(events, panel, capital=capital)
    classification = _classification(source_inventory, phase_b, candidate_inventory)
    manifest_path = repo / DEFAULT_LIQUIDITY_MANIFEST
    payload = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": pd.Timestamp.now("UTC").isoformat(),
        "artifact_date": output_date,
        "governance_label": "RESEARCH_ONLY",
        "execution_impact": "NON_EXECUTIONAL",
        "production_impact": "none",
        "decision_state": "liquidity_capacity_validated",
        "phase_b_input": {
            "path": str(phase_b_path),
            "sha256": _sha256(phase_b_path),
            "best_variant_id": variant_id,
            "phase_b_classification": (phase_b.get("readiness_conclusion") or {}).get("classification"),
        },
        "pit_safe_inputs": {
            "source": "repo-local Sharadar SEP OHLCV cache, PIT liquidity panel, and FR-068 PIT large-cap family lineage",
            "source_column_inventory": source_inventory,
            "liquidity_panel": {
                "path": str(panel_path),
                "sha256": _sha256(panel_path) if panel_path.exists() else None,
            },
            "liquidity_manifest": {
                "path": str(manifest_path),
                "sha256": _sha256(manifest_path) if manifest_path.exists() else None,
            },
        },
        "reference_capital": capital,
        "candidate_inventory": candidate_inventory,
        "measurements": {
            "adv_participation": {
                "metric": "adv_participation",
                "status": "MEASURED" if candidate_inventory["adv_participation"]["count"] else "BLOCKED",
                "value": candidate_inventory["adv_participation"],
                "reason_codes": ["pit_liquidity_panel_available"],
            },
            "dollar_adv_participation": {
                "metric": "dollar_adv_participation",
                "status": "MEASURED" if candidate_inventory["dollar_adv_participation"]["count"] else "BLOCKED",
                "value": candidate_inventory["dollar_adv_participation"],
                "reason_codes": ["pit_liquidity_panel_available"],
            },
            "position_liquidity": {
                "metric": "position_liquidity",
                "status": "MEASURED" if candidate_inventory["dollar_adv_participation"]["count"] else "BLOCKED",
                "value": {
                    "capacity_at_5pct_adv": candidate_inventory["capacity_at_5pct_adv"],
                    "capacity_at_10pct_adv": candidate_inventory["capacity_at_10pct_adv"],
                },
                "reason_codes": ["pit_liquidity_panel_available"],
            },
            "turnover": {
                "metric": "turnover",
                "status": "MEASURED_FROM_PHASE_B",
                "value": candidate_inventory.get("average_event_turnover"),
                "reason_codes": ["phase_b_event_turnover_available"],
                "detail": "Turnover measured as Phase B event basket entry/exit turnover; not ADV-normalized.",
            },
            "slippage_sensitivity": {
                "metric": "slippage_sensitivity",
                "status": "MEASURED_PROXY" if candidate_inventory["implementation_shortfall_bps"]["count"] else "BLOCKED",
                "value": candidate_inventory["implementation_shortfall_bps"],
                "reason_codes": ["sqrt_participation_proxy"],
            },
            "capacity_limits": {
                "metric": "capacity_limits",
                "status": "MEASURED" if candidate_inventory["capacity_at_5pct_adv"]["count"] else "BLOCKED",
                "value": {
                    "at_5pct_adv": candidate_inventory["capacity_at_5pct_adv"],
                    "at_10pct_adv": candidate_inventory["capacity_at_10pct_adv"],
                },
                "reason_codes": ["pit_liquidity_panel_available"],
            },
            "crisis_period_liquidity_degradation": {
                "metric": "crisis_period_liquidity_degradation",
                "status": "MEASURED" if candidate_inventory["crisis_liquidity_degradation"]["count"] else "BLOCKED",
                "value": candidate_inventory["crisis_liquidity_degradation"],
                "reason_codes": ["adv20_vs_adv60"],
            },
            "implementation_shortfall": {
                "metric": "implementation_shortfall",
                "status": "MEASURED_PROXY" if candidate_inventory["implementation_shortfall_bps"]["count"] else "BLOCKED",
                "value": candidate_inventory["implementation_shortfall_bps"],
                "reason_codes": ["sqrt_participation_proxy"],
            },
        },
        "slippage_sensitivity": {
            "decision_grade": True,
            "reason_codes": ["adv_participation_proxy_available", "spread_model_not_required_for_phase_c_gate"],
            "proxy": "10bps base plus 50bps times square-root of dollar ADV participation",
        },
        "capacity_limits": {
            "decision_grade": bool(candidate_inventory["capacity_at_5pct_adv"]["count"]),
            "reason_codes": ["capacity_at_5pct_and_10pct_adv_measured"],
            "reference_capital": capital,
        },
        "classification": classification,
        "output": classification["classification"],
        "non_goals": [
            "no Phoenix activation",
            "no live signals",
            "no allocation changes",
            "no execution changes",
            "no broker behavior changes",
            "no promotion threshold changes",
        ],
    }
    return payload


def write_artifact(repo: Path, payload: dict[str, Any]) -> tuple[Path, Path]:
    out_dir = repo / "outputs" / "research" / "phoenix_evidence"
    date = payload["artifact_date"]
    json_path = out_dir / f"phoenix_phase_c_liquidity_capacity_{date}.json"
    md_path = out_dir / f"phoenix_phase_c_liquidity_capacity_{date}.md"
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    classification = payload.get("classification") or {}
    source = ((payload.get("pit_safe_inputs") or {}).get("source_column_inventory") or {})
    lines = [
        "# Phoenix Phase C Liquidity & Capacity Validation",
        "",
        f"Date: `{date}`",
        "",
        "RESEARCH_ONLY / NO_RUNTIME_CHANGE",
        "",
        f"Output: `{payload.get('output')}`",
        f"Shadow ready: `{classification.get('is_shadow_ready')}`",
        f"Reason codes: `{classification.get('reason_codes')}`",
        "",
        "## Source Inventory",
        "",
        f"- Observed columns: `{source.get('observed_columns')}`",
        f"- Missing liquidity fields: `{source.get('missing_required_liquidity_fields')}`",
        f"- Panel rows: `{source.get('row_count')}`",
        "",
        "## Measurements",
        "",
        f"- Candidate rows measured: `{payload.get('candidate_inventory', {}).get('measured_candidate_row_count')}` / `{payload.get('candidate_inventory', {}).get('candidate_row_count')}`",
        f"- Measurement coverage: `{payload.get('candidate_inventory', {}).get('measurement_coverage')}`",
        f"- Max dollar ADV participation: `{payload.get('candidate_inventory', {}).get('dollar_adv_participation', {}).get('max')}`",
        f"- Minimum capacity at 5% ADV: `{payload.get('candidate_inventory', {}).get('capacity_at_5pct_adv', {}).get('min')}`",
        f"- Minimum capacity at 10% ADV: `{payload.get('candidate_inventory', {}).get('capacity_at_10pct_adv', {}).get('min')}`",
        f"- Max implementation shortfall proxy bps: `{payload.get('candidate_inventory', {}).get('implementation_shortfall_bps', {}).get('max')}`",
        "",
        "## Interpretation",
        "",
        str(classification.get("rationale") or ""),
    ]
    md_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return json_path, md_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--date", default=pd.Timestamp.now("UTC").strftime("%Y-%m-%d"))
    parser.add_argument("--liquidity-panel", type=Path, default=None)
    parser.add_argument("--capital", type=float, default=DEFAULT_CAPITAL)
    args = parser.parse_args()
    repo = args.repo_root.resolve()
    payload = build_artifact(repo=repo, output_date=args.date, liquidity_panel_path=args.liquidity_panel, capital=args.capital)
    json_path, md_path = write_artifact(repo, payload)
    print(json.dumps({
        "status": "OK",
        "json_path": str(json_path),
        "markdown_path": str(md_path),
        "output": payload.get("output"),
        "reason_codes": (payload.get("classification") or {}).get("reason_codes"),
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
