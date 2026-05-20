from __future__ import annotations

import html
import json
import datetime as dt
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from paper.trading_calendar import is_trading_day, prev_trading_day


MODEL_SLUGS = ("caerus_polaris", "caerus_orion", "caerus_lyra")
DISPLAY_NAMES = {
    "caerus_polaris": "Polaris",
    "caerus_orion": "Orion",
    "caerus_lyra": "Lyra",
}
SUMMARY_KEYS = {
    "caerus_polaris": "polaris",
    "caerus_orion": "orion",
    "caerus_lyra": "lyra",
}
BENCHMARK_SLUG = "spy_benchmark"
BLOCKED_LANGUAGE = ("promote", "replace", "deploy capital")
ET = ZoneInfo("America/New_York")
MARKET_EOD_READY_TIME = dt.time(hour=16, minute=15)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _as_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _fmt_pct(value: Any, *, signed: bool = True) -> str:
    number = _as_float(value)
    if number is None:
        return "UNAVAILABLE"
    prefix = "+" if signed and number >= 0 else ""
    return f"{prefix}{number:.2%}"


def _fmt_status(value: Any) -> str:
    raw = str(value or "").strip()
    return raw if raw else "UNAVAILABLE"


def _current_et(now: dt.datetime | None = None) -> dt.datetime:
    if now is None:
        return dt.datetime.now(ET)
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return now.astimezone(ET)


def _latest_completed_trading_day(now: dt.datetime | None = None) -> str:
    now_et = _current_et(now)
    today = now_et.date().isoformat()
    if is_trading_day(today) and now_et.time() >= MARKET_EOD_READY_TIME:
        return today
    return prev_trading_day(today)


def _load_shadow_bundle(
    repo_root: Path,
    trade_date: str,
) -> tuple[Path, dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None]:
    dated_dir = repo_root / "outputs" / "shadow_candidates" / trade_date
    return (
        dated_dir,
        _read_json(dated_dir / "shadow_evaluation.json"),
        _read_json(dated_dir / "comparison.json"),
        _read_json(dated_dir / "feedback_loop_summary.json"),
    )


def _price_cache_stale_no_data(
    evaluation: dict[str, Any] | None,
    comparison: dict[str, Any] | None,
) -> bool:
    if not isinstance(comparison, dict):
        return False
    if comparison.get("status") == "NO_DATA" and comparison.get("reason_code") == "PRICE_CACHE_STALE":
        return True
    strategies = (evaluation or {}).get("strategies")
    if not isinstance(strategies, dict):
        return False
    for payload in strategies.values():
        if not isinstance(payload, dict):
            continue
        if payload.get("data_status") == "NO_DATA" and payload.get("data_reason") == "PRICE_CACHE_STALE":
            return True
    return False


def _has_complete_shadow_bundle(
    evaluation: dict[str, Any] | None,
    comparison: dict[str, Any] | None,
    feedback: dict[str, Any] | None,
) -> bool:
    return evaluation is not None and comparison is not None and feedback is not None


def _should_use_completed_session_snapshot(
    *,
    repo_root: Path,
    requested_trade_date: str,
    evaluation: dict[str, Any] | None,
    comparison: dict[str, Any] | None,
    now: dt.datetime | None,
) -> str | None:
    completed_trade_date = _latest_completed_trading_day(now)
    if completed_trade_date >= requested_trade_date:
        return None
    if not _price_cache_stale_no_data(evaluation, comparison):
        return None
    _, completed_evaluation, completed_comparison, completed_feedback = _load_shadow_bundle(repo_root, completed_trade_date)
    if not _has_complete_shadow_bundle(completed_evaluation, completed_comparison, completed_feedback):
        return None
    if _price_cache_stale_no_data(completed_evaluation, completed_comparison):
        return None
    return completed_trade_date


def _data_unavailable_reason(payload: dict[str, Any], comparison: dict[str, Any]) -> str:
    reason = str(payload.get("data_reason") or comparison.get("reason_code") or "").strip()
    parts = [reason] if reason else []
    data = comparison.get("data") if isinstance(comparison.get("data"), dict) else {}
    coverage = data.get("coverage") if isinstance(data.get("coverage"), dict) else {}
    coverage_end = str(coverage.get("end_date") or "").strip()
    if coverage_end:
        parts.append(f"cache coverage through {coverage_end}")
    return "; ".join(parts) if parts else "daily data unavailable"


def _strategy_block(slug: str, evaluation: dict[str, Any], comparison: dict[str, Any], feedback: dict[str, Any]) -> list[str]:
    strategies = evaluation.get("strategies") if isinstance(evaluation.get("strategies"), dict) else {}
    payload = strategies.get(slug) if isinstance(strategies.get(slug), dict) else {}
    spy = strategies.get(BENCHMARK_SLUG) if isinstance(strategies.get(BENCHMARK_SLUG), dict) else {}
    comparison_strategies = comparison.get("strategies") if isinstance(comparison.get("strategies"), dict) else {}
    comparison_payload = comparison_strategies.get(slug) if isinstance(comparison_strategies.get(slug), dict) else {}
    feedback_strategies = feedback.get("strategies") if isinstance(feedback.get("strategies"), dict) else {}
    feedback_payload = feedback_strategies.get(SUMMARY_KEYS[slug]) if isinstance(feedback_strategies.get(SUMMARY_KEYS[slug]), dict) else {}
    if not payload:
        return [
            f"{DISPLAY_NAMES[slug]}:",
            "Artifact status: DEGRADED",
            f"Diagnostics unavailable: shadow_evaluation.json missing strategy {slug}",
        ]

    data_status = str(payload.get("data_status") or "UNAVAILABLE")
    artifact_status = "OK" if data_status == "OK" else "DEGRADED"

    cumulative = _as_float(payload.get("cumulative_return"))
    polaris = strategies.get("caerus_polaris") if isinstance(strategies.get("caerus_polaris"), dict) else {}
    polaris_cumulative = _as_float(polaris.get("cumulative_return"))
    spy_cumulative = _as_float(spy.get("cumulative_return"))
    vs_polaris = cumulative - polaris_cumulative if slug != "caerus_polaris" and cumulative is not None and polaris_cumulative is not None else None
    vs_spy = _as_float(payload.get("excess_return_vs_spy"))
    if vs_spy is None and cumulative is not None and spy_cumulative is not None:
        vs_spy = cumulative - spy_cumulative

    concentration = comparison_payload.get("weight_concentration") if isinstance(comparison_payload.get("weight_concentration"), dict) else {}
    if data_status == "OK":
        today_line = f"Today: {_fmt_pct(payload.get('daily_return'))}"
    else:
        today_line = f"Today: unavailable ({_data_unavailable_reason(payload, comparison)})"
    lines = [
        f"{DISPLAY_NAMES[slug]}:",
        f"Artifact status: {artifact_status}",
        f"Data status: {data_status}",
        today_line,
        f"Since inception: {_fmt_pct(cumulative)}",
    ]
    if slug != "caerus_polaris":
        lines.append(f"vs Polaris: {_fmt_pct(vs_polaris)}")
    lines.extend(
        [
            f"vs SPY: {_fmt_pct(vs_spy)}",
            f"Turnover: {_fmt_pct(comparison_payload.get('expected_turnover'), signed=False)}",
            f"Top-3 concentration: {_fmt_pct(concentration.get('top3_concentration'), signed=False)}",
            f"Constituent changes: {_fmt_status(payload.get('constituent_change_count'))}",
            f"Learning readiness: {_fmt_status(feedback_payload.get('learning_readiness'))}",
            f"Diagnostic state: {_diagnostic_state(payload=payload, feedback=feedback_payload)}",
        ]
    )
    return lines


def _diagnostic_state(*, payload: dict[str, Any], feedback: dict[str, Any]) -> str:
    readiness = str(feedback.get("learning_readiness") or "").upper()
    valid_days = int(payload.get("rolling_count_of_valid_days") or 0)
    data_status = str(payload.get("data_status") or "")
    if data_status and data_status != "OK":
        return f"data unavailable ({data_status})"
    if valid_days < 10:
        return "insufficient history"
    if readiness == "LOW":
        return "learning gap"
    if readiness == "MEDIUM":
        return "building evidence"
    return "stable"


def build_shadow_scoreboard(
    repo_root: Path,
    trade_date: str,
    *,
    now: dt.datetime | None = None,
) -> dict[str, str]:
    dated_dir, evaluation, comparison, feedback = _load_shadow_bundle(repo_root, trade_date)
    if not dated_dir.exists():
        completed_trade_date = _latest_completed_trading_day(now)
        if completed_trade_date < trade_date:
            completed_dir, completed_evaluation, completed_comparison, completed_feedback = _load_shadow_bundle(
                repo_root,
                completed_trade_date,
            )
            if completed_dir.exists() and _has_complete_shadow_bundle(
                completed_evaluation,
                completed_comparison,
                completed_feedback,
            ):
                dated_dir = completed_dir
                evaluation = completed_evaluation
                comparison = completed_comparison
                feedback = completed_feedback
                trade_date = completed_trade_date
            else:
                reason = f"shadow directory missing for {trade_date}"
                return _unavailable(reason)
        else:
            reason = f"shadow directory missing for {trade_date}"
            return _unavailable(reason)
    fallback_trade_date = _should_use_completed_session_snapshot(
        repo_root=repo_root,
        requested_trade_date=trade_date,
        evaluation=evaluation,
        comparison=comparison,
        now=now,
    )
    if fallback_trade_date:
        dated_dir, evaluation, comparison, feedback = _load_shadow_bundle(repo_root, fallback_trade_date)
    missing = []
    if evaluation is None:
        missing.append("shadow_evaluation.json")
    if comparison is None:
        missing.append("comparison.json")
    if feedback is None:
        missing.append("feedback_loop_summary.json")
    if missing:
        return _unavailable("missing " + ", ".join(missing))

    lines = [
        "",
        "--- Shadow Strategy Snapshot ---",
        "Diagnostic only; no trading or strategy-change instruction is implied.",
        f"Artifact status: {'DEGRADED' if comparison.get('status') == 'NO_DATA' else 'OK'}",
        f"Snapshot as of: {fallback_trade_date or trade_date}",
        "",
    ]
    for slug in MODEL_SLUGS:
        lines.extend(_strategy_block(slug, evaluation, comparison, feedback))
        lines.append("")
    text = "\n".join(lines).rstrip() + "\n"
    lowered = text.lower()
    if any(blocked in lowered for blocked in BLOCKED_LANGUAGE):
        return _unavailable("scoreboard language policy violation")
    return {
        "status": "OK",
        "text": text,
        "html": _html_from_text(text),
    }


def _unavailable(reason: str) -> dict[str, str]:
    text = f"\n--- Shadow Strategy Snapshot ---\nShadow snapshot unavailable: {reason}\n"
    return {
        "status": "UNAVAILABLE",
        "text": text,
        "html": "<h3>Shadow Strategy Snapshot</h3><p>Shadow snapshot unavailable: "
        + html.escape(reason)
        + "</p>",
    }


def _html_from_text(text: str) -> str:
    body = html.escape(text).replace("\n", "<br>")
    return f"<h3>Shadow Strategy Snapshot</h3><p style='font-family:monospace;'>{body}</p>"
