from __future__ import annotations

import html
import json
import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


MODEL_SLUGS = ("caerus_polaris", "caerus_orion", "caerus_lyra")
DISPLAY_NAMES = {
    "caerus_polaris": "Polaris",
    "caerus_orion": "Orion",
    "caerus_lyra": "Lyra",
    "spy_benchmark": "SPY",
}
SHORT_NAMES = {
    "caerus_polaris": "polaris",
    "caerus_orion": "orion",
    "caerus_lyra": "lyra",
}
ARTIFACT_NAMES = (
    "decision_trace.json",
    "attribution.json",
    "stability_analysis.json",
    "regime_performance.json",
)
REQUIRED_ARTIFACTS = (
    ("shadow_evaluation", "shadow_evaluation.json"),
    ("comparison", "comparison.json"),
)
OPTIONAL_ARTIFACTS = (
    ("feedback_loop_summary", "feedback_loop_summary.json"),
)
DIAGNOSTIC_ARTIFACTS = (
    ("comparison_markdown_exists", "comparison.md"),
)
BANNED_LANGUAGE = ("promote", "replace", "deploy capital", "go live", "allocate")
ET = ZoneInfo("America/New_York")
MARKET_EOD_READY_TIME = dt.time(hour=16, minute=15)


@dataclass(frozen=True)
class PortfolioLearningReport:
    trade_date: str
    status: str
    subject: str
    body_text: str
    body_html: str
    payload: dict[str, Any]


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


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


def _fmt_value(value: Any) -> str:
    if value in (None, ""):
        return "UNAVAILABLE"
    return str(value)


def _load_inputs(shadow_dir: Path, trade_date: str) -> dict[str, Any]:
    dated_dir = shadow_dir / trade_date
    strategy_artifacts: dict[str, dict[str, dict[str, Any] | None]] = {}
    for slug in MODEL_SLUGS:
        strategy_dir = dated_dir / SHORT_NAMES[slug]
        strategy_artifacts[slug] = {
            name: _read_json(strategy_dir / name)
            for name in ARTIFACT_NAMES
        }
    return {
        "dated_dir": dated_dir,
        "shadow_evaluation": _read_json(dated_dir / "shadow_evaluation.json"),
        "comparison": _read_json(dated_dir / "comparison.json"),
        "feedback_loop_summary": _read_json(dated_dir / "feedback_loop_summary.json"),
        "comparison_markdown_exists": (dated_dir / "comparison.md").exists(),
        "strategy_artifacts": strategy_artifacts,
    }


def _artifact_status(inputs: dict[str, Any]) -> tuple[str, dict[str, list[str]], list[str]]:
    required_missing = []
    optional_missing = []
    diagnostic_missing = []
    stale = []
    for key, filename in REQUIRED_ARTIFACTS:
        if inputs.get(key) is None:
            required_missing.append(filename)
    for key, filename in OPTIONAL_ARTIFACTS:
        if inputs.get(key) is None:
            optional_missing.append(filename)
    for key, filename in DIAGNOSTIC_ARTIFACTS:
        if not inputs.get(key):
            diagnostic_missing.append(filename)
    for slug, artifacts in (inputs.get("strategy_artifacts") or {}).items():
        short = SHORT_NAMES[slug]
        for name, payload in artifacts.items():
            if payload is None:
                optional_missing.append(f"{short}/{name}")
    evaluation = inputs.get("shadow_evaluation") or {}
    comparison = inputs.get("comparison") or {}
    if comparison.get("reason_code"):
        stale.append(str(comparison["reason_code"]))
    for payload in ((evaluation.get("strategies") or {}).values() if isinstance(evaluation.get("strategies"), dict) else []):
        reason = payload.get("data_reason") if isinstance(payload, dict) else None
        if reason:
            stale.append(str(reason))
    missing = {
        "required": sorted(set(required_missing)),
        "optional": sorted(set(optional_missing)),
        "diagnostic": sorted(set(diagnostic_missing)),
    }
    if inputs.get("shadow_evaluation") is None:
        return "NO_DATA", missing, sorted(set(stale))
    if required_missing or stale:
        return "PARTIAL", missing, sorted(set(stale))
    return "OK", missing, []


def _current_et(now: dt.datetime | None = None) -> dt.datetime:
    if now is None:
        return dt.datetime.now(ET)
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return now.astimezone(ET)


def _hydration_status(repo_root: Path, trade_date: str) -> dict[str, Any]:
    status_path = repo_root / "outputs" / "price_hydration" / trade_date / "status.json"
    payload = _read_json(status_path)
    if payload is None:
        return {
            "status_path": str(status_path.as_posix()),
            "status": "MISSING",
            "max_cache_date": None,
            "reason": "hydration status artifact missing",
            "download_attempted": None,
            "provider": None,
        }
    return {
        "status_path": str(status_path.as_posix()),
        "status": _fmt_value(payload.get("status")),
        "max_cache_date": payload.get("max_cache_date"),
        "reason": payload.get("reason"),
        "download_attempted": payload.get("download_attempted"),
        "provider": payload.get("provider"),
    }


def _is_current_session_incomplete(trade_date: str, generated_at_et: dt.datetime | None) -> bool:
    now_et = _current_et(generated_at_et)
    return trade_date == now_et.date().isoformat() and now_et.time() < MARKET_EOD_READY_TIME


def _freshness_diagnostics(
    *,
    repo_root: Path,
    trade_date: str,
    stale_reasons: list[str],
    generated_at_et: dt.datetime | None,
) -> tuple[list[str], dict[str, Any]]:
    hydration = _hydration_status(repo_root, trade_date)
    diagnostics: list[str] = []
    for reason in stale_reasons:
        if reason != "PRICE_CACHE_STALE":
            diagnostics.append(reason)
            continue
        if _is_current_session_incomplete(trade_date, generated_at_et):
            diagnostics.append("CURRENT_SESSION_INCOMPLETE")
            continue
        status = hydration.get("status")
        max_cache_date = hydration.get("max_cache_date")
        if status == "OK" and max_cache_date is not None and str(max_cache_date) >= trade_date:
            continue
        if status == "FAILED":
            diagnostics.append("HYDRATION_FAILED")
        elif status == "MISSING":
            diagnostics.append("HYDRATION_NOT_RUN")
        elif hydration.get("download_attempted") is True and max_cache_date is not None and str(max_cache_date) < trade_date:
            diagnostics.append("PROVIDER_DATA_LAG")
        else:
            diagnostics.append("PRICE_CACHE_STALE")
    return sorted(set(diagnostics)), hydration


def _strategy_metrics(inputs: dict[str, Any], slug: str) -> dict[str, Any]:
    evaluation = inputs.get("shadow_evaluation") or {}
    comparison = inputs.get("comparison") or {}
    feedback = inputs.get("feedback_loop_summary") or {}
    strategies = evaluation.get("strategies") if isinstance(evaluation.get("strategies"), dict) else {}
    payload = strategies.get(slug) if isinstance(strategies.get(slug), dict) else {}
    spy = strategies.get("spy_benchmark") if isinstance(strategies.get("spy_benchmark"), dict) else {}
    polaris = strategies.get("caerus_polaris") if isinstance(strategies.get("caerus_polaris"), dict) else {}
    comparison_strategies = comparison.get("strategies") if isinstance(comparison.get("strategies"), dict) else {}
    comparison_payload = comparison_strategies.get(slug) if isinstance(comparison_strategies.get(slug), dict) else {}
    concentration = comparison_payload.get("weight_concentration") if isinstance(comparison_payload.get("weight_concentration"), dict) else {}
    feedback_strategies = feedback.get("strategies") if isinstance(feedback.get("strategies"), dict) else {}
    feedback_payload = feedback_strategies.get(SHORT_NAMES.get(slug, slug)) if isinstance(feedback_strategies.get(SHORT_NAMES.get(slug, slug)), dict) else {}
    cumulative = _as_float(payload.get("cumulative_return"))
    polaris_cumulative = _as_float(polaris.get("cumulative_return"))
    spy_cumulative = _as_float(spy.get("cumulative_return"))
    vs_polaris = cumulative - polaris_cumulative if slug != "caerus_polaris" and cumulative is not None and polaris_cumulative is not None else None
    vs_spy = _as_float(payload.get("excess_return_vs_spy"))
    if vs_spy is None and cumulative is not None and spy_cumulative is not None:
        vs_spy = cumulative - spy_cumulative
    return {
        "name": DISPLAY_NAMES[slug],
        "data_status": _fmt_value(payload.get("data_status")),
        "daily_return": payload.get("daily_return") if payload.get("data_status") == "OK" else None,
        "since_inception_return": cumulative,
        "excess_vs_spy": vs_spy,
        "excess_vs_polaris": vs_polaris,
        "valid_days": payload.get("rolling_count_of_valid_days"),
        "constituent_change_count": payload.get("constituent_change_count"),
        "turnover": comparison_payload.get("expected_turnover"),
        "top_3_concentration": concentration.get("top3_concentration"),
        "learning_readiness": _fmt_value(feedback_payload.get("learning_readiness")),
        "primary_learning_gap": _fmt_value(feedback_payload.get("primary_learning_gap")),
        "decision_trace_status": _fmt_value(feedback_payload.get("decision_trace_status")),
        "attribution_status": _fmt_value(feedback_payload.get("attribution_status")),
        "stability_status": _fmt_value(feedback_payload.get("stability_status")),
        "regime_status": _fmt_value(feedback_payload.get("regime_status")),
    }


def _spy_metrics(inputs: dict[str, Any]) -> dict[str, Any]:
    evaluation = inputs.get("shadow_evaluation") or {}
    strategies = evaluation.get("strategies") if isinstance(evaluation.get("strategies"), dict) else {}
    payload = strategies.get("spy_benchmark") if isinstance(strategies.get("spy_benchmark"), dict) else {}
    return {
        "name": "SPY",
        "data_status": _fmt_value(payload.get("data_status")),
        "daily_return": payload.get("daily_return") if payload.get("data_status") == "OK" else None,
        "since_inception_return": payload.get("cumulative_return"),
        "excess_vs_spy": 0.0 if payload.get("cumulative_return") is not None else None,
        "excess_vs_polaris": None,
        "valid_days": payload.get("rolling_count_of_valid_days"),
    }


def _learning_rows(inputs: dict[str, Any]) -> list[dict[str, Any]]:
    return [_strategy_metrics(inputs, slug) for slug in MODEL_SLUGS]


def _stability_review(inputs: dict[str, Any], slug: str) -> dict[str, Any]:
    artifacts = (inputs.get("strategy_artifacts") or {}).get(slug) or {}
    stability = artifacts.get("stability_analysis.json") or {}
    window = ((stability.get("rolling_windows") or {}).get("10d") or {})
    return {
        "strategy": DISPLAY_NAMES[slug],
        "status": _fmt_value(stability.get("status")),
        "valid_days_10d": window.get("valid_days"),
        "avg_turnover_10d": window.get("avg_turnover"),
        "max_turnover_10d": window.get("max_turnover"),
        "avg_top_3_concentration_10d": window.get("avg_top_3_concentration"),
        "constituent_change_count": window.get("constituent_change_count"),
        "flags": list(stability.get("flags") or []),
    }


def _attribution_review(inputs: dict[str, Any], slug: str) -> dict[str, Any]:
    artifacts = (inputs.get("strategy_artifacts") or {}).get(slug) or {}
    attribution = artifacts.get("attribution.json") or {}
    position_items = attribution.get("position_contribution") if isinstance(attribution.get("position_contribution"), list) else []
    decision = attribution.get("decision_contribution") if isinstance(attribution.get("decision_contribution"), dict) else {}
    signal = attribution.get("signal_contribution") if isinstance(attribution.get("signal_contribution"), dict) else {}
    reason = "available" if attribution.get("status") == "OK" else "asset returns or contribution inputs unavailable"
    return {
        "strategy": DISPLAY_NAMES[slug],
        "status": _fmt_value(attribution.get("status")),
        "position_contribution_count": len(position_items),
        "top_position_contributions": sorted(
            [
                {
                    "ticker": str(item.get("ticker") or ""),
                    "contribution": item.get("contribution"),
                    "status": item.get("contribution_status"),
                }
                for item in position_items
            ],
            key=lambda item: abs(float(item["contribution"] or 0.0)),
            reverse=True,
        )[:3],
        "decision_contribution_status": _fmt_value(decision.get("status")),
        "signal_contribution_status": _fmt_value(signal.get("status")),
        "explanation": reason,
    }


def _regime_review(inputs: dict[str, Any], slug: str) -> dict[str, Any]:
    artifacts = (inputs.get("strategy_artifacts") or {}).get(slug) or {}
    regime = artifacts.get("regime_performance.json") or {}
    return {
        "strategy": DISPLAY_NAMES[slug],
        "status": _fmt_value(regime.get("status")),
        "current_regime": regime.get("current_regime") or {},
        "performance_by_regime": regime.get("performance_by_regime") or {},
    }


def _watch_items(status: str, missing: dict[str, list[str]], stale: list[str], stability: list[dict[str, Any]], learning: list[dict[str, Any]]) -> list[str]:
    items = []
    if status != "OK":
        items.append(f"Artifact set is {status}.")
    for reason in stale:
        items.append(f"Data watch: {reason}.")
    required_missing = missing.get("required", [])
    optional_missing = missing.get("optional", [])
    diagnostic_missing = missing.get("diagnostic", [])
    if required_missing:
        items.append(f"Missing required artifacts: {', '.join(required_missing[:8])}" + (" ..." if len(required_missing) > 8 else "") + ".")
    if optional_missing:
        items.append(f"Missing optional learning artifacts: {', '.join(optional_missing[:8])}" + (" ..." if len(optional_missing) > 8 else "") + ".")
    if diagnostic_missing:
        items.append(f"Missing diagnostic artifacts: {', '.join(diagnostic_missing[:8])}" + (" ..." if len(diagnostic_missing) > 8 else "") + ".")
    for row in stability:
        flags = set(row.get("flags") or [])
        if "INSUFFICIENT_VALID_DAYS" in flags:
            items.append(f"{row['strategy']}: insufficient valid days.")
        if "HIGH_CONCENTRATION" in flags:
            items.append(f"{row['strategy']}: concentration risk watch.")
        if "HIGH_CONSTITUENT_CHURN" in flags:
            items.append(f"{row['strategy']}: high constituent churn.")
        if "HIGH_TURNOVER" in flags or "TURNOVER_SPIKE" in flags:
            items.append(f"{row['strategy']}: turnover watch.")
    for row in learning:
        if row.get("learning_readiness") == "LOW":
            items.append(f"{row['name']}: learning readiness LOW; gap is {row.get('primary_learning_gap')}.")
    return list(dict.fromkeys(items)) or ["No additional watch items from available artifacts."]


def _operator_diagnosis(
    *,
    status: str,
    missing: dict[str, list[str]],
    stale: list[str],
    scoreboard: list[dict[str, Any]],
    attribution: list[dict[str, Any]],
) -> list[str]:
    diagnosis = []
    if status == "OK":
        diagnosis.append("The weekly learning artifact set is complete for the requested date.")
    elif status == "NO_DATA":
        diagnosis.append("The weekly learning artifact set is not available for the requested date; the report is a diagnostic shell with explicit unavailable fields.")
    else:
        diagnosis.append("The weekly learning artifact set is partial; some sections are useful, but stale or missing inputs limit interpretation.")
    if stale:
        diagnosis.append(f"Data freshness watch: {', '.join(stale)}. Shadow data should be read as stale until the price panel is refreshed for the requested market date.")
    required_missing = missing.get("required", [])
    optional_missing = missing.get("optional", [])
    diagnostic_missing = missing.get("diagnostic", [])
    if required_missing:
        diagnosis.append(f"Missing required inputs: {', '.join(required_missing[:6])}" + (" ..." if len(required_missing) > 6 else "") + ".")
    if optional_missing:
        diagnosis.append(f"Optional learning inputs unavailable: {', '.join(optional_missing[:6])}" + (" ..." if len(optional_missing) > 6 else "") + ".")
    if diagnostic_missing:
        diagnosis.append(f"Diagnostics-only inputs unavailable: {', '.join(diagnostic_missing[:6])}" + (" ..." if len(diagnostic_missing) > 6 else "") + ".")
    if any(row.get("data_status") == "NO_DATA" for row in scoreboard):
        diagnosis.append("Daily return fields are unavailable because the shadow evaluation reports NO_DATA for at least one model.")
    if all(row.get("position_contribution_count") == 0 for row in attribution):
        diagnosis.append("Attribution has zero position rows because no current strategy holdings were generated for the requested date.")
    diagnosis.append("This report is read-only and diagnostic; it contains no trading instruction.")
    return diagnosis


def _payload(
    repo_root: Path,
    shadow_dir: Path,
    trade_date: str,
    report_generated_date: str | None = None,
    generated_at_et: dt.datetime | None = None,
) -> dict[str, Any]:
    inputs = _load_inputs(shadow_dir, trade_date)
    status, missing_by_category, stale = _artifact_status(inputs)
    freshness, hydration = _freshness_diagnostics(
        repo_root=repo_root,
        trade_date=trade_date,
        stale_reasons=stale,
        generated_at_et=generated_at_et,
    )
    if status == "PARTIAL" and stale and not freshness and not missing_by_category.get("required"):
        status = "OK"
    flat_missing = (
        missing_by_category["required"]
        + missing_by_category["optional"]
        + missing_by_category["diagnostic"]
    )
    scoreboard = [_strategy_metrics(inputs, slug) for slug in MODEL_SLUGS] + [_spy_metrics(inputs)]
    learning = _learning_rows(inputs)
    stability = [_stability_review(inputs, slug) for slug in MODEL_SLUGS]
    attribution = [_attribution_review(inputs, slug) for slug in MODEL_SLUGS]
    regime = [_regime_review(inputs, slug) for slug in MODEL_SLUGS]
    latest_run = _read_json(repo_root / "outputs" / "latest_run.json") or {}
    watch_items = _watch_items(status, missing_by_category, freshness, stability, learning)
    operator_diagnosis = _operator_diagnosis(
        status=status,
        missing=missing_by_category,
        stale=freshness,
        scoreboard=scoreboard,
        attribution=attribution,
    )
    return {
        "schema_version": "portfolio_learning_v1",
        "trade_date": trade_date,
        "as_of_trade_date": trade_date,
        "report_generated_date": report_generated_date or trade_date,
        "status": status,
        "artifact_health": {
            "missing": flat_missing,
            "missing_by_category": missing_by_category,
            "required_missing": missing_by_category["required"],
            "optional_missing": missing_by_category["optional"],
            "diagnostic_missing": missing_by_category["diagnostic"],
            "raw_stale_reasons": stale,
            "stale_reasons": freshness,
            "hydration_status": hydration,
            "shadow_dir": str((shadow_dir / trade_date).as_posix()),
        },
        "execution_context": {
            "latest_run_id": latest_run.get("run_id"),
            "latest_run_status": latest_run.get("status"),
            "latest_run_stage": latest_run.get("workflow_stage"),
        },
        "operator_diagnosis": operator_diagnosis,
        "portfolio_scoreboard": scoreboard,
        "learning_readiness": learning,
        "stability_review": stability,
        "attribution_review": attribution,
        "regime_review": regime,
        "watch_items": watch_items,
        "next_observations": [
            "Check whether current-week data freshness improves before reading daily moves as evidence.",
            "Track whether turnover and constituent churn settle over the next full week.",
            "Compare attribution availability against the next generated feedback artifacts.",
            "Review regime coverage once regime history is complete for the same dates.",
        ],
    }


def _table(headers: list[str], rows: list[list[str]]) -> list[str]:
    return [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
        *["| " + " | ".join(row) + " |" for row in rows],
    ]


def render_markdown(payload: dict[str, Any]) -> str:
    status = payload["status"]
    missing = payload["artifact_health"]["missing"]
    stale = payload["artifact_health"]["stale_reasons"]
    lines = [
        f"# Caerus Weekly Portfolio Learning Brief — as of {payload['as_of_trade_date']}",
        "",
        f"Report generated date: {payload['report_generated_date']}",
        "",
        "Diagnostic only. No trading instruction is implied.",
        "",
        "## Executive Summary",
        f"- System learning status: {status}",
        f"- Artifact health: {'complete' if status == 'OK' else 'partial or stale'}",
        f"- Missing artifact count: {len(missing)}",
        f"- Stale data reasons: {', '.join(stale) if stale else 'None'}",
        "",
        "## Operator Diagnosis",
        *[f"- {item}" for item in payload["operator_diagnosis"]],
        "",
        "## Portfolio Scoreboard",
    ]
    lines.extend(
        _table(
            ["Strategy", "Data", "Daily", "Since inception", "Excess vs SPY", "Excess vs Polaris", "Valid days"],
            [
                [
                    row["name"],
                    row["data_status"],
                    _fmt_pct(row.get("daily_return")) if row.get("daily_return") is not None else row["data_status"],
                    _fmt_pct(row.get("since_inception_return")),
                    _fmt_pct(row.get("excess_vs_spy")),
                    _fmt_pct(row.get("excess_vs_polaris")) if row.get("excess_vs_polaris") is not None else "N/A",
                    _fmt_value(row.get("valid_days")),
                ]
                for row in payload["portfolio_scoreboard"]
            ],
        )
    )
    lines.extend(["", "## Learning Readiness"])
    lines.extend(
        _table(
            ["Strategy", "Readiness", "Primary gap", "Decision", "Attribution", "Stability", "Regime"],
            [
                [
                    row["name"],
                    row["learning_readiness"],
                    row["primary_learning_gap"],
                    row["decision_trace_status"],
                    row["attribution_status"],
                    row["stability_status"],
                    row["regime_status"],
                ]
                for row in payload["learning_readiness"]
            ],
        )
    )
    lines.extend(["", "## Stability Review"])
    for row in payload["stability_review"]:
        flags = ", ".join(row["flags"]) if row["flags"] else "None"
        lines.extend(
            [
                f"- {row['strategy']}: status {row['status']}; 10d valid days {_fmt_value(row['valid_days_10d'])}; "
                f"avg turnover {_fmt_pct(row['avg_turnover_10d'], signed=False)}; "
                f"top-3 concentration {_fmt_pct(row['avg_top_3_concentration_10d'], signed=False)}; "
                f"constituent churn {_fmt_value(row['constituent_change_count'])}; flags {flags}.",
            ]
        )
    lines.extend(["", "## Attribution Review"])
    for row in payload["attribution_review"]:
        top = ", ".join(
            f"{item['ticker']} {_fmt_pct(item['contribution'])}"
            for item in row["top_position_contributions"]
            if item.get("ticker")
        ) or "UNAVAILABLE"
        lines.append(
            f"- {row['strategy']}: status {row['status']}; positions {row['position_contribution_count']}; "
            f"top contributions {top}; decision status {row['decision_contribution_status']}; "
            f"signal status {row['signal_contribution_status']}; note: {row['explanation']}."
        )
    lines.extend(["", "## Regime Review"])
    for row in payload["regime_review"]:
        current = row.get("current_regime") or {}
        regime = current.get("regime") or "UNAVAILABLE"
        lines.append(f"- {row['strategy']}: status {row['status']}; current regime {regime}.")
    lines.extend(["", "## Watch Items"])
    lines.extend([f"- {item}" for item in payload["watch_items"]])
    lines.extend(["", "## Next Observations"])
    lines.extend([f"- {item}" for item in payload["next_observations"]])
    text = "\n".join(lines) + "\n"
    _assert_language_policy(text)
    return text


def _html_table(headers: list[str], rows: list[list[str]]) -> str:
    header_html = "".join(f"<th>{html.escape(str(header))}</th>" for header in headers)
    row_html = []
    for row in rows:
        row_html.append("<tr>" + "".join(f"<td>{html.escape(str(cell))}</td>" for cell in row) + "</tr>")
    return f"<table><thead><tr>{header_html}</tr></thead><tbody>{''.join(row_html)}</tbody></table>"


def _html_list(items: list[str]) -> str:
    return "<ul>" + "".join(f"<li>{html.escape(str(item))}</li>" for item in items) + "</ul>"


def render_html_from_payload(payload: dict[str, Any]) -> str:
    scoreboard_rows = [
        [
            row["name"],
            row["data_status"],
            _fmt_pct(row.get("daily_return")) if row.get("daily_return") is not None else row["data_status"],
            _fmt_pct(row.get("since_inception_return")),
            _fmt_pct(row.get("excess_vs_spy")),
            _fmt_pct(row.get("excess_vs_polaris")) if row.get("excess_vs_polaris") is not None else "N/A",
            _fmt_value(row.get("valid_days")),
        ]
        for row in payload["portfolio_scoreboard"]
    ]
    learning_rows = [
        [
            row["name"],
            row["learning_readiness"],
            row["primary_learning_gap"],
            row["decision_trace_status"],
            row["attribution_status"],
            row["stability_status"],
            row["regime_status"],
        ]
        for row in payload["learning_readiness"]
    ]
    stability_rows = [
        [
            row["strategy"],
            row["status"],
            _fmt_value(row["valid_days_10d"]),
            _fmt_pct(row["avg_turnover_10d"], signed=False),
            _fmt_pct(row["avg_top_3_concentration_10d"], signed=False),
            _fmt_value(row["constituent_change_count"]),
            ", ".join(row["flags"]) if row["flags"] else "None",
        ]
        for row in payload["stability_review"]
    ]
    attribution_rows = [
        [
            row["strategy"],
            row["status"],
            _fmt_value(row["position_contribution_count"]),
            row["decision_contribution_status"],
            row["signal_contribution_status"],
            row["explanation"],
        ]
        for row in payload["attribution_review"]
    ]
    regime_rows = [
        [
            row["strategy"],
            row["status"],
            str((row.get("current_regime") or {}).get("regime") or "UNAVAILABLE"),
        ]
        for row in payload["regime_review"]
    ]
    status = html.escape(str(payload["status"]))
    stale = ", ".join(payload["artifact_health"]["stale_reasons"]) if payload["artifact_health"]["stale_reasons"] else "None"
    missing_count = len(payload["artifact_health"]["missing"])
    body = f"""
    <html>
    <head>
      <style>
        body {{ font-family: Arial, sans-serif; color: #1f2933; line-height: 1.35; }}
        h1 {{ font-size: 22px; margin-bottom: 4px; }}
        h2 {{ font-size: 17px; margin-top: 22px; border-bottom: 1px solid #d9e2ec; padding-bottom: 4px; }}
        .note {{ color: #52606d; margin-top: 0; }}
        .status {{ display: inline-block; padding: 3px 8px; border-radius: 4px; background: #fff3cd; color: #5f4b00; font-weight: 700; }}
        table {{ border-collapse: collapse; width: 100%; margin: 8px 0 14px; font-size: 13px; }}
        th {{ text-align: left; background: #102a43; color: #ffffff; padding: 7px; border: 1px solid #bcccdc; }}
        td {{ padding: 7px; border: 1px solid #d9e2ec; vertical-align: top; }}
        ul {{ margin-top: 6px; }}
      </style>
    </head>
    <body>
      <h1>Caerus Weekly Portfolio Learning Brief — as of {html.escape(str(payload['as_of_trade_date']))}</h1>
      <p class="note">Report generated date: {html.escape(str(payload['report_generated_date']))}</p>
      <p class="note">Diagnostic only. No trading instruction is implied.</p>
      <h2>Executive Summary</h2>
      <p><span class="status">{status}</span></p>
      <ul>
        <li>Artifact health: {html.escape('complete' if payload['status'] == 'OK' else 'partial or stale')}</li>
        <li>Missing artifact count: {missing_count}</li>
        <li>Stale data reasons: {html.escape(stale)}</li>
      </ul>
      <h2>Operator Diagnosis</h2>
      {_html_list(payload['operator_diagnosis'])}
      <h2>Portfolio Scoreboard</h2>
      {_html_table(['Strategy', 'Data', 'Daily', 'Since inception', 'Excess vs SPY', 'Excess vs Polaris', 'Valid days'], scoreboard_rows)}
      <h2>Learning Readiness</h2>
      {_html_table(['Strategy', 'Readiness', 'Primary gap', 'Decision', 'Attribution', 'Stability', 'Regime'], learning_rows)}
      <h2>Stability Review</h2>
      {_html_table(['Strategy', 'Status', '10d valid days', 'Avg turnover', 'Top-3 concentration', 'Constituent churn', 'Flags'], stability_rows)}
      <h2>Attribution Review</h2>
      {_html_table(['Strategy', 'Status', 'Positions', 'Decision', 'Signal', 'Explanation'], attribution_rows)}
      <h2>Regime Review</h2>
      {_html_table(['Strategy', 'Status', 'Current regime'], regime_rows)}
      <h2>Watch Items</h2>
      {_html_list(payload['watch_items'])}
      <h2>Next Observations</h2>
      {_html_list(payload['next_observations'])}
    </body>
    </html>
    """
    _assert_language_policy(body)
    return body


def _assert_language_policy(text: str) -> None:
    lowered = text.lower()
    violations = [word for word in BANNED_LANGUAGE if word in lowered]
    if violations:
        raise ValueError(f"Portfolio learning report language policy violation: {', '.join(violations)}")


def build_portfolio_learning_report(
    *,
    repo_root: Path,
    trade_date: str,
    shadow_dir: Path,
    report_generated_date: str | None = None,
    generated_at_et: dt.datetime | None = None,
) -> PortfolioLearningReport:
    payload = _payload(
        repo_root=repo_root,
        shadow_dir=shadow_dir,
        trade_date=trade_date,
        report_generated_date=report_generated_date,
        generated_at_et=generated_at_et,
    )
    body_text = render_markdown(payload)
    body_html = render_html_from_payload(payload)
    subject = f"Caerus Weekly Portfolio Learning Brief — as of {trade_date}"
    return PortfolioLearningReport(
        trade_date=trade_date,
        status=str(payload["status"]),
        subject=subject,
        body_text=body_text,
        body_html=body_html,
        payload=payload,
    )


def write_portfolio_learning_artifacts(
    *,
    report: PortfolioLearningReport,
    output_dir: Path,
) -> tuple[Path, Path]:
    dated_dir = output_dir / report.trade_date
    json_path = dated_dir / "weekly_portfolio_learning.json"
    md_path = dated_dir / "weekly_portfolio_learning.md"
    _write_json(json_path, report.payload)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(report.body_text, encoding="utf-8")
    return json_path, md_path
