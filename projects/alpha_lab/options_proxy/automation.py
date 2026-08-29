"""Standalone research recurrence with session gates, locking, and health evidence."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import time
from typing import Any, Callable, Dict, Optional
from zoneinfo import ZoneInfo

from projects.alpha_lab.factory import canonical_hash
from projects.alpha_lab.factory.canonical import format_datetime

from .config import ProxyConfig
from .market_calendar import session_for, trading_sessions
from .pipeline import collect_and_build, mature_signal, write_boundary_attestation
from .source import YFinanceSource
from .storage import (
    complete_evaluation_for_signal,
    output_root,
    read_json,
    research_run_lock,
    signal_artifact_for_date,
    signal_artifacts,
    write_immutable_json,
)


Clock = Callable[[], datetime]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _write_run_artifact(
    repo_root: Path, folder: str, payload: Dict[str, Any], generated_at: datetime
) -> Path:
    payload_hash = canonical_hash(payload)
    run_id = "{}-{}".format(
        generated_at.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ%f"),
        payload_hash[:12],
    )
    return write_immutable_json(
        output_root(repo_root) / folder / generated_at.date().isoformat() / run_id / "status.json",
        payload,
        repo_root=repo_root,
    )


def _maturity_date(decision_date: date, holding_days: int) -> date:
    horizon = decision_date + timedelta(days=max(holding_days * 4, 30))
    later_sessions = [
        value for value in trading_sessions(decision_date, horizon) if value > decision_date
    ]
    if len(later_sessions) < holding_days:  # pragma: no cover - defensive calendar bound
        raise ValueError("calendar does not contain enough later sessions for maturity")
    return later_sessions[holding_days - 1]


def maturation_readiness(
    *,
    repo_root: Path,
    config: ProxyConfig,
    through_date: date,
) -> Dict[str, Any]:
    """Explain exactly which accumulated cohorts can produce return evidence."""

    cohorts = []
    for path in signal_artifacts(repo_root):
        signal = read_json(path, repo_root=repo_root)
        signal_hash = str(signal.get("signal_hash") or "")
        decision_date = date.fromisoformat(str(signal["as_of_date"]))
        target_count = len(signal.get("research_targets", []))
        if signal.get("config_hash") != config.config_hash:
            status = "CONFIG_HASH_MISMATCH"
            earliest = None
        elif not signal.get("decision_eligible"):
            status = "SIGNAL_NOT_ELIGIBLE"
            earliest = None
        else:
            earliest = _maturity_date(decision_date, config.holding_period_trading_days)
            complete_path = complete_evaluation_for_signal(repo_root, signal_hash)
            if complete_path is not None:
                status = "MATURE_COMPLETE"
            elif through_date >= earliest:
                status = "READY_TO_MATURE"
            else:
                status = "WAITING_FOR_HOLDING_WINDOW"
        sessions_observed = len(
            [
                value
                for value in trading_sessions(decision_date, through_date)
                if value > decision_date
            ]
        )
        cohorts.append(
            {
                "signal_hash": signal_hash,
                "signal_path": str(path),
                "decision_date": decision_date.isoformat(),
                "decision_eligible": bool(signal.get("decision_eligible")),
                "research_target_count": target_count,
                "source_coverage": signal.get("source_coverage"),
                "later_sessions_observed": sessions_observed,
                "holding_sessions_required": config.holding_period_trading_days,
                "sessions_remaining": max(
                    config.holding_period_trading_days - sessions_observed, 0
                ),
                "earliest_maturity_date": earliest.isoformat() if earliest else None,
                "status": status,
            }
        )
    counts = {
        status: sum(1 for row in cohorts if row["status"] == status)
        for status in (
            "SIGNAL_NOT_ELIGIBLE",
            "WAITING_FOR_HOLDING_WINDOW",
            "READY_TO_MATURE",
            "MATURE_COMPLETE",
            "CONFIG_HASH_MISMATCH",
        )
    }
    return {
        "schema_version": "caerus_options_proxy_maturation_readiness_v1",
        "classification": "PROXY_FORWARD_OBSERVATION_ONLY",
        "alpha_claim_permitted": False,
        "through_date": through_date.isoformat(),
        "cohorts": cohorts,
        "counts": counts,
        "next_maturity_date": min(
            (
                row["earliest_maturity_date"]
                for row in cohorts
                if row["status"] == "WAITING_FOR_HOLDING_WINDOW"
                and row["earliest_maturity_date"]
            ),
            default=None,
        ),
        "trading_behavior_changed": False,
    }


def mature_all(
    *,
    repo_root: Path,
    config: ProxyConfig,
    through_date: date,
    source: Optional[Any] = None,
    generated_at: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Mature every eligible signal once; isolate failures by cohort."""

    timestamp = generated_at or _utc_now()
    adapter = source or YFinanceSource()
    processed = []
    skipped = []
    errors = []
    for path in signal_artifacts(repo_root):
        signal = read_json(path, repo_root=repo_root)
        signal_hash = str(signal.get("signal_hash") or "")
        decision_date = date.fromisoformat(str(signal["as_of_date"]))
        if signal.get("config_hash") != config.config_hash:
            skipped.append({"signal_hash": signal_hash, "reason": "CONFIG_HASH_MISMATCH"})
            continue
        if not signal.get("decision_eligible"):
            skipped.append({"signal_hash": signal_hash, "reason": "SIGNAL_NOT_ELIGIBLE"})
            continue
        complete_path = complete_evaluation_for_signal(repo_root, signal_hash)
        if complete_path is not None:
            skipped.append({"signal_hash": signal_hash, "reason": "ALREADY_MATURE"})
            continue
        sessions = list(trading_sessions(decision_date, through_date))
        later_sessions = [value for value in sessions if value > decision_date]
        if len(later_sessions) < config.holding_period_trading_days:
            earliest = _maturity_date(decision_date, config.holding_period_trading_days)
            skipped.append(
                {
                    "signal_hash": signal_hash,
                    "reason": "HOLDING_WINDOW_NOT_MATURE",
                    "later_sessions_observed": len(later_sessions),
                    "holding_sessions_required": config.holding_period_trading_days,
                    "sessions_remaining": config.holding_period_trading_days
                    - len(later_sessions),
                    "earliest_maturity_date": earliest.isoformat(),
                }
            )
            continue
        try:
            result = mature_signal(
                repo_root=repo_root,
                config=config,
                signal_path=path,
                through_date=through_date,
                source=adapter,
                generated_at=timestamp,
            )
            processed.append(
                {
                    "signal_hash": signal_hash,
                    "status": result["evaluation"]["status"],
                    "evaluation_path": str(result["evaluation_path"]),
                }
            )
        except Exception as exc:
            errors.append(
                {"signal_hash": signal_hash, "error_type": type(exc).__name__, "message": str(exc)}
            )
    readiness = maturation_readiness(
        repo_root=repo_root,
        config=config,
        through_date=through_date,
    )
    payload = {
        "schema_version": "caerus_options_proxy_maturation_batch_v1",
        "classification": "PROXY_FORWARD_OBSERVATION_ONLY",
        "alpha_claim_permitted": False,
        "through_date": through_date.isoformat(),
        "generated_at": format_datetime(timestamp),
        "processed": processed,
        "skipped": skipped,
        "errors": errors,
        "readiness_counts": readiness["counts"],
        "next_maturity_date": readiness["next_maturity_date"],
        "trading_behavior_changed": False,
    }
    payload["batch_hash"] = canonical_hash(payload)
    path = _write_run_artifact(repo_root, "maturation_batches", payload, timestamp)
    return {"batch": payload, "path": path}


def run_daily(
    *,
    repo_root: Path,
    config: ProxyConfig,
    source: Optional[Any] = None,
    now: Optional[datetime] = None,
    clock: Clock = _utc_now,
    sleeper: Callable[[float], None] = time.sleep,
) -> Dict[str, Any]:
    """Run one idempotent, research-only session observation and maturity sweep."""

    timestamp = now or clock()
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("daily run timestamp must be timezone-aware")
    local = timestamp.astimezone(ZoneInfo(config.decision_timezone))
    session = session_for(local.date())
    with research_run_lock(repo_root):
        observation: Dict[str, Any]
        maturation: Optional[Dict[str, Any]] = None
        if session.close_at is None:
            observation = {"status": "SKIPPED_NON_SESSION", "session_status": session.status}
            overall = "CLOSED_NON_SESSION"
        elif local < session.decision_not_before:
            observation = {
                "status": "SKIPPED_BEFORE_DECISION_WINDOW",
                "session_status": session.status,
                "decision_not_before": format_datetime(session.decision_not_before),
            }
            overall = "BLOCKED_BEFORE_DECISION_WINDOW"
        else:
            existing = signal_artifact_for_date(
                repo_root, local.date().isoformat(), config.minimum_source_coverage
            )
            if existing is None:
                result = collect_and_build(
                    repo_root=repo_root,
                    config=config,
                    source=source,
                    collected_at=timestamp,
                    clock=clock,
                    sleeper=sleeper,
                )
                observation = {
                    "status": "COLLECTED",
                    "signal_path": str(result["signal_path"]),
                    "source_coverage": result["signal"]["source_coverage"],
                    "decision_eligible": result["signal"]["decision_eligible"],
                    "decision_blockers": result["signal"]["decision_blockers"],
                }
            else:
                existing_signal = read_json(existing, repo_root=repo_root)
                observation = {
                    "status": "SKIPPED_ALREADY_OBSERVED",
                    "signal_path": str(existing),
                    "source_coverage": existing_signal.get("source_coverage"),
                    "decision_eligible": existing_signal.get("decision_eligible"),
                }
            maturation = mature_all(
                repo_root=repo_root,
                config=config,
                through_date=local.date(),
                source=source,
                generated_at=timestamp,
            )
            if float(observation.get("source_coverage") or 0.0) < config.minimum_source_coverage:
                overall = "DEGRADED_SOURCE_COVERAGE"
            else:
                overall = "HEALTHY" if not maturation["batch"]["errors"] else "DEGRADED"
        boundary = write_boundary_attestation(repo_root=repo_root)
        if boundary["attestation"]["production_boundary_status"] != "CLEAN":
            overall = "BOUNDARY_VIOLATION"
        health = {
            "schema_version": "caerus_options_proxy_daily_health_v1",
            "classification": "PROXY_FORWARD_OBSERVATION_ONLY",
            "alpha_claim_permitted": False,
            "generated_at": format_datetime(timestamp),
            "session_date": local.date().isoformat(),
            "session_status": session.status,
            "overall_status": overall,
            "observation": observation,
            "maturation_batch_path": str(maturation["path"]) if maturation else None,
            "production_boundary_status": boundary["attestation"]["production_boundary_status"],
            "production_scheduler_integration": False,
            "trading_behavior_changed": False,
            "orders_submitted": False,
        }
        health["health_hash"] = canonical_hash(health)
        health_path = _write_run_artifact(repo_root, "health", health, timestamp)
        return {"health": health, "health_path": health_path, "maturation": maturation}
