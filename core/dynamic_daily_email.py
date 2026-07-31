from __future__ import annotations

import datetime as dt
import html
import json
import math
from pathlib import Path
from typing import Any, Mapping

from core.strategy_registry import load_strategy_registry_for_repo


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _safe_float(value: object) -> float | None:
    try:
        numeric = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return numeric if math.isfinite(numeric) else None


def _fmt_return(value: object) -> str:
    numeric = _safe_float(value)
    if numeric is None:
        return "unavailable"
    sign = "+" if numeric >= 0 else ""
    return f"{sign}{numeric * 100:.2f}%"


def _fmt_pct(value: object) -> str:
    numeric = _safe_float(value)
    if numeric is None:
        return "unavailable"
    return f"{numeric * 100:.2f}%"


def _fmt_money(value: object) -> str:
    numeric = _safe_float(value)
    if numeric is None:
        return "unavailable"
    return f"${numeric:,.2f}"


def _fmt_number(value: object, *, digits: int = 2) -> str:
    numeric = _safe_float(value)
    if numeric is None:
        return "unavailable"
    return f"{numeric:.{digits}f}"


def _first_present(*values: Any) -> Any:
    for value in values:
        if value not in (None, ""):
            return value
    return None


def _payload_rows(payload: Mapping[str, Any], key: str = "orders") -> list[dict[str, Any]]:
    rows = payload.get(key)
    if not isinstance(rows, list):
        rows = payload.get("trades")
    if not isinstance(rows, list):
        return []
    return [dict(row) for row in rows if isinstance(row, Mapping)]


def _row_side(row: Mapping[str, Any]) -> str:
    return str(row.get("side") or "").strip().upper()


def _row_status(row: Mapping[str, Any]) -> str:
    order = row.get("order") if isinstance(row.get("order"), Mapping) else {}
    status = row.get("status") or order.get("status") or ""
    value = str(status).strip().lower()
    if "." in value:
        value = value.rsplit(".", 1)[-1]
    return value


def _is_unfilled_buy(row: Mapping[str, Any]) -> bool:
    if _row_side(row) != "BUY":
        return False
    status = _row_status(row)
    return status not in {"", "filled", "rejected", "canceled", "cancelled", "expired", "failed", "dry_run_not_submitted"}


def _manifest_sleeves(repo_root: Path) -> dict[str, dict[str, Any]]:
    path = repo_root / "research_registry" / "sleeves" / "manifest.json"
    payload = _load_json(path)
    sleeves = payload.get("sleeves")
    if not isinstance(sleeves, list):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for row in sleeves:
        if not isinstance(row, dict):
            continue
        sleeve_id = str(row.get("sleeve_id") or "").strip()
        strategy_id = str(row.get("strategy_id") or "").strip()
        if strategy_id:
            out[strategy_id] = dict(row)
        if sleeve_id and sleeve_id not in out:
            out[sleeve_id] = dict(row)
    return out


def _shadow_dir_for(repo_root: Path, trade_date: str) -> tuple[Path | None, str]:
    root = repo_root / "outputs" / "shadow_candidates"
    exact = root / str(trade_date)
    if exact.exists():
        return exact, "CURRENT_DATE"
    dated = sorted(
        item
        for item in root.iterdir()
        if item.is_dir() and len(item.name) == 10 and item.name[:4].isdigit()
    ) if root.exists() else []
    if dated:
        return dated[-1], f"LATEST_AVAILABLE:{dated[-1].name}"
    latest = root / "latest"
    if latest.exists():
        return latest, "LATEST_POINTER"
    return None, "MISSING"


def _latest_live_pilot_run(repo_root: Path) -> Path | None:
    runs_root = repo_root / "outputs" / "live_pilot" / "runs"
    if not runs_root.exists():
        return None
    runs = sorted((item for item in runs_root.iterdir() if item.is_dir()), key=lambda item: (item.stat().st_mtime, item.name))
    return runs[-1] if runs else None


def _latest_live_pilot_plan(repo_root: Path) -> Path | None:
    plans_root = repo_root / "outputs" / "live_pilot" / "plans"
    if not plans_root.exists():
        return None
    plans = sorted(plans_root.glob("live_pilot_plan_*.json"), key=lambda item: (item.stat().st_mtime, item.name))
    return plans[-1] if plans else None


def _role_for(strategy_id: str, registry_role: str, manifest: Mapping[str, Any], raw: Mapping[str, Any]) -> str:
    text = " ".join(
        str(value or "").lower()
        for value in (
            strategy_id,
            registry_role,
            manifest.get("sleeve_id"),
            manifest.get("display_name"),
            raw.get("display_name"),
        )
    )
    tracking = raw.get("shadow_tracking") if isinstance(raw.get("shadow_tracking"), dict) else {}
    if "baseline_strategy_id" in tracking or "alpha" in text:
        return "alpha"
    if registry_role:
        return registry_role
    return "baseline" if "baseline" in text else "candidate"


def _lifecycle_for(status: str, manifest_stage: str, execution_impact: str) -> str:
    status_norm = str(status or "").strip().lower()
    impact_norm = str(execution_impact or "").strip().upper()
    if status_norm in {"production", "live"} or impact_norm == "LIVE":
        return "pilot/live"
    if status_norm == "paper":
        return "paper"
    if status_norm == "shadow":
        return "shadow"
    if status_norm == "research":
        return "research"
    return manifest_stage or status_norm or "unknown"


def build_dynamic_sleeve_rows(repo_root: Path | str, trade_date: str) -> dict[str, Any]:
    root = Path(repo_root)
    manifest_by_key = _manifest_sleeves(root)
    shadow_dir, shadow_source = _shadow_dir_for(root, trade_date)
    shadow_eval = _load_json(shadow_dir / "shadow_evaluation.json") if shadow_dir else {}
    promotion = _load_json(shadow_dir / "promotion_readiness.json") if shadow_dir else {}
    eval_strategies = shadow_eval.get("strategies") if isinstance(shadow_eval.get("strategies"), dict) else {}
    readiness_strategies = promotion.get("strategies") if isinstance(promotion.get("strategies"), dict) else {}

    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    registry_error: str | None = None
    try:
        registry = load_strategy_registry_for_repo(root)
        registry_entries = list(registry.entries)
    except Exception as exc:
        registry_entries = []
        registry_error = str(exc)

    for entry in registry_entries:
        raw = entry.raw or {}
        manifest = manifest_by_key.get(entry.strategy_id) or manifest_by_key.get(entry.compact_name()) or {}
        metrics = eval_strategies.get(entry.strategy_id) if isinstance(eval_strategies.get(entry.strategy_id), dict) else {}
        readiness = readiness_strategies.get(entry.strategy_id) if isinstance(readiness_strategies.get(entry.strategy_id), dict) else {}
        artifact_path = shadow_dir / f"{entry.strategy_id}.json" if shadow_dir else None
        artifact_exists = bool(artifact_path and artifact_path.exists())
        artifact_status = str(metrics.get("status") or ("OK" if artifact_exists else "MISSING")).upper()
        if shadow_source != "CURRENT_DATE" and artifact_status == "OK":
            artifact_status = shadow_source
        role = _role_for(entry.strategy_id, entry.role or "", manifest, raw)
        lifecycle = _lifecycle_for(entry.status, str(manifest.get("lifecycle_stage") or ""), entry.execution_impact)
        data_status = str(metrics.get("data_status") or ("OK" if metrics else "UNAVAILABLE"))
        concentration = (
            f"HHI {_fmt_number(metrics.get('avg_hhi'), digits=3)}; "
            f"effN {_fmt_number(metrics.get('avg_effective_n'), digits=2)}; "
            f"top3 {_fmt_pct(metrics.get('avg_top_3_concentration'))}"
        )
        rows.append(
            {
                "strategy_id": entry.strategy_id,
                "display_name": entry.display_name,
                "sleeve_id": manifest.get("sleeve_id") or entry.compact_name(),
                "role": role,
                "lifecycle": lifecycle,
                "registry_status": entry.status,
                "manifest_stage": manifest.get("lifecycle_stage") or "unavailable",
                "artifact_status": artifact_status,
                "data_status": data_status,
                "today_return": _fmt_return(metrics.get("daily_return")) if data_status == "OK" else "unavailable",
                "since_inception_return": _fmt_return(metrics.get("cumulative_return")),
                "turnover": _fmt_pct(metrics.get("avg_turnover") if metrics.get("avg_turnover") is not None else metrics.get("expected_turnover")),
                "concentration": concentration,
                "readiness": readiness.get("readiness_state") or ("not_applicable" if entry.status in {"paper", "research"} else "UNAVAILABLE"),
                "readiness_confidence": readiness.get("confidence") or "unavailable",
                "artifact_path": str(artifact_path) if artifact_path else "unavailable",
            }
        )
        seen.add(entry.strategy_id)

    for key, manifest in sorted(manifest_by_key.items()):
        strategy_id = str(manifest.get("strategy_id") or "").strip()
        if strategy_id and strategy_id in seen:
            continue
        if key in seen:
            continue
        sleeve_id = str(manifest.get("sleeve_id") or key).strip()
        if not sleeve_id:
            continue
        rows.append(
            {
                "strategy_id": strategy_id or sleeve_id,
                "display_name": str(manifest.get("display_name") or sleeve_id),
                "sleeve_id": sleeve_id,
                "role": "alpha" if "alpha" in sleeve_id.lower() else "manifest_only",
                "lifecycle": str(manifest.get("lifecycle_stage") or "research"),
                "registry_status": "manifest_only",
                "manifest_stage": str(manifest.get("lifecycle_stage") or "unavailable"),
                "artifact_status": "UNAVAILABLE",
                "data_status": "UNAVAILABLE",
                "today_return": "unavailable",
                "since_inception_return": "unavailable",
                "turnover": "unavailable",
                "concentration": "unavailable",
                "readiness": "UNAVAILABLE",
                "readiness_confidence": "unavailable",
                "artifact_path": "unavailable",
            }
        )

    return {
        "status": "OK" if rows and registry_error is None else "DEGRADED" if rows else "UNAVAILABLE",
        "registry_error": registry_error,
        "shadow_source": shadow_source,
        "shadow_dir": str(shadow_dir) if shadow_dir else "unavailable",
        "shadow_as_of_date": shadow_dir.name if shadow_dir and len(shadow_dir.name) == 10 else "unavailable",
        "rows": rows,
    }


def _open_order_count(snapshot: Mapping[str, Any]) -> int:
    orders = snapshot.get("open_orders")
    if isinstance(orders, list):
        return len(orders)
    return 0


def build_live_pilot_account_payload(
    repo_root: Path | str,
    *,
    run_root: Path | str | None = None,
) -> dict[str, Any]:
    root = Path(repo_root)
    run_root = Path(run_root) if run_root is not None else _latest_live_pilot_run(root)
    latest_plan = _latest_live_pilot_plan(root)
    if run_root is None:
        plan_payload = _load_json(latest_plan) if latest_plan else {}
        return {
            "status": "NO_LIVE_PILOT_RUN",
            "latest_run_id": "unavailable",
            "latest_run_root": "unavailable",
            "latest_plan_path": str(latest_plan) if latest_plan else "unavailable",
            "latest_plan_status": plan_payload.get("status") or "unavailable",
            "account": {},
            "open_orders": [],
            "positions": [],
            "filled_orders": [],
            "reconciliation_status": "unavailable",
            "evidence_metrics": {},
            "paper_live_divergence": _paper_live_divergence(root),
        }

    summary = _load_json(run_root / "live_pilot_operator_summary.json")
    gate_state = _load_json(run_root / "live_pilot_gate_state.json")
    recon = _load_json(run_root / "live_pilot_reconciliation.json")
    evidence = _load_json(run_root / "live_pilot_evidence_metrics.json")
    execution_results = _load_json(run_root / "execution_results.json")
    intended_payload = _load_json(run_root / "live_pilot_orders_intended.json")
    submitted_payload = _load_json(run_root / "live_pilot_orders_submitted.json")
    intended = _payload_rows(intended_payload)
    submitted = _payload_rows(submitted_payload)
    broker_responses = execution_results.get("broker_responses")
    if isinstance(broker_responses, list):
        refreshed_rows = [
            dict(row) for row in broker_responses if isinstance(row, Mapping)
        ]
        if refreshed_rows:
            submitted = refreshed_rows
    snapshot = _load_json(run_root / "live_pilot_broker_snapshot_post.json")
    if not snapshot.get("account"):
        snapshot = _load_json(run_root / "live_pilot_broker_snapshot_pre.json")
    account = snapshot.get("account") if isinstance(snapshot.get("account"), dict) else {}
    open_orders = snapshot.get("open_orders") if isinstance(snapshot.get("open_orders"), list) else []
    positions = snapshot.get("positions") if isinstance(snapshot.get("positions"), list) else []
    filled = [
        row for row in submitted
        if isinstance(row, dict) and _row_status(row) == "filled"
    ]
    approved_buys = [row for row in intended if _row_side(row) == "BUY"]
    submitted_buys = [
        row for row in submitted
        if _row_side(row) == "BUY" and _row_status(row) != "dry_run_not_submitted"
    ]
    unfilled_buys = [row for row in submitted_buys if _is_unfilled_buy(row)]
    escalated_buys = [
        row for row in (submitted or intended)
        if _row_side(row) == "BUY"
        and str(row.get("escalation_reason") or "").strip()
        and str(row.get("escalation_reason")) != "none"
    ]
    policy_rows = submitted or intended
    first_policy_row = (policy_rows or [{}])[0]
    fallback_marketable_count = (
        sum(1 for row in policy_rows if bool(row.get("is_marketable")))
        if policy_rows
        else "unavailable"
    )
    fallback_passive_count = (
        sum(1 for row in policy_rows if bool(row.get("is_passive")))
        if policy_rows
        else "unavailable"
    )
    approved_buy_count = _first_present(
        execution_results.get("approved_buy_count"),
        summary.get("approved_buy_count"),
        evidence.get("approved_buy_count"),
        len(approved_buys),
    )
    submitted_buy_count = _first_present(
        execution_results.get("submitted_buy_count"),
        summary.get("submitted_buy_count"),
        evidence.get("submitted_buy_count"),
        len(submitted_buys),
    )
    unfilled_buy_count = _first_present(
        execution_results.get("unfilled_buy_count"),
        summary.get("unfilled_buy_count"),
        evidence.get("unfilled_buy_count"),
        len(unfilled_buys),
    )
    escalated_buy_count = _first_present(
        execution_results.get("escalated_buy_count"),
        summary.get("escalated_buy_count"),
        evidence.get("escalated_buy_count"),
        len(escalated_buys),
    )
    remaining_blocked_or_suppressed_buy_count = _first_present(
        execution_results.get("remaining_blocked_or_suppressed_buy_count"),
        summary.get("remaining_blocked_or_suppressed_buy_count"),
        evidence.get("remaining_blocked_or_suppressed_buy_count"),
        max(int(approved_buy_count or 0) - int(submitted_buy_count or 0), 0),
    )
    gate_decision = str(gate_state.get("decision") or "").strip().upper()
    payload_status = str(execution_results.get("status") or "").strip().upper()
    operator_execution_status = str(
        execution_results.get("operator_execution_status") or ""
    ).strip().lower()
    if gate_decision == "BLOCKED":
        live_status = "BLOCKED"
    elif operator_execution_status in {"executed", "reconciled_success"}:
        live_status = "EXECUTED"
    elif operator_execution_status == "dry_run":
        live_status = "DRY_RUN"
    elif operator_execution_status in {"submitted_unfilled", "open"}:
        live_status = "SUBMITTED_UNFILLED"
    elif payload_status:
        live_status = payload_status
    elif gate_state:
        live_status = "DEGRADED"
    else:
        live_status = "OK"
    gate_reason = gate_state.get("block_reason")
    suppressed_reason = _first_present(
        execution_results.get("blocked_or_suppressed_buy_reason"),
        summary.get("blocked_or_suppressed_buy_reason"),
        evidence.get("blocked_or_suppressed_buy_reason"),
        gate_reason,
        summary.get("reason_code"),
    )
    submitted_count = int(execution_results.get("submitted_count") or len(submitted))
    filled_count = int(execution_results.get("filled_count") or len(filled))
    if submitted_count > 0:
        evidence = {
            **evidence,
            "filled_count": filled_count,
            "fill_rate": filled_count / submitted_count,
            "idle_cash_reason": _first_present(
                execution_results.get("idle_cash_reason"),
                evidence.get("idle_cash_reason"),
            ),
        }
    return {
        "status": live_status,
        "latest_run_id": summary.get("run_id") or run_root.name,
        "latest_run_root": str(run_root),
        "latest_plan_path": summary.get("plan_path") or "unavailable",
        "latest_plan_status": "unavailable",
        "account": account,
        "open_orders": open_orders,
        "positions": positions,
        "filled_orders": filled,
        "reconciliation_status": recon.get("status") or gate_reason or summary.get("reason_code") or "unavailable",
        "evidence_metrics": evidence,
        "execution_results": execution_results,
        "approved_buy_count": approved_buy_count,
        "submitted_buy_count": submitted_buy_count,
        "unfilled_buy_count": unfilled_buy_count,
        "escalated_buy_count": escalated_buy_count,
        "remaining_blocked_or_suppressed_buy_count": remaining_blocked_or_suppressed_buy_count,
        "blocked_or_suppressed_buy_reason": (
            suppressed_reason
            if gate_decision == "BLOCKED" or int(remaining_blocked_or_suppressed_buy_count or 0) > 0
            else "none"
        ),
        "entry_execution_policy": _first_present(
            execution_results.get("entry_execution_policy"),
            summary.get("entry_execution_policy"),
            evidence.get("entry_execution_policy"),
            first_policy_row.get("entry_execution_policy"),
            "unavailable",
        ),
        "submitted_order_type": _first_present(
            execution_results.get("submitted_order_type"),
            summary.get("submitted_order_type"),
            evidence.get("submitted_order_type"),
            first_policy_row.get("submitted_order_type"),
            "unavailable",
        ),
        "marketable_order_count": _first_present(
            execution_results.get("marketable_order_count"),
            summary.get("marketable_order_count"),
            evidence.get("marketable_order_count"),
            fallback_marketable_count,
            "unavailable",
        ),
        "passive_order_count": _first_present(
            execution_results.get("passive_order_count"),
            summary.get("passive_order_count"),
            evidence.get("passive_order_count"),
            fallback_passive_count,
            "unavailable",
        ),
        "prior_unfilled_attempts": _first_present(
            execution_results.get("prior_unfilled_attempts"),
            summary.get("prior_unfilled_attempts"),
            evidence.get("prior_unfilled_attempts"),
            first_policy_row.get("prior_unfilled_attempts"),
            0,
        ),
        "escalation_reason": _first_present(
            execution_results.get("escalation_reason"),
            summary.get("escalation_reason"),
            evidence.get("escalation_reason"),
            first_policy_row.get("escalation_reason"),
            "none",
        ),
        "paper_live_divergence": _paper_live_divergence(root),
        "open_order_count": _open_order_count(snapshot),
    }


def _paper_live_divergence(repo_root: Path) -> str:
    path = repo_root / "outputs" / "reconciliation" / "live_vs_shadow" / "latest" / "live_vs_shadow_reconciliation.json"
    payload = _load_json(path)
    if not payload:
        return "unavailable"
    status = str(payload.get("status") or payload.get("reconciliation_status") or "UNKNOWN").upper()
    reason = payload.get("reason") or payload.get("reason_code")
    return f"{status}" + (f" ({reason})" if reason else "")


def render_dynamic_email_sections(
    repo_root: Path | str,
    trade_date: str,
    *,
    live_pilot_run_root: Path | str | None = None,
) -> dict[str, str]:
    sleeve_payload = build_dynamic_sleeve_rows(repo_root, trade_date)
    live_payload = build_live_pilot_account_payload(
        repo_root,
        run_root=live_pilot_run_root,
    )

    text_lines = [
        "",
        "--- Dynamic Sleeve Inventory ---",
        "Hypothetical Shadow metrics only; these are not Paper account returns.",
        f"Status: {sleeve_payload.get('status')}",
        f"Shadow artifact source: {sleeve_payload.get('shadow_source')} ({sleeve_payload.get('shadow_dir')})",
        f"Sleeve | Lifecycle | Role | Artifact | Data | Shadow 1D ({sleeve_payload.get('shadow_as_of_date')}) | Since inception | Turnover | Concentration | Readiness",
        "------ | --------- | ---- | -------- | ---- | ----- | --------------- | -------- | ------------- | ---------",
    ]
    for row in sleeve_payload.get("rows") or []:
        text_lines.append(
            " | ".join(
                [
                    str(row.get("display_name") or row.get("strategy_id")),
                    str(row.get("lifecycle")),
                    str(row.get("role")),
                    str(row.get("artifact_status")),
                    str(row.get("data_status")),
                    str(row.get("today_return")),
                    str(row.get("since_inception_return")),
                    str(row.get("turnover")),
                    str(row.get("concentration")),
                    str(row.get("readiness")),
                ]
            )
        )
    if sleeve_payload.get("registry_error"):
        text_lines.append(f"Registry warning: {sleeve_payload.get('registry_error')}")

    account = live_payload.get("account") if isinstance(live_payload.get("account"), dict) else {}
    evidence = live_payload.get("evidence_metrics") if isinstance(live_payload.get("evidence_metrics"), dict) else {}
    text_lines.extend(
        [
            "",
            "--- Live Pilot / Account ---",
            f"Status: {live_payload.get('status')}",
            f"Latest live pilot run id: {live_payload.get('latest_run_id')}",
            f"Latest live pilot run root: {live_payload.get('latest_run_root')}",
            f"Cash: {_fmt_money(account.get('cash'))}",
            f"Equity: {_fmt_money(account.get('equity') or account.get('portfolio_value'))}",
            f"Buying power: {_fmt_money(account.get('buying_power'))}",
            f"Open orders: {len(live_payload.get('open_orders') or [])}",
            f"Filled pilot orders: {len(live_payload.get('filled_orders') or [])}",
            f"Current live pilot positions: {len(live_payload.get('positions') or [])}",
            f"Latest reconciliation status: {live_payload.get('reconciliation_status')}",
            f"Paper/live divergence: {live_payload.get('paper_live_divergence')}",
            f"Fill rate: {_fmt_pct(evidence.get('fill_rate'))}",
            f"Cash deployment rate: {_fmt_pct(evidence.get('cash_deployment_rate'))}",
            f"Idle cash reason: {evidence.get('idle_cash_reason') or 'unavailable'}",
            f"Approved buys: {live_payload.get('approved_buy_count')}",
            f"Submitted buys: {live_payload.get('submitted_buy_count')}",
            f"Unfilled buys: {live_payload.get('unfilled_buy_count')}",
            f"Escalated buys: {live_payload.get('escalated_buy_count')}",
            f"Entry execution policy: {live_payload.get('entry_execution_policy')}",
            f"Submitted order type: {live_payload.get('submitted_order_type')}",
            f"Marketable/passive orders: {live_payload.get('marketable_order_count')}/{live_payload.get('passive_order_count')}",
            f"Prior unfilled attempts: {live_payload.get('prior_unfilled_attempts')}",
            f"Escalation reason: {live_payload.get('escalation_reason')}",
            f"Remaining blocked/suppressed buys: {live_payload.get('remaining_blocked_or_suppressed_buy_count')}",
            f"Blocked/suppressed reason: {live_payload.get('blocked_or_suppressed_buy_reason')}",
        ]
    )

    sleeve_rows_html = "".join(
        "<tr>"
        + "".join(
            f"<td>{html.escape(str(value))}</td>"
            for value in (
                row.get("display_name") or row.get("strategy_id"),
                row.get("lifecycle"),
                row.get("role"),
                row.get("artifact_status"),
                row.get("data_status"),
                row.get("today_return"),
                row.get("since_inception_return"),
                row.get("turnover"),
                row.get("concentration"),
                row.get("readiness"),
            )
        )
        + "</tr>"
        for row in sleeve_payload.get("rows") or []
    )
    html_table_style = "border-collapse:collapse;font-family:monospace;font-size:0.92em;"
    html_cell_style = "border:1px solid #ddd;padding:4px 6px;"
    html_section = (
        "<h3>Dynamic Sleeve Inventory</h3>"
        "<p><b>Scope:</b> Hypothetical Shadow metrics only; these are not Paper account returns.</p>"
        f"<p><b>Status:</b> {html.escape(str(sleeve_payload.get('status')))}; "
        f"<b>Shadow artifact source:</b> {html.escape(str(sleeve_payload.get('shadow_source')))}</p>"
        f"<table style='{html_table_style}'>"
        "<thead><tr>"
        + "".join(
            f"<th style='{html_cell_style}'>{label}</th>"
            for label in (
                "Sleeve",
                "Lifecycle",
                "Role",
                "Artifact",
                "Data",
                f"Shadow 1D ({sleeve_payload.get('shadow_as_of_date')})",
                "Since inception",
                "Turnover",
                "Concentration",
                "Readiness",
            )
        )
        + "</tr></thead><tbody>"
        + sleeve_rows_html
        + "</tbody></table>"
        "<h3>Live Pilot / Account</h3>"
        f"<p><b>Status:</b> {html.escape(str(live_payload.get('status')))} | "
        f"<b>Run:</b> {html.escape(str(live_payload.get('latest_run_id')))} | "
        f"<b>Reconciliation:</b> {html.escape(str(live_payload.get('reconciliation_status')))}</p>"
        f"<p><b>Cash:</b> {html.escape(_fmt_money(account.get('cash')))} | "
        f"<b>Equity:</b> {html.escape(_fmt_money(account.get('equity') or account.get('portfolio_value')))} | "
        f"<b>Buying power:</b> {html.escape(_fmt_money(account.get('buying_power')))} | "
        f"<b>Open orders:</b> {len(live_payload.get('open_orders') or [])} | "
        f"<b>Filled pilot orders:</b> {len(live_payload.get('filled_orders') or [])}</p>"
        f"<p><b>Fill rate:</b> {html.escape(_fmt_pct(evidence.get('fill_rate')))} | "
        f"<b>Cash deployment:</b> {html.escape(_fmt_pct(evidence.get('cash_deployment_rate')))} | "
        f"<b>Idle cash reason:</b> {html.escape(str(evidence.get('idle_cash_reason') or 'unavailable'))} | "
        f"<b>Paper/live divergence:</b> {html.escape(str(live_payload.get('paper_live_divergence')))}</p>"
        f"<p><b>Approved buys:</b> {html.escape(str(live_payload.get('approved_buy_count')))} | "
        f"<b>Submitted buys:</b> {html.escape(str(live_payload.get('submitted_buy_count')))} | "
        f"<b>Unfilled buys:</b> {html.escape(str(live_payload.get('unfilled_buy_count')))} | "
        f"<b>Escalated buys:</b> {html.escape(str(live_payload.get('escalated_buy_count')))}</p>"
        f"<p><b>Entry execution policy:</b> {html.escape(str(live_payload.get('entry_execution_policy')))} | "
        f"<b>Submitted order type:</b> {html.escape(str(live_payload.get('submitted_order_type')))} | "
        f"<b>Marketable/passive:</b> {html.escape(str(live_payload.get('marketable_order_count')))}"
        f"/{html.escape(str(live_payload.get('passive_order_count')))} | "
        f"<b>Prior unfilled attempts:</b> {html.escape(str(live_payload.get('prior_unfilled_attempts')))}</p>"
        f"<p><b>Escalation reason:</b> {html.escape(str(live_payload.get('escalation_reason')))} | "
        f"<b>Remaining blocked/suppressed buys:</b> "
        f"{html.escape(str(live_payload.get('remaining_blocked_or_suppressed_buy_count')))} | "
        f"<b>Reason:</b> {html.escape(str(live_payload.get('blocked_or_suppressed_buy_reason')))}</p>"
    )

    return {"text": "\n".join(text_lines) + "\n", "html": html_section}
