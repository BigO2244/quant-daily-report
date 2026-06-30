from __future__ import annotations

import csv
import json
from datetime import date
from pathlib import Path
from typing import Any

from core.strategy_registry import load_strategy_registry


SCHEMA_VERSION = "alpha_evidence_chain_v1"
DEFAULT_OUTPUT_ROOT = Path("outputs/shadow_candidates")
REQUIRED_DAILY_FIELDS = (
    "nav",
    "return",
    "drawdown",
    "turnover",
    "holdings_ranks",
    "concentration",
    "hhi_effective_n",
    "cash_exposure",
    "artifact_freshness",
    "observed_day_count",
)
DATED_REQUIRED_ARTIFACTS = (
    "comparison.json",
    "delta.json",
    "shadow_evaluation.json",
    "shadow_performance.json",
)
LATEST_REQUIRED_ARTIFACTS = (
    "comparison.json",
    "shadow_evaluation.json",
)
PERFORMANCE_REQUIRED_ARTIFACTS = (
    "performance/shadow_nav_series.csv",
)


def build_alpha_evidence_chain_payload(
    *,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    trade_date: str | None = None,
    assess_latest_pointer: bool = True,
) -> dict[str, Any]:
    output_root = Path(output_root)
    resolved_trade_date = trade_date or latest_dated_trade_date(output_root)
    dated_dir = output_root / str(resolved_trade_date) if resolved_trade_date else None
    evaluation = _read_json(dated_dir / "shadow_evaluation.json") if dated_dir else None
    comparison = _read_json(dated_dir / "comparison.json") if dated_dir else None
    nav_rows = _read_nav_rows(output_root / "performance" / "shadow_nav_series.csv")
    nav_row = _nav_row_for_date(nav_rows, str(resolved_trade_date or ""))
    latest_status = (
        _latest_pointer_status(output_root=output_root, trade_date=resolved_trade_date)
        if assess_latest_pointer
        else {"status": "NOT_ASSESSED", "reason": "dated artifact producer runs before latest publication", "artifacts": []}
    )
    artifacts = _artifact_status(output_root=output_root, dated_dir=dated_dir)

    strategies = []
    for slug in required_strategy_slugs(trade_date=resolved_trade_date):
        strategy_payload = _strategy_payload(
            slug=slug,
            output_root=output_root,
            dated_dir=dated_dir,
            trade_date=resolved_trade_date,
            evaluation=evaluation,
            comparison=comparison,
            nav_row=nav_row,
            nav_rows=nav_rows,
            latest_status=latest_status,
        )
        strategies.append(strategy_payload)

    blocked = [
        {"strategy": item["strategy_id"], "missing": item["missing_fields"]}
        for item in strategies
        if item["missing_fields"]
    ]
    evidence_ready = not blocked and bool(resolved_trade_date)
    reporting_current = latest_status["status"] == "CURRENT"
    status = "OK" if evidence_ready and reporting_current else "WARN" if evidence_ready else "BLOCKED"
    return {
        "schema_version": SCHEMA_VERSION,
        "trade_date": resolved_trade_date,
        "governance_label": "REPORTING_ONLY",
        "execution_impact": "NO_RUNTIME_CHANGE",
        "non_goals": [
            "no broker commands",
            "no execution scripts",
            "no precompute",
            "no allocation changes",
            "no sizing changes",
            "no routing changes",
            "no min-notional changes",
            "no rebudgeting changes",
            "no strategy lifecycle changes",
            "no alpha parameter changes",
            "no promotion",
        ],
        "status": status,
        "can_start_20_60_day_evidence_collection": evidence_ready,
        "reporting_status": (
            "CURRENT"
            if reporting_current
            else "NOT_ASSESSED"
            if latest_status["status"] == "NOT_ASSESSED"
            else "STALE_OR_MISSING_LATEST"
        ),
        "required_daily_fields": list(REQUIRED_DAILY_FIELDS),
        "artifact_status": artifacts,
        "latest_pointer": latest_status,
        "strategies": strategies,
        "blocked_reasons": blocked,
        "daily_evidence_checklist": _daily_checklist_summary(strategies),
        "research_artifact_gaps": [
            {
                "artifact": "outputs/research/canonical_pit_replay/<date>/decision_tape_*.parquet",
                "status": "NOT_REQUIRED_FOR_DAILY_FORWARD_COLLECTION",
                "reason": "Needed for PIT replay/recompute lineage, not for daily shadow NAV evidence capture.",
            },
            {
                "artifact": "outputs/research/canonical_pit_replay/<date>/decision_tape_manifest.json",
                "status": "NOT_REQUIRED_FOR_DAILY_FORWARD_COLLECTION",
                "reason": "Needed for PIT replay/recompute lineage, not for daily shadow NAV evidence capture.",
            },
            {
                "artifact": "outputs/research/holdings_concentration_frontier",
                "status": "NOT_REQUIRED_FOR_DAILY_FORWARD_COLLECTION",
                "reason": "Daily concentration/HHI/effective-N are collected from shadow strategy artifacts; frontier remains separate research evidence.",
            },
        ],
    }


def write_alpha_evidence_chain_artifacts(
    *,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    trade_date: str | None = None,
    assess_latest_pointer: bool = True,
) -> dict[str, Any]:
    payload = build_alpha_evidence_chain_payload(
        output_root=output_root,
        trade_date=trade_date,
        assess_latest_pointer=assess_latest_pointer,
    )
    resolved_trade_date = payload.get("trade_date") or trade_date
    if not resolved_trade_date:
        return payload
    dated_dir = Path(output_root) / str(resolved_trade_date)
    dated_dir.mkdir(parents=True, exist_ok=True)
    (dated_dir / "alpha_evidence_chain.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (dated_dir / "alpha_evidence_chain.md").write_text(render_alpha_evidence_chain_markdown(payload), encoding="utf-8")
    return payload


def render_alpha_evidence_chain_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Alpha Evidence Chain",
        "",
        f"- Trade date: `{payload.get('trade_date') or 'UNKNOWN'}`",
        f"- Status: `{payload.get('status')}`",
        f"- Reporting status: `{payload.get('reporting_status')}`",
        f"- 20/60-day evidence collection can start: `{payload.get('can_start_20_60_day_evidence_collection')}`",
        "- Runtime impact: `NO_RUNTIME_CHANGE`",
        "",
        "## Daily Checklist",
        "",
        "| Strategy | Status | Missing fields | Observed days | NAV | Return | Drawdown | Turnover | Concentration | HHI | Effective N | Cash |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in payload.get("strategies") or []:
        evidence = row.get("evidence") or {}
        concentration = evidence.get("concentration") or {}
        lines.append(
            "| {strategy} | {status} | {missing} | {observed} | {nav} | {ret} | {dd} | {turnover} | {top3} | {hhi} | {effective_n} | {cash} |".format(
                strategy=row.get("display_name") or row.get("strategy_id"),
                status=row.get("status"),
                missing=", ".join(row.get("missing_fields") or []) or "None",
                observed=_md(evidence.get("observed_day_count")),
                nav=_md(evidence.get("nav")),
                ret=_md(evidence.get("return")),
                dd=_md(evidence.get("drawdown")),
                turnover=_md(evidence.get("turnover")),
                top3=_md(concentration.get("top3_concentration")),
                hhi=_md((evidence.get("hhi_effective_n") or {}).get("hhi")),
                effective_n=_md((evidence.get("hhi_effective_n") or {}).get("effective_n")),
                cash=_md(evidence.get("cash_exposure")),
            )
        )
    lines.extend(
        [
            "",
            "## Latest Pointer",
            "",
            f"- Status: `{(payload.get('latest_pointer') or {}).get('status')}`",
            f"- Reason: `{(payload.get('latest_pointer') or {}).get('reason')}`",
            "",
            "## Runtime Boundary",
            "",
            "This artifact is reporting-only. It does not change live/paper execution, broker routing, allocation, sizing, min-notional, rebudgeting, strategy lifecycle, alpha parameters, or promotion state.",
            "",
        ]
    )
    return "\n".join(lines)


def required_strategy_slugs(*, trade_date: str | None = None) -> tuple[str, ...]:
    registry = load_strategy_registry()
    return tuple(
        entry.strategy_id
        for entry in registry.active_shadow_security_selection_entries()
        if _shadow_tracking_active_on(entry.shadow_tracking or {}, trade_date=trade_date)
    )


def _shadow_tracking_active_on(shadow_tracking: dict[str, Any], *, trade_date: str | None) -> bool:
    start_date = shadow_tracking.get("observation_start_date")
    if trade_date is None or not start_date:
        return True
    try:
        return date.fromisoformat(str(trade_date)) >= date.fromisoformat(str(start_date))
    except ValueError:
        return True


def latest_dated_trade_date(output_root: Path) -> str | None:
    if not output_root.exists():
        return None
    dates = [
        child.name
        for child in output_root.iterdir()
        if child.is_dir() and _looks_like_date(child.name)
    ]
    return sorted(dates)[-1] if dates else None


def _strategy_payload(
    *,
    slug: str,
    output_root: Path,
    dated_dir: Path | None,
    trade_date: str | None,
    evaluation: dict[str, Any] | None,
    comparison: dict[str, Any] | None,
    nav_row: dict[str, str] | None,
    nav_rows: list[dict[str, str]],
    latest_status: dict[str, Any],
) -> dict[str, Any]:
    entry = load_strategy_registry().get(slug)
    eval_row = ((evaluation or {}).get("strategies") or {}).get(slug) or {}
    comparison_row = ((comparison or {}).get("strategies") or {}).get(slug) or {}
    strategy_artifact = _read_json(dated_dir / f"{slug}.json") if dated_dir else None
    concentration = _first_dict(
        (strategy_artifact or {}).get("weight_concentration"),
        comparison_row.get("weight_concentration"),
    )
    holdings = _first_non_empty_list((strategy_artifact or {}).get("holdings"), comparison_row.get("holdings"))
    rank_table = _first_non_empty_list((strategy_artifact or {}).get("rank_table"), comparison_row.get("rank_table"))
    ranked_holdings_count = _ranked_holdings_count(holdings)
    hhi = _first_number(concentration.get("hhi"), eval_row.get("avg_hhi"))
    effective_n = _first_number(concentration.get("effective_n"), eval_row.get("avg_effective_n"))
    evidence = {
        "nav": _first_number((nav_row or {}).get(slug), eval_row.get("nav")),
        "return": _first_number(eval_row.get("daily_return")),
        "drawdown": _first_number(
            eval_row.get("max_drawdown"),
            _nav_series_drawdown(nav_rows=nav_rows, slug=slug, trade_date=trade_date),
        ),
        "turnover": _first_number((strategy_artifact or {}).get("expected_turnover"), comparison_row.get("expected_turnover"), eval_row.get("avg_turnover")),
        "holdings_ranks": {
            "holdings_count": len(holdings),
            "rank_rows": len(rank_table) if rank_table else ranked_holdings_count,
            "ranked_holdings_count": ranked_holdings_count,
            "rank_source": "rank_table" if rank_table else "holdings_momentum_rank" if ranked_holdings_count else None,
            "status": "PRESENT" if holdings and (rank_table or ranked_holdings_count) else "MISSING",
        },
        "concentration": {
            "top3_concentration": _first_number(concentration.get("top3_concentration"), eval_row.get("avg_top_3_concentration")),
            "top5_concentration": _first_number(concentration.get("top5_concentration")),
            "gross_exposure": _first_number(concentration.get("gross_exposure")),
        },
        "hhi_effective_n": {
            "hhi": hhi,
            "effective_n": effective_n,
        },
        "cash_exposure": _first_number(concentration.get("cash_weight"), eval_row.get("avg_cash_weight")),
        "artifact_freshness": {
            "dated_artifact_trade_date": (evaluation or {}).get("trade_date"),
            "requested_trade_date": trade_date,
            "latest_pointer_status": latest_status.get("status"),
        },
        "observed_day_count": _int_or_none(eval_row.get("rolling_count_of_valid_days")),
    }
    missing = _missing_fields(evidence=evidence, trade_date=trade_date, evaluation=evaluation, latest_status=latest_status)
    return {
        "strategy_id": slug,
        "display_name": entry.display_name if entry else slug,
        "source_variant": ((entry.shadow_tracking or {}).get("source_variant") if entry else None),
        "baseline_strategy_id": ((entry.shadow_tracking or {}).get("baseline_strategy_id") if entry else None),
        "status": "PASS" if not missing else "BLOCKED",
        "missing_fields": missing,
        "evidence": evidence,
        "source_artifacts": {
            "strategy_snapshot": str((dated_dir / f"{slug}.json") if dated_dir else output_root / "<date>" / f"{slug}.json"),
            "shadow_evaluation": str((dated_dir / "shadow_evaluation.json") if dated_dir else output_root / "<date>" / "shadow_evaluation.json"),
            "comparison": str((dated_dir / "comparison.json") if dated_dir else output_root / "<date>" / "comparison.json"),
            "nav_series": str(output_root / "performance" / "shadow_nav_series.csv"),
        },
    }


def _missing_fields(
    *,
    evidence: dict[str, Any],
    trade_date: str | None,
    evaluation: dict[str, Any] | None,
    latest_status: dict[str, Any],
) -> list[str]:
    missing: list[str] = []
    if evidence["nav"] is None:
        missing.append("nav")
    if evidence["return"] is None:
        missing.append("return")
    if evidence["drawdown"] is None:
        missing.append("drawdown")
    if evidence["turnover"] is None:
        missing.append("turnover")
    if (evidence["holdings_ranks"] or {}).get("status") != "PRESENT":
        missing.append("holdings_ranks")
    concentration = evidence["concentration"]
    if concentration.get("top3_concentration") is None:
        missing.append("concentration")
    hhi = evidence["hhi_effective_n"]
    if hhi.get("hhi") is None or hhi.get("effective_n") is None:
        missing.append("hhi_effective_n")
    if evidence["cash_exposure"] is None:
        missing.append("cash_exposure")
    if not trade_date or not isinstance(evaluation, dict) or evaluation.get("trade_date") != trade_date:
        missing.append("artifact_freshness")
    if evidence["observed_day_count"] is None:
        missing.append("observed_day_count")
    return missing


def _artifact_status(*, output_root: Path, dated_dir: Path | None) -> dict[str, Any]:
    dated = []
    for name in DATED_REQUIRED_ARTIFACTS:
        path = dated_dir / name if dated_dir else output_root / "<date>" / name
        dated.append({"artifact": name, "status": "PRESENT" if dated_dir and path.exists() else "MISSING", "path": str(path)})
    performance = []
    for name in PERFORMANCE_REQUIRED_ARTIFACTS:
        path = output_root / name
        performance.append({"artifact": name, "status": "PRESENT" if path.exists() else "MISSING", "path": str(path)})
    return {"dated": dated, "performance": performance}


def _latest_pointer_status(*, output_root: Path, trade_date: str | None) -> dict[str, Any]:
    if not trade_date:
        return {"status": "MISSING", "reason": "no dated shadow artifact directory found", "artifacts": []}
    artifacts = []
    stale = False
    missing = False
    for name in LATEST_REQUIRED_ARTIFACTS:
        path = output_root / "latest" / name
        payload = _read_json(path)
        artifact_date = payload.get("trade_date") if isinstance(payload, dict) else None
        artifact_status = "CURRENT" if artifact_date == trade_date else "MISSING" if payload is None else "STALE"
        if artifact_status == "MISSING":
            missing = True
        if artifact_status == "STALE":
            stale = True
        artifacts.append({"artifact": name, "path": str(path), "trade_date": artifact_date, "status": artifact_status})
    status = "MISSING" if missing else "STALE" if stale else "CURRENT"
    reason = "latest pointer matches dated trade_date" if status == "CURRENT" else "latest pointer missing or does not match dated trade_date"
    return {"status": status, "reason": reason, "artifacts": artifacts}


def _daily_checklist_summary(strategies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for strategy in strategies:
        evidence = strategy.get("evidence") or {}
        rows.append(
            {
                "strategy_id": strategy.get("strategy_id"),
                "status": strategy.get("status"),
                **{field: field not in (strategy.get("missing_fields") or []) for field in REQUIRED_DAILY_FIELDS},
                "observed_day_count": evidence.get("observed_day_count"),
            }
        )
    return rows


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _read_nav_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _nav_row_for_date(rows: list[dict[str, str]], trade_date: str) -> dict[str, str] | None:
    for row in rows:
        if row.get("date") == trade_date:
            return row
    return None


def _first_dict(*items: Any) -> dict[str, Any]:
    for item in items:
        if isinstance(item, dict):
            return item
    return {}


def _first_list(*items: Any) -> list[Any]:
    for item in items:
        if isinstance(item, list):
            return item
    return []


def _first_non_empty_list(*items: Any) -> list[Any]:
    fallback: list[Any] = []
    for item in items:
        if not isinstance(item, list):
            continue
        if item:
            return item
        fallback = item
    return fallback


def _first_number(*items: Any) -> float | None:
    for item in items:
        try:
            if item in ("", None):
                continue
            return float(item)
        except (TypeError, ValueError):
            continue
    return None


def _int_or_none(value: Any) -> int | None:
    try:
        if value in ("", None):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _ranked_holdings_count(holdings: list[Any]) -> int:
    count = 0
    for item in holdings:
        if not isinstance(item, dict):
            continue
        rank = item.get("momentum_rank", item.get("rank"))
        if _first_number(rank) is not None:
            count += 1
    return count


def _nav_series_drawdown(*, nav_rows: list[dict[str, str]], slug: str, trade_date: str | None) -> float | None:
    navs: list[float] = []
    for row in sorted(nav_rows, key=lambda item: str(item.get("date") or "")):
        row_date = str(row.get("date") or "")
        if trade_date and row_date and row_date > trade_date:
            continue
        value = _first_number(row.get(slug))
        if value is not None:
            navs.append(value)
    if not navs:
        return None
    peak = navs[0]
    max_drawdown = 0.0
    for nav in navs:
        if nav > peak:
            peak = nav
        if peak > 0.0:
            max_drawdown = min(max_drawdown, (nav / peak) - 1.0)
    return round(float(max_drawdown), 10)


def _looks_like_date(value: str) -> bool:
    parts = value.split("-")
    return len(parts) == 3 and all(part.isdigit() for part in parts) and len(parts[0]) == 4


def _md(value: Any) -> str:
    return "NA" if value is None else str(value)
