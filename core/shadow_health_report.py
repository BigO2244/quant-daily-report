"""Registry-driven, read-only Shadow surveillance; never grants capital authority."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
from pathlib import Path
from typing import Any

from core.governed_xnys_calendar import XNYS_CALENDAR_POLICY_ID, is_xnys_session
from core.portfolio_learning_report import ARTIFACT_NAMES
from core.strategy_registry import StrategyRegistry


def _object(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _finite(value: Any) -> bool:
    return isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value)


def build_shadow_health_report(*, repo_root: Path, trade_date: str,
                               observation_start: str = "2026-05-12") -> dict:
    """Read dated artifacts only. Missing values remain unknown, never zero."""
    end = dt.date.fromisoformat(trade_date)
    start = dt.date.fromisoformat(observation_start)
    if start > end or not is_xnys_session(trade_date):
        raise ValueError("report requires an XNYS session on/after observation_start")
    root = Path(repo_root)
    registry_path = root / "config/research/strategy_registry.json"
    registry = StrategyRegistry.from_path(registry_path)  # no fallback registry
    shadow = root / "outputs/shadow_candidates"
    evidence: dict[str, dict] = {}
    cache: dict[str, dict] = {}

    def read(relative: str, date: str = trade_date) -> dict:
        path = shadow / date / relative
        key = str(path.relative_to(root))
        if key in cache:
            return cache[key]
        try:
            raw = path.read_bytes()
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError("expected object")
            status = "PASS" if payload.get("trade_date") == date else "WRONG_OR_MISSING_DATE"
            evidence[key] = {"status": status, "sha256": hashlib.sha256(raw).hexdigest()}
            cache[key] = payload if status == "PASS" else {}
        except (OSError, ValueError):
            evidence[key] = {"status": "MISSING_OR_INVALID", "sha256": None}
            cache[key] = {}
        return cache[key]

    evaluation = read("shadow_evaluation.json")
    comparison = read("comparison.json")
    feedback = read("feedback_loop_summary.json")
    promotion = read("promotion_readiness.json")
    rows = []
    failures = []
    for entry in registry.entries:
        if entry.strategy_type == "benchmark":
            continue
        required = entry.active_in_shadow_tracking
        row = {"strategy_id": entry.strategy_id, "display_name": entry.display_name,
               "registry_status": entry.status.upper(), "expected_shadow": required,
               "positions": None, "daily_return": None, "cumulative_return": None,
               "benchmark": entry.benchmark, "benchmark_daily_return": None,
               "benchmark_cumulative_return": None, "observation_days": None,
               "missing_days": None, "trades": None, "learning_readiness": "NOT_APPLICABLE",
               "promotion_readiness": "NOT_APPLICABLE", "promotion_authorized": False}
        if not required:
            row.update(status="NOT_EXPECTED", reason="Registry does not authorize active Shadow tracking; no Shadow performance inferred.")
            rows.append(row)
            continue
        reasons = []
        snapshot = read(f"{entry.strategy_id}.json")
        artifacts = {name: read(f"{entry.compact_name()}/{name}") for name in ARTIFACT_NAMES}
        metric = _object(_object(evaluation.get("strategies")).get(entry.strategy_id))
        compare = _object(_object(comparison.get("strategies")).get(entry.strategy_id))
        learn = _object(_object(feedback.get("strategies")).get(entry.compact_name()))
        promo = _object(_object(promotion.get("strategies")).get(entry.strategy_id))
        baseline_control = (entry.role == "baseline" and not entry.eligible_for_promotion
                            and registry.baseline_strategy_id() == entry.strategy_id
                            and promotion.get("active_baseline") == entry.strategy_id)
        benchmark = _object(_object(evaluation.get("strategies")).get("spy_benchmark"))
        if not snapshot or snapshot.get("strategy_slug") != entry.strategy_id:
            reasons.append("missing_or_mismatched_sleeve_snapshot")
        if not metric or not compare:
            reasons.append("expected_sleeve_missing_from_evaluation_or_comparison")
        for name, artifact in artifacts.items():
            if not artifact:
                reasons.append(f"missing_or_invalid_artifact:{name}")
        positions = snapshot.get("holdings")
        if not isinstance(positions, list):
            reasons.append("positions_unavailable")
        if metric.get("data_status") != "OK" or not all(_finite(metric.get(k)) for k in ("daily_return", "cumulative_return")):
            reasons.append("returns_unavailable_or_invalid")
        if benchmark.get("data_status") != "OK" or not all(_finite(benchmark.get(k)) for k in ("daily_return", "cumulative_return")):
            reasons.append("benchmark_unavailable_or_invalid")
        trace = artifacts.get("decision_trace.json", {})
        changes = _object(trace.get("changes_vs_prior"))
        trade_keys = ("new_entries", "exits", "weight_increases", "weight_decreases")
        trades = None
        if trace.get("prior_positions_available") is True and all(isinstance(changes.get(k), list) for k in trade_keys):
            trades = {"kind": "MODELED_POSITION_CHANGES", "count": sum(len(changes[k]) for k in trade_keys), "changes": {k: changes[k] for k in trade_keys}}
        else:
            reasons.append("modeled_trades_unavailable")
        if not learn.get("learning_readiness"):
            reasons.append("learning_readiness_unavailable")
        if not promo.get("readiness_state") and not baseline_control:
            reasons.append("promotion_readiness_unavailable")
        sleeve_start = max(start, dt.date.fromisoformat((entry.shadow_tracking or {}).get("observation_start_date", observation_start)))
        missing, invalid, valid = [], [], []
        day = sleeve_start
        while day <= end:
            date = day.isoformat()
            if is_xnys_session(date):
                history = read("shadow_evaluation.json", date)
                historical = _object(_object(history.get("strategies")).get(entry.strategy_id))
                if not historical:
                    missing.append(date)
                elif historical.get("data_status") != "OK" or not _finite(historical.get("daily_return")):
                    invalid.append(date)
                else:
                    valid.append(date)
            day += dt.timedelta(days=1)
        if missing:
            reasons.append("missing_expected_observation_sessions")
        if invalid:
            reasons.append("invalid_observation_sessions")
        row.update(status="PIPELINE_FAILURE" if reasons else "PASS", reason="; ".join(reasons) or "Expected Shadow evidence present",
                   positions=positions if isinstance(positions, list) else None,
                   position_kind="MODELED_HOLDINGS", trades=trades,
                   data_status=metric.get("data_status", "UNKNOWN"),
                   daily_return=metric.get("daily_return") if metric.get("data_status") == "OK" and _finite(metric.get("daily_return")) else None,
                   cumulative_return=metric.get("cumulative_return") if metric.get("data_status") == "OK" and _finite(metric.get("cumulative_return")) else None,
                   return_convention=metric.get("return_convention"),
                   benchmark_daily_return=benchmark.get("daily_return") if _finite(benchmark.get("daily_return")) else None,
                   benchmark_cumulative_return=benchmark.get("cumulative_return") if _finite(benchmark.get("cumulative_return")) else None,
                   observation_start=sleeve_start.isoformat(), observation_days=len(valid),
                   source_observation_days=metric.get("rolling_count_of_valid_days"),
                   missing_days=missing, invalid_days=invalid,
                   learning_readiness=learn.get("learning_readiness", "UNKNOWN"),
                   learning_reason=learn.get("primary_learning_gap") or "Existing feedback artifact",
                   promotion_readiness="BLOCKED_PIPELINE" if reasons else (
                       "NOT_APPLICABLE_CONTROL" if baseline_control else promo.get("readiness_state", "UNKNOWN")),
                   source_promotion_readiness=promo.get("readiness_state"),
                   promotion_reason=reasons or (["Registry baseline is a non-promotable comparison control"] if baseline_control
                                               else promo.get("reason_codes") or ["Existing advisory readiness; explicit owner approval still required"]))
        rows.append(row)
        failures.extend(f"{entry.strategy_id}:{reason}" for reason in reasons)
    failures.extend(f"artifact:{path}:{item['status']}" for path, item in evidence.items() if item["status"] != "PASS" and f"/{trade_date}/" in path)
    return {"schema_version": "caerus.shadow_health_report.v1", "trade_date": trade_date,
            "status": "PIPELINE_FAILURE" if failures else "PASS", "runtime_effect": "none",
            "calendar_policy": XNYS_CALENDAR_POLICY_ID,
            "registry_path": str(registry_path.relative_to(root)),
            "registry_sha256": hashlib.sha256(registry_path.read_bytes()).hexdigest(),
            "expected_sleeves": [e.strategy_id for e in registry.active_shadow_security_selection_entries()],
            "sleeves": rows, "pipeline_failures": failures, "evidence": evidence,
            "promotion_authorized": False}
