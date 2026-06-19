from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
from collections import Counter
from pathlib import Path
from typing import Any


SCHEMA_DATE = "2026-06-19"
GOVERNANCE_LABEL = "RESEARCH_ONLY"
EXECUTION_IMPACT = "NON_EXECUTIONAL"

FULL_EVIDENCE = "FULL_EVIDENCE"
PARTIAL_EVIDENCE = "PARTIAL_EVIDENCE"
LOW_EVIDENCE = "LOW_EVIDENCE"
NOT_CLASSIFIABLE = "NOT_CLASSIFIABLE"

RECON_VERIFIED = "RECON_VERIFIED"
RECON_PARTIAL = "RECON_PARTIAL"
RECON_MISSING = "RECON_MISSING"
RECON_NOT_RECONSTRUCTABLE = "RECON_NOT_RECONSTRUCTABLE"


def read_json(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_md(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.strip() + "\n", encoding="utf-8")


def relpath(path: Path, repo_root: Path) -> str:
    try:
        return str(path.relative_to(repo_root))
    except ValueError:
        return str(path)


def parse_date(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return dt.date.fromisoformat(text[:10]).isoformat()
    except ValueError:
        return None


def read_csv_summary(path: Path, *, date_fields: tuple[str, ...] = ("date", "trade_date")) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False}
    row_count = 0
    first_date: str | None = None
    last_date: str | None = None
    non_weekday_date_count = 0
    columns: list[str] = []
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            columns = list(reader.fieldnames or [])
            for row in reader:
                row_count += 1
                date_value = None
                for field in date_fields:
                    candidate = parse_date(row.get(field))
                    if candidate:
                        date_value = candidate
                        break
                if date_value:
                    parsed_date = dt.date.fromisoformat(date_value)
                    if parsed_date.weekday() >= 5:
                        non_weekday_date_count += 1
                    first_date = min(first_date, date_value) if first_date else date_value
                    last_date = max(last_date, date_value) if last_date else date_value
    except Exception as exc:
        return {"exists": True, "read_error": str(exc), "path": str(path)}
    return {
        "exists": True,
        "path": str(path),
        "row_count": row_count,
        "columns": columns,
        "first_date": first_date,
        "last_date": last_date,
        "non_weekday_date_count": non_weekday_date_count,
    }


def _dimension(name: str, path: Path | None, repo_root: Path, *, required: bool = True, present: bool | None = None) -> dict[str, Any]:
    exists = bool(path and path.exists()) if present is None else bool(present)
    return {
        "name": name,
        "required": required,
        "present": exists,
        "path": relpath(path, repo_root) if path is not None else None,
        "status": "PRESENT" if exists else ("MISSING" if required else "NOT_APPLICABLE"),
    }


def _first_glob(root: Path, pattern: str) -> Path | None:
    matches = sorted(root.glob(pattern))
    return matches[0] if matches else None


def _first_rglob(root: Path, pattern: str) -> Path | None:
    matches = sorted(root.rglob(pattern))
    return matches[0] if matches else None


def infer_run_trade_date(run_root: Path) -> str | None:
    for candidate in (
        read_json(run_root / "execution_payload.json"),
        read_json(run_root / "operator_summary.json"),
        read_json(run_root / "execution_results.json"),
    ):
        if isinstance(candidate, dict):
            for key in ("trade_date", "date", "as_of_date"):
                parsed = parse_date(candidate.get(key))
                if parsed:
                    return parsed
    parsed = parse_date(run_root.name)
    return parsed


def classify_artifact_dimensions(dimensions: dict[str, dict[str, Any]]) -> str:
    required = [row for row in dimensions.values() if row.get("required")]
    if not required:
        return NOT_CLASSIFIABLE
    present = sum(1 for row in required if row.get("present"))
    if present == len(required):
        return FULL_EVIDENCE
    if present >= max(4, len(required) // 2):
        return PARTIAL_EVIDENCE
    if present > 0:
        return LOW_EVIDENCE
    return NOT_CLASSIFIABLE


def evaluate_run_artifact_coverage(repo_root: Path, run_root: Path) -> dict[str, Any]:
    trade_date = infer_run_trade_date(run_root)
    precompute_payload = (
        repo_root / "outputs" / "precompute" / trade_date / "planned_execution_payload.json"
        if trade_date
        else None
    )
    precompute_required = bool(
        (precompute_payload and precompute_payload.exists())
        or (run_root / "execution_payload.json").exists()
    )
    sleeve_trace = _first_rglob(run_root / "audit", "sleeve_numeric_trace_*.json")
    dimensions = {
        "execution_payload": _dimension("execution_payload", run_root / "execution_payload.json", repo_root),
        "execution_results": _dimension(
            "execution_results", _first_rglob(run_root, "execution_results.json"), repo_root
        ),
        "operator_summary": _dimension("operator_summary", run_root / "operator_summary.json", repo_root),
        "precompute_payload": _dimension(
            "precompute_payload", precompute_payload, repo_root, required=precompute_required
        ),
        "posttrade_reconciliation": _dimension(
            "posttrade_reconciliation",
            _first_glob(run_root / "broker", "recon_posttrade_*.json"),
            repo_root,
        ),
        "target_attainment": _dimension(
            "target_attainment", _first_rglob(run_root, "*target_attainment*.json"), repo_root
        ),
        "execution_integrity": _dimension(
            "execution_integrity", _first_rglob(run_root, "execution_integrity.json"), repo_root
        ),
        "reliability_report": _dimension(
            "reliability_report", _first_rglob(run_root, "execution_reliability_report_*.json"), repo_root
        ),
        "broker_evidence": _dimension(
            "broker_evidence",
            _first_rglob(run_root / "broker", "*.json"),
            repo_root,
            present=bool(list((run_root / "broker").glob("*.json"))) if (run_root / "broker").exists() else False,
        ),
        "sleeve_trace_artifacts": _dimension(
            "sleeve_trace_artifacts", sleeve_trace, repo_root, required=False
        ),
    }
    coverage_class = classify_artifact_dimensions(dimensions)
    required = [row for row in dimensions.values() if row["required"]]
    present = sum(1 for row in required if row["present"])
    missing = [
        name for name, row in sorted(dimensions.items()) if row["required"] and not row["present"]
    ]
    return {
        "run_id": run_root.name,
        "trade_date": trade_date,
        "run_root": relpath(run_root, repo_root),
        "coverage_class": coverage_class,
        "trust_class": "DECISION_GRADE" if coverage_class == FULL_EVIDENCE else "NOT_DECISION_GRADE",
        "required_dimensions": len(required),
        "present_required_dimensions": present,
        "missing_required_dimensions": missing,
        "reason_codes": [f"missing_{name}" for name in missing],
        "same_run_root_required": True,
        "same_trade_date_required": True,
        "dimensions": dimensions,
    }


def build_artifact_coverage_matrix(repo_root: Path) -> dict[str, Any]:
    runs_root = repo_root / "outputs" / "runs"
    run_rows = [
        evaluate_run_artifact_coverage(repo_root, child)
        for child in sorted(runs_root.iterdir())
        if child.is_dir()
    ] if runs_root.exists() else []
    counts = Counter(row["coverage_class"] for row in run_rows)
    return {
        "schema_version": "fr078_artifact_coverage_matrix.v1",
        "artifact_date": SCHEMA_DATE,
        "governance_label": GOVERNANCE_LABEL,
        "execution_impact": EXECUTION_IMPACT,
        "runtime_change": False,
        "run_count": len(run_rows),
        "coverage_distribution": dict(sorted(counts.items())),
        "decision_grade_run_count": counts.get(FULL_EVIDENCE, 0),
        "pilot_capital_blocker": counts.get(FULL_EVIDENCE, 0) == 0,
        "required_dimensions": [
            "execution_payload",
            "execution_results",
            "operator_summary",
            "precompute_payload",
            "posttrade_reconciliation",
            "target_attainment",
            "execution_integrity",
            "reliability_report",
            "broker_evidence",
            "sleeve_trace_artifacts",
        ],
        "coverage_class_definitions": {
            FULL_EVIDENCE: "All required artifact classes are present in the same run evidence surface.",
            PARTIAL_EVIDENCE: "At least half of required evidence is present, but one or more critical classes are missing.",
            LOW_EVIDENCE: "Some planning/operator/broker context exists, but coverage is not sufficient for execution truth.",
            NOT_CLASSIFIABLE: "No usable run evidence is available.",
        },
        "runs": run_rows,
    }


def _artifact_class_from_coverage(coverage_class: str | None) -> str:
    if coverage_class == FULL_EVIDENCE:
        return FULL_EVIDENCE
    if coverage_class == PARTIAL_EVIDENCE:
        return PARTIAL_EVIDENCE
    if coverage_class == LOW_EVIDENCE:
        return LOW_EVIDENCE
    return "NOT_DECISION_GRADE"


def build_evidence_labeled_performance(repo_root: Path, coverage: dict[str, Any]) -> dict[str, Any]:
    sources = {
        "shadow_summary": repo_root / "outputs" / "shadow_candidates" / "performance" / "shadow_summary.json",
        "promotion_windows": repo_root / "outputs" / "research" / "promotion_readiness" / "2026-06-08" / "promotion_readiness_windows.json",
        "performance_veracity": repo_root / "outputs" / "audits" / "performance_veracity" / "20260521-local-audit-v2" / "audit_findings.md",
        "portfolio_freshness": repo_root / "outputs" / "model_quality" / "2026-06-08" / "portfolio_history_freshness.md",
        "orion_lyra_pit": repo_root / "outputs" / "research" / "pit_rebaseline" / "orion_lyra_matched_2026-06-17.json",
        "polaris_pit": repo_root / "outputs" / "research" / "pit_rebaseline" / "polaris_priced_2026-06-10.json",
        "cassiopeia_b2": repo_root / "outputs" / "research" / "cassiopeia" / "cassiopeia_phase_b2_campaign_segmentation_2026-06-19.json",
    }
    any_full_execution = coverage.get("decision_grade_run_count", 0) > 0
    claims = [
        {
            "claim_id": "shadow_long_run_metrics_exist",
            "claim": "Polaris, Orion, Lyra, and SPY shadow/backtest metrics exist.",
            "evidence_label": PARTIAL_EVIDENCE if sources["shadow_summary"].exists() else LOW_EVIDENCE,
            "allowed_use": "research comparison only",
            "downgrade_reason": "Shadow/backtest metrics are not broker-authoritative capital evidence.",
            "source_paths": [relpath(sources["shadow_summary"], repo_root)],
        },
        {
            "claim_id": "orion_lyra_redundancy",
            "claim": "Orion/Lyra redundancy triage remains valid.",
            "evidence_label": PARTIAL_EVIDENCE if sources["orion_lyra_pit"].exists() else LOW_EVIDENCE,
            "allowed_use": "merge/disposition triage, not promotion",
            "downgrade_reason": "PIT matched evidence supports redundancy, but not paper or pilot capital.",
            "source_paths": [relpath(sources["orion_lyra_pit"], repo_root)],
        },
        {
            "claim_id": "polaris_legacy_risk_downgrade",
            "claim": "Legacy Polaris current-universe risk claims are non-decision-grade.",
            "evidence_label": PARTIAL_EVIDENCE if sources["polaris_pit"].exists() else LOW_EVIDENCE,
            "allowed_use": "risk restatement and governance caveat",
            "downgrade_reason": "PIT rebaseline is research evidence; live/paper execution coverage is incomplete.",
            "source_paths": [relpath(sources["polaris_pit"], repo_root)],
        },
        {
            "claim_id": "cassiopeia_promotion",
            "claim": "Cassiopeia is ready for promotion.",
            "evidence_label": "NOT_DECISION_GRADE",
            "allowed_use": "none for promotion",
            "downgrade_reason": "13D campaign evidence weakened and Form 4 is pilot-only.",
            "source_paths": [relpath(sources["cassiopeia_b2"], repo_root)],
        },
        {
            "claim_id": "live_paper_alpha",
            "claim": "Current live/paper alpha can be separated from execution noise.",
            "evidence_label": FULL_EVIDENCE if any_full_execution else "NOT_DECISION_GRADE",
            "allowed_use": "capital evidence only if full run bundles exist",
            "downgrade_reason": "No full decision-grade run bundle exists locally.",
            "source_paths": [relpath(sources["portfolio_freshness"], repo_root)],
        },
    ]
    return {
        "schema_version": "fr079_evidence_labeled_performance.v1",
        "artifact_date": SCHEMA_DATE,
        "governance_label": GOVERNANCE_LABEL,
        "execution_impact": EXECUTION_IMPACT,
        "runtime_change": False,
        "overall_classification": "PARTIAL_NOT_PROMOTION_GRADE",
        "required_performance_fields": [
            "source_path",
            "artifact_date",
            "trade_date",
            "schema_version",
            "strategy_id",
            "sleeve_id",
            "claim_type",
            "metric_scope",
            "price_source",
            "benchmark_source",
            "universe_method",
            "return_convention",
            "weight_convention",
            "holdout_status",
            "cost_model_bps",
            "observation_start",
            "observation_end",
            "observation_count",
            "missing_days",
            "data_status",
            "chain_status",
            "stale_inputs",
            "repair_tokens",
            "performance_metrics",
            "promotion_recommendation",
            "decision_grade_flag",
            "execution_bundle_status",
            "reliability_classification",
            "evidence_label",
            "allowed_use",
            "downgrade_reasons",
            "surviving_conclusion",
        ],
        "downgrade_rules": [
            "Broken chain, reset, stale cache, mixed NAV bases, or missing return convention downgrades scorecards/promotion to LOW_EVIDENCE.",
            "Live/paper performance without same-run execution, broker, reconciliation, target-attainment, integrity, and reliability artifacts downgrades to LOW_EVIDENCE.",
            "Legacy current-universe backtests remain lineage-only unless rebuilt with PIT universe and same-source benchmark.",
            "NO_PROMOTION_RECOMMENDED, OBSERVE, BLOCKED, decision_grade=false, or zero valid observation windows block promotion evidence.",
            "Thin samples, pilot-only artifacts, missing PIT sector/SIC, missing matched comparator returns, or missing security master block alpha/promotion claims.",
        ],
        "source_artifacts": {key: {"path": relpath(path, repo_root), "exists": path.exists()} for key, path in sources.items()},
        "claims": claims,
        "conclusions_upheld": [
            "Orion/Lyra redundancy triage remains valid as research-only evidence.",
            "Legacy Polaris current-universe performance must remain downgraded.",
        ],
        "conclusions_downgraded": [
            "No shadow/backtest metric is pilot-capital evidence.",
            "Cassiopeia is not promotion-ready.",
            "Live/paper alpha is not separable from execution noise with current local evidence.",
        ],
    }


def classify_reconciliation_run(row: dict[str, Any]) -> str:
    dims = row.get("dimensions", {})
    recon = bool(dims.get("posttrade_reconciliation", {}).get("present"))
    target = bool(dims.get("target_attainment", {}).get("present"))
    broker = bool(dims.get("broker_evidence", {}).get("present"))
    payload = bool(dims.get("execution_payload", {}).get("present"))
    results = bool(dims.get("execution_results", {}).get("present"))
    if recon and target and broker and payload and results:
        return RECON_VERIFIED
    if recon or target or (broker and (payload or results)):
        return RECON_PARTIAL
    if payload or results or broker:
        return RECON_MISSING
    return RECON_NOT_RECONSTRUCTABLE


def build_reconciliation_coverage(repo_root: Path, coverage: dict[str, Any]) -> dict[str, Any]:
    run_rows = []
    for row in coverage.get("runs", []):
        recon_class = classify_reconciliation_run(row)
        run_rows.append(
            {
                "run_id": row["run_id"],
                "trade_date": row.get("trade_date"),
                "reconciliation_class": recon_class,
                "coverage_class": row.get("coverage_class"),
                "missing_required_dimensions": row.get("missing_required_dimensions", []),
            }
        )
    standalone_recon = sorted((repo_root / "outputs" / "broker").glob("recon_posttrade_*.json"))
    standalone_target = sorted((repo_root / "outputs" / "target_attainment").rglob("*target_attainment*.json"))
    standalone_recon_rows = []
    for path in standalone_recon:
        payload = read_json(path)
        standalone_recon_rows.append(
            {
                "path": relpath(path, repo_root),
                "trade_date": path.stem.replace("recon_posttrade_", ""),
                "status": payload.get("status") if isinstance(payload, dict) else None,
                "classification": RECON_PARTIAL,
                "reason": "Standalone posttrade reconciliation is diagnostic only unless linked to a run root and terminal broker evidence.",
            }
        )
    standalone_target_rows = []
    for path in standalone_target:
        payload = read_json(path)
        standalone_target_rows.append(
            {
                "path": relpath(path, repo_root),
                "trade_date": parse_date(path.name),
                "confidence": payload.get("confidence") if isinstance(payload, dict) else None,
                "status": payload.get("status") if isinstance(payload, dict) else None,
                "classification": RECON_PARTIAL,
                "reason": "Standalone target-attainment is not run-level reconciliation proof unless linked to complete same-run broker evidence.",
            }
        )
    counts = Counter(row["reconciliation_class"] for row in run_rows)
    return {
        "schema_version": "fr080_reconciliation_coverage.v1",
        "artifact_date": SCHEMA_DATE,
        "governance_label": GOVERNANCE_LABEL,
        "execution_impact": EXECUTION_IMPACT,
        "runtime_change": False,
        "run_count": len(run_rows),
        "reconciliation_distribution": dict(sorted(counts.items())),
        "standalone_posttrade_reconciliation": standalone_recon_rows,
        "standalone_target_attainment": standalone_target_rows,
        "known_not_reconstructable_incidents": [
            {
                "run_id": "2026-06-12T093506-0400_8f010b2",
                "classification": RECON_NOT_RECONSTRUCTABLE,
                "reason": "Incident documentation exists, but the local outputs/runs bundle is absent; exact terminal order and final economic state cannot be proven locally.",
            },
            {
                "run_id": "2026-06-15T093505-0400_c68a22d",
                "classification": RECON_NOT_RECONSTRUCTABLE,
                "reason": "Operator-supplied broker fills support the hotfix narrative, but the local outputs/runs bundle is absent.",
            },
        ],
        "pilot_capital_blocker": counts.get(RECON_VERIFIED, 0) == 0,
        "minimum_forward_artifacts": [
            "execution_payload.json",
            "execution_results.json",
            "operator_summary.json",
            "broker order/fill evidence",
            "broker/recon_posttrade_<TRADE_DATE>.json",
            "audit/execution_target_attainment_<TRADE_DATE>.json",
            "audit/execution_integrity.json",
            "audit/execution_reliability_report_<TRADE_DATE>.json",
        ],
        "runs": run_rows,
    }


def build_pit_integrity_audit(repo_root: Path) -> dict[str, Any]:
    paths = {
        "pit_universe_manifest": repo_root / "data" / "pit_universe" / "manifest.json",
        "pit_security_master": repo_root / "data" / "pit_universe" / "security_master.csv",
        "pit_security_events": repo_root / "data" / "pit_universe" / "security_events.csv",
        "manual_aliases": repo_root / "data" / "security_master" / "manual_aliases.json",
        "sec_ticker_map": repo_root / "data" / "alpha_stack_cache" / "edgar" / "sec_ticker_map.json",
        "cik_mapping": repo_root / "cik_mapping_results.csv",
        "pit_liquidity_manifest": repo_root / "outputs" / "research" / "pit_liquidity" / "manifest.json",
        "benchmark_close": repo_root / "outputs" / "perf" / "benchmark_close_history.csv",
        "live_overlay_benchmark": repo_root / "outputs" / "perf" / "live_overlay_benchmark_close_history.csv",
        "shadow_summary": repo_root / "outputs" / "shadow_candidates" / "performance" / "shadow_summary.json",
    }
    pit_liquidity = read_json(paths["pit_liquidity_manifest"])
    pit_manifest = read_json(paths["pit_universe_manifest"])
    checks = [
        {
            "check": "pit_universe_manifest",
            "status": "PASS" if isinstance(pit_manifest, dict) else "FAIL",
            "evidence_class": PARTIAL_EVIDENCE if isinstance(pit_manifest, dict) else LOW_EVIDENCE,
            "path": relpath(paths["pit_universe_manifest"], repo_root),
            "sha256_present": bool(isinstance(pit_manifest, dict) and "sha256" in json.dumps(pit_manifest).lower()),
        },
        {
            "check": "pit_security_master_exists",
            "status": "PASS" if paths["pit_security_master"].exists() else "FAIL",
            "evidence_class": PARTIAL_EVIDENCE if paths["pit_security_master"].exists() else LOW_EVIDENCE,
            "path": relpath(paths["pit_security_master"], repo_root),
        },
        {
            "check": "pit_liquidity_manifest",
            "status": "PASS" if isinstance(pit_liquidity, dict) else "FAIL",
            "evidence_class": FULL_EVIDENCE if isinstance(pit_liquidity, dict) else LOW_EVIDENCE,
            "coverage": pit_liquidity.get("coverage") if isinstance(pit_liquidity, dict) else None,
            "path": relpath(paths["pit_liquidity_manifest"], repo_root),
        },
        {
            "check": "benchmark_consistency",
            "status": "WARN",
            "evidence_class": PARTIAL_EVIDENCE,
            "reason": "SPY/benchmark surfaces exist in multiple files and need explicit source and return convention labels.",
            "paths": [
                relpath(paths["benchmark_close"], repo_root),
                relpath(paths["live_overlay_benchmark"], repo_root),
            ],
        },
        {
            "check": "execution_security_master",
            "status": "WARN" if paths["manual_aliases"].exists() else "FAIL",
            "evidence_class": PARTIAL_EVIDENCE if paths["manual_aliases"].exists() else LOW_EVIDENCE,
            "reason": "Local data/security_master contains aliases, not a full execution security-master surface.",
            "path": relpath(paths["manual_aliases"], repo_root),
        },
        {
            "check": "ticker_cik_mapping",
            "status": "WARN" if paths["sec_ticker_map"].exists() and paths["cik_mapping"].exists() else "FAIL",
            "evidence_class": PARTIAL_EVIDENCE,
            "reason": "Static CIK/ticker maps exist but are not sufficient without PIT identity joins.",
            "paths": [relpath(paths["sec_ticker_map"], repo_root), relpath(paths["cik_mapping"], repo_root)],
        },
    ]
    return {
        "schema_version": "fr081_pit_benchmark_universe_audit.v1",
        "artifact_date": SCHEMA_DATE,
        "governance_label": GOVERNANCE_LABEL,
        "execution_impact": EXECUTION_IMPACT,
        "runtime_change": False,
        "overall_classification": "PARTIAL_NOT_DECISION_GRADE",
        "required_fields": [
            "artifact_path",
            "schema_version",
            "evaluation_start",
            "evaluation_end",
            "price_source",
            "benchmark_symbol",
            "benchmark_source",
            "benchmark_max_date",
            "return_convention",
            "calendar_name",
            "non_trading_date_count",
            "universe_method",
            "universe_family",
            "universe_snapshot_hash",
            "security_id_required",
            "cik_join_method",
            "liquidity_panel_sha256",
            "trust_class",
            "reason_codes",
        ],
        "csv_summaries": {
            "benchmark_close": read_csv_summary(paths["benchmark_close"]),
            "live_overlay_benchmark": read_csv_summary(paths["live_overlay_benchmark"]),
            "pit_security_master": read_csv_summary(paths["pit_security_master"], date_fields=("firstpricedate", "lastpricedate")),
            "cik_mapping": read_csv_summary(paths["cik_mapping"], date_fields=("date",)),
        },
        "checks": checks,
        "pilot_capital_blockers": [
            "Benchmark and return convention labels are not uniform.",
            "Static ticker/CIK maps are not enough for PIT event joins.",
            "Execution security-master evidence is incomplete locally.",
        ],
    }


def _manifest_sleeves(repo_root: Path) -> list[dict[str, Any]]:
    payload = read_json(repo_root / "research_registry" / "sleeves" / "manifest.json")
    if isinstance(payload, dict) and isinstance(payload.get("sleeves"), list):
        return [row for row in payload["sleeves"] if isinstance(row, dict)]
    return []


def classify_sleeve_gate(sleeve_id: str, *, lifecycle_stage: str, operational_full: bool) -> str:
    sid = sleeve_id.lower()
    if sid in {"orion", "lyra"}:
        return "RETIRE_OR_MERGE_REVIEW"
    if sid == "polaris":
        return "PILOT_CAPITAL_BLOCKED" if not operational_full else "PAPER_READY"
    if sid in {"cassiopeia", "argo"}:
        return "RESEARCH_DIRECTIONAL"
    if lifecycle_stage.startswith("research") or lifecycle_stage in {"spec_only", "research_placeholder"}:
        return "PROMOTION_BLOCKED"
    return "PROMOTION_BLOCKED"


def build_sleeve_promotion_gate(repo_root: Path, coverage: dict[str, Any]) -> dict[str, Any]:
    operational_full = coverage.get("decision_grade_run_count", 0) > 0
    prior = read_json(repo_root / "outputs" / "research" / "data_trust_audit" / "sleeve_promotion_evidence_review.json")
    prior_by_name = {}
    if isinstance(prior, dict):
        for row in prior.get("sleeves", []):
            if isinstance(row, dict):
                prior_by_name[str(row.get("sleeve") or "").lower()] = row
    rows = []
    for sleeve in _manifest_sleeves(repo_root):
        sid = str(sleeve.get("sleeve_id") or "").lower()
        stage = str(sleeve.get("lifecycle_stage") or sleeve.get("status") or "")
        prior_row = prior_by_name.get(sid, {})
        gate = classify_sleeve_gate(sid, lifecycle_stage=stage, operational_full=operational_full)
        blockers = list(sleeve.get("promotion_requirements") or [])
        if not operational_full:
            blockers.append("No FULL_EVIDENCE operational run bundle exists locally.")
        rows.append(
            {
                "sleeve_id": sid,
                "strategy_id": sleeve.get("strategy_id"),
                "current_lifecycle_state": stage,
                "classification": gate,
                "evidence_sufficiency": prior_row.get("classification") or "NOT_DECISION_GRADE",
                "performance_evidence": "PARTIAL_OR_RESEARCH_ONLY",
                "correlation_redundancy_evidence": "BLOCKER" if sid in {"orion", "lyra"} else "NOT_PRIMARY",
                "liquidity_capacity_evidence": "PARTIAL",
                "operational_evidence": FULL_EVIDENCE if operational_full else "NOT_DECISION_GRADE",
                "promotion_blockers": blockers,
                "retirement_or_demotion_candidate": sid in {"orion", "lyra"},
            }
        )
    return {
        "schema_version": "fr082_sleeve_promotion_gate.v1",
        "artifact_date": SCHEMA_DATE,
        "governance_label": GOVERNANCE_LABEL,
        "execution_impact": EXECUTION_IMPACT,
        "runtime_change": False,
        "pilot_capital_ready_sleeve_count": 0,
        "sleeves": rows,
        "global_blockers": [
            "No sleeve is pilot-capital decision-grade under FR-077/FR-100.",
            "Operational evidence is not decision-grade locally.",
            "Machine-readable model evidence contract is not yet enforced.",
        ],
    }


def reliability_capital_eligible(
    *,
    reliability_signal: str,
    evidence_coverage: str,
    fail_count: int = 0,
    warn_count: int = 0,
) -> bool:
    return (
        reliability_signal == "RELIABILITY_GREEN"
        and evidence_coverage == FULL_EVIDENCE
        and fail_count == 0
        and warn_count == 0
    )


def build_reliability_coverage_review(repo_root: Path, coverage: dict[str, Any]) -> dict[str, Any]:
    replay = read_json(repo_root / "outputs" / "research" / "fr074_replay" / "fr074_replay_runs.json")
    rows = []
    if isinstance(replay, dict):
        replay_rows = replay.get("runs", [])
    elif isinstance(replay, list):
        replay_rows = replay
    else:
        replay_rows = []
    coverage_by_run = {row["run_id"]: row["coverage_class"] for row in coverage.get("runs", [])}
    for row in replay_rows:
        if not isinstance(row, dict):
            continue
        run_id = str(row.get("run_id") or "")
        signal = str(row.get("classification") or row.get("reliability_classification") or "UNKNOWN")
        evidence = coverage_by_run.get(run_id) or str(row.get("evidence_coverage") or LOW_EVIDENCE)
        fail_count = int(row.get("fail_count") or row.get("fail_invariants") or 0)
        warn_count = int(row.get("warn_count") or row.get("warn_invariants") or 0)
        rows.append(
            {
                "run_id": run_id,
                "trade_date": row.get("trade_date"),
                "reliability_signal": signal,
                "evidence_coverage": evidence,
                "capital_readiness_eligible": reliability_capital_eligible(
                    reliability_signal=signal,
                    evidence_coverage=evidence,
                    fail_count=fail_count,
                    warn_count=warn_count,
                ),
                "fail_count": fail_count,
                "warn_count": warn_count,
            }
        )
    return {
        "schema_version": "fr083_reliability_coverage_review.v1",
        "artifact_date": SCHEMA_DATE,
        "governance_label": GOVERNANCE_LABEL,
        "execution_impact": EXECUTION_IMPACT,
        "runtime_change": False,
        "observe_first": True,
        "implementation_status": "research_review_only_no_runtime_gate",
        "capital_readiness_eligible_run_count": sum(1 for row in rows if row["capital_readiness_eligible"]),
        "reviewed_replay_run_count": len(rows),
        "required_report_fields": [
            "reliability_signal",
            "evidence_coverage",
            "capital_readiness_eligible",
        ],
        "runs": rows,
        "blockers": [
            "FR-074 GREEN can overstate confidence when evidence coverage is LOW.",
            "No local run has FULL_EVIDENCE reliability coverage.",
            "Runtime reliability reports do not yet enforce artifact-completeness gating.",
        ],
    }


def classify_pilot_checklist(sections: list[dict[str, Any]]) -> str:
    failed = [row for row in sections if row.get("status") == "FAIL"]
    warn = [row for row in sections if row.get("status") == "WARN"]
    if failed:
        return "PILOT_CAPITAL_NOT_READY"
    if warn:
        return "PILOT_CAPITAL_CONDITIONALLY_READY"
    return "PILOT_CAPITAL_READY"


def build_pilot_capital_readiness_checklist(
    *,
    coverage: dict[str, Any],
    performance: dict[str, Any],
    reconciliation: dict[str, Any],
    pit_integrity: dict[str, Any],
    sleeve_gate: dict[str, Any],
    reliability: dict[str, Any],
) -> dict[str, Any]:
    sections = [
        {
            "section": "Data Trust",
            "status": "FAIL" if pit_integrity.get("overall_classification") != "DECISION_GRADE" else "PASS",
            "reason": "PIT/benchmark/universe integrity remains partial.",
        },
        {
            "section": "Model Trust",
            "status": "FAIL" if sleeve_gate.get("pilot_capital_ready_sleeve_count", 0) == 0 else "PASS",
            "reason": "No sleeve is pilot-capital decision-grade.",
        },
        {
            "section": "Operational Trust",
            "status": "FAIL" if coverage.get("decision_grade_run_count", 0) == 0 else "PASS",
            "reason": "No FULL_EVIDENCE run bundle exists locally.",
        },
        {
            "section": "Reconciliation",
            "status": "FAIL" if reconciliation.get("pilot_capital_blocker") else "PASS",
            "reason": "No run-linked verified reconciliation coverage exists.",
        },
        {
            "section": "Reliability",
            "status": "FAIL" if reliability.get("capital_readiness_eligible_run_count", 0) == 0 else "PASS",
            "reason": "No reliability row is GREEN + FULL_EVIDENCE.",
        },
        {
            "section": "Liquidity/capacity",
            "status": "WARN",
            "reason": "PIT liquidity is strong, but must be tested per sleeve and capital cap.",
        },
        {
            "section": "Risk controls",
            "status": "WARN",
            "reason": "FR-100 thresholds for slippage, concentration, drawdown, and capacity are not yet machine-enforced.",
        },
        {
            "section": "Approval/rollback",
            "status": "FAIL",
            "reason": "No signed pilot capital approval packet exists.",
        },
    ]
    return {
        "schema_version": "fr084_pilot_capital_readiness_checklist.v1",
        "artifact_date": SCHEMA_DATE,
        "governance_label": GOVERNANCE_LABEL,
        "execution_impact": EXECUTION_IMPACT,
        "runtime_change": False,
        "final_classification": classify_pilot_checklist(sections),
        "sections": sections,
        "required_before_first_dollar": [
            "Decision-grade data contract passes.",
            "Sleeve promotion gate passes for the target sleeve.",
            "Trailing run window is RELIABILITY_GREEN + FULL_EVIDENCE.",
            "Run-linked reconciliation and target attainment are clean.",
            "Liquidity/capacity/slippage thresholds pass at the proposed capital cap.",
            "Brett/CIO approval packet records cap, duration, monitoring, rollback, and waiver rules.",
        ],
    }


def markdown_table(headers: list[str], rows: list[list[Any]]) -> str:
    out = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    for row in rows:
        out.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return "\n".join(out)


def render_artifact_coverage_md(payload: dict[str, Any]) -> str:
    rows = [
        [key, value]
        for key, value in sorted(payload.get("coverage_distribution", {}).items())
    ]
    sample = [
        [row["run_id"], row.get("trade_date") or "unknown", row["coverage_class"], ",".join(row["missing_required_dimensions"][:5])]
        for row in payload.get("runs", [])[:20]
    ]
    return f"""
# FR-078 Artifact Coverage Matrix

Status: RESEARCH_ONLY

Run count: `{payload.get('run_count')}`  
Decision-grade run count: `{payload.get('decision_grade_run_count')}`  
Pilot-capital blocker: `{payload.get('pilot_capital_blocker')}`

## Coverage Distribution

{markdown_table(['Class', 'Count'], rows)}

## Sample Runs

{markdown_table(['Run ID', 'Trade Date', 'Coverage', 'Missing Required Dimensions'], sample)}
"""


def render_claims_md(title: str, payload: dict[str, Any], claims_key: str = "claims") -> str:
    claims = payload.get(claims_key, [])
    rows = []
    for row in claims:
        rows.append([
            row.get("claim_id") or row.get("section") or row.get("check") or row.get("sleeve_id"),
            row.get("evidence_label") or row.get("status") or row.get("classification"),
            row.get("allowed_use") or row.get("reason") or row.get("downgrade_reason") or "",
        ])
    return f"""
# {title}

Status: RESEARCH_ONLY

{markdown_table(['Item', 'Classification', 'Reason / Allowed Use'], rows)}
"""


def render_reconciliation_md(payload: dict[str, Any]) -> str:
    rows = [[key, value] for key, value in sorted(payload.get("reconciliation_distribution", {}).items())]
    return f"""
# FR-080 Broker / Model Reconciliation Backfill

Status: RESEARCH_ONLY

Run count: `{payload.get('run_count')}`  
Pilot-capital blocker: `{payload.get('pilot_capital_blocker')}`

## Distribution

{markdown_table(['Class', 'Count'], rows)}

## Minimum Forward Artifacts

{chr(10).join(f'- `{item}`' for item in payload.get('minimum_forward_artifacts', []))}
"""


def render_pit_md(payload: dict[str, Any]) -> str:
    rows = [[row["check"], row["status"], row["evidence_class"], row.get("reason", "")] for row in payload.get("checks", [])]
    return f"""
# FR-081 PIT Benchmark / Universe Integrity Audit

Status: RESEARCH_ONLY

Overall classification: `{payload.get('overall_classification')}`

{markdown_table(['Check', 'Status', 'Evidence Class', 'Reason'], rows)}
"""


def render_sleeve_gate_md(payload: dict[str, Any]) -> str:
    rows = [
        [row["sleeve_id"], row["current_lifecycle_state"], row["classification"], row["operational_evidence"]]
        for row in payload.get("sleeves", [])
    ]
    return f"""
# FR-082 Sleeve Promotion Evidence Gate

Status: RESEARCH_ONLY

Pilot-capital ready sleeve count: `{payload.get('pilot_capital_ready_sleeve_count')}`

{markdown_table(['Sleeve', 'Lifecycle', 'Classification', 'Operational Evidence'], rows)}
"""


def render_reliability_md(payload: dict[str, Any]) -> str:
    rows = [
        [row["run_id"], row.get("trade_date") or "unknown", row["reliability_signal"], row["evidence_coverage"], row["capital_readiness_eligible"]]
        for row in payload.get("runs", [])[:25]
    ]
    return f"""
# FR-083 Reliability Coverage Hardening

Status: RESEARCH_ONLY

Implementation status: `{payload.get('implementation_status')}`  
Capital-readiness eligible run count: `{payload.get('capital_readiness_eligible_run_count')}`

{markdown_table(['Run ID', 'Trade Date', 'Reliability Signal', 'Evidence Coverage', 'Capital Eligible'], rows)}
"""


def render_checklist_md(payload: dict[str, Any]) -> str:
    rows = [[row["section"], row["status"], row["reason"]] for row in payload.get("sections", [])]
    return f"""
# FR-084 Pilot Capital Readiness Checklist

Status: RESEARCH_ONLY

Final classification: `{payload.get('final_classification')}`

{markdown_table(['Section', 'Status', 'Reason'], rows)}
"""


def write_outputs(repo_root: Path) -> dict[str, dict[str, Any]]:
    coverage = build_artifact_coverage_matrix(repo_root)
    performance = build_evidence_labeled_performance(repo_root, coverage)
    reconciliation = build_reconciliation_coverage(repo_root, coverage)
    pit_integrity = build_pit_integrity_audit(repo_root)
    sleeve_gate = build_sleeve_promotion_gate(repo_root, coverage)
    reliability = build_reliability_coverage_review(repo_root, coverage)
    checklist = build_pilot_capital_readiness_checklist(
        coverage=coverage,
        performance=performance,
        reconciliation=reconciliation,
        pit_integrity=pit_integrity,
        sleeve_gate=sleeve_gate,
        reliability=reliability,
    )
    payloads = {
        "artifact_coverage_matrix": coverage,
        "performance_with_evidence_labels": performance,
        "reconciliation_coverage": reconciliation,
        "pit_benchmark_universe_audit": pit_integrity,
        "sleeve_promotion_gate": sleeve_gate,
        "reliability_coverage_review": reliability,
        "pilot_capital_readiness_checklist": checklist,
    }
    output_specs = {
        "artifact_coverage_matrix": ("artifact_coverage_matrix", "artifact_coverage_matrix", render_artifact_coverage_md),
        "performance_with_evidence_labels": ("evidence_labeled_performance", "performance_with_evidence_labels", lambda p: render_claims_md("FR-079 Evidence-Labeled Performance Rebuild", p)),
        "reconciliation_coverage": ("reconciliation_backfill", "reconciliation_coverage", render_reconciliation_md),
        "pit_benchmark_universe_audit": ("pit_integrity_audit", "pit_benchmark_universe_audit", render_pit_md),
        "sleeve_promotion_gate": ("sleeve_promotion_gate", "sleeve_promotion_gate", render_sleeve_gate_md),
        "reliability_coverage_review": ("reliability_coverage_hardening", "reliability_coverage_review", render_reliability_md),
        "pilot_capital_readiness_checklist": ("pilot_capital_readiness", "pilot_capital_readiness_checklist", render_checklist_md),
    }
    for key, (folder, basename, renderer) in output_specs.items():
        root = repo_root / "outputs" / "research" / folder
        write_json(root / f"{basename}.json", payloads[key])
        write_md(root / f"{basename}.md", renderer(payloads[key]))
    return payloads


def governance_doc(title: str, summary: str, artifact_paths: list[str], status: str = "RESEARCH_ONLY") -> str:
    return f"""
# {title}

Status: {status}  
Date: 2026-06-19  
Governance Label: RESEARCH_ONLY  
Execution Impact: NON_EXECUTIONAL

## Summary

{summary}

## Artifacts

{chr(10).join(f'- `{path}`' for path in artifact_paths)}

## Non-Goals

- No trading behavior change.
- No allocation behavior change.
- No broker submission change.
- No strategy selection change.
- No cron/runtime scheduling change.
"""


def write_governance_docs(repo_root: Path, payloads: dict[str, dict[str, Any]]) -> None:
    docs = repo_root / "docs" / "governance" / "fr_active"
    write_md(
        docs / "fr_078_artifact_coverage_matrix_and_required_evidence_gate.md",
        governance_doc(
            "FR-078 Artifact Coverage Matrix And Required Evidence Gate",
            "FR-078 creates a machine-readable run evidence matrix. Current local runs do not contain any FULL_EVIDENCE bundles, so artifact coverage remains a pilot-capital blocker.",
            [
                "outputs/research/artifact_coverage_matrix/artifact_coverage_matrix.json",
                "outputs/research/artifact_coverage_matrix/artifact_coverage_matrix.md",
            ],
        ),
    )
    write_md(
        docs / "fr_079_evidence_labeled_performance_rebuild.md",
        governance_doc(
            "FR-079 Evidence-Labeled Performance Rebuild",
            "FR-079 labels performance claims by evidence quality. Orion/Lyra redundancy triage survives as research-only evidence; live/paper alpha and Cassiopeia promotion claims are downgraded.",
            [
                "outputs/research/evidence_labeled_performance/performance_with_evidence_labels.json",
                "outputs/research/evidence_labeled_performance/performance_with_evidence_labels.md",
            ],
        ),
    )
    write_md(
        docs / "fr_080_broker_model_reconciliation_backfill.md",
        governance_doc(
            "FR-080 Broker / Model Reconciliation Backfill",
            "FR-080 audits run-linked reconciliation coverage. Standalone reconciliation files exist, but no local run is RECON_VERIFIED from a complete same-run evidence bundle.",
            [
                "outputs/research/reconciliation_backfill/reconciliation_coverage.json",
                "outputs/research/reconciliation_backfill/reconciliation_coverage.md",
            ],
        ),
    )
    write_md(
        docs / "fr_081_pit_benchmark_universe_integrity_audit.md",
        governance_doc(
            "FR-081 PIT Benchmark / Universe Integrity Audit",
            "FR-081 audits benchmark, universe, symbol, and security-master assumptions. PIT liquidity is strong, but benchmark conventions, identity joins, and execution security-master evidence remain partial.",
            [
                "outputs/research/pit_integrity_audit/pit_benchmark_universe_audit.json",
                "outputs/research/pit_integrity_audit/pit_benchmark_universe_audit.md",
            ],
        ),
    )
    write_md(
        docs / "fr_082_sleeve_promotion_evidence_gate.md",
        governance_doc(
            "FR-082 Sleeve Promotion Evidence Gate",
            "FR-082 creates a machine-readable sleeve promotion evidence gate aligned to FR-100. No sleeve is pilot-capital ready under current evidence.",
            [
                "outputs/research/sleeve_promotion_gate/sleeve_promotion_gate.json",
                "outputs/research/sleeve_promotion_gate/sleeve_promotion_gate.md",
            ],
        ),
    )
    write_md(
        docs / "fr_083_reliability_coverage_hardening.md",
        governance_doc(
            "FR-083 Reliability Coverage Hardening",
            "FR-083 separates reliability_signal, evidence_coverage, and capital_readiness_eligible in a research review so GREEN cannot be interpreted as decision-grade when coverage is LOW.",
            [
                "outputs/research/reliability_coverage_hardening/reliability_coverage_review.json",
                "outputs/research/reliability_coverage_hardening/reliability_coverage_review.md",
            ],
            status="RESEARCH_REVIEW_COMPLETE_OBSERVE_FIRST",
        ),
    )
    write_md(
        docs / "fr_084_pilot_capital_readiness_checklist.md",
        governance_doc(
            "FR-084 Pilot Capital Readiness Checklist",
            f"FR-084 operationalizes the FR-100 pilot gate. Current classification is `{payloads['pilot_capital_readiness_checklist'].get('final_classification')}`.",
            [
                "outputs/research/pilot_capital_readiness/pilot_capital_readiness_checklist.json",
                "outputs/research/pilot_capital_readiness/pilot_capital_readiness_checklist.md",
            ],
        ),
    )
    synthesis = f"""
# FR-078 To FR-084 Evidence Hardening Synthesis

Status: RESEARCH_SYNTHESIS_COMPLETE  
Date: 2026-06-19  
Governance Label: RESEARCH_ONLY  
Execution Impact: NON_EXECUTIONAL

## Executive Answer

The most blocking FR-100 pillar is Operational Trust, because current local run
roots have zero FULL_EVIDENCE execution bundles and zero run-linked verified
reconciliation bundles. Data Trust and Model Trust also block pilot capital, but
Operational Trust is the shortest immediate evidence gap to close for paper
trust.

## Current Readiness

Final pilot capital classification:
`{payloads['pilot_capital_readiness_checklist'].get('final_classification')}`

## Upheld Conclusions

- Orion/Lyra redundancy triage remains valid as research-only evidence.
- Legacy Polaris current-universe conclusions remain downgraded after PIT
  rebaseline.
- PIT liquidity/ADV evidence is strong for capacity analysis when joined to the
  correct PIT sleeve/candidate set.

## Downgraded Conclusions

- FR-074/FR-076 GREEN is not capital-grade unless evidence coverage is FULL.
- Shadow/backtest performance is not live/pilot capital evidence.
- Cassiopeia is promising research but not promotion-ready.
- Standalone broker/recon files do not prove a run without run ID and trade
  date linkage.

## Shortest Path To Paper-Trading Trust

1. Implement FR-078/FR-083 artifact-completeness checks in the daily run review
   surface.
2. Ensure every paper run writes payload, execution results, operator summary,
   execution integrity, target attainment, reliability, broker evidence, and
   posttrade reconciliation in the same run root.
3. Resolve terminal reason and operator-action gaps for NO_ACTION, HALTED,
   FAILED, SKIPPED, and PARTIAL states.

## Shortest Path To Pilot-Capital Readiness

1. Close paper trust first with FULL_EVIDENCE run bundles.
2. Rebuild historical/model performance with evidence labels and source
   conventions.
3. Pass PIT benchmark/universe/security-master integrity checks.
4. Pass sleeve promotion gate for the target sleeve.
5. Produce a signed FR-084 approval packet with cap, rollback, kill criteria,
   and monitoring.

## Implement Next

1. Runtime-safe artifact completeness field in FR-074 reliability reports.
2. Run-retention validator for same-run broker/recon/target/reliability bundles.
3. Machine-readable decision-grade evidence contract consumed by performance
   and promotion reports.

## Defer

- Multi-asset research expansion.
- Scaled capital readiness.
- Dashboard label changes until capital-readiness labels are partitioned.

## Retire Or Merge

- Keep FR-076 as a child evidence artifact under FR-074.
- Keep FR-063 as supporting Orion/Lyra evidence under FR-069.
- Register or fold FR-075 into a future machine-readable controls registry.
"""
    write_md(docs / "fr_078_to_fr_084_evidence_hardening_synthesis.md", synthesis)


def run_all(repo_root: Path) -> dict[str, dict[str, Any]]:
    payloads = write_outputs(repo_root)
    write_governance_docs(repo_root, payloads)
    return payloads


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build FR-078 through FR-084 evidence-hardening artifacts.")
    parser.add_argument("--repo-root", default=".", help="Repository root")
    parser.add_argument("--artifact-coverage-only", action="store_true", help="Write only FR-078 artifact coverage outputs")
    args = parser.parse_args(argv)
    repo_root = Path(args.repo_root).resolve()
    if args.artifact_coverage_only:
        payload = build_artifact_coverage_matrix(repo_root)
        root = repo_root / "outputs" / "research" / "artifact_coverage_matrix"
        write_json(root / "artifact_coverage_matrix.json", payload)
        write_md(root / "artifact_coverage_matrix.md", render_artifact_coverage_md(payload))
    else:
        run_all(repo_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
