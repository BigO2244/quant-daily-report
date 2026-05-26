"""Read-only research source readiness diagnostics for FR-030/Orion packets."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    repo_root = Path(__file__).resolve().parents[2]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

from scripts.research.check_price_hydration_health import inspect_hydration_health


DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _read_json(path: Path) -> tuple[dict[str, Any], str | None]:
    if not path.exists():
        return {}, None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {}, f"json_read_error:{exc}"
    if not isinstance(payload, dict):
        return {}, "json_read_error:not_object"
    return payload, None


def _latest_shadow_trade_date(repo_root: Path) -> str | None:
    shadow_root = repo_root / "outputs" / "shadow_candidates"
    if not shadow_root.exists():
        return None
    candidates = [
        path.name
        for path in shadow_root.iterdir()
        if path.is_dir() and DATE_RE.match(path.name)
    ]
    return sorted(candidates)[-1] if candidates else None


def _first_present(payload: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = payload.get(key)
        if value not in (None, ""):
            return value
    return None


def _hydration_max_cache_date(hydration: dict[str, Any]) -> Any:
    direct = _first_present(
        hydration,
        "max_cache_date",
        "cache_max_date",
        "max_price_date",
        "data_through_date",
        "as_of_date",
        "trade_date",
    )
    if direct:
        return direct
    cache = hydration.get("cache") if isinstance(hydration.get("cache"), dict) else {}
    direct_cache = _first_present(cache, "max_cache_date", "cache_max_date", "max_price_date")
    if direct_cache:
        return direct_cache
    coverage = hydration.get("coverage") if isinstance(hydration.get("coverage"), dict) else {}
    return _first_present(coverage, "max_cache_date", "cache_max_date", "max_price_date")


def _recommended_next_action(reasons: list[str], hydration_classification: str | None = None) -> str:
    if hydration_classification == "waiting_for_post_close":
        return (
            "Post-close hydration window has not occurred yet. Same-day shadow artifacts are expected "
            "to remain incomplete until after 18:30 ET."
        )
    joined = " ".join(reasons).lower()
    if not reasons:
        return "Source readiness is READY; run Orion.command or build the FR-030 packet normally."
    if "price_cache_stale" in joined or "hydration" in joined or "cache date" in joined:
        return (
            "Wait for post-close hydration and shadow artifact refresh, then rerun Orion.command. "
            "Do not force an incomplete packet unless diagnosing source readiness."
        )
    if "shadow_performance" in joined or "comparison" in joined or "strategies" in joined:
        return (
            "Wait for shadow candidate comparison/performance artifacts to refresh after hydration, "
            "then rerun Orion.command."
        )
    return "Inspect the listed source artifacts before using FR-030 for research interpretation."


def inspect_source_readiness(
    *,
    repo_root: Path,
    trade_date: str | None = None,
    latest: bool = False,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    repo_root = repo_root.resolve()
    resolved_trade_date = trade_date
    if latest or not resolved_trade_date:
        resolved_trade_date = _latest_shadow_trade_date(repo_root)

    if not resolved_trade_date:
        return {
            "trade_date": None,
            "shadow_performance_path": None,
            "shadow_data_status": "UNKNOWN",
            "shadow_data_reason": "NO_SHADOW_CANDIDATE_DATE",
            "comparison_path": None,
            "comparison_status": "UNKNOWN",
            "strategy_count": 0,
            "price_hydration_status_path": None,
            "price_hydration_status": "UNKNOWN",
            "cache_max_date": None,
            "max_cache_date": None,
            "hydration_covers_trade_date": False,
            "hydration_health": {},
            "stale_days": None,
            "cache_lag_interpretation": "structurally_broken",
            "hydration_state_classification": "unknown",
            "hydration_window_passed": False,
            "symbols_missing_count": None,
            "missing_symbols_sample": [],
            "partial_or_complete": "UNKNOWN",
            "readiness_explanation": "No dated shadow candidate directory was found.",
            "source_readiness": "UNKNOWN",
            "blocking_reasons": ["no dated shadow candidate directory found"],
            "recommended_next_action": "Inspect shadow candidate generation before running FR-030.",
        }

    shadow_dir = repo_root / "outputs" / "shadow_candidates" / resolved_trade_date
    performance_path = shadow_dir / "shadow_performance.json"
    comparison_path = shadow_dir / "comparison.json"
    hydration_path = repo_root / "outputs" / "price_hydration" / resolved_trade_date / "status.json"
    hydration_health = inspect_hydration_health(repo_root=repo_root, trade_date=resolved_trade_date, now=now)

    performance, performance_error = _read_json(performance_path)
    comparison, comparison_error = _read_json(comparison_path)
    hydration, hydration_error = _read_json(hydration_path)

    strategies = comparison.get("strategies") if isinstance(comparison.get("strategies"), dict) else {}
    shadow_data_status = performance.get("data_status") if performance else "MISSING"
    shadow_data_reason = performance.get("data_reason")
    comparison_status = comparison.get("status") if comparison else "MISSING"
    if comparison and comparison_status in (None, ""):
        comparison_status = "OK" if strategies else "UNKNOWN"
    strategy_count = len(strategies)
    price_hydration_status = hydration.get("status") if hydration else "MISSING"
    max_cache_date = _hydration_max_cache_date(hydration) or hydration_health.get("cache_max_date")
    hydration_covers_trade_date = bool(max_cache_date and str(max_cache_date) >= str(resolved_trade_date))

    blocking_reasons: list[str] = []
    if not shadow_dir.exists():
        blocking_reasons.append(f"missing shadow candidate directory: outputs/shadow_candidates/{resolved_trade_date}")
    if not performance_path.exists():
        blocking_reasons.append("missing shadow_performance.json")
    if performance_error:
        blocking_reasons.append(f"shadow_performance.json {performance_error}")
    if shadow_data_status != "OK":
        blocking_reasons.append(f"shadow_performance.data_status={shadow_data_status}")
    if shadow_data_reason not in (None, "", "OK"):
        blocking_reasons.append(f"shadow_performance.data_reason={shadow_data_reason}")
    if not comparison_path.exists():
        blocking_reasons.append("missing comparison.json")
    if comparison_error:
        blocking_reasons.append(f"comparison.json {comparison_error}")
    if comparison_status != "OK":
        blocking_reasons.append(f"comparison.status={comparison_status}")
    if strategy_count == 0:
        blocking_reasons.append("comparison.strategies is empty")
    if not hydration_path.exists():
        blocking_reasons.append("missing price hydration status")
    if hydration_error:
        blocking_reasons.append(f"price hydration status {hydration_error}")
    if hydration_path.exists():
        if price_hydration_status != "OK":
            blocking_reasons.append(f"price_hydration.status={price_hydration_status}")
        elif not hydration_covers_trade_date:
            blocking_reasons.append("price hydration max cache date does not cover trade date")

    source_readiness = "READY" if not blocking_reasons else "INCOMPLETE"
    hydration_classification = str(hydration_health.get("hydration_state_classification") or hydration_health.get("hydration_interpretation") or "unknown")
    if source_readiness == "READY":
        readiness_explanation = "All required shadow and hydration source artifacts are ready."
    elif hydration_classification == "waiting_for_post_close":
        readiness_explanation = (
            "Incomplete because the post-close hydration window has not occurred yet; "
            "same-day shadow artifacts are expected to remain incomplete until after 18:30 ET."
        )
    elif hydration_classification in {"stale_but_recoverable", "partial"}:
        readiness_explanation = "Incomplete because post-close hydration evidence is stale, missing, or partial."
    elif hydration_classification == "structurally_broken":
        readiness_explanation = "Incomplete because hydration evidence appears structurally broken or unreadable."
    else:
        readiness_explanation = "Incomplete because required source artifacts are missing or not usable."

    return {
        "trade_date": resolved_trade_date,
        "shadow_performance_path": str(performance_path.relative_to(repo_root)),
        "shadow_data_status": shadow_data_status,
        "shadow_data_reason": shadow_data_reason,
        "comparison_path": str(comparison_path.relative_to(repo_root)),
        "comparison_status": comparison_status,
        "strategy_count": int(strategy_count),
        "price_hydration_status_path": str(hydration_path.relative_to(repo_root)),
        "price_hydration_status": price_hydration_status,
        "cache_max_date": max_cache_date,
        "max_cache_date": max_cache_date,
        "hydration_covers_trade_date": hydration_covers_trade_date,
        "hydration_health": hydration_health,
        "stale_days": hydration_health.get("stale_days"),
        "cache_lag_interpretation": hydration_classification,
        "hydration_state_classification": hydration_classification,
        "hydration_window_passed": hydration_health.get("hydration_window_passed"),
        "symbols_missing_count": hydration_health.get("symbols_missing_count"),
        "missing_symbols_sample": list(hydration_health.get("missing_symbols_sample") or []),
        "partial_or_complete": hydration_health.get("partial_or_complete"),
        "readiness_explanation": readiness_explanation,
        "source_readiness": source_readiness,
        "blocking_reasons": blocking_reasons,
        "recommended_next_action": _recommended_next_action(blocking_reasons, hydration_classification),
    }


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Research Source Readiness",
        "",
        f"- Trade date: {payload.get('trade_date') or 'UNKNOWN'}",
        f"- Source readiness: {payload.get('source_readiness')}",
        f"- Shadow data status: {payload.get('shadow_data_status')}",
        f"- Shadow data reason: {payload.get('shadow_data_reason') or 'none'}",
        f"- Comparison status: {payload.get('comparison_status')}",
        f"- Strategy count: {payload.get('strategy_count')}",
        f"- Price hydration status: {payload.get('price_hydration_status')}",
        f"- Max cache date: {payload.get('max_cache_date') or 'unknown'}",
        f"- Stale days: {payload.get('stale_days') if payload.get('stale_days') is not None else 'unknown'}",
        f"- Hydration classification: {payload.get('hydration_state_classification') or payload.get('cache_lag_interpretation') or 'unknown'}",
        f"- Hydration window passed: {payload.get('hydration_window_passed')}",
        f"- Missing symbols: {payload.get('symbols_missing_count') if payload.get('symbols_missing_count') is not None else 'unknown'}",
        f"- Readiness explanation: {payload.get('readiness_explanation') or 'unknown'}",
        "",
        "## Blocking Reasons",
    ]
    reasons = list(payload.get("blocking_reasons") or [])
    if reasons:
        lines.extend([f"- {reason}" for reason in reasons])
    else:
        lines.append("- none")
    lines.extend(
        [
            "",
            "## Recommended Next Action",
            "",
            str(payload.get("recommended_next_action") or "Inspect source artifacts."),
            "",
        ]
    )
    return "\n".join(lines)


def render_text(payload: dict[str, Any]) -> str:
    reasons = list(payload.get("blocking_reasons") or [])
    lines = [
        f"[SOURCE] Trade Date: {payload.get('trade_date') or 'UNKNOWN'}",
        f"[SOURCE] Readiness: {payload.get('source_readiness')}",
        f"[SOURCE] Shadow: status={payload.get('shadow_data_status')} reason={payload.get('shadow_data_reason') or 'none'}",
        f"[SOURCE] Comparison: status={payload.get('comparison_status')} strategies={payload.get('strategy_count')}",
        f"[SOURCE] Hydration: status={payload.get('price_hydration_status')} max_cache_date={payload.get('max_cache_date') or 'unknown'}",
        f"[SOURCE] Hydration Detail: stale_days={payload.get('stale_days') if payload.get('stale_days') is not None else 'unknown'} missing_symbols={payload.get('symbols_missing_count') if payload.get('symbols_missing_count') is not None else 'unknown'} cause={payload.get('hydration_state_classification') or payload.get('cache_lag_interpretation') or 'unknown'}",
        f"[SOURCE] Explanation: {payload.get('readiness_explanation') or 'unknown'}",
    ]
    if reasons:
        lines.append("[SOURCE] Blocking Reasons:")
        lines.extend([f"[SOURCE] - {reason}" for reason in reasons])
    lines.append(f"[SOURCE] Next Action: {payload.get('recommended_next_action')}")
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Check read-only FR-030/Orion source readiness.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--trade-date", help="Trade date to inspect, YYYY-MM-DD.")
    group.add_argument("--latest", action="store_true", help="Inspect the latest dated shadow candidate directory.")
    parser.add_argument("--repo-root", default=".", help="Repository root to inspect. Defaults to current directory.")
    parser.add_argument("--json", action="store_true", help="Emit JSON.")
    parser.add_argument("--markdown", action="store_true", help="Emit Markdown.")
    parser.add_argument("--strict", action="store_true", help="Exit nonzero unless source_readiness is READY.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = inspect_source_readiness(
        repo_root=Path(args.repo_root),
        trade_date=args.trade_date,
        latest=bool(args.latest),
    )
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    elif args.markdown:
        print(render_markdown(payload))
    else:
        print(render_text(payload), end="")

    if args.strict and payload.get("source_readiness") != "READY":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
