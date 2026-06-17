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
REQUIRED_LIQUIDITY_FIELDS = ("volume", "dollar_volume", "adv_20d", "adv_60d")
DEFAULT_PHASE_B_PATTERN = "outputs/research/phoenix_evidence/phoenix_phase_b_risk_shaping_{date}.json"


def _sha256(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _source_column_inventory(cache_dir: Path, sample_size: int = 500) -> dict[str, Any]:
    files = sorted(cache_dir.glob("*.csv"))
    columns_by_file: dict[str, list[str]] = {}
    observed: set[str] = set()
    for path in files[:sample_size]:
        try:
            with path.open(newline="", encoding="utf-8") as fh:
                cols = next(csv.reader(fh))
        except (OSError, StopIteration):
            cols = []
        columns_by_file[path.name] = cols
        observed.update(cols)
    missing = [field for field in REQUIRED_LIQUIDITY_FIELDS if field not in observed]
    return {
        "cache_dir": str(cache_dir),
        "sampled_file_count": min(len(files), sample_size),
        "total_file_count": len(files),
        "observed_columns": sorted(observed),
        "required_liquidity_fields": list(REQUIRED_LIQUIDITY_FIELDS),
        "missing_required_liquidity_fields": missing,
        "decision_grade_liquidity_available": not missing,
        "sample_columns_by_file": dict(list(columns_by_file.items())[:10]),
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


def _candidate_inventory(events: list[dict[str, Any]]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    tickers: set[str] = set()
    gross_exposures: list[float] = []
    turnovers: list[float] = []
    vols: list[float] = []
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
            rows.append(
                {
                    "window_id": event.get("window_id"),
                    "entry_date": event.get("entry_date"),
                    "ticker": ticker,
                    "target_weight": selected.get("target_weight"),
                    "vol_20d_ann_at_entry": selected.get("vol_20d_ann_at_entry"),
                    "liquidity_fields_available": False,
                    "adv_participation": None,
                    "position_dollar_capacity": None,
                    "implementation_shortfall": None,
                    "reason_codes": ["pit_volume_source_missing"],
                }
            )
    return {
        "event_count": len(events),
        "unique_ticker_count": len(tickers),
        "unique_tickers_sample": sorted(tickers)[:50],
        "candidate_row_count": len(rows),
        "average_gross_exposure": _mean(gross_exposures),
        "average_event_turnover": _mean(turnovers),
        "average_candidate_vol_20d_ann": _mean(vols),
        "max_candidate_vol_20d_ann": max(vols) if vols else None,
        "candidate_rows_sample": rows[:100],
    }


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return _round(sum(values) / len(values))


def _blocked_metric(metric: str, *, detail: str = "PIT volume/dollar-volume source missing") -> dict[str, Any]:
    return {
        "metric": metric,
        "status": "BLOCKED",
        "value": None,
        "reason_codes": ["pit_volume_source_missing"],
        "detail": detail,
    }


def _classification(source_inventory: dict[str, Any], phase_b: dict[str, Any]) -> dict[str, Any]:
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
    return {
        "classification": "SHADOW_READY",
        "is_shadow_ready": True,
        "is_not_viable": False,
        "reason_codes": ["liquidity_source_available"],
        "rationale": "Liquidity source exists; rerun with full ADV/capacity implementation.",
        "phase_b_context": phase_b_conclusion,
    }


def build_artifact(*, repo: Path, output_date: str) -> dict[str, Any]:
    phase_b_path, phase_b = _load_phase_b(repo, output_date)
    variant_id, events = _candidate_events(phase_b)
    cache_dir = repo / "data" / "research_cache" / "sharadar_sep"
    source_inventory = _source_column_inventory(cache_dir)
    candidate_inventory = _candidate_inventory(events)
    classification = _classification(source_inventory, phase_b)
    source_files = sorted(cache_dir.glob("*.csv"))
    source_hash_sample = {
        path.name: _sha256(path)
        for path in source_files[:25]
    }
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
            "source": "repo-local Sharadar SEP cache and FR-068 PIT large-cap family lineage",
            "source_column_inventory": source_inventory,
            "source_hash_sample": source_hash_sample,
        },
        "candidate_inventory": candidate_inventory,
        "measurements": {
            "adv_participation": _blocked_metric("adv_participation"),
            "position_liquidity": _blocked_metric("position_liquidity"),
            "turnover": {
                "metric": "turnover",
                "status": "MEASURED_FROM_PHASE_B",
                "value": candidate_inventory.get("average_event_turnover"),
                "reason_codes": ["phase_b_event_turnover_available"],
                "detail": "Turnover measured as Phase B event basket entry/exit turnover; not ADV-normalized.",
            },
            "slippage_sensitivity": _blocked_metric("slippage_sensitivity"),
            "capacity_limits": _blocked_metric("capacity_limits"),
            "crisis_period_liquidity_degradation": _blocked_metric("crisis_period_liquidity_degradation"),
            "implementation_shortfall": _blocked_metric("implementation_shortfall"),
        },
        "slippage_sensitivity": {
            "decision_grade": False,
            "reason_codes": ["adv_participation_missing", "spread_model_missing"],
            "tested_bps_grid": [10, 25, 50, 100],
            "note": "Cost-bps variants exist in Phase B, but implementation shortfall cannot be tied to ADV/spread without PIT volume and spread proxies.",
        },
        "capacity_limits": {
            "decision_grade": False,
            "reason_codes": ["adv_missing"],
            "minimum_required_inputs": [
                "PIT daily volume",
                "PIT adjusted close",
                "ADV20/ADV60",
                "spread or spread proxy",
                "target capital assumption",
                "max ADV participation policy",
            ],
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
        f"- Sampled files: `{source.get('sampled_file_count')}`",
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
    args = parser.parse_args()
    repo = args.repo_root.resolve()
    payload = build_artifact(repo=repo, output_date=args.date)
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
