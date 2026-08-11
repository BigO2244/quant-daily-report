from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.execution_equality_gate import classify_equality_gate_observe_status
from core.strategy_registry import active_shadow_security_selection_ids


STATUS_ORDER = {"GREEN": 0, "YELLOW": 1, "RED": 2}
RECOMMENDED_ACTIONS = {
    "GREEN": "HOLD_NO_ACTION",
    "YELLOW": "HOLD_MONITOR",
    "RED": "INVESTIGATE_BEFORE_TRADING_CHANGES",
}
SHADOW_STRATEGIES = active_shadow_security_selection_ids()


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: str
    reason_codes: list[str]
    summary: str
    evidence_paths: list[str]

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "reason_codes": self.reason_codes,
            "summary": self.summary,
            "evidence_paths": self.evidence_paths,
        }


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _read_json(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    if not path.exists():
        return None, "MISSING_FILE"
    try:
        payload = json.loads(path.read_text())
    except Exception as exc:
        return None, f"UNREADABLE_JSON:{type(exc).__name__}"
    if not isinstance(payload, dict):
        return None, "JSON_NOT_OBJECT"
    return payload, None


def _status_max(checks: list[CheckResult]) -> str:
    return max((check.status for check in checks), key=lambda status: STATUS_ORDER.get(status, 2))


def _is_number(value: Any) -> bool:
    if isinstance(value, bool) or value is None:
        return False
    try:
        float(value)
    except (TypeError, ValueError):
        return False
    return True


def _norm_text(value: Any) -> str:
    return str(value or "").strip()


def _artifact_date(payload: dict[str, Any] | None) -> str | None:
    if not isinstance(payload, dict):
        return None
    for key in ("trade_date", "date", "as_of", "asof", "snapshot_date"):
        value = payload.get(key)
        if value:
            try:
                return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date().isoformat()
            except Exception:
                text = str(value).strip()
                return text[:10] if len(text) >= 10 else text
    meta = payload.get("meta")
    if isinstance(meta, dict):
        return _artifact_date(meta)
    return None


def _latest_precompute_date(root: Path) -> str | None:
    precompute_dir = root / "outputs" / "precompute"
    if not precompute_dir.exists():
        return None
    dates: list[str] = []
    for child in precompute_dir.iterdir():
        if not child.is_dir():
            continue
        try:
            normalized = datetime.fromisoformat(child.name).date().isoformat()
        except Exception:
            continue
        if normalized == child.name:
            dates.append(child.name)
    return sorted(dates)[-1] if dates else None


def resolve_trade_date(root: Path, explicit_trade_date: str | None) -> str:
    if explicit_trade_date:
        return datetime.fromisoformat(explicit_trade_date).date().isoformat()

    candidates: list[str] = []
    for path in (
        root / "outputs" / "shadow_candidates" / "latest" / "shadow_evaluation.json",
        root / "outputs" / "reconciliation" / "live_vs_shadow" / "latest" / "live_vs_shadow_reconciliation.json",
        root / "outputs" / "shadow_candidates" / "latest" / "comparison.json",
    ):
        payload, _ = _read_json(path)
        date_value = _artifact_date(payload)
        if date_value:
            candidates.append(date_value)

    precompute_date = _latest_precompute_date(root)
    if precompute_date:
        candidates.append(precompute_date)

    latest_run, _ = _read_json(root / "outputs" / "latest_run.json")
    latest_run_date = _artifact_date(latest_run)
    if latest_run_date:
        candidates.append(latest_run_date)

    if not candidates:
        return datetime.now().date().isoformat()
    return sorted(candidates)[-1]


def _check_vix_regime(root: Path, trade_date: str) -> CheckResult:
    path = root / "outputs" / "vix_regime" / "regime_current.json"
    payload, error = _read_json(path)
    if error:
        return CheckResult("VIX/regime", "RED", [error], "VIX/regime artifact is missing or unreadable.", [str(path)])

    assert payload is not None
    vix = payload.get("vix")
    regime = _norm_text(payload.get("regime")).upper()
    degraded_reason = payload.get("degraded_reason") or payload.get("reason") or payload.get("fallback_reason")
    fallback_used = bool(payload.get("fallback_used")) or _norm_text(payload.get("source")).lower() == "fallback"
    reason_codes: list[str] = []

    if not _is_number(vix):
        reason_codes.append("VIX_MISSING_OR_NON_NUMERIC")
    if regime in {"", "?", "UNKNOWN", "N/A", "NONE"}:
        reason_codes.append("REGIME_UNKNOWN")

    if "VIX_MISSING_OR_NON_NUMERIC" in reason_codes:
        status = "YELLOW" if fallback_used or degraded_reason else "RED"
        summary = "VIX is unavailable but degradation is explicit." if status == "YELLOW" else "VIX is unavailable without explicit fallback."
    elif "REGIME_UNKNOWN" in reason_codes:
        status = "YELLOW" if fallback_used or degraded_reason else "RED"
        summary = "Regime is unknown with explicit degraded reason." if status == "YELLOW" else "Regime is UNKNOWN without explicit degraded reason."
    else:
        status = "GREEN"
        if fallback_used:
            reason_codes.append("VIX_FALLBACK_USED")
        summary = f"VIX={float(vix):.2f}, regime={regime}."

    date_value = _artifact_date(payload)
    if date_value and date_value != trade_date:
        reason_codes.append("VIX_DATE_DIFFERS_FROM_HEALTH_DATE")
    return CheckResult("VIX/regime", status, reason_codes, summary, [str(path)])


def _check_shadow_artifacts(root: Path) -> CheckResult:
    path = root / "outputs" / "shadow_candidates" / "latest" / "comparison.md"
    json_path = root / "outputs" / "shadow_candidates" / "latest" / "comparison.json"
    if not path.exists():
        return CheckResult("Shadow artifacts", "RED", ["MISSING_SHADOW_COMPARISON_MD"], "Missing latest shadow comparison markdown.", [str(path)])
    try:
        text = path.read_text()
    except Exception as exc:
        return CheckResult("Shadow artifacts", "RED", [f"UNREADABLE_SHADOW_COMPARISON:{type(exc).__name__}"], "Shadow comparison markdown is unreadable.", [str(path)])

    reason_codes: list[str] = []
    if "## Executive Summary" not in text:
        reason_codes.append("MISSING_EXECUTIVE_SUMMARY")
    if "## Performance Scoreboard" not in text:
        reason_codes.append("MISSING_PERFORMANCE_SCOREBOARD")
    if "SPY" not in text:
        reason_codes.append("MISSING_SPY_COMPARISON")
    no_data_lines = [
        line.strip()
        for line in text.splitlines()
        if "NO_DATA" in line and not line.strip().upper().endswith(": NO")
    ]
    comparison_payload, _ = _read_json(json_path)
    comparison_reason = _norm_text((comparison_payload or {}).get("reason_code") or (comparison_payload or {}).get("data_reason")).upper()
    if no_data_lines and comparison_reason:
        reason_codes.append(comparison_reason)
    elif no_data_lines and "PRICE_CACHE_STALE" not in text and "reason" not in text.lower():
        reason_codes.append("SILENT_NO_DATA")
    if "PRICE_CACHE_STALE" in text:
        reason_codes.append("PRICE_CACHE_STALE")

    if "SILENT_NO_DATA" in reason_codes:
        status = "RED"
        summary = "Shadow comparison contains NO_DATA without an explicit reason."
    elif "PRICE_CACHE_STALE" in reason_codes:
        status = "YELLOW"
        summary = "Shadow comparison reports PRICE_CACHE_STALE explicitly."
    elif reason_codes:
        status = "RED"
        summary = "Shadow comparison is missing required sections."
    else:
        status = "GREEN"
        summary = "Latest comparison includes Executive Summary, Performance Scoreboard, and SPY context."
    return CheckResult("Shadow artifacts", status, sorted(set(reason_codes)), summary, [str(path), str(json_path)])


def _check_shadow_performance(root: Path) -> CheckResult:
    path = root / "outputs" / "shadow_candidates" / "latest" / "shadow_evaluation.json"
    payload, error = _read_json(path)
    if error:
        return CheckResult("Shadow performance report", "RED", [error], "Missing or unreadable shadow_evaluation.json.", [str(path)])

    assert payload is not None
    trade_date = str(payload.get("trade_date") or "")
    performance_path = root / "outputs" / "shadow_candidates" / trade_date / "shadow_performance.json" if trade_date else None
    performance_payload, _ = _read_json(performance_path) if performance_path else (None, None)
    fallback_reason = _norm_text((performance_payload or {}).get("data_reason") or (performance_payload or {}).get("reason_code"))
    strategies = payload.get("strategies")
    if not isinstance(strategies, dict):
        return CheckResult("Shadow performance report", "RED", ["MISSING_STRATEGIES"], "shadow_evaluation.json has no strategies object.", [str(path)])

    reason_codes: list[str] = []
    summaries: list[str] = []
    for slug in SHADOW_STRATEGIES:
        row = strategies.get(slug)
        if not isinstance(row, dict):
            reason_codes.append(f"MISSING_{slug.upper()}")
            continue
        data_status = _norm_text(row.get("data_status")).upper()
        status = _norm_text(row.get("status")).upper()
        valid_days = row.get("rolling_count_of_valid_days")
        reason = row.get("reason_code") or row.get("data_reason") or row.get("reason") or fallback_reason
        summaries.append(f"{slug}={data_status or status or 'UNKNOWN'}")
        if data_status == "OK":
            continue
        if data_status == "NO_DATA" and not reason:
            reason_codes.append(f"{slug.upper()}_NO_DATA_WITHOUT_REASON")
        elif data_status == "NO_DATA":
            reason_codes.append(str(reason))
        elif data_status:
            reason_codes.append(f"{slug.upper()}_{data_status}")
        else:
            reason_codes.append(f"{slug.upper()}_DATA_STATUS_MISSING")
        if _is_number(valid_days) and float(valid_days) < 2:
            reason_codes.append("INSUFFICIENT_HISTORY")

    if any(code.endswith("NO_DATA_WITHOUT_REASON") for code in reason_codes):
        status = "RED"
        summary = "At least one shadow strategy has NO_DATA without a reason code."
    elif any(code.startswith("MISSING_") or code.endswith("DATA_STATUS_MISSING") for code in reason_codes):
        status = "RED"
        summary = "Shadow evaluation is missing required strategy status."
    elif reason_codes:
        status = "YELLOW"
        summary = "Shadow evaluation has explicit degraded status: " + ", ".join(sorted(set(reason_codes)))
    else:
        status = "GREEN"
        summary = "Shadow evaluation data_status=OK for Polaris, Orion, and Lyra."
    evidence = [str(path)]
    if performance_path:
        evidence.append(str(performance_path))
    return CheckResult("Shadow performance report", status, sorted(set(reason_codes)), summary, evidence)


def _check_reconciliation(root: Path) -> CheckResult:
    path = root / "outputs" / "reconciliation" / "live_vs_shadow" / "latest" / "live_vs_shadow_reconciliation.json"
    payload, error = _read_json(path)
    if error:
        return CheckResult("Live vs shadow reconciliation", "RED", [error], "Missing or unreadable latest reconciliation artifact.", [str(path)])

    assert payload is not None
    classification = _norm_text(payload.get("classification") or payload.get("status")).upper()
    reason_codes = [str(item) for item in payload.get("reason_codes") or []]
    if classification in {"", "?", "UNKNOWN"}:
        return CheckResult("Live vs shadow reconciliation", "RED", ["CLASSIFICATION_MISSING"], "Reconciliation classification is missing or ambiguous.", [str(path)])
    if classification == "NOT_ALIGNED" and "DIFFERENT_STRATEGY_PATH" in reason_codes:
        return CheckResult("Live vs shadow reconciliation", "YELLOW", reason_codes, "NOT_ALIGNED is explicit due to DIFFERENT_STRATEGY_PATH.", [str(path)])
    if classification == "NOT_COMPARABLE":
        if reason_codes:
            return CheckResult("Live vs shadow reconciliation", "YELLOW", reason_codes, "NOT_COMPARABLE with explicit reason codes.", [str(path)])
        return CheckResult("Live vs shadow reconciliation", "RED", ["NOT_COMPARABLE_WITHOUT_REASON"], "NOT_COMPARABLE is missing reason codes.", [str(path)])
    if classification == "ALIGNED_INITIALIZING":
        required = {
            "IMMUTABLE_LINEAGE_VERIFIED",
            "TARGET_ATTAINED",
            "PERFORMANCE_HISTORY_INITIALIZING",
        }
        if required.issubset(reason_codes):
            return CheckResult(
                "Live vs shadow reconciliation",
                "GREEN",
                reason_codes,
                "First governed Orion run has verified package lineage and target attainment; return history is initializing.",
                [str(path)],
            )
        return CheckResult(
            "Live vs shadow reconciliation",
            "RED",
            sorted(required - set(reason_codes)),
            "ALIGNED_INITIALIZING is missing required immutable-lineage evidence.",
            [str(path)],
        )
    if classification in {"RECONCILED", "GREEN"}:
        return CheckResult("Live vs shadow reconciliation", "GREEN", reason_codes, f"Classification is explicit: {classification}.", [str(path)])
    return CheckResult("Live vs shadow reconciliation", "YELLOW", reason_codes, f"Classification is explicit: {classification}.", [str(path)])


def _check_strategy_identity(root: Path, trade_date: str) -> CheckResult:
    recon_path = root / "outputs" / "reconciliation" / "live_vs_shadow" / "latest" / "live_vs_shadow_reconciliation.json"
    signals_path = root / "outputs" / "precompute" / trade_date / "signals.json"
    recon, recon_error = _read_json(recon_path)
    signals, _ = _read_json(signals_path)

    live_strategy = None
    shadow_baseline = None
    identity: dict[str, Any] = {}
    reason_codes: list[str] = []
    if recon:
        live_strategy = recon.get("live_strategy_id") or ((recon.get("strategy_alignment") or {}).get("live_strategy_id"))
        shadow_baseline = recon.get("shadow_baseline_strategy") or ((recon.get("strategy_alignment") or {}).get("shadow_baseline_strategy"))
    if (not live_strategy or not shadow_baseline) and isinstance(signals, dict):
        identity = signals.get("strategy_identity") if isinstance(signals.get("strategy_identity"), dict) else {}
        live_strategy = live_strategy or identity.get("live_strategy_id")
        shadow_baseline = shadow_baseline or identity.get("shadow_baseline_strategy")
    elif isinstance(signals, dict) and isinstance(signals.get("strategy_identity"), dict):
        identity = dict(signals["strategy_identity"])
    if recon_error:
        reason_codes.append(recon_error)
    if not live_strategy:
        reason_codes.append("LIVE_STRATEGY_ID_MISSING")
    if not shadow_baseline:
        reason_codes.append("SHADOW_BASELINE_STRATEGY_MISSING")

    evidence = [str(recon_path), str(signals_path)]
    if reason_codes:
        return CheckResult("Strategy identity", "RED", reason_codes, "Strategy identity is missing live or shadow identifiers.", evidence)
    recon_alignment = (recon or {}).get("strategy_alignment") or {}
    immutable_lineage = (recon or {}).get("immutable_lineage") or {}
    if (
        str(recon_alignment.get("status") or "").upper() == "ALIGNED"
        and str(live_strategy) == str(shadow_baseline)
        and immutable_lineage.get("verified") is True
    ):
        return CheckResult(
            "Strategy identity",
            "GREEN",
            [],
            f"PAPER execution strategy={live_strategy}; governed shadow baseline={shadow_baseline}; immutable lineage verified.",
            evidence,
        )
    if (
        identity.get("live_pilot_tracks_approved_strategy") is False
        or str(identity.get("live_pilot_mapping_status") or "").upper()
        == "NOT_TRACKING_GOVERNED_STRATEGY"
    ):
        return CheckResult(
            "Strategy identity",
            "YELLOW",
            ["LIVE_PILOT_STRATEGY_TARGET_MISMATCH"],
            (
                f"Execution target={identity.get('execution_target_strategy_id') or live_strategy}; "
                f"live-pilot governed strategy={identity.get('live_pilot_governed_strategy_id') or 'unknown'}; "
                "live pilot must remain blocked."
            ),
            evidence,
        )
    summary = f"Live strategy={live_strategy}; shadow baseline={shadow_baseline}."
    return CheckResult("Strategy identity", "GREEN", [], summary, evidence)


def _check_data_freshness(root: Path, trade_date: str) -> CheckResult:
    evidence_paths = [
        str(root / "outputs" / "shadow_candidates" / "latest" / "shadow_evaluation.json"),
        str(root / "outputs" / "reconciliation" / "live_vs_shadow" / "latest" / "live_vs_shadow_reconciliation.json"),
        str(root / "outputs" / "precompute" / trade_date / "daily_snapshot.json"),
        str(root / "outputs" / "precompute" / trade_date / "signals.json"),
        str(root / "outputs" / "latest_run.json"),
    ]
    reason_codes: list[str] = []
    shadow, shadow_error = _read_json(Path(evidence_paths[0]))
    recon, recon_error = _read_json(Path(evidence_paths[1]))
    daily_snapshot_path = Path(evidence_paths[2])
    signals_path = Path(evidence_paths[3])
    latest_run, _ = _read_json(Path(evidence_paths[4]))

    shadow_date = _artifact_date(shadow)
    recon_date = _artifact_date(recon)
    latest_run_date = _artifact_date(latest_run)

    if shadow_error:
        reason_codes.append("SHADOW_LATEST_MISSING")
    elif shadow_date != trade_date:
        reason_codes.append("SHADOW_DATE_MISMATCH")
    if recon_error:
        reason_codes.append("RECONCILIATION_LATEST_MISSING")
    elif recon_date != trade_date:
        reason_codes.append("RECONCILIATION_DATE_MISMATCH")
    if not daily_snapshot_path.exists():
        reason_codes.append("PRECOMPUTE_DAILY_SNAPSHOT_MISSING")
    if not signals_path.exists():
        reason_codes.append("PRECOMPUTE_SIGNALS_MISSING")
    if latest_run_date and latest_run_date != trade_date:
        reason_codes.append("LATEST_RUN_SECONDARY_CONTEXT_STALE")

    if "RECONCILIATION_LATEST_MISSING" in reason_codes or "SHADOW_LATEST_MISSING" in reason_codes:
        status = "RED"
        summary = "Required latest pointer is missing."
    elif "RECONCILIATION_DATE_MISMATCH" in reason_codes or "SHADOW_DATE_MISMATCH" in reason_codes:
        status = "RED"
        summary = "Latest shadow or reconciliation artifact is stale versus selected trade date."
    elif reason_codes:
        status = "YELLOW"
        summary = "Freshness has explicit gaps: " + ", ".join(reason_codes)
    else:
        status = "GREEN"
        summary = "Latest shadow and reconciliation artifacts match the selected trade date."
    return CheckResult("Data freshness", status, reason_codes, summary, evidence_paths)


def _resolve_run_root(root: Path, latest_run: dict[str, Any] | None) -> Path | None:
    if not isinstance(latest_run, dict):
        return None
    raw = _norm_text(latest_run.get("run_root") or latest_run.get("path"))
    if not raw:
        run_id = _norm_text(latest_run.get("run_id"))
        raw = f"outputs/runs/{run_id}" if run_id else ""
    if not raw:
        return None
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = root / path
    return path


def _equality_gate_observe_surface(root: Path) -> dict[str, Any]:
    latest_path = root / "outputs" / "latest_run.json"
    latest_run, latest_error = _read_json(latest_path)
    if latest_error:
        return {
            "status": "unavailable",
            "decision": None,
            "would_block": None,
            "hashes_equal": None,
            "pricing_asof_match": None,
            "artifact_ref": None,
        }

    run_root = _resolve_run_root(root, latest_run)
    if run_root is None:
        return {
            "status": "unavailable",
            "decision": None,
            "would_block": None,
            "hashes_equal": None,
            "pricing_asof_match": None,
            "artifact_ref": None,
        }

    equality_path = run_root / "equality_gate.json"
    equality_gate, _equality_error = _read_json(equality_path)
    record: dict[str, Any] = equality_gate or {}
    if not record:
        operator_summary, _operator_error = _read_json(run_root / "operator_summary.json")
        candidate = (operator_summary or {}).get("equality_gate_observe")
        record = candidate if isinstance(candidate, dict) else {}

    return {
        "status": classify_equality_gate_observe_status(record),
        "decision": record.get("decision") if record else None,
        "would_block": record.get("would_block") if record else None,
        "hashes_equal": record.get("hashes_equal") if record else None,
        "pricing_asof_match": record.get("pricing_asof_match") if record else None,
        "artifact_ref": record.get("artifact_ref") if record.get("artifact_ref") else str(equality_path),
    }


def _check_execution_equality(root: Path) -> CheckResult:
    surface = _equality_gate_observe_surface(root)
    status = str(surface.get("status") or "unavailable")
    artifact = str(surface.get("artifact_ref") or "")
    evidence = [artifact] if artifact else []
    if status == "ok":
        return CheckResult(
            "Execution equality",
            "GREEN",
            [],
            "Final mechanical order identities exactly match broker submissions.",
            evidence,
        )
    if status == "unavailable":
        return CheckResult(
            "Execution equality",
            "YELLOW",
            ["EXECUTION_EQUALITY_UNAVAILABLE"],
            "Required execution-equality evidence is unavailable.",
            evidence,
        )
    return CheckResult(
        "Execution equality",
        "RED",
        [f"EXECUTION_EQUALITY_{status.upper()}"],
        f"Execution equality is not clean: {status}.",
        evidence,
    )


def _check_execution_timeline_provenance(root: Path, trade_date: str) -> CheckResult:
    latest_path = root / "outputs" / "latest_run.json"
    latest_run, latest_error = _read_json(latest_path)
    evidence_paths = [str(latest_path)]
    reason_codes: list[str] = []
    if latest_error:
        return CheckResult(
            "Execution timeline provenance",
            "YELLOW",
            ["LATEST_RUN_MISSING"],
            "Latest run pointer is missing; execution timeline availability is unknown.",
            evidence_paths,
        )

    run_root = _resolve_run_root(root, latest_run)
    if run_root is None:
        return CheckResult(
            "Execution timeline provenance",
            "YELLOW",
            ["LATEST_RUN_ROOT_MISSING"],
            "Latest run pointer does not identify a run root.",
            evidence_paths,
        )

    operator_path = run_root / "operator_summary.json"
    payload_path = run_root / "execution_payload.json"
    timeline_path = run_root / "execution_timeline.json"
    integrity_path = run_root / "audit" / "execution_integrity.json"
    evidence_paths.extend(str(path) for path in (operator_path, payload_path, timeline_path, integrity_path))

    operator_summary, operator_error = _read_json(operator_path)
    execution_payload, payload_error = _read_json(payload_path)
    timeline, timeline_error = _read_json(timeline_path)
    integrity, integrity_error = _read_json(integrity_path)
    operator_summary = operator_summary or {}
    execution_payload = execution_payload or {}
    timeline = timeline or {}
    integrity = integrity or {}

    latest_run_date = _artifact_date(latest_run)
    if latest_run_date and latest_run_date != trade_date:
        reason_codes.append("LATEST_RUN_DATE_MISMATCH")
    if operator_error:
        reason_codes.append("OPERATOR_SUMMARY_MISSING")
    if payload_error:
        reason_codes.append("EXECUTION_PAYLOAD_MISSING")
    if timeline_error:
        reason_codes.append("EXECUTION_TIMELINE_MISSING")
    if integrity_error:
        reason_codes.append("EXECUTION_INTEGRITY_MISSING")

    provenance = timeline.get("provenance") if isinstance(timeline.get("provenance"), dict) else {}
    execution_source = _norm_text(provenance.get("execution_source") or execution_payload.get("execution_source"))
    freshness_scope = _norm_text(provenance.get("price_freshness_scope") or execution_payload.get("price_freshness_scope"))
    integrity_status = _norm_text(integrity.get("status") or operator_summary.get("execution_integrity_status"))
    terminal_status = _norm_text(operator_summary.get("terminal_status") or latest_run.get("status"))
    asset_validation_status = _norm_text(
        execution_payload.get("asset_validation_status")
        or operator_summary.get("asset_validation_status")
    ).upper()
    integrity_findings = [
        str(item) for item in integrity.get("findings") or [] if str(item).strip()
    ]

    if not execution_source:
        reason_codes.append("EXECUTION_SOURCE_MISSING")
    if not freshness_scope:
        reason_codes.append("PRICE_FRESHNESS_SCOPE_MISSING")
    if asset_validation_status == "FAIL":
        reason_codes.append("PRETRADE_ASSET_VALIDATION_FAILED")
    if integrity_status.upper() != "OK":
        reason_codes.append("EXECUTION_INTEGRITY_NOT_OK")
    if integrity_findings:
        reason_codes.append("EXECUTION_AUDIT_FINDINGS_PRESENT")
    if terminal_status.upper() not in {
        "SUCCESS",
        "SUBMITTED",
        "DRY_RUN",
        "NO_ORDERS_NEEDED",
    }:
        reason_codes.append("EXECUTION_TERMINAL_STATUS_NOT_CLEAN")

    hard_failures = {
        "PRETRADE_ASSET_VALIDATION_FAILED",
        "EXECUTION_INTEGRITY_NOT_OK",
        "EXECUTION_AUDIT_FINDINGS_PRESENT",
        "EXECUTION_TERMINAL_STATUS_NOT_CLEAN",
    }
    status = (
        "RED"
        if hard_failures.intersection(reason_codes)
        else ("GREEN" if not reason_codes else "YELLOW")
    )
    summary = (
        "timeline_present={timeline_present}; execution_source={execution_source}; "
        "price_freshness_scope={freshness_scope}; integrity_status={integrity_status}; "
        "terminal_status={terminal_status}; asset_validation_status={asset_validation_status}"
    ).format(
        timeline_present=str(timeline_error is None).lower(),
        execution_source=execution_source or "unknown",
        freshness_scope=freshness_scope or "unknown",
        integrity_status=integrity_status or "unknown",
        terminal_status=terminal_status or "unknown",
        asset_validation_status=asset_validation_status or "unknown",
    )
    return CheckResult(
        "Execution timeline provenance",
        status,
        sorted(set(reason_codes)),
        summary,
        evidence_paths,
    )


def build_health_check(root: Path = Path("."), trade_date: str | None = None) -> dict[str, Any]:
    root = root.resolve()
    resolved_trade_date = resolve_trade_date(root, trade_date)
    checks = [
        _check_vix_regime(root, resolved_trade_date),
        _check_shadow_artifacts(root),
        _check_shadow_performance(root),
        _check_reconciliation(root),
        _check_strategy_identity(root, resolved_trade_date),
        _check_data_freshness(root, resolved_trade_date),
        _check_execution_timeline_provenance(root, resolved_trade_date),
        _check_execution_equality(root),
    ]
    overall_status = _status_max(checks)
    return {
        "trade_date": resolved_trade_date,
        "generated_at": _utc_now_iso(),
        "overall_status": overall_status,
        "checks": [check.to_json() for check in checks],
        "equality_gate_observe": _equality_gate_observe_surface(root),
        "recommended_action": RECOMMENDED_ACTIONS[overall_status],
    }


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Caerus Daily Health Check",
        "",
        f"- Trade Date: {payload.get('trade_date')}",
        f"- Generated At: {payload.get('generated_at')}",
        f"- Overall Status: {payload.get('overall_status')}",
        f"- Recommended Action: {payload.get('recommended_action')}",
        f"- Equality Gate Observe: {(payload.get('equality_gate_observe') or {}).get('status')}",
        "",
        "## Checks",
    ]
    for check in payload.get("checks") or []:
        reasons = ", ".join(check.get("reason_codes") or []) or "none"
        lines.append(f"- {check.get('name')}: {check.get('status')} - {check.get('summary')} Reason codes: {reasons}.")
    return "\n".join(lines) + "\n"


def render_console(payload: dict[str, Any]) -> str:
    lines = [
        "Caerus Daily Health Check",
        f"Trade Date: {payload.get('trade_date')}",
        f"Overall Status: {payload.get('overall_status')}",
        f"Equality Gate Observe: {(payload.get('equality_gate_observe') or {}).get('status')}",
        "",
        "Checks:",
    ]
    for check in payload.get("checks") or []:
        lines.append(f"- {check.get('name')}: {check.get('status')} - {check.get('summary')}")
    lines.append("")
    lines.append(f"Recommended Action: {payload.get('recommended_action')}")
    return "\n".join(lines)


def write_artifacts(payload: dict[str, Any], root: Path = Path(".")) -> tuple[Path, Path, Path, Path]:
    output_root = root / "outputs" / "health" / "caerus_daily_health_check"
    trade_date = str(payload["trade_date"])
    dated_dir = output_root / trade_date
    latest_dir = output_root / "latest"
    dated_dir.mkdir(parents=True, exist_ok=True)
    latest_dir.mkdir(parents=True, exist_ok=True)

    dated_json = dated_dir / "health_check.json"
    dated_md = dated_dir / "health_check.md"
    latest_json = latest_dir / "health_check.json"
    latest_md = latest_dir / "health_check.md"
    json_text = json.dumps(payload, indent=2, sort_keys=True)
    md_text = render_markdown(payload)
    dated_json.write_text(json_text)
    dated_md.write_text(md_text)
    latest_json.write_text(json_text)
    latest_md.write_text(md_text)
    return dated_json, dated_md, latest_json, latest_md


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Artifact-only Caerus daily health check.")
    parser.add_argument("--trade-date", default=None, help="Trade date to inspect. Defaults to latest available artifacts.")
    parser.add_argument("--root", default=".", help="Repository root. Defaults to current directory.")
    args = parser.parse_args(argv)

    payload = build_health_check(root=Path(args.root), trade_date=args.trade_date)
    write_artifacts(payload, root=Path(args.root))
    print(render_console(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
