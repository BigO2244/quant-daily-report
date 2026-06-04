from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import re
from pathlib import Path
from typing import Any

from core.strategy_registry import StrategyRegistryEntry, load_strategy_registry_for_repo
from research.cio_briefing import build_cio_briefing


SCHEMA_VERSION = "caerus_research_review_packet_v1"
SCORECARD_DIMENSIONS = (
    "Signal Quality",
    "Infrastructure",
    "Risk Management",
    "Regime Detection",
    "Execution",
    "Attribution",
    "Data Quality",
)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def _is_date_text(value: str) -> bool:
    try:
        dt.date.fromisoformat(value)
    except Exception:
        return False
    return True


def _today() -> str:
    return dt.date.today().isoformat()


def _safe_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        out = float(value)
    except Exception:
        return None
    if out != out:
        return None
    return out


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 10)


def _fmt(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:.4f}".rstrip("0").rstrip(".")
    if isinstance(value, list):
        return ", ".join(str(item) for item in value) if value else "none"
    return str(value)


def _md(value: Any) -> str:
    return _fmt(value).replace("|", "\\|").replace("\n", " ")


def _status(
    *,
    artifact_name: str,
    path: Path | None,
    date: str | None,
    exists: bool,
    confidence: str = "LOW",
    reason_codes: list[str] | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    reasons = sorted(set(reason_codes or []))
    if not reasons:
        reasons = ["ok"] if exists else [f"missing_{artifact_name}"]
    return {
        "artifact_name": artifact_name,
        "status": status or ("PRESENT" if exists else "MISSING"),
        "date": date,
        "confidence": confidence,
        "reason_codes": reasons,
        "path": str(path) if path else None,
    }


def _date_dirs(root: Path, required_file: str) -> set[str]:
    if not root.exists():
        return set()
    out: set[str] = set()
    for child in root.iterdir():
        if child.is_dir() and _is_date_text(child.name) and (child / required_file).exists():
            out.add(child.name)
    return out


def select_review_date(repo_root: Path | str = Path("."), explicit_date: str | None = None) -> tuple[str, list[str]]:
    if explicit_date:
        return explicit_date, ["date_explicit"]
    repo = Path(repo_root)
    attribution_dates = _date_dirs(repo / "outputs" / "attribution", "attribution_summary.json")
    decision_dates = _date_dirs(repo / "outputs" / "decision_attribution", "strategy_decision_summary.json")
    both = sorted(attribution_dates & decision_dates)
    if both:
        return both[-1], ["date_selected_latest_attribution_and_decision"]
    either = sorted(attribution_dates | decision_dates)
    if either:
        return either[-1], ["date_selected_latest_partial_core_artifact"]
    return _today(), ["date_defaulted_today_no_core_artifacts"]


def _source_from_payload(path: Path, payload: dict[str, Any] | None, artifact_name: str, date: str) -> dict[str, Any]:
    if payload is None:
        return _status(
            artifact_name=artifact_name,
            path=path,
            date=date,
            exists=False,
            reason_codes=[f"missing_{artifact_name}"],
        )
    confidence = str(
        payload.get("aggregate_confidence")
        or payload.get("confidence")
        or "MEDIUM"
    )
    reasons = list(payload.get("reason_codes") or ["ok"])
    status = "PRESENT"
    if any("missing" in str(code).lower() for code in reasons):
        status = "PARTIAL"
    return _status(
        artifact_name=artifact_name,
        path=path,
        date=str(payload.get("date") or payload.get("trade_date") or date),
        exists=True,
        confidence=confidence,
        reason_codes=reasons,
        status=status,
    )


def _discover_model_review(repo: Path, trade_date: str) -> tuple[dict[str, Any], dict[str, Any]]:
    candidates: list[tuple[str, Path]] = []
    for path in sorted((repo / "research").glob("model_review_*.md")):
        match = re.search(r"model_review_(\d{4}-\d{2}-\d{2})\.md$", path.name)
        if match:
            candidates.append((match.group(1), path))
    for path in sorted((repo / "outputs" / "model_review").glob("*")):
        if path.is_dir() and _is_date_text(path.name):
            for name in ("model_review.json", "model_review.md", "weekly_model_review.json", "weekly_model_review.md"):
                candidate = path / name
                if candidate.exists():
                    candidates.append((path.name, candidate))
    if not candidates:
        source = _status(
            artifact_name="model_review",
            path=None,
            date=trade_date,
            exists=False,
            reason_codes=["missing_model_review"],
        )
        return {
            "available": False,
            "scores": {},
            "average_score": None,
            "confidence": "LOW",
            "reason_codes": ["missing_model_review"],
        }, source

    dated = sorted(candidates, key=lambda item: (item[0] > trade_date, item[0], str(item[1])))
    before_or_equal = [item for item in dated if item[0] <= trade_date]
    selected_date, selected_path = (before_or_equal[-1] if before_or_equal else dated[-1])
    scores = _parse_model_review_scores(selected_path)
    reasons = [] if scores else ["model_review_scores_unparsed"]
    if selected_date != trade_date:
        reasons.append("model_review_date_differs_from_packet_date")
    if not reasons:
        reasons = ["ok"]
    average = _mean([float(value) for value in scores.values()])
    section = {
        "available": True,
        "date": selected_date,
        "scores": scores,
        "average_score": average,
        "confidence": "MEDIUM" if scores else "LOW",
        "reason_codes": sorted(set(reasons)) if reasons != ["ok"] else ["ok"],
        "source_artifacts": [str(selected_path)],
    }
    source = _status(
        artifact_name="model_review",
        path=selected_path,
        date=selected_date,
        exists=True,
        confidence=section["confidence"],
        reason_codes=section["reason_codes"],
        status="PRESENT" if scores else "PARTIAL",
    )
    return section, source


def _parse_model_review_scores(path: Path) -> dict[str, int]:
    if path.suffix.lower() == ".json":
        payload = _read_json(path) or {}
        raw_scores = payload.get("scores") or payload.get("scorecard") or {}
        if isinstance(raw_scores, dict):
            out: dict[str, int] = {}
            for dim in SCORECARD_DIMENSIONS:
                value = raw_scores.get(dim)
                if isinstance(value, dict):
                    value = value.get("score")
                numeric = _safe_float(value)
                if numeric is not None:
                    out[dim] = int(round(numeric))
            return out
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return {}
    scores: dict[str, int] = {}
    for line in text.splitlines():
        if "|" not in line:
            continue
        for dim in SCORECARD_DIMENSIONS:
            if dim not in line:
                continue
            match = re.search(r"(\d{1,2})\s*/\s*10", line)
            if match:
                scores[dim] = int(match.group(1))
    return {dim: scores[dim] for dim in SCORECARD_DIMENSIONS if dim in scores}


def _build_position_attribution_section(repo: Path, trade_date: str) -> tuple[dict[str, Any], dict[str, Any]]:
    path = repo / "outputs" / "attribution" / trade_date / "attribution_summary.json"
    payload = _read_json(path)
    source = _source_from_payload(path, payload, "attribution", trade_date)
    if payload is None:
        return {
            "available": False,
            "confidence": "LOW",
            "reason_codes": ["missing_attribution"],
            "source_artifacts": [],
        }, source | {"reason_codes": ["missing_attribution"]}
    reason_codes = list(payload.get("reason_codes") or ["ok"])
    if payload.get("is_price_source_fresh") is False and "price_source_stale" not in reason_codes:
        reason_codes.append("price_source_stale")
    section = {
        "available": True,
        "strategies_covered": list(payload.get("strategies_covered") or []),
        "total_positions_analyzed": payload.get("total_positions_analyzed"),
        "positions_with_complete_price_data": payload.get("positions_with_complete_price_data"),
        "positions_missing_price_data": payload.get("positions_missing_price_data"),
        "aggregate_confidence": payload.get("aggregate_confidence"),
        "price_source": payload.get("price_source"),
        "price_source_max_date": payload.get("price_source_max_date"),
        "is_price_source_fresh": payload.get("is_price_source_fresh"),
        "freshness_lag_days": payload.get("freshness_lag_days"),
        "freshness_reason_codes": list(payload.get("freshness_reason_codes") or []),
        "top_contributor_per_strategy": payload.get("top_contributor_per_strategy") or {},
        "top_detractor_per_strategy": payload.get("top_detractor_per_strategy") or {},
        "confidence": payload.get("aggregate_confidence") or "LOW",
        "reason_codes": sorted(set(reason_codes)) if reason_codes else ["ok"],
        "source_artifacts": sorted(set(list(payload.get("source_artifacts") or []) + [str(path)])),
    }
    return section, source


def _build_decision_attribution_section(repo: Path, trade_date: str) -> tuple[dict[str, Any], dict[str, Any]]:
    base = repo / "outputs" / "decision_attribution" / trade_date
    strategy_path = base / "strategy_decision_summary.json"
    signal_path = base / "signal_outcome_summary.json"
    strategy_payload = _read_json(strategy_path)
    signal_payload = _read_json(signal_path)
    exists = strategy_payload is not None and signal_payload is not None
    if not exists:
        missing = []
        if strategy_payload is None:
            missing.append("missing_strategy_decision_summary")
        if signal_payload is None:
            missing.append("missing_signal_outcome_summary")
        reasons = ["missing_decision_attribution"] + missing
        source = _status(
            artifact_name="decision_attribution",
            path=base,
            date=trade_date,
            exists=False,
            reason_codes=reasons,
        )
        return {
            "available": False,
            "strategies": [],
            "signals": [],
            "confidence": "LOW",
            "reason_codes": reasons,
            "source_artifacts": [],
        }, source

    strategies = list(strategy_payload.get("strategies") or [])
    signals = list(signal_payload.get("signals") or [])
    reason_codes = sorted(set(list(strategy_payload.get("reason_codes") or []) + list(signal_payload.get("reason_codes") or [])))
    if not reason_codes:
        reason_codes = ["ok"]
    confidences = [str(row.get("confidence") or "LOW") for row in strategies + signals if isinstance(row, dict)]
    confidence = _min_confidence(confidences) if confidences else "LOW"
    section = {
        "available": True,
        "strategies": sorted(strategies, key=lambda row: str(row.get("strategy") or "")),
        "signals": sorted(signals, key=lambda row: str(row.get("signal_name") or "")),
        "decisions_analyzed": sum(int(row.get("decisions_analyzed") or 0) for row in strategies if isinstance(row, dict)),
        "confidence": confidence,
        "reason_codes": reason_codes,
        "source_artifacts": sorted(set(list(strategy_payload.get("source_artifacts") or []) + list(signal_payload.get("source_artifacts") or []) + [str(strategy_path), str(signal_path)])),
    }
    source = _status(
        artifact_name="decision_attribution",
        path=base,
        date=trade_date,
        exists=True,
        confidence=confidence,
        reason_codes=reason_codes,
        status="PRESENT" if reason_codes == ["ok"] else "PARTIAL",
    )
    return section, source


def _min_confidence(values: list[str]) -> str:
    order = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}
    if not values:
        return "LOW"
    return min((value if value in order else "LOW" for value in values), key=lambda value: order[value])


def _build_signal_quality_section(decision_section: dict[str, Any]) -> dict[str, Any]:
    signals = list(decision_section.get("signals") or [])
    complete = [row for row in signals if _safe_float(row.get("average_realized_return")) is not None]
    strongest = sorted(
        complete,
        key=lambda row: (
            -float(row.get("average_realized_return") or 0.0),
            -float(row.get("hit_rate") or 0.0),
            str(row.get("signal_name") or ""),
        ),
    )[0] if complete else None
    weakest = sorted(
        complete,
        key=lambda row: (
            float(row.get("average_realized_return") or 0.0),
            float(row.get("hit_rate") or 0.0),
            str(row.get("signal_name") or ""),
        ),
    )[0] if complete else None
    observations_by_signal = {
        str(row.get("signal_name") or ""): int(row.get("observations") or 0)
        for row in signals
        if isinstance(row, dict)
    }
    min_observations = min(observations_by_signal.values()) if observations_by_signal else 0
    reasons = [] if signals else ["missing_signal_outcome_summary"]
    if signals and min_observations < 30:
        reasons.append("signal_evidence_sample_size_low")
    if not reasons:
        reasons = ["ok"]
    return {
        "strongest_observed_signal": strongest,
        "weakest_observed_signal": weakest,
        "observations_by_signal": dict(sorted(observations_by_signal.items())),
        "signals": signals,
        "early_evidence": bool(signals and min_observations < 30),
        "confidence": "LOW" if reasons != ["ok"] else str(decision_section.get("confidence") or "LOW"),
        "reason_codes": sorted(set(reasons)) if reasons != ["ok"] else ["ok"],
    }


def _build_execution_quality_section(repo: Path, trade_date: str) -> tuple[dict[str, Any], dict[str, Any]]:
    candidates = [
        repo / "outputs" / "execution_timeline" / trade_date / "execution_timeline.json",
        repo / "outputs" / "latest_execution_timeline_status" / trade_date / "status.json",
        repo / "outputs" / "health" / "caerus_daily_health_check" / trade_date / "health_check.json",
        repo / "outputs" / "daily" / f"health_{trade_date}.json",
        repo / "outputs" / "health" / "caerus_daily_health_check" / "latest" / "health_check.json",
    ]
    payload = None
    selected_path = None
    for path in candidates:
        payload = _read_json(path)
        if payload is not None:
            selected_path = path
            break
    if payload is None:
        summary_txt = repo / "outputs" / "latest_execution_summary.txt"
        if summary_txt.exists():
            text = summary_txt.read_text(encoding="utf-8", errors="ignore")
            selected_path = summary_txt
            payload = {
                "status": _extract_text_field(text, "Executor") or "UNKNOWN",
                "validation_status": _extract_text_field(text, "Reconciliation"),
                "known_blockers": [
                    line.strip()
                    for line in text.splitlines()
                    if "MISSING" in line or "UNKNOWN" in line or "FAILED" in line
                ],
            }
    if payload is None or selected_path is None:
        source = _status(
            artifact_name="execution",
            path=None,
            date=trade_date,
            exists=False,
            reason_codes=["missing_execution_summary"],
        )
        return {
            "available": False,
            "latest_execution_status": None,
            "validation_status": None,
            "failed_or_stale_symbols": [],
            "pending_sells": None,
            "pending_buys": None,
            "known_blockers": ["execution telemetry summary unavailable"],
            "confidence": "LOW",
            "reason_codes": ["missing_execution_summary"],
            "source_artifacts": [],
        }, source

    checks = payload.get("checks") if isinstance(payload.get("checks"), list) else []
    check_reasons = [
        str(code)
        for check in checks
        if isinstance(check, dict)
        for code in list(check.get("reason_codes") or [])
    ]
    latest_status = (
        payload.get("overall_status")
        or payload.get("status")
        or payload.get("execution_status")
        or payload.get("latest_status")
    )
    validation_status = (
        payload.get("validation_status")
        or payload.get("reconciliation_status")
        or payload.get("status")
    )
    blockers = list(payload.get("known_blockers") or payload.get("blockers") or [])
    blockers.extend(check_reasons)
    failed_symbols = list(payload.get("failed_symbols") or payload.get("stale_symbols") or payload.get("invalid_symbols") or [])
    reasons = sorted(set([str(code) for code in blockers if code]))
    if not reasons:
        reasons = ["ok"]
    section = {
        "available": True,
        "latest_execution_status": latest_status,
        "validation_status": validation_status,
        "failed_or_stale_symbols": sorted(set(str(item) for item in failed_symbols)),
        "pending_sells": payload.get("pending_sell_count") or payload.get("pending_sells"),
        "pending_buys": payload.get("pending_buy_count") or payload.get("pending_buys"),
        "known_blockers": sorted(set(str(item) for item in blockers if item)),
        "confidence": "MEDIUM" if reasons == ["ok"] else "LOW",
        "reason_codes": reasons,
        "source_artifacts": [str(selected_path)],
    }
    source = _status(
        artifact_name="execution",
        path=selected_path,
        date=str(payload.get("trade_date") or trade_date),
        exists=True,
        confidence=section["confidence"],
        reason_codes=reasons,
        status="PRESENT" if reasons == ["ok"] else "PARTIAL",
    )
    return section, source


def _extract_text_field(text: str, label: str) -> str | None:
    pattern = re.compile(rf"^\s*{re.escape(label)}:\s*(.+?)\s*$", re.MULTILINE)
    match = pattern.search(text)
    return match.group(1).strip() if match else None


def _risk_section_from_risk_coverage(payload: dict[str, Any], path: Path, trade_date: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
    if not bool(payload.get("available")):
        return None
    strategies_payload = payload.get("strategies") if isinstance(payload.get("strategies"), dict) else {}
    strategy_rows = {
        str(strategy): {
            "holdings_count": row.get("position_count"),
            "position_count": row.get("position_count"),
            "max_weight": row.get("max_single_name_weight"),
            "max_position_weight": row.get("max_single_name_weight"),
            "top3_weight": row.get("top3_concentration"),
            "top3_concentration": row.get("top3_concentration"),
            "top5_concentration": row.get("top5_concentration"),
            "top10_concentration": row.get("top10_concentration"),
            "gross_exposure": row.get("gross_exposure"),
            "net_exposure": row.get("net_exposure"),
            "cash_unallocated": row.get("cash_unallocated"),
            "sector_exposure": row.get("sector_exposure") or {},
            "missing_sector_coverage_count": row.get("missing_sector_coverage_count"),
            "max_sector_weight": row.get("sector_concentration"),
            "concentration_risk_level": row.get("risk_level"),
            "exposure_risk_level": row.get("risk_level"),
            "confidence": row.get("confidence"),
            "reason_codes": list(row.get("reason_codes") or []),
        }
        for strategy, row in sorted(strategies_payload.items())
        if isinstance(row, dict)
    }
    reasons = sorted(set(str(code) for code in list(payload.get("reason_codes") or []) if code != "ok")) or ["ok"]
    confidence = str(payload.get("confidence") or "LOW")
    section = {
        "available": True,
        "strategies": strategy_rows,
        "top_holdings": {
            strategy: list(row.get("top_holdings") or [])
            for strategy, row in sorted(strategies_payload.items())
            if isinstance(row, dict)
        },
        "position_count": payload.get("position_count"),
        "max_position_weight": payload.get("max_single_name_weight"),
        "top3_concentration": payload.get("top3_concentration"),
        "top5_concentration": payload.get("top5_concentration"),
        "top10_concentration": payload.get("top10_concentration"),
        "gross_exposure": payload.get("gross_exposure"),
        "net_exposure": payload.get("net_exposure"),
        "concentration_risk_level": payload.get("risk_level"),
        "exposure_risk_level": payload.get("risk_level"),
        "missing_sector_coverage_count": sum(int((row or {}).get("missing_sector_coverage_count") or 0) for row in strategy_rows.values()),
        "confidence": confidence,
        "reason_codes": reasons,
        "source_artifacts": sorted(set(list(payload.get("source_artifacts") or []) + [str(path)])),
    }
    source = _status(
        artifact_name="risk",
        path=path,
        date=str(payload.get("date") or trade_date),
        exists=True,
        confidence=confidence,
        reason_codes=reasons,
        status="PRESENT" if reasons == ["ok"] else "PARTIAL",
    )
    return section, source


def _build_risk_section(repo: Path, trade_date: str) -> tuple[dict[str, Any], dict[str, Any]]:
    canonical_path = repo / "outputs" / "risk_summary" / trade_date / "risk_summary.json"
    canonical = _read_json(canonical_path)
    if canonical is not None:
        reasons = list(canonical.get("reason_codes") or ["ok"])
        confidence = str(canonical.get("confidence") or "LOW")
        strategies_payload = canonical.get("strategies") if isinstance(canonical.get("strategies"), dict) else {}
        strategy_rows = {
            str(strategy): {
                "holdings_count": row.get("position_count"),
                "position_count": row.get("position_count"),
                "max_weight": row.get("max_position_weight"),
                "max_position_weight": row.get("max_position_weight"),
                "top3_weight": row.get("top3_concentration"),
                "top3_concentration": row.get("top3_concentration"),
                "top5_concentration": row.get("top5_concentration"),
                "sector_exposure": row.get("sector_exposure") or {},
                "missing_sector_coverage_count": row.get("missing_sector_coverage_count"),
                "max_sector_weight": row.get("max_sector_weight"),
                "market_beta": row.get("market_beta"),
                "concentration_risk_level": row.get("concentration_risk_level"),
                "exposure_risk_level": row.get("exposure_risk_level"),
                "confidence": row.get("confidence"),
                "reason_codes": list(row.get("reason_codes") or []),
            }
            for strategy, row in sorted(strategies_payload.items())
            if isinstance(row, dict)
        }
        position_count = int(_safe_float(canonical.get("position_count")) or 0)
        usable = position_count > 0 and bool(strategy_rows)
        if not usable and "empty_risk_summary" not in reasons:
            reasons.append("empty_risk_summary")
        if not usable and "missing_risk_summary" not in reasons:
            reasons.append("missing_risk_summary")
        if not usable:
            confidence = "LOW"
            risk_coverage_path = repo / "outputs" / "research" / "risk_coverage" / trade_date / "risk_coverage.json"
            risk_coverage = _read_json(risk_coverage_path)
            if risk_coverage is not None:
                fallback = _risk_section_from_risk_coverage(risk_coverage, risk_coverage_path, trade_date)
                if fallback is not None:
                    return fallback
        section = {
            "available": usable,
            "strategies": strategy_rows,
            "top_holdings": canonical.get("top_holdings") or {},
            "position_count": canonical.get("position_count"),
            "max_position_weight": canonical.get("max_position_weight"),
            "top3_concentration": canonical.get("top3_concentration"),
            "top5_concentration": canonical.get("top5_concentration"),
            "concentration_risk_level": canonical.get("concentration_risk_level"),
            "exposure_risk_level": canonical.get("exposure_risk_level"),
            "missing_sector_coverage_count": canonical.get("missing_sector_coverage_count"),
            "confidence": confidence,
            "reason_codes": sorted(set(str(code) for code in reasons)) if reasons else ["ok"],
            "source_artifacts": sorted(set(list(canonical.get("source_artifacts") or []) + [str(canonical_path)])),
        }
        source = _status(
            artifact_name="risk",
            path=canonical_path,
            date=str(canonical.get("date") or trade_date),
            exists=True,
            confidence=confidence,
            reason_codes=section["reason_codes"],
            status="MISSING" if not usable else "PRESENT" if section["reason_codes"] == ["ok"] else "PARTIAL",
        )
        return section, source

    risk_coverage_path = repo / "outputs" / "research" / "risk_coverage" / trade_date / "risk_coverage.json"
    risk_coverage = _read_json(risk_coverage_path)
    if risk_coverage is not None:
        fallback = _risk_section_from_risk_coverage(risk_coverage, risk_coverage_path, trade_date)
        if fallback is not None:
            return fallback

    concentration_path = repo / "outputs" / "attribution" / trade_date / "concentration_analysis.json"
    exposure_path = repo / "outputs" / "attribution" / trade_date / "exposure_summary.json"
    holdings_path = repo / "outputs" / "portfolio_history" / trade_date / "holdings_snapshot.json"
    concentration = _read_json(concentration_path)
    exposure = _read_json(exposure_path)
    holdings = _read_json(holdings_path)
    if concentration is None and exposure is None and holdings is None:
        source = _status(
            artifact_name="risk",
            path=concentration_path,
            date=trade_date,
            exists=False,
            reason_codes=["missing_risk_summary"],
        )
        return {
            "available": False,
            "strategies": {},
            "top_holdings": {},
            "confidence": "LOW",
            "reason_codes": ["missing_risk_summary"],
            "source_artifacts": [],
        }, source

    strategies: dict[str, Any] = {}
    conc_strategies = concentration.get("strategies") if isinstance(concentration, dict) else {}
    exp_strategies = exposure.get("strategies") if isinstance(exposure, dict) else {}
    for strategy in sorted(set(list((conc_strategies or {}).keys()) + list((exp_strategies or {}).keys()))):
        conc = (conc_strategies or {}).get(strategy) or {}
        exp = (exp_strategies or {}).get(strategy) or {}
        sector = (exp.get("sector_exposure") or {}) if isinstance(exp, dict) else {}
        strategies[strategy] = {
            "holdings_count": conc.get("holdings_count"),
            "max_weight": conc.get("max_weight"),
            "top3_weight": conc.get("top3_weight"),
            "top3_contribution_share_21d": conc.get("top3_contribution_share_21d"),
            "sector_exposure": sector.get("weights") or {},
            "max_sector_weight": sector.get("max_sector_weight"),
            "market_beta": exp.get("market_beta") if isinstance(exp, dict) else None,
        }
    top_holdings: dict[str, list[dict[str, Any]]] = {}
    holdings_strategies = holdings.get("strategies") if isinstance(holdings, dict) else {}
    if isinstance(holdings_strategies, dict):
        for strategy, payload in sorted(holdings_strategies.items()):
            rows = payload.get("holdings") if isinstance(payload, dict) else []
            if isinstance(rows, list):
                top_holdings[strategy] = sorted(
                    [
                        {
                            "symbol": str(row.get("ticker") or row.get("symbol") or ""),
                            "weight": row.get("target_weight") or row.get("weight"),
                            "sector": row.get("sector"),
                        }
                        for row in rows
                        if isinstance(row, dict)
                    ],
                    key=lambda row: (-float(row.get("weight") or 0.0), str(row.get("symbol") or "")),
                )[:5]
    sources = [str(path) for path, payload in ((concentration_path, concentration), (exposure_path, exposure), (holdings_path, holdings)) if payload is not None]
    section = {
        "available": True,
        "strategies": strategies,
        "top_holdings": top_holdings,
        "confidence": "MEDIUM",
        "reason_codes": ["ok"],
        "source_artifacts": sources,
    }
    source = _status(
        artifact_name="risk",
        path=concentration_path if concentration is not None else exposure_path,
        date=trade_date,
        exists=True,
        confidence="MEDIUM",
        reason_codes=["ok"],
    )
    return section, source


def _build_regime_section(repo: Path, trade_date: str) -> tuple[dict[str, Any], dict[str, Any]]:
    regime_path = repo / "outputs" / "attribution" / trade_date / "regime_performance_breakdown.json"
    fallback_path = repo / "outputs" / "attribution" / trade_date / "regime_analysis.json"
    current_path = repo / "outputs" / "vix_regime" / "regime_current.json"
    regime = _read_json(regime_path) or _read_json(fallback_path)
    current = _read_json(current_path)
    if regime is None and current is None:
        source = _status(
            artifact_name="regime",
            path=regime_path,
            date=trade_date,
            exists=False,
            reason_codes=["missing_regime_summary"],
        )
        return {
            "available": False,
            "detected_regime": None,
            "strategies": {},
            "confidence": "LOW",
            "reason_codes": ["missing_regime_summary"],
            "source_artifacts": [],
        }, source

    strategies: dict[str, Any] = {}
    for strategy, payload in sorted(((regime or {}).get("strategies") or {}).items()):
        interpretation = payload.get("interpretation") if isinstance(payload, dict) else {}
        risk_perf = (((payload.get("performance_by_regime") or {}).get("risk_regime") or {}) if isinstance(payload, dict) else {})
        strategies[strategy] = {
            "best_risk_regime": (interpretation or {}).get("best_risk_regime"),
            "worst_risk_regime": (interpretation or {}).get("worst_risk_regime"),
            "risk_regime_hit_rates": {
                name: row.get("hit_rate")
                for name, row in sorted(risk_perf.items())
                if isinstance(row, dict)
            },
        }
    sources = []
    if regime is not None:
        sources.append(str(regime_path if regime_path.exists() else fallback_path))
    if current is not None:
        sources.append(str(current_path))
    reasons = []
    if current is not None and current.get("as_of") and current.get("as_of") != trade_date:
        reasons.append("detected_regime_date_differs_from_packet_date")
    if not reasons:
        reasons = ["ok"]
    section = {
        "available": True,
        "detected_regime": (current or {}).get("regime"),
        "detected_regime_as_of": (current or {}).get("as_of"),
        "vix": (current or {}).get("vix"),
        "position_scale": (current or {}).get("position_scale"),
        "strategies": strategies,
        "confidence": "MEDIUM" if reasons == ["ok"] else "LOW",
        "reason_codes": reasons,
        "source_artifacts": sources,
    }
    source = _status(
        artifact_name="regime",
        path=Path(sources[0]) if sources else regime_path,
        date=trade_date,
        exists=True,
        confidence=section["confidence"],
        reason_codes=reasons,
        status="PRESENT" if reasons == ["ok"] else "PARTIAL",
    )
    return section, source


def _build_execution_timing_study_section(repo: Path, trade_date: str) -> tuple[dict[str, Any], dict[str, Any]]:
    path = repo / "outputs" / "research" / "execution_timing" / trade_date / "execution_timing_summary.json"
    payload = _read_json(path)
    if payload is None:
        source = _status(
            artifact_name="execution_timing_study",
            path=path,
            date=trade_date,
            exists=False,
            reason_codes=["missing_execution_timing_study"],
        )
        return {
            "available": False,
            "best_offset_vs_baseline": None,
            "worst_offset_vs_baseline": None,
            "baseline_offset": "T+5m",
            "baseline_time_et": "09:35",
            "coverage_ratio": None,
            "confidence": "LOW",
            "reason_codes": ["missing_execution_timing_study"],
            "source_artifacts": [],
        }, source
    reasons = list(payload.get("reason_codes") or ["ok"])
    available = bool(payload.get("available"))
    if not available and "timing_study_unavailable" not in reasons:
        reasons.append("timing_study_unavailable")
    section = {
        "available": available,
        "best_offset_vs_baseline": payload.get("best_offset_vs_baseline"),
        "worst_offset_vs_baseline": payload.get("worst_offset_vs_baseline"),
        "baseline_offset": payload.get("baseline_offset"),
        "baseline_time_et": payload.get("baseline_time_et"),
        "coverage_ratio": payload.get("coverage_ratio"),
        "symbols_evaluated": payload.get("symbols_evaluated"),
        "symbols_missing_bars": payload.get("symbols_missing_bars") or [],
        "confidence": payload.get("confidence") or ("MEDIUM" if available else "LOW"),
        "reason_codes": sorted(set(reasons)),
        "source_artifacts": [str(path)] + list(payload.get("source_artifacts") or []),
    }
    source = _status(
        artifact_name="execution_timing_study",
        path=path,
        date=str(payload.get("date") or trade_date),
        exists=True,
        confidence=section["confidence"],
        reason_codes=section["reason_codes"],
        status="PRESENT" if available else "PARTIAL",
    )
    return section, source


def _build_promotion_windows_section(repo: Path, trade_date: str) -> tuple[dict[str, Any], dict[str, Any]]:
    path = repo / "outputs" / "research" / "promotion_readiness" / trade_date / "promotion_readiness_windows.json"
    payload = _read_json(path)
    if payload is None:
        source = _status(
            artifact_name="promotion_readiness_windows",
            path=path,
            date=trade_date,
            exists=False,
            reason_codes=["missing_promotion_readiness_windows"],
        )
        return {
            "available": False,
            "promotion_recommendation": "NO_PROMOTION_RECOMMENDED",
            "strategies": {},
            "blockers": ["missing_promotion_readiness_windows"],
            "confidence": "LOW",
            "reason_codes": ["missing_promotion_readiness_windows"],
            "source_artifacts": [],
        }, source
    reasons = list(payload.get("reason_codes") or ["ok"])
    available = bool(payload.get("available"))
    if not available and "promotion_windows_unavailable" not in reasons:
        reasons.append("promotion_windows_unavailable")
    section = {
        "available": available,
        "promotion_recommendation": payload.get("promotion_recommendation") or "NO_PROMOTION_RECOMMENDED",
        "strategies": payload.get("strategies") or {},
        "windows": payload.get("windows") or [],
        "blockers": payload.get("blockers") or [],
        "confidence": payload.get("confidence") or "LOW",
        "reason_codes": sorted(set(reasons)),
        "source_artifacts": [str(path)] + list(payload.get("source_artifacts") or []),
    }
    source = _status(
        artifact_name="promotion_readiness_windows",
        path=path,
        date=str(payload.get("date") or trade_date),
        exists=True,
        confidence=section["confidence"],
        reason_codes=section["reason_codes"],
        status="PRESENT" if available else "PARTIAL",
    )
    return section, source


def _build_strategy_differentiation_section(repo: Path, trade_date: str) -> tuple[dict[str, Any], dict[str, Any]]:
    path = repo / "outputs" / "research" / "strategy_differentiation" / trade_date / "strategy_differentiation.json"
    payload = _read_json(path)
    if payload is None:
        source = _status(
            artifact_name="strategy_differentiation",
            path=path,
            date=trade_date,
            exists=False,
            reason_codes=["missing_strategy_differentiation"],
        )
        return {
            "available": False,
            "pairs": [],
            "blockers": ["missing_strategy_differentiation"],
            "factor_exposure_available": False,
            "position_contributions_available": False,
            "confidence": "LOW",
            "reason_codes": ["missing_strategy_differentiation"],
            "source_artifacts": [],
        }, source
    reasons = list(payload.get("reason_codes") or ["ok"])
    available = bool(payload.get("available"))
    if not available and "strategy_differentiation_unavailable" not in reasons:
        reasons.append("strategy_differentiation_unavailable")
    section = {
        "available": available,
        "pairs": payload.get("pairs") or [],
        "blockers": payload.get("blockers") or [],
        "factor_exposure_available": bool(payload.get("factor_exposure_available")),
        "position_contributions_available": bool(payload.get("position_contributions_available")),
        "factor_exposure_source_artifacts": payload.get("factor_exposure_source_artifacts") or [],
        "position_contribution_source_artifacts": payload.get("position_contribution_source_artifacts") or [],
        "confidence": payload.get("confidence") or "LOW",
        "reason_codes": sorted(set(reasons)),
        "source_artifacts": [str(path)] + list(payload.get("source_artifacts") or []),
    }
    source = _status(
        artifact_name="strategy_differentiation",
        path=path,
        date=str(payload.get("date") or trade_date),
        exists=True,
        confidence=section["confidence"],
        reason_codes=section["reason_codes"],
        status="PRESENT" if available else "PARTIAL",
    )
    return section, source


def _build_risk_coverage_section(repo: Path, trade_date: str) -> tuple[dict[str, Any], dict[str, Any]]:
    path = repo / "outputs" / "research" / "risk_coverage" / trade_date / "risk_coverage.json"
    payload = _read_json(path)
    if payload is None:
        source = _status(
            artifact_name="risk_coverage",
            path=path,
            date=trade_date,
            exists=False,
            reason_codes=["missing_risk_coverage"],
        )
        return {
            "available": False,
            "confidence": "LOW",
            "risk_level": "UNKNOWN",
            "strategies": {},
            "reason_codes": ["missing_risk_coverage"],
            "source_artifacts": [],
        }, source
    reasons = sorted(set(str(code) for code in list(payload.get("reason_codes") or ["ok"])))
    available = bool(payload.get("available"))
    if not available and "risk_coverage_unavailable" not in reasons:
        reasons.append("risk_coverage_unavailable")
    section = {
        "available": available,
        "confidence": payload.get("confidence") or "LOW",
        "risk_level": payload.get("risk_level") or "UNKNOWN",
        "holdings_source_date": payload.get("holdings_source_date"),
        "position_count": payload.get("position_count"),
        "gross_exposure": payload.get("gross_exposure"),
        "net_exposure": payload.get("net_exposure"),
        "top3_concentration": payload.get("top3_concentration"),
        "top5_concentration": payload.get("top5_concentration"),
        "top10_concentration": payload.get("top10_concentration"),
        "max_single_name_weight": payload.get("max_single_name_weight"),
        "strategies": payload.get("strategies") or {},
        "reason_codes": sorted(set(reasons)),
        "source_artifacts": [str(path)] + list(payload.get("source_artifacts") or []),
    }
    source = _status(
        artifact_name="risk_coverage",
        path=path,
        date=str(payload.get("date") or trade_date),
        exists=True,
        confidence=section["confidence"],
        reason_codes=section["reason_codes"],
        status="PRESENT" if available else "PARTIAL",
    )
    return section, source


def _build_strategy_differentiation_deep_section(repo: Path, trade_date: str) -> tuple[dict[str, Any], dict[str, Any]]:
    path = repo / "outputs" / "research" / "strategy_differentiation" / trade_date / "strategy_differentiation_deep.json"
    payload = _read_json(path)
    if payload is None:
        source = _status(
            artifact_name="strategy_differentiation_deep",
            path=path,
            date=trade_date,
            exists=False,
            reason_codes=["missing_strategy_differentiation_deep"],
        )
        return {
            "available": False,
            "confidence": "LOW",
            "aggregate_verdict": "WEAK_DIFFERENTIATION",
            "pairs": [],
            "blockers": ["missing_strategy_differentiation_deep"],
            "reason_codes": ["missing_strategy_differentiation_deep"],
            "source_artifacts": [],
        }, source
    reasons = sorted(set(str(code) for code in list(payload.get("reason_codes") or ["ok"])))
    available = bool(payload.get("available"))
    section = {
        "available": available,
        "confidence": payload.get("confidence") or "LOW",
        "aggregate_verdict": payload.get("aggregate_verdict") or "WEAK_DIFFERENTIATION",
        "pairs": payload.get("pairs") or [],
        "blockers": payload.get("blockers") or [],
        "reason_codes": reasons,
        "source_artifacts": [str(path)] + list(payload.get("source_artifacts") or []),
    }
    source = _status(
        artifact_name="strategy_differentiation_deep",
        path=path,
        date=str(payload.get("date") or trade_date),
        exists=True,
        confidence=section["confidence"],
        reason_codes=reasons,
        status="PRESENT" if available else "PARTIAL",
    )
    return section, source


def _build_position_sizing_section(repo: Path, trade_date: str) -> tuple[dict[str, Any], dict[str, Any]]:
    path = repo / "outputs" / "research" / "position_sizing" / trade_date / "position_sizing_research.json"
    payload = _read_json(path)
    if payload is None:
        source = _status(
            artifact_name="position_sizing_research",
            path=path,
            date=trade_date,
            exists=False,
            reason_codes=["missing_position_sizing_research"],
        )
        return {
            "available": False,
            "confidence": "LOW",
            "strategies": {},
            "reason_codes": ["missing_position_sizing_research"],
            "source_artifacts": [],
        }, source
    reasons = sorted(set(str(code) for code in list(payload.get("reason_codes") or ["ok"])))
    available = bool(payload.get("available"))
    section = {
        "available": available,
        "confidence": payload.get("confidence") or "LOW",
        "holdings_source_date": payload.get("holdings_source_date"),
        "returns_source_date": payload.get("returns_source_date"),
        "strategies": payload.get("strategies") or {},
        "reason_codes": reasons,
        "source_artifacts": [str(path)] + list(payload.get("source_artifacts") or []),
    }
    source = _status(
        artifact_name="position_sizing_research",
        path=path,
        date=str(payload.get("date") or trade_date),
        exists=True,
        confidence=section["confidence"],
        reason_codes=reasons,
        status="PRESENT" if available else "PARTIAL",
    )
    return section, source


def _build_universe_governance_section(repo: Path, trade_date: str) -> tuple[dict[str, Any], dict[str, Any]]:
    path = repo / "outputs" / "research" / "universe_governance" / trade_date / "universe_governance.json"
    payload = _read_json(path)
    if payload is None:
        source = _status(
            artifact_name="universe_governance",
            path=path,
            date=trade_date,
            exists=False,
            reason_codes=["missing_universe_governance"],
        )
        return {
            "available": False,
            "confidence": "LOW",
            "blockers": ["missing_universe_governance"],
            "reason_codes": ["missing_universe_governance"],
            "source_artifacts": [],
        }, source
    reasons = sorted(set(str(code) for code in list(payload.get("reason_codes") or ["ok"])))
    available = bool(payload.get("available"))
    section = {
        "available": available,
        "confidence": payload.get("confidence") or "LOW",
        "security_master_asof_date": payload.get("security_master_asof_date"),
        "stale_universe": bool(payload.get("stale_universe")),
        "planned_symbols": payload.get("planned_symbols") or [],
        "holdings_symbols": payload.get("holdings_symbols") or [],
        "alias_resolutions": payload.get("alias_resolutions") or [],
        "blockers": payload.get("blockers") or [],
        "coverage_summary": payload.get("coverage_summary") or {},
        "reason_codes": reasons,
        "source_artifacts": [str(path)] + list(payload.get("source_artifacts") or []),
    }
    source = _status(
        artifact_name="universe_governance",
        path=path,
        date=str(payload.get("date") or trade_date),
        exists=True,
        confidence=section["confidence"],
        reason_codes=reasons,
        status="PRESENT" if available else "PARTIAL",
    )
    return section, source


def _generic_section_loader(
    *,
    artifact_name: str,
    path: Path,
    trade_date: str,
    default_section: dict[str, Any],
    extra_keys: tuple[str, ...] = (),
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Common loader for audit/diagnostic artifacts."""
    payload = _read_json(path)
    if payload is None:
        source = _status(
            artifact_name=artifact_name,
            path=path,
            date=trade_date,
            exists=False,
            reason_codes=[f"missing_{artifact_name}"],
        )
        return default_section, source
    reasons = sorted(set(str(code) for code in list(payload.get("reason_codes") or ["ok"])))
    available = bool(payload.get("available", True))
    section: dict[str, Any] = {
        "available": available,
        "confidence": payload.get("confidence") or "LOW",
        "reason_codes": reasons,
        "source_artifacts": [str(path)] + list(payload.get("source_artifacts") or []),
    }
    for key in extra_keys:
        if key in payload:
            section[key] = payload[key]
    source = _status(
        artifact_name=artifact_name,
        path=path,
        date=str(payload.get("date") or trade_date),
        exists=True,
        confidence=section["confidence"],
        reason_codes=reasons,
        status="PRESENT" if available else "PARTIAL",
    )
    return section, source


def _build_governance_blocker_audit_section(repo: Path, trade_date: str) -> tuple[dict[str, Any], dict[str, Any]]:
    return _generic_section_loader(
        artifact_name="governance_blocker_audit",
        path=repo / "outputs" / "research" / "governance_blocker_audit" / trade_date / "governance_blocker_audit.json",
        trade_date=trade_date,
        default_section={
            "available": False,
            "confidence": "LOW",
            "classification_counts": {},
            "classifications": [],
            "reason_codes": ["missing_governance_blocker_audit"],
            "source_artifacts": [],
        },
        extra_keys=("classification_counts", "classifications"),
    )


def _build_security_master_reconciliation_section(repo: Path, trade_date: str) -> tuple[dict[str, Any], dict[str, Any]]:
    return _generic_section_loader(
        artifact_name="security_master_reconciliation",
        path=repo / "outputs" / "research" / "security_master_reconciliation" / trade_date / "security_master_reconciliation.json",
        trade_date=trade_date,
        default_section={
            "available": False,
            "confidence": "LOW",
            "security_master_asof_date": None,
            "coverage": {},
            "unknown_symbols": [],
            "duplicates": [],
            "inactive_aliases": [],
            "reason_codes": ["missing_security_master_reconciliation"],
            "source_artifacts": [],
        },
        extra_keys=("security_master_asof_date", "coverage", "unknown_symbols", "duplicates", "inactive_aliases", "per_strategy_holdings"),
    )


def _build_execution_payload_audit_section(repo: Path, trade_date: str) -> tuple[dict[str, Any], dict[str, Any]]:
    return _generic_section_loader(
        artifact_name="execution_payload_audit",
        path=repo / "outputs" / "research" / "execution_payload_audit" / trade_date / "execution_payload_audit.json",
        trade_date=trade_date,
        default_section={
            "available": False,
            "confidence": "LOW",
            "verdict": "UNKNOWN",
            "root_cause": "missing_execution_payload_audit",
            "remediation": "Run scripts/build_execution_payload_audit.py --date <date>.",
            "target_payload_present": False,
            "most_recent_payload": None,
            "reason_codes": ["missing_execution_payload_audit"],
            "source_artifacts": [],
        },
        extra_keys=("verdict", "root_cause", "remediation", "target_payload_present", "most_recent_payload", "precompute_dates", "coverage"),
    )


def _build_differentiation_diagnostic_section(repo: Path, trade_date: str) -> tuple[dict[str, Any], dict[str, Any]]:
    return _generic_section_loader(
        artifact_name="differentiation_diagnostic",
        path=repo / "outputs" / "research" / "differentiation_diagnostic" / trade_date / "differentiation_diagnostic.json",
        trade_date=trade_date,
        default_section={
            "available": False,
            "confidence": "LOW",
            "aggregate_verdict": "UNKNOWN",
            "verdict_counts": {},
            "pairs": [],
            "reason_codes": ["missing_differentiation_diagnostic"],
            "source_artifacts": [],
        },
        extra_keys=("aggregate_verdict", "verdict_counts", "pairs", "differentiation_inputs_complete"),
    )


def _build_concentration_diagnostic_section(repo: Path, trade_date: str) -> tuple[dict[str, Any], dict[str, Any]]:
    return _generic_section_loader(
        artifact_name="concentration_diagnostic",
        path=repo / "outputs" / "research" / "concentration_diagnostic" / trade_date / "concentration_diagnostic.json",
        trade_date=trade_date,
        default_section={
            "available": False,
            "confidence": "LOW",
            "classification_counts": {},
            "strategies": [],
            "reason_codes": ["missing_concentration_diagnostic"],
            "source_artifacts": [],
        },
        extra_keys=("classification_counts", "strategies"),
    )


def _build_governance_maturity_section(repo: Path, trade_date: str) -> tuple[dict[str, Any], dict[str, Any]]:
    return _generic_section_loader(
        artifact_name="governance_maturity",
        path=repo / "outputs" / "research" / "governance_maturity" / trade_date / "governance_maturity.json",
        trade_date=trade_date,
        default_section={
            "available": False,
            "confidence": "LOW",
            "total_score": 0.0,
            "tier": "IMMATURE",
            "components": [],
            "blockers_real": 0,
            "blockers_configuration": 0,
            "blockers_data_quality": 0,
            "blockers_observation_window": 0,
            "reason_codes": ["missing_governance_maturity"],
            "source_artifacts": [],
        },
        extra_keys=(
            "total_score",
            "tier",
            "components",
            "blockers_real",
            "blockers_configuration",
            "blockers_data_quality",
            "blockers_observation_window",
        ),
    )


def _build_operational_drag_section(repo: Path, trade_date: str) -> tuple[dict[str, Any], dict[str, Any]]:
    base = repo / "outputs" / "operational_drag" / trade_date
    drag_path = base / "operational_drag.json"
    drag = _read_json(drag_path)
    if drag is None:
        source = _status(
            artifact_name="operational_drag",
            path=drag_path,
            date=trade_date,
            exists=False,
            reason_codes=["missing_operational_drag"],
        )
        return {
            "available": False,
            "confidence": "LOW",
            "latest_daily_operational_drag": None,
            "current_cumulative_operational_drag": None,
            "performance_gap_driver": "indeterminate",
            "data_confidence": "LOW",
            "missing_data_warnings": ["missing_operational_drag"],
            "main_drag_contributors": [],
            "stable_windows": [],
            "reason_codes": ["missing_operational_drag"],
            "source_artifacts": [],
        }, source
    attribution = _read_json(base / "operational_drag_attribution.json") or {}
    windows = _read_json(base / "stable_window_analysis.json") or {}
    latest = drag.get("latest") if isinstance(drag.get("latest"), dict) else {}
    contributors = attribution.get("attributions") if isinstance(attribution.get("attributions"), list) else []
    reasons = sorted(set(str(code) for code in list(drag.get("reason_codes") or ["ok"])))
    warnings = [
        reason for reason in reasons
        if "missing" in reason or "unavailable" in reason or "no_aligned" in reason
    ]
    section = {
        "available": bool(drag.get("available")),
        "confidence": drag.get("confidence") or "LOW",
        "latest_daily_operational_drag": latest.get("daily_operational_drag"),
        "current_cumulative_operational_drag": latest.get("cumulative_operational_drag"),
        "performance_gap_driver": _classify_operational_drag_driver(latest, contributors),
        "data_confidence": drag.get("confidence") or "LOW",
        "missing_data_warnings": warnings,
        "main_drag_contributors": contributors,
        "stable_windows": windows.get("windows") or [],
        # FR-061: cleaned, CIO-readable view of requested-date health vs caveats.
        "current_date_status": drag.get("current_date_status"),
        "decision_grade": drag.get("decision_grade"),
        "decision_grade_explanation": drag.get("decision_grade_explanation"),
        "current_date_reason_codes": list(drag.get("current_date_reason_codes") or []),
        "historical_reason_codes": list(drag.get("historical_reason_codes") or []),
        "window_reason_codes": list(drag.get("window_reason_codes") or []),
        "material_reason_codes": list(drag.get("material_reason_codes") or []),
        "reason_codes": reasons,
        "source_artifacts": [
            str(path) for path in (
                drag_path,
                base / "operational_drag_attribution.json",
                base / "stable_window_analysis.json",
            )
            if path.exists()
        ],
    }
    source = _status(
        artifact_name="operational_drag",
        path=drag_path,
        date=str(drag.get("date") or trade_date),
        exists=True,
        confidence=section["confidence"],
        reason_codes=reasons,
        status="PRESENT" if section["available"] else "PARTIAL",
    )
    return section, source


def _classify_operational_drag_driver(latest: dict[str, Any], contributors: list[Any]) -> str:
    drag = _safe_float(latest.get("cumulative_operational_drag"))
    intended_excess = _safe_float(latest.get("intended_vs_spy_excess"))
    actual_excess = _safe_float(latest.get("actual_vs_spy_excess"))
    categories = {
        str(row.get("category"))
        for row in contributors
        if isinstance(row, dict)
    }
    operational_categories = {
        "under_deployment_cash_drag",
        "stale_price_gate",
        "buy_suppression",
        "partial_execution",
        "symbol_resolution",
        "missing_broker_position",
        "reconciliation_mismatch",
    }
    if drag is None:
        return "indeterminate"
    if abs(drag) >= 0.005 and categories & operational_categories:
        return "operations_driven"
    if intended_excess is not None and actual_excess is not None:
        if intended_excess < -0.005 and abs(drag) < 0.0025:
            return "strategy_driven"
        if intended_excess < -0.005 and abs(drag) >= 0.0025:
            return "mixed"
    return "indeterminate"


def _build_governance_calibration_section(repo: Path, trade_date: str) -> tuple[dict[str, Any], dict[str, Any]]:
    return _generic_section_loader(
        artifact_name="governance_calibration",
        path=repo / "outputs" / "research" / "governance_calibration" / trade_date / "governance_calibration.json",
        trade_date=trade_date,
        default_section={
            "available": False,
            "confidence": "LOW",
            "design_aware_thresholds": {},
            "legacy_fixed_thresholds": {},
            "strategies": [],
            "calibration_status_counts": {},
            "reason_codes": ["missing_governance_calibration"],
            "source_artifacts": [],
        },
        extra_keys=(
            "design_aware_thresholds",
            "legacy_fixed_thresholds",
            "strategies",
            "calibration_status_counts",
        ),
    )


def _build_governance_reclassification_section(repo: Path, trade_date: str) -> tuple[dict[str, Any], dict[str, Any]]:
    return _generic_section_loader(
        artifact_name="governance_reclassification",
        path=repo / "outputs" / "research" / "governance_calibration" / trade_date / "governance_reclassification.json",
        trade_date=trade_date,
        default_section={
            "available": False,
            "confidence": "LOW",
            "comparisons": [],
            "change_counts": {},
            "reason_codes": ["missing_governance_reclassification"],
            "source_artifacts": [],
        },
        extra_keys=("comparisons", "change_counts"),
    )


def _build_promotion_governance_section(repo: Path, trade_date: str) -> tuple[dict[str, Any], dict[str, Any]]:
    path = repo / "outputs" / "research" / "promotion_governance" / trade_date / "promotion_governance.json"
    payload = _read_json(path)
    control_strategy = load_strategy_registry_for_repo(repo).baseline_strategy_id()
    if payload is None:
        source = _status(
            artifact_name="promotion_governance",
            path=path,
            date=trade_date,
            exists=False,
            reason_codes=["missing_promotion_governance"],
        )
        return {
            "available": False,
            "confidence": "LOW",
            "current_control_strategy": control_strategy,
            "promotion_recommendation": "NO_PROMOTION_RECOMMENDED",
            "demotion_recommendation": "NO_DEMOTION_RECOMMENDED",
            "challenger_rankings": [],
            "strategies": {},
            "blocker_categories": ["NONE"],
            "evidence_strength": "LOW",
            "reason_codes": ["missing_promotion_governance"],
            "source_artifacts": [],
        }, source
    reasons = sorted(set(str(code) for code in list(payload.get("reason_codes") or ["ok"])))
    available = bool(payload.get("available"))
    section = {
        "available": available,
        "confidence": payload.get("confidence") or "LOW",
        "current_control_strategy": payload.get("current_control_strategy") or control_strategy,
        "promotion_recommendation": payload.get("promotion_recommendation") or "NO_PROMOTION_RECOMMENDED",
        "demotion_recommendation": payload.get("demotion_recommendation") or "NO_DEMOTION_RECOMMENDED",
        "challenger_rankings": payload.get("challenger_rankings") or [],
        "strategies": payload.get("strategies") or {},
        "blocker_categories": payload.get("blocker_categories") or ["NONE"],
        "evidence_strength": payload.get("evidence_strength") or "LOW",
        "reason_codes": reasons,
        "source_artifacts": [str(path)] + list(payload.get("source_artifacts") or []),
    }
    source = _status(
        artifact_name="promotion_governance",
        path=path,
        date=str(payload.get("date") or trade_date),
        exists=True,
        confidence=section["confidence"],
        reason_codes=reasons,
        status="PRESENT" if available else "PARTIAL",
    )
    return section, source


def _build_regime_attribution_section(repo: Path, trade_date: str) -> tuple[dict[str, Any], dict[str, Any]]:
    path = repo / "outputs" / "research" / "regime_attribution" / trade_date / "regime_attribution.json"
    payload = _read_json(path)
    if payload is None:
        source = _status(
            artifact_name="regime_attribution",
            path=path,
            date=trade_date,
            exists=False,
            reason_codes=["missing_regime_attribution"],
        )
        return {
            "available": False,
            "confidence": "LOW",
            "regime_labels": [],
            "regime_distribution": {},
            "history_window": {},
            "strategies": {},
            "reason_codes": ["missing_regime_attribution"],
            "source_artifacts": [],
        }, source
    reasons = sorted(set(str(code) for code in list(payload.get("reason_codes") or ["ok"])))
    available = bool(payload.get("available"))
    section = {
        "available": available,
        "confidence": payload.get("confidence") or "LOW",
        "regime_labels": payload.get("regime_labels") or [],
        "regime_distribution": payload.get("regime_distribution") or {},
        "history_window": payload.get("history_window") or {},
        "strategies": payload.get("strategies") or {},
        "reason_codes": reasons,
        "source_artifacts": [str(path)] + list(payload.get("source_artifacts") or []),
    }
    source = _status(
        artifact_name="regime_attribution",
        path=path,
        date=str(payload.get("date") or trade_date),
        exists=True,
        confidence=section["confidence"],
        reason_codes=reasons,
        status="PRESENT" if available else "PARTIAL",
    )
    return section, source


def _build_dynamic_allocation_section(repo: Path, trade_date: str) -> tuple[dict[str, Any], dict[str, Any]]:
    path = repo / "outputs" / "research" / "dynamic_strategy_allocation" / trade_date / "dynamic_strategy_allocation.json"
    payload = _read_json(path)
    if payload is None:
        source = _status(
            artifact_name="dynamic_strategy_allocation",
            path=path,
            date=trade_date,
            exists=False,
            reason_codes=["missing_dynamic_strategy_allocation"],
        )
        return {
            "available": False,
            "confidence": "LOW",
            "is_research_only": True,
            "production_weights_modified": False,
            "allocation_recommendation": "no_allocation_change_recommended",
            "promotion_governance_allows_change": False,
            "policies": [],
            "ranking": [],
            "reason_codes": ["missing_dynamic_strategy_allocation"],
            "source_artifacts": [],
        }, source
    reasons = sorted(set(str(code) for code in list(payload.get("reason_codes") or ["ok"])))
    available = bool(payload.get("available"))
    section = {
        "available": available,
        "confidence": payload.get("confidence") or "LOW",
        "is_research_only": bool(payload.get("is_research_only", True)),
        "production_weights_modified": bool(payload.get("production_weights_modified", False)),
        "allocation_recommendation": payload.get("allocation_recommendation") or "no_allocation_change_recommended",
        "promotion_governance_allows_change": bool(payload.get("promotion_governance_allows_change", False)),
        "policies": payload.get("policies") or [],
        "ranking": payload.get("ranking") or [],
        "reason_codes": reasons,
        "source_artifacts": [str(path)] + list(payload.get("source_artifacts") or []),
    }
    source = _status(
        artifact_name="dynamic_strategy_allocation",
        path=path,
        date=str(payload.get("date") or trade_date),
        exists=True,
        confidence=section["confidence"],
        reason_codes=reasons,
        status="PRESENT" if available else "PARTIAL",
    )
    return section, source


def _build_tier1_research_controls_section(sections: dict[str, Any]) -> dict[str, Any]:
    timing = sections["execution_timing_study"]
    promotion = sections["promotion_readiness_windows"]
    differentiation = sections["strategy_differentiation"]
    risk = sections.get("risk_concentration") or {}
    blockers: list[str] = []
    if not timing.get("available"):
        blockers.append("missing_timing_coverage")
    if not differentiation.get("factor_exposure_available"):
        blockers.append("factor_exposure_missing")
    if not differentiation.get("position_contributions_available"):
        blockers.append("position_contributions_missing")
    if not risk.get("available"):
        blockers.append("missing_risk_summary")
    blockers.extend(str(code) for code in promotion.get("blockers") or [])
    blockers.extend(str(code) for code in differentiation.get("blockers") or [])
    if any(str(code).endswith("weak_differentiation") for code in blockers):
        blockers.append("weak_differentiation")
    if not blockers:
        blockers = ["no_tier1_blockers_detected"]
    blocker_categories: set[str] = set()
    data_reason_tokens = ("missing", "unavailable", "coverage", "bad_schema", "parser_error", "empty", "date_differs_from_target")
    reason_pool = list(timing.get("reason_codes") or []) + list(differentiation.get("reason_codes") or []) + blockers
    if any(any(token in str(reason) for token in data_reason_tokens) for reason in reason_pool):
        blocker_categories.add("DATA_COVERAGE")
    if "missing_risk_summary" in blockers:
        blocker_categories.add("RISK_COVERAGE")
    if any("insufficient_observations" in str(reason) for reason in list(promotion.get("reason_codes") or []) + list(promotion.get("blockers") or [])):
        blocker_categories.add("OBSERVATION_WINDOW")
    if any("weak_differentiation" in str(reason) for reason in reason_pool):
        blocker_categories.add("MODEL_DIFFERENTIATION")
    if not blocker_categories:
        blocker_categories.add("NONE")
    promotion_recommendation = str(promotion.get("promotion_recommendation") or "NO_PROMOTION_RECOMMENDED")
    promotion_blocked = any(
        blocker != "no_tier1_blockers_detected"
        for blocker in blockers
    )
    if promotion_recommendation.startswith("PROMOTION_REVIEW_READY") and differentiation.get("available") and timing.get("available") and not promotion_blocked:
        recommendation = promotion_recommendation
    else:
        recommendation = "No promotion recommended"
    reason_codes = sorted(set(
        list(timing.get("reason_codes") or [])
        + list(promotion.get("reason_codes") or [])
        + list(differentiation.get("reason_codes") or [])
        + list(risk.get("reason_codes") or [])
    ))
    if len(reason_codes) > 1:
        reason_codes = [code for code in reason_codes if code != "ok"]
    return {
        "available": any(section.get("available") for section in (timing, promotion, differentiation)),
        "execution_timing_status": "available" if timing.get("available") else "missing_or_unavailable",
        "promotion_readiness_status": "available" if promotion.get("available") else "missing_or_unavailable",
        "strategy_differentiation_status": "available" if differentiation.get("available") else "missing_or_unavailable",
        "factor_exposure_status": "available" if differentiation.get("factor_exposure_available") else "missing_or_unavailable",
        "position_contribution_status": "available" if differentiation.get("position_contributions_available") else "missing_or_unavailable",
        "differentiation_confidence": differentiation.get("confidence") or "LOW",
        "blocker_categories": sorted(blocker_categories),
        "recommendation": recommendation,
        "blockers": sorted(set(blockers)),
        "reason_codes": reason_codes or ["ok"],
    }


def _build_tier2_research_controls_section(sections: dict[str, Any]) -> dict[str, Any]:
    risk = sections["risk_coverage"]
    deep = sections["strategy_differentiation_deep"]
    sizing = sections["position_sizing_research"]
    universe = sections["universe_governance"]
    promotion = sections["promotion_readiness_windows"]
    blockers: list[str] = []
    if not risk.get("available"):
        blockers.append("risk_coverage_incomplete")
    if str(deep.get("aggregate_verdict") or "WEAK_DIFFERENTIATION") != "STRONG_DIFFERENTIATION":
        blockers.append("weak_or_incomplete_deep_differentiation")
    if not universe.get("available"):
        blockers.extend(str(code) for code in universe.get("blockers") or ["universe_governance_incomplete"])
    if not sizing.get("available"):
        blockers.append("position_sizing_research_incomplete")
    if any("insufficient_observations" in str(code) for code in list(promotion.get("reason_codes") or []) + list(promotion.get("blockers") or [])):
        blockers.append("immature_observation_window")
    blocker_categories: set[str] = set()
    if any("risk" in blocker for blocker in blockers):
        blocker_categories.add("RISK_COVERAGE")
    if any("differentiation" in blocker for blocker in blockers):
        blocker_categories.add("MODEL_DIFFERENTIATION")
    if any("universe" in blocker or "symbol" in blocker or "security_master" in blocker for blocker in blockers):
        blocker_categories.add("UNIVERSE_GOVERNANCE")
    if any("observation" in blocker for blocker in blockers):
        blocker_categories.add("OBSERVATION_WINDOW")
    if any("sizing" in blocker for blocker in blockers):
        blocker_categories.add("SIZING_RESEARCH")
    if not blocker_categories:
        blocker_categories.add("NONE")
    promotion_recommendation = str(promotion.get("promotion_recommendation") or "NO_PROMOTION_RECOMMENDED")
    if not blockers and promotion_recommendation.startswith("PROMOTION_REVIEW_READY"):
        recommendation = promotion_recommendation
    else:
        recommendation = "No promotion recommended"
    reason_codes = sorted(
        {
            str(code)
            for code in list(risk.get("reason_codes") or [])
            + list(deep.get("reason_codes") or [])
            + list(sizing.get("reason_codes") or [])
            + list(universe.get("reason_codes") or [])
            + blockers
            if code != "ok"
        }
    ) or ["ok"]
    return {
        "available": any(section.get("available") for section in (risk, deep, sizing, universe)),
        "risk_coverage_status": "available" if risk.get("available") else "missing_or_unavailable",
        "deep_differentiation_verdict": deep.get("aggregate_verdict") or "WEAK_DIFFERENTIATION",
        "position_sizing_status": "available" if sizing.get("available") else "missing_or_unavailable",
        "universe_governance_status": "available" if universe.get("available") else "missing_or_unavailable",
        "recommendation": recommendation,
        "blocker_categories": sorted(blocker_categories),
        "blockers": sorted(set(blockers)) or ["no_tier2_blockers_detected"],
        "reason_codes": reason_codes,
    }


def _build_tier3_research_controls_section(sections: dict[str, Any]) -> dict[str, Any]:
    governance = sections["promotion_governance"]
    regime = sections["regime_attribution"]
    allocation = sections["dynamic_strategy_allocation"]
    blockers: list[str] = []
    if not governance.get("available"):
        blockers.append("promotion_governance_incomplete")
    if not regime.get("available"):
        blockers.append("regime_attribution_incomplete")
    if not allocation.get("available"):
        blockers.append("dynamic_strategy_allocation_incomplete")
    governance_blockers = list(governance.get("blocker_categories") or [])
    for cat in governance_blockers:
        if cat and cat != "NONE":
            blockers.append(f"governance_blocker:{cat}")
    promotion_rec = str(governance.get("promotion_recommendation") or "NO_PROMOTION_RECOMMENDED")
    # Conservative: only recommend a strategy promotion when promotion
    # governance explicitly names a single strategy. Strings like
    # "NO_PROMOTION_RECOMMENDED" or "MULTIPLE_PROMOTE_CANDIDATES" are
    # treated as no-promotion.
    if promotion_rec.startswith("caerus_") and not blockers:
        recommendation = f"Promote {promotion_rec}"
    else:
        recommendation = "No promotion recommended"
    # Allocation recommendation only surfaces when governance permits.
    if (
        allocation.get("available")
        and promotion_rec.startswith("caerus_")
        and bool(allocation.get("promotion_governance_allows_change"))
        and str(allocation.get("allocation_recommendation") or "") != "no_allocation_change_recommended"
    ):
        allocation_recommendation = allocation.get("allocation_recommendation")
    else:
        allocation_recommendation = "no_allocation_change_recommended"
    reason_codes = sorted(
        {
            str(code)
            for code in list(governance.get("reason_codes") or [])
            + list(regime.get("reason_codes") or [])
            + list(allocation.get("reason_codes") or [])
            + blockers
            if code != "ok"
        }
    ) or ["ok"]
    return {
        "available": any(section.get("available") for section in (governance, regime, allocation)),
        "promotion_governance_status": "available" if governance.get("available") else "missing_or_unavailable",
        "regime_attribution_status": "available" if regime.get("available") else "missing_or_unavailable",
        "dynamic_strategy_allocation_status": "available" if allocation.get("available") else "missing_or_unavailable",
        "promotion_recommendation": promotion_rec,
        "demotion_recommendation": governance.get("demotion_recommendation") or "NO_DEMOTION_RECOMMENDED",
        "allocation_recommendation": allocation_recommendation,
        "recommendation": recommendation,
        "evidence_strength": governance.get("evidence_strength") or "LOW",
        "blockers": sorted(set(blockers)) or ["no_tier3_blockers_detected"],
        "reason_codes": reason_codes,
    }


def _strategy_status_from_decision(entry: StrategyRegistryEntry, decision: str) -> str:
    if entry.role == "baseline" or entry.status == "paper":
        return "BENCHMARK_CONTROL" if decision in {"HOLD", "BLOCKED"} else decision
    if decision == "DEMOTE":
        return "DEMOTED"
    if decision == "BLOCKED":
        return "BLOCKED"
    return decision


def _build_strategy_statuses(
    strategies: dict[str, Any],
    active_entries: tuple[StrategyRegistryEntry, ...],
) -> dict[str, dict[str, Any]]:
    statuses: dict[str, dict[str, Any]] = {}
    for entry in active_entries:
        default_decision = "HOLD" if entry.role == "baseline" or entry.status == "paper" else "BLOCKED"
        raw = strategies.get(entry.strategy_id) or {}
        decision = str(raw.get("decision") or default_decision).upper()
        statuses[entry.strategy_id] = {
            "strategy_id": entry.strategy_id,
            "display_name": entry.display_name,
            "short_name": entry.compact_name(),
            "role": entry.role,
            "decision": decision,
            "status": _strategy_status_from_decision(entry, decision),
            "strategy_type": entry.strategy_type,
            "family": entry.family,
        }
    return statuses


def _build_final_control_summary_section(
    sections: dict[str, Any],
    active_entries: tuple[StrategyRegistryEntry, ...],
) -> dict[str, Any]:
    """All-tier rollup so consumers can read a single object for the
    final control status. Conservative by construction: no promotion or
    allocation change unless every tier agrees.
    """
    tier1 = sections.get("tier1_research_controls") or {}
    tier2 = sections.get("tier2_research_controls") or {}
    tier3 = sections.get("tier3_research_controls") or {}
    governance = sections.get("promotion_governance") or {}
    allocation = sections.get("dynamic_strategy_allocation") or {}
    blocker_audit = sections.get("governance_blocker_audit") or {}
    maturity = sections.get("governance_maturity") or {}

    tier1_rec = str(tier1.get("recommendation") or "No promotion recommended")
    tier2_rec = str(tier2.get("recommendation") or "No promotion recommended")
    tier3_rec = str(tier3.get("recommendation") or "No promotion recommended")
    promotion_rec = str(tier3.get("promotion_recommendation") or "NO_PROMOTION_RECOMMENDED")

    # Final recommendation requires all three tiers to be non-defaulted
    # AND tier 3 to name a specific strategy.
    all_tiers_clear = all(rec != "No promotion recommended" for rec in (tier1_rec, tier2_rec, tier3_rec))
    if all_tiers_clear and promotion_rec.startswith("caerus_"):
        current_recommendation = f"Promote {promotion_rec}"
    else:
        current_recommendation = "No promotion recommended"

    # Per-strategy status snapshots.
    strategies = governance.get("strategies") or {}
    strategy_statuses = _build_strategy_statuses(strategies, active_entries)
    lyra_decision = str((strategies.get("caerus_lyra") or {}).get("decision") or "BLOCKED").upper()
    orion_decision = str((strategies.get("caerus_orion") or {}).get("decision") or "BLOCKED").upper()
    polaris_decision = str((strategies.get("caerus_polaris") or {}).get("decision") or "HOLD").upper()

    lyra_status = "BLOCKED" if lyra_decision in {"BLOCKED", "DEMOTE", "HOLD"} else lyra_decision
    orion_status = (
        "BLOCKED" if orion_decision == "BLOCKED"
        else "DEMOTED" if orion_decision == "DEMOTE"
        else orion_decision  # WATCH, HOLD, PROMOTION_CANDIDATE, PROMOTE
    )
    polaris_status = "BENCHMARK_CONTROL" if polaris_decision in {"HOLD", "BLOCKED"} else polaris_decision

    # Top blockers: union of all-tier blocker categories.
    top_blockers: list[str] = []
    for tier_section in (tier1, tier2, tier3):
        for blocker in tier_section.get("blockers") or []:
            code = str(blocker)
            if code in {"no_tier1_blockers_detected", "no_tier2_blockers_detected", "no_tier3_blockers_detected"}:
                continue
            if code not in top_blockers:
                top_blockers.append(code)

    # Evidence maturity now comes from the deterministic governance_maturity
    # tier when available; fall back to confidence min for older artifacts.
    if maturity.get("available") and maturity.get("tier"):
        evidence_maturity = str(maturity.get("tier"))
    else:
        confidences = [
            str((sections.get("promotion_governance") or {}).get("confidence") or "LOW"),
            str((sections.get("regime_attribution") or {}).get("confidence") or "LOW"),
            str((sections.get("dynamic_strategy_allocation") or {}).get("confidence") or "LOW"),
        ]
        rank = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}
        min_rank = min(rank.get(c, 0) for c in confidences)
        evidence_maturity = ["LOW", "MEDIUM", "HIGH"][min_rank]

    # Audit-driven blocker split: when the governance_blocker_audit is
    # available we know which blockers are DATA_QUALITY (eliminable) vs
    # REAL/CONFIGURATION/OBSERVATION_WINDOW (actual strategy issues or
    # configuration mismatches).
    audit_classifications = blocker_audit.get("classifications") or []
    blockers_eliminated: list[str] = []
    blockers_remaining: list[str] = []
    data_quality_issues: list[str] = []
    actual_strategy_issues: list[str] = []
    for row in audit_classifications:
        name = str((row or {}).get("blocker") or "")
        cls = str((row or {}).get("classification") or "")
        if not name:
            continue
        if cls == "DATA_QUALITY":
            data_quality_issues.append(name)
            # Data-quality blockers are eliminable in principle; until
            # they actually clear they're "remaining" but tagged as
            # eliminable rather than strategy issues.
            blockers_remaining.append(name)
        elif cls in ("REAL", "CONFIGURATION", "OBSERVATION_WINDOW"):
            actual_strategy_issues.append(name)
            blockers_remaining.append(name)
        else:
            blockers_remaining.append(name)
    # A blocker is "eliminated" only when the audit explicitly judges it
    # cleared (root_cause includes "blocker_should_clear"). Pull the
    # name out of any "remaining" / "data quality" / "actual strategy"
    # buckets it was filed into above.
    for row in audit_classifications:
        if "blocker_should_clear" in str((row or {}).get("root_cause") or ""):
            name = str((row or {}).get("blocker") or "")
            if name:
                blockers_eliminated.append(name)
                if name in blockers_remaining:
                    blockers_remaining.remove(name)
                if name in data_quality_issues:
                    data_quality_issues.remove(name)
                if name in actual_strategy_issues:
                    actual_strategy_issues.remove(name)

    allocation_rec = str(tier3.get("allocation_recommendation") or "no_allocation_change_recommended")
    # An allocation change requires ALL THREE tiers clear AND the
    # underlying allocation artifact to be available. Tier 3 alone is
    # not sufficient — Tier 1 (timing / promotion windows / dif) and
    # Tier 2 (risk coverage / deep dif / sizing / universe) must also
    # produce non-default recommendations.
    if (
        allocation_rec != "no_allocation_change_recommended"
        and bool(allocation.get("available"))
        and all_tiers_clear
        and promotion_rec.startswith("caerus_")
    ):
        allocation_summary = allocation_rec
    else:
        allocation_summary = "no_allocation_change_recommended"

    # Calibration-aware blocker accounting (FR-040): split the
    # remaining blockers by what kind of finding they represent so the
    # final summary clearly distinguishes true risks from configuration
    # mismatches and from eliminated false positives.
    true_blockers: list[str] = []
    configuration_blockers: list[str] = []
    for row in audit_classifications:
        name = str((row or {}).get("blocker") or "")
        cls = str((row or {}).get("classification") or "")
        if not name:
            continue
        if "blocker_should_clear" in str((row or {}).get("root_cause") or ""):
            continue
        if cls == "REAL":
            true_blockers.append(name)
        elif cls == "CONFIGURATION":
            configuration_blockers.append(name)
    current_blockers = sorted(set(blockers_remaining))
    eliminated_blockers = sorted(set(blockers_eliminated))

    return {
        "current_recommendation": current_recommendation,
        "promotion_status": promotion_rec,
        "demotion_status": str(tier3.get("demotion_recommendation") or "NO_DEMOTION_RECOMMENDED"),
        "allocation_status": allocation_summary,
        "strategy_statuses": strategy_statuses,
        "lyra_status": lyra_status,
        "orion_status": orion_status,
        "polaris_status": polaris_status,
        "top_blockers": top_blockers or ["no_blockers"],
        "evidence_maturity": evidence_maturity,
        "blockers_eliminated": eliminated_blockers,
        "blockers_remaining": current_blockers,
        "data_quality_issues": sorted(set(data_quality_issues)),
        "actual_strategy_issues": sorted(set(actual_strategy_issues)),
        "current_blockers": current_blockers,
        "true_blockers": sorted(set(true_blockers)),
        "configuration_blockers": sorted(set(configuration_blockers)),
        "eliminated_blockers": eliminated_blockers,
        "governance_maturity_tier": str(maturity.get("tier") or "IMMATURE"),
        "governance_maturity_score": maturity.get("total_score"),
        "tier1_recommendation": tier1_rec,
        "tier2_recommendation": tier2_rec,
        "tier3_recommendation": tier3_rec,
        "reason_codes": sorted(set(
            list(tier1.get("reason_codes") or [])
            + list(tier2.get("reason_codes") or [])
            + list(tier3.get("reason_codes") or [])
            + list(blocker_audit.get("reason_codes") or [])
        )) or ["ok"],
    }


def _build_data_freshness_section(sources: dict[str, dict[str, Any]], position_section: dict[str, Any]) -> dict[str, Any]:
    rows = [dict(sources[key]) for key in sorted(sources)]
    price_reasons = list(position_section.get("freshness_reason_codes") or [])
    if not price_reasons:
        price_reasons = ["ok"] if position_section.get("is_price_source_fresh") else ["price_freshness_unknown"]
    rows.append(
        {
            "artifact_name": "price_source",
            "status": "FRESH" if position_section.get("is_price_source_fresh") else "STALE_OR_MISSING",
            "date": position_section.get("price_source_max_date"),
            "confidence": position_section.get("aggregate_confidence") or position_section.get("confidence") or "LOW",
            "reason_codes": price_reasons,
            "path": position_section.get("price_source"),
        }
    )
    rows = sorted(rows, key=lambda row: str(row.get("artifact_name") or ""))
    return {
        "health_table": rows,
        "confidence": _min_confidence([str(row.get("confidence") or "LOW") for row in rows]),
        "reason_codes": sorted({
            str(code)
            for row in rows
            for code in list(row.get("reason_codes") or [])
            if code != "ok"
        }) or ["ok"],
    }


def _recommended_actions(sections: dict[str, Any], sources: dict[str, Any]) -> list[str]:
    actions: list[str] = []
    position = sections["position_attribution"]
    decision = sections["decision_attribution"]
    signal = sections["signal_quality"]
    if not position.get("available"):
        actions.append("Run .venv/bin/python scripts/build_position_attribution.py --date YYYY-MM-DD.")
    if not decision.get("available"):
        actions.append("Run .venv/bin/python scripts/build_decision_attribution.py --date YYYY-MM-DD after Phase A exists.")
    if position.get("is_price_source_fresh") is False:
        actions.append("Refresh the canonical price cache with .venv/bin/python scripts/hydrate_price_cache_only.py --strict, then rebuild attribution.")
    if not sections["model_review"].get("available"):
        actions.append("Run scripts/weekly_model_review.py or preserve the latest research/model_review_YYYY-MM-DD.md artifact.")
    if not sections["execution_quality"].get("available"):
        actions.append("Build a canonical execution telemetry summary so review packets can report order lifecycle health.")
    if not sections["risk_concentration"].get("available"):
        actions.append("Regenerate/build canonical risk/concentration summary artifacts to populate concentration and sector risk.")
    if not sections["regime_context"].get("available"):
        actions.append("Regenerate regime attribution/context artifacts to populate regime behavior.")
    if not sections["execution_timing_study"].get("available"):
        actions.append("Run .venv/bin/python scripts/build_execution_timing_counterfactual.py --date YYYY-MM-DD to populate opening-window timing evidence.")
    if not sections["promotion_readiness_windows"].get("available"):
        actions.append("Run .venv/bin/python scripts/build_promotion_readiness_windows.py --date YYYY-MM-DD to populate 20/40/60-day promotion readiness.")
    if not sections["strategy_differentiation"].get("available"):
        actions.append("Run .venv/bin/python scripts/build_strategy_differentiation.py --date YYYY-MM-DD to populate strategy differentiation evidence.")
    if sections["strategy_differentiation"].get("available") and not sections["strategy_differentiation"].get("factor_exposure_available"):
        actions.append("Generate or refresh canonical factor exposure artifacts, then rebuild strategy differentiation evidence.")
    if sections["strategy_differentiation"].get("available") and not sections["strategy_differentiation"].get("position_contributions_available"):
        actions.append("Run .venv/bin/python scripts/build_position_attribution.py --date YYYY-MM-DD, then rebuild strategy differentiation evidence.")
    if not sections.get("risk_coverage", {}).get("available"):
        actions.append("Run .venv/bin/python scripts/build_risk_coverage.py --date YYYY-MM-DD to populate Tier 2 risk coverage.")
    if not sections.get("strategy_differentiation_deep", {}).get("available"):
        actions.append("Run .venv/bin/python scripts/build_strategy_differentiation.py --date YYYY-MM-DD to populate deep differentiation evidence.")
    if not sections.get("position_sizing_research", {}).get("available"):
        actions.append("Run .venv/bin/python scripts/build_position_sizing_research.py --date YYYY-MM-DD to populate research-only sizing alternatives.")
    if not sections.get("universe_governance", {}).get("available"):
        actions.append("Run .venv/bin/python scripts/build_universe_governance.py --date YYYY-MM-DD to populate universe governance checks.")
    if not sections.get("promotion_governance", {}).get("available"):
        actions.append("Run .venv/bin/python scripts/build_promotion_governance.py --date YYYY-MM-DD to populate Tier 3 promotion governance.")
    if not sections.get("regime_attribution", {}).get("available"):
        actions.append("Run .venv/bin/python scripts/build_regime_attribution.py --date YYYY-MM-DD to populate Tier 3 regime attribution.")
    if not sections.get("dynamic_strategy_allocation", {}).get("available"):
        actions.append("Run .venv/bin/python scripts/build_dynamic_strategy_allocation.py --date YYYY-MM-DD to populate Tier 3 research-only dynamic allocation evidence.")
    if not sections.get("governance_blocker_audit", {}).get("available"):
        actions.append("Run .venv/bin/python scripts/build_governance_blocker_audit.py --date YYYY-MM-DD to classify governance blockers (REAL/DATA_QUALITY/CONFIGURATION/OBSERVATION_WINDOW).")
    if not sections.get("security_master_reconciliation", {}).get("available"):
        actions.append("Run .venv/bin/python scripts/build_security_master_reconciliation.py --date YYYY-MM-DD to reconcile holdings/planned/attribution symbols vs the security master.")
    if not sections.get("execution_payload_audit", {}).get("available"):
        actions.append("Run .venv/bin/python scripts/build_execution_payload_audit.py --date YYYY-MM-DD to diagnose planned_execution_payload availability.")
    if not sections.get("differentiation_diagnostic", {}).get("available"):
        actions.append("Run .venv/bin/python scripts/build_differentiation_diagnostic.py --date YYYY-MM-DD to assess whether weak differentiation is real vs a data limitation.")
    if not sections.get("concentration_diagnostic", {}).get("available"):
        actions.append("Run .venv/bin/python scripts/build_concentration_diagnostic.py --date YYYY-MM-DD to classify concentration blockers as actual vs configuration vs artifact.")
    if not sections.get("governance_maturity", {}).get("available"):
        actions.append("Run .venv/bin/python scripts/build_governance_maturity.py --date YYYY-MM-DD to score governance maturity deterministically.")
    if not sections.get("operational_drag", {}).get("available"):
        actions.append("Run python3 -m research.operational_drag --date YYYY-MM-DD to build intended-vs-actual operational drag artifacts.")
    if not sections.get("governance_calibration", {}).get("available"):
        actions.append("Run .venv/bin/python scripts/build_governance_calibration.py --date YYYY-MM-DD to evaluate concentration against design-aware (FR-040) thresholds and produce the OLD vs NEW reclassification artifact.")
    if signal.get("early_evidence"):
        actions.append("Accumulate more decision attribution observations before treating signal hit rates as durable.")
    if (
        position.get("available")
        and decision.get("available")
        and position.get("is_price_source_fresh") is True
        and sources.get("execution", {}).get("status") != "MISSING"
        and sources.get("risk", {}).get("status") != "MISSING"
        and sources.get("regime", {}).get("status") != "MISSING"
    ):
        actions.append("Move next to signal IC and rank IC analysis across a longer decision history.")
    deduped: list[str] = []
    for action in actions:
        if action not in deduped:
            deduped.append(action)
    return deduped


def _overall(
    *,
    sections: dict[str, Any],
    sources: dict[str, Any],
    actions: list[str],
) -> dict[str, Any]:
    blocking_reasons: list[str] = []
    if not sections["position_attribution"].get("available"):
        blocking_reasons.append("missing_attribution")
    if not sections["decision_attribution"].get("available"):
        blocking_reasons.append("missing_decision_attribution")
    if sections["position_attribution"].get("is_price_source_fresh") is False:
        blocking_reasons.append("price_source_stale")
    missing_optional = [
        key
        for key in ("model_review", "execution", "risk", "regime")
        if sources.get(key, {}).get("status") == "MISSING"
    ]
    reason_codes = sorted(set(blocking_reasons + [f"missing_{key}" for key in missing_optional]))
    if not reason_codes:
        reason_codes = ["ok"]
    if blocking_reasons:
        readiness = "LOW"
    elif missing_optional or sections["signal_quality"].get("early_evidence"):
        readiness = "MEDIUM"
    else:
        readiness = "HIGH"
    confidence = "LOW" if blocking_reasons else ("MEDIUM" if readiness == "HIGH" else "LOW")
    biggest_improvement = "Decision attribution links selected positions to realized outcomes." if sections["decision_attribution"].get("available") else "Position attribution is available." if sections["position_attribution"].get("available") else "No core attribution packet is available yet."
    biggest_blocker = reason_codes[0] if reason_codes != ["ok"] else "No blocking artifact gaps detected."
    recommended_next_action = actions[0] if actions else "No action generated."
    return {
        "readiness": readiness,
        "confidence": confidence,
        "summary": f"Research review readiness is {readiness}; {biggest_blocker}",
        "biggest_improvement": biggest_improvement,
        "biggest_blocker": biggest_blocker,
        "recommended_next_action": recommended_next_action,
        "reason_codes": reason_codes,
    }


def build_research_review_packet(
    *,
    trade_date: str | None = None,
    repo_root: Path | str = Path("."),
    output_root: Path | str | None = None,
) -> dict[str, Any]:
    repo = Path(repo_root)
    registry = load_strategy_registry_for_repo(repo)
    active_strategy_entries = registry.active_shadow_security_selection_entries()
    selected_date, date_reason_codes = select_review_date(repo, trade_date)
    model_section, model_source = _discover_model_review(repo, selected_date)
    position_section, attribution_source = _build_position_attribution_section(repo, selected_date)
    decision_section, decision_source = _build_decision_attribution_section(repo, selected_date)
    signal_section = _build_signal_quality_section(decision_section)
    execution_section, execution_source = _build_execution_quality_section(repo, selected_date)
    risk_section, risk_source = _build_risk_section(repo, selected_date)
    regime_section, regime_source = _build_regime_section(repo, selected_date)
    timing_study_section, timing_study_source = _build_execution_timing_study_section(repo, selected_date)
    promotion_windows_section, promotion_windows_source = _build_promotion_windows_section(repo, selected_date)
    differentiation_section, differentiation_source = _build_strategy_differentiation_section(repo, selected_date)
    risk_coverage_section, risk_coverage_source = _build_risk_coverage_section(repo, selected_date)
    differentiation_deep_section, differentiation_deep_source = _build_strategy_differentiation_deep_section(repo, selected_date)
    position_sizing_section, position_sizing_source = _build_position_sizing_section(repo, selected_date)
    universe_governance_section, universe_governance_source = _build_universe_governance_section(repo, selected_date)
    promotion_governance_section, promotion_governance_source = _build_promotion_governance_section(repo, selected_date)
    regime_attribution_section, regime_attribution_source = _build_regime_attribution_section(repo, selected_date)
    dynamic_allocation_section, dynamic_allocation_source = _build_dynamic_allocation_section(repo, selected_date)
    calibration_section, calibration_source = _build_governance_calibration_section(repo, selected_date)
    reclassification_section, reclassification_source = _build_governance_reclassification_section(repo, selected_date)
    blocker_audit_section, blocker_audit_source = _build_governance_blocker_audit_section(repo, selected_date)
    sm_reconciliation_section, sm_reconciliation_source = _build_security_master_reconciliation_section(repo, selected_date)
    payload_audit_section, payload_audit_source = _build_execution_payload_audit_section(repo, selected_date)
    diff_diagnostic_section, diff_diagnostic_source = _build_differentiation_diagnostic_section(repo, selected_date)
    conc_diagnostic_section, conc_diagnostic_source = _build_concentration_diagnostic_section(repo, selected_date)
    maturity_section, maturity_source = _build_governance_maturity_section(repo, selected_date)
    operational_drag_section, operational_drag_source = _build_operational_drag_section(repo, selected_date)
    sources = {
        "model_review": model_source,
        "attribution": attribution_source,
        "decision_attribution": decision_source,
        "execution": execution_source,
        "risk": risk_source,
        "regime": regime_source,
        "execution_timing_study": timing_study_source,
        "promotion_readiness_windows": promotion_windows_source,
        "strategy_differentiation": differentiation_source,
        "risk_coverage": risk_coverage_source,
        "strategy_differentiation_deep": differentiation_deep_source,
        "position_sizing_research": position_sizing_source,
        "universe_governance": universe_governance_source,
        "promotion_governance": promotion_governance_source,
        "regime_attribution": regime_attribution_source,
        "dynamic_strategy_allocation": dynamic_allocation_source,
        "governance_blocker_audit": blocker_audit_source,
        "security_master_reconciliation": sm_reconciliation_source,
        "execution_payload_audit": payload_audit_source,
        "differentiation_diagnostic": diff_diagnostic_source,
        "concentration_diagnostic": conc_diagnostic_source,
        "governance_maturity": maturity_source,
        "operational_drag": operational_drag_source,
        "governance_calibration": calibration_source,
        "governance_reclassification": reclassification_source,
    }
    data_freshness = _build_data_freshness_section(sources, position_section)
    sections = {
        "model_review": model_section,
        "position_attribution": position_section,
        "decision_attribution": decision_section,
        "signal_quality": signal_section,
        "execution_quality": execution_section,
        "risk_concentration": risk_section,
        "regime_context": regime_section,
        "execution_timing_study": timing_study_section,
        "promotion_readiness_windows": promotion_windows_section,
        "strategy_differentiation": differentiation_section,
        "risk_coverage": risk_coverage_section,
        "strategy_differentiation_deep": differentiation_deep_section,
        "position_sizing_research": position_sizing_section,
        "universe_governance": universe_governance_section,
        "promotion_governance": promotion_governance_section,
        "regime_attribution": regime_attribution_section,
        "dynamic_strategy_allocation": dynamic_allocation_section,
        "governance_blocker_audit": blocker_audit_section,
        "security_master_reconciliation": sm_reconciliation_section,
        "execution_payload_audit": payload_audit_section,
        "differentiation_diagnostic": diff_diagnostic_section,
        "concentration_diagnostic": conc_diagnostic_section,
        "governance_maturity": maturity_section,
        "operational_drag": operational_drag_section,
        "governance_calibration": calibration_section,
        "governance_reclassification": reclassification_section,
        "data_freshness": data_freshness,
    }
    sections["tier1_research_controls"] = _build_tier1_research_controls_section(sections)
    sections["tier2_research_controls"] = _build_tier2_research_controls_section(sections)
    sections["tier3_research_controls"] = _build_tier3_research_controls_section(sections)
    sections["final_control_summary"] = _build_final_control_summary_section(sections, active_strategy_entries)
    actions = _recommended_actions(sections, sources)
    sections["recommended_next_actions"] = actions
    overall = _overall(sections=sections, sources=sources, actions=actions)
    reason_codes = sorted(set(list(overall["reason_codes"]) + date_reason_codes))
    if overall["reason_codes"] == ["ok"]:
        reason_codes = date_reason_codes if date_reason_codes else ["ok"]
    overall["reason_codes"] = reason_codes
    payload = {
        "schema_version": SCHEMA_VERSION,
        "date": selected_date,
        "generated_at": f"{selected_date}T00:00:00Z",
        "overall": overall,
        "sources": sources,
        "sections": sections,
    }
    cio_briefing = build_cio_briefing(payload, repo)
    payload["cio_briefing"] = cio_briefing
    sections["cio_briefing"] = cio_briefing
    out_root = Path(output_root) if output_root is not None else repo / "outputs" / "research_review"
    out_dir = out_root / selected_date
    markdown = render_markdown(payload)
    html_text = render_html(payload, markdown)
    summary = {
        "schema_version": SCHEMA_VERSION,
        "date": selected_date,
        "generated_at": payload["generated_at"],
        "overall": overall,
        "sections": {
            "position_attribution": position_section,
            "decision_attribution": decision_section,
            "signal_quality": signal_section,
            "execution_timing_study": timing_study_section,
            "promotion_readiness_windows": promotion_windows_section,
            "strategy_differentiation": differentiation_section,
            "risk_coverage": risk_coverage_section,
            "strategy_differentiation_deep": differentiation_deep_section,
            "position_sizing_research": position_sizing_section,
            "universe_governance": universe_governance_section,
            "promotion_governance": promotion_governance_section,
            "regime_attribution": regime_attribution_section,
            "dynamic_strategy_allocation": dynamic_allocation_section,
            "governance_blocker_audit": blocker_audit_section,
            "security_master_reconciliation": sm_reconciliation_section,
            "execution_payload_audit": payload_audit_section,
            "differentiation_diagnostic": diff_diagnostic_section,
            "concentration_diagnostic": conc_diagnostic_section,
            "governance_maturity": maturity_section,
            "operational_drag": operational_drag_section,
            "governance_calibration": calibration_section,
            "governance_reclassification": reclassification_section,
            "tier1_research_controls": sections["tier1_research_controls"],
            "tier2_research_controls": sections["tier2_research_controls"],
            "tier3_research_controls": sections["tier3_research_controls"],
            "final_control_summary": sections["final_control_summary"],
        },
        "cio_briefing": cio_briefing,
        "output_paths": {
            "research_review_json": str(out_dir / "research_review.json"),
            "research_review_md": str(out_dir / "research_review.md"),
            "research_review_html": str(out_dir / "research_review.html"),
            "cio_briefing": str(out_dir / "cio_briefing.json"),
        },
    }
    _write_json(out_dir / "research_review.json", payload)
    _write_text(out_dir / "research_review.md", markdown)
    _write_text(out_dir / "research_review.html", html_text)
    _write_json(out_dir / "research_review_sources.json", sources)
    _write_json(out_dir / "research_review_summary.json", summary)
    _write_json(out_dir / "cio_briefing.json", cio_briefing)
    payload["artifact_paths"] = summary["output_paths"] | {
        "cio_briefing": str(out_dir / "cio_briefing.json"),
        "research_review_sources": str(out_dir / "research_review_sources.json"),
        "research_review_summary": str(out_dir / "research_review_summary.json"),
    }
    return payload


def render_markdown(payload: dict[str, Any]) -> str:
    date = payload["date"]
    overall = payload["overall"]
    sections = payload["sections"]
    lines: list[str] = [
        "# Caerus Research Review Packet",
        "",
        f"**Date:** {date}",
        f"**Generated at:** {payload['generated_at']}",
        "",
    ]
    lines.extend(_render_cio_md(payload.get("cio_briefing") or sections.get("cio_briefing") or {}))
    lines += [
        "## Executive Summary",
        "",
        f"- **Overall research readiness:** {overall['readiness']}",
        f"- **Confidence:** {overall['confidence']}",
        f"- **Biggest improvement:** {overall['biggest_improvement']}",
        f"- **Biggest blocker:** {overall['biggest_blocker']}",
        f"- **Recommended next action:** {overall['recommended_next_action']}",
        f"- **Reason codes:** {_md(overall['reason_codes'])}",
        "",
    ]
    lines.extend(_render_model_review_md(sections["model_review"]))
    lines.extend(_render_position_md(sections["position_attribution"]))
    lines.extend(_render_decision_md(sections["decision_attribution"]))
    lines.extend(_render_signal_md(sections["signal_quality"]))
    lines.extend(_render_execution_md(sections["execution_quality"]))
    lines.extend(_render_risk_md(sections["risk_concentration"]))
    lines.extend(_render_regime_md(sections["regime_context"]))
    lines.extend(_render_tier1_md(sections["tier1_research_controls"]))
    lines.extend(_render_execution_timing_study_md(sections["execution_timing_study"]))
    lines.extend(_render_promotion_windows_md(sections["promotion_readiness_windows"]))
    lines.extend(_render_strategy_differentiation_md(sections["strategy_differentiation"]))
    lines.extend(_render_tier2_md(sections["tier2_research_controls"]))
    lines.extend(_render_risk_coverage_md(sections["risk_coverage"]))
    lines.extend(_render_strategy_differentiation_deep_md(sections["strategy_differentiation_deep"]))
    lines.extend(_render_position_sizing_md(sections["position_sizing_research"]))
    lines.extend(_render_universe_governance_md(sections["universe_governance"]))
    lines.extend(_render_promotion_governance_md(sections["promotion_governance"]))
    lines.extend(_render_regime_attribution_md(sections["regime_attribution"]))
    lines.extend(_render_dynamic_allocation_md(sections["dynamic_strategy_allocation"]))
    lines.extend(_render_tier3_md(sections["tier3_research_controls"]))
    lines.extend(_render_governance_blocker_audit_md(sections["governance_blocker_audit"]))
    lines.extend(_render_security_master_reconciliation_md(sections["security_master_reconciliation"]))
    lines.extend(_render_execution_payload_audit_md(sections["execution_payload_audit"]))
    lines.extend(_render_differentiation_diagnostic_md(sections["differentiation_diagnostic"]))
    lines.extend(_render_concentration_diagnostic_md(sections["concentration_diagnostic"]))
    lines.extend(_render_governance_maturity_md(sections["governance_maturity"]))
    lines.extend(_render_operational_drag_md(sections["operational_drag"]))
    lines.extend(_render_governance_calibration_md(sections["governance_calibration"]))
    lines.extend(_render_governance_reclassification_md(sections["governance_reclassification"]))
    lines.extend(_render_final_control_summary_md(sections["final_control_summary"]))
    lines.extend(_render_freshness_md(sections["data_freshness"]))
    lines.extend(_render_actions_md(sections["recommended_next_actions"]))
    lines.extend(_render_sources_md(payload["sources"]))
    return "\n".join(lines)


def _render_cio_md(section: dict[str, Any]) -> list[str]:
    lines = [
        "## CIO Briefing",
        "",
        "### CIO Takeaway",
        "",
        _md(section.get("cio_takeaway")),
        "",
        "### What Changed Since Prior Review",
        "",
        _md((section.get("what_changed_since_prior_review") or {}).get("narrative")),
        "",
        "### 30-Second Read",
        "",
    ]
    thirty = section.get("thirty_second_read") or {}
    for label, key in (
        ("Readiness", "readiness"),
        ("Confidence", "confidence"),
        ("Leading strategy", "leading_strategy"),
        ("Main contributor", "main_contributor"),
        ("Main detractor", "main_detractor"),
        ("Biggest blocker", "biggest_blocker"),
        ("Recommended action", "recommended_action"),
    ):
        lines.append(f"- **{label}:** {_md(thirty.get(key))}")
    lines += [
        "",
        "### Strategy Leaderboard",
        "",
        "| Rank | Strategy | Decisions | Hit Rate | Avg Return | Avg PnL Contribution | Confidence |",
        "|---:|---|---:|---:|---:|---:|---|",
    ]
    for row in section.get("strategy_leaderboard") or []:
        lines.append(
            f"| {_md(row.get('rank'))} | {_md(row.get('strategy'))} | {_md(row.get('decisions_analyzed'))} | {_md(row.get('hit_rate'))} | {_md(row.get('average_realized_return'))} | {_md(row.get('average_pnl_contribution'))} | {_md(row.get('confidence'))} |"
        )
    attr = section.get("attribution_interpretation") or {}
    signal = section.get("signal_evidence_assessment") or {}
    risk = section.get("risk_blocker_assessment") or {}
    rec = section.get("cio_recommendation") or {}
    lines += [
        "",
        "### Key Attribution Notes",
        "",
        _md(attr.get("narrative")),
        "",
        "### Signal Evidence",
        "",
        _md(signal.get("conclusion")),
        "",
        "### Risks / Blockers",
        "",
        _md(risk.get("narrative")),
        "",
        "### Recommended Action",
        "",
        f"- **Primary:** {_md(rec.get('primary'))}",
    ]
    for item in rec.get("secondary") or []:
        lines.append(f"- {item}")
    lines.append("")
    return lines


def _render_model_review_md(section: dict[str, Any]) -> list[str]:
    lines = ["## Model Review Scorecard", ""]
    if not section.get("available"):
        return lines + ["Missing model review artifact. Reason codes: missing_model_review", ""]
    lines += ["| Dimension | Score |", "|---|---:|"]
    for dim in SCORECARD_DIMENSIONS:
        lines.append(f"| {dim} | {_md(section.get('scores', {}).get(dim))} |")
    lines += ["", f"Average score: {_md(section.get('average_score'))}", f"Reason codes: {_md(section.get('reason_codes'))}", ""]
    return lines


def _render_position_md(section: dict[str, Any]) -> list[str]:
    lines = ["## Attribution Phase A: Position PnL", ""]
    if not section.get("available"):
        return lines + ["Missing attribution summary. Reason codes: missing_attribution", ""]
    lines += [
        f"Strategies covered: {_md(section.get('strategies_covered'))}",
        f"Positions analyzed: {_md(section.get('total_positions_analyzed'))}",
        f"Complete price data: {_md(section.get('positions_with_complete_price_data'))}",
        f"Missing price data: {_md(section.get('positions_missing_price_data'))}",
        f"Aggregate confidence: {_md(section.get('aggregate_confidence'))}",
        f"Price source: `{_md(section.get('price_source'))}`",
        f"Price source max date: {_md(section.get('price_source_max_date'))}",
        f"Is price source fresh: {_md(section.get('is_price_source_fresh'))}",
        f"Freshness lag days: {_md(section.get('freshness_lag_days'))}",
        "",
        "| Strategy | Top Contributor | Top Detractor |",
        "|---|---|---|",
    ]
    contributors = section.get("top_contributor_per_strategy") or {}
    detractors = section.get("top_detractor_per_strategy") or {}
    for strategy in sorted(set(list(contributors.keys()) + list(detractors.keys()))):
        lines.append(f"| {strategy} | {_md(_position_label(contributors.get(strategy)))} | {_md(_position_label(detractors.get(strategy)))} |")
    lines += ["", f"Reason codes: {_md(section.get('reason_codes'))}", ""]
    return lines


def _position_label(row: dict[str, Any] | None) -> str:
    if not row:
        return "n/a"
    realized_return = row.get("return_pct")
    if realized_return is None:
        realized_return = row.get("realized_return")
    pnl = row.get("pnl_contribution_pct")
    if pnl is None:
        pnl = row.get("pnl_contribution")
    return f"{row.get('symbol')} ret={_fmt(realized_return)} pnl={_fmt(pnl)}"


def _render_decision_md(section: dict[str, Any]) -> list[str]:
    lines = ["## Attribution Phase B: Decision Attribution", ""]
    if not section.get("available"):
        return lines + ["Missing decision attribution artifacts. Reason codes: missing_decision_attribution", ""]
    lines += [
        f"Decisions analyzed: {_md(section.get('decisions_analyzed'))}",
        f"Confidence: {_md(section.get('confidence'))}",
        "",
        "| Strategy | Decisions | Hit Rate | Avg Return | Avg PnL Contribution | Top Decision | Worst Decision |",
        "|---|---:|---:|---:|---:|---|---|",
    ]
    for row in section.get("strategies") or []:
        lines.append(
            "| "
            + " | ".join(
                [
                    _md(row.get("strategy")),
                    _md(row.get("decisions_analyzed")),
                    _md(row.get("hit_rate")),
                    _md(row.get("average_realized_return")),
                    _md(row.get("average_pnl_contribution")),
                    _md(_position_label(row.get("top_decision"))),
                    _md(_position_label(row.get("worst_decision"))),
                ]
            )
            + " |"
        )
    lines += ["", "| Signal | Observations | Avg Score | Avg Return | Hit Rate | Confidence |", "|---|---:|---:|---:|---:|---|"]
    for row in section.get("signals") or []:
        lines.append(
            f"| {_md(row.get('signal_name'))} | {_md(row.get('observations'))} | {_md(row.get('average_score'))} | {_md(row.get('average_realized_return'))} | {_md(row.get('hit_rate'))} | {_md(row.get('confidence'))} |"
        )
    lines += ["", f"Reason codes: {_md(section.get('reason_codes'))}", ""]
    return lines


def _render_signal_md(section: dict[str, Any]) -> list[str]:
    return [
        "## Signal Quality",
        "",
        f"Strongest observed signal: {_md((section.get('strongest_observed_signal') or {}).get('signal_name'))}",
        f"Weakest observed signal: {_md((section.get('weakest_observed_signal') or {}).get('signal_name'))}",
        f"Early evidence: {_md(section.get('early_evidence'))}",
        f"Observations by signal: {_md([f'{k}={v}' for k, v in (section.get('observations_by_signal') or {}).items()])}",
        f"Confidence: {_md(section.get('confidence'))}",
        f"Reason codes: {_md(section.get('reason_codes'))}",
        "",
    ]


def _render_execution_md(section: dict[str, Any]) -> list[str]:
    return [
        "## Execution Quality",
        "",
        f"Latest execution status: {_md(section.get('latest_execution_status'))}",
        f"Validation status: {_md(section.get('validation_status'))}",
        f"Failed/stale symbols: {_md(section.get('failed_or_stale_symbols'))}",
        f"Pending sells: {_md(section.get('pending_sells'))}",
        f"Pending buys: {_md(section.get('pending_buys'))}",
        f"Known blockers: {_md(section.get('known_blockers'))}",
        f"Confidence: {_md(section.get('confidence'))}",
        f"Reason codes: {_md(section.get('reason_codes'))}",
        "",
    ]


def _render_risk_md(section: dict[str, Any]) -> list[str]:
    lines = ["## Risk / Concentration", ""]
    if not section.get("available"):
        return lines + ["Missing risk summary. Reason codes: missing_risk_summary", ""]
    lines += ["| Strategy | Holdings | Max Weight | Top3 Weight | Max Sector Weight | Market Beta |", "|---|---:|---:|---:|---:|---:|"]
    for strategy, row in sorted((section.get("strategies") or {}).items()):
        lines.append(f"| {strategy} | {_md(row.get('holdings_count'))} | {_md(row.get('max_weight'))} | {_md(row.get('top3_weight'))} | {_md(row.get('max_sector_weight'))} | {_md(row.get('market_beta'))} |")
    lines += ["", f"Confidence: {_md(section.get('confidence'))}", f"Reason codes: {_md(section.get('reason_codes'))}", ""]
    return lines


def _render_regime_md(section: dict[str, Any]) -> list[str]:
    lines = ["## Regime Context", ""]
    if not section.get("available"):
        return lines + ["Missing regime summary. Reason codes: missing_regime_summary", ""]
    lines += [
        f"Detected regime: {_md(section.get('detected_regime'))}",
        f"Detected regime as of: {_md(section.get('detected_regime_as_of'))}",
        f"VIX: {_md(section.get('vix'))}",
        f"Position scale: {_md(section.get('position_scale'))}",
        "",
        "| Strategy | Best Risk Regime | Worst Risk Regime | Risk Regime Hit Rates |",
        "|---|---|---|---|",
    ]
    for strategy, row in sorted((section.get("strategies") or {}).items()):
        hit_rates = ", ".join(f"{name}={_fmt(value)}" for name, value in sorted((row.get("risk_regime_hit_rates") or {}).items()))
        lines.append(f"| {strategy} | {_md(row.get('best_risk_regime'))} | {_md(row.get('worst_risk_regime'))} | {_md(hit_rates)} |")
    lines += ["", f"Confidence: {_md(section.get('confidence'))}", f"Reason codes: {_md(section.get('reason_codes'))}", ""]
    return lines


def _render_tier1_md(section: dict[str, Any]) -> list[str]:
    return [
        "## Tier 1 Research Controls",
        "",
        f"Execution timing study: {_md(section.get('execution_timing_status'))}",
        f"Promotion readiness windows: {_md(section.get('promotion_readiness_status'))}",
        f"Strategy differentiation: {_md(section.get('strategy_differentiation_status'))}",
        f"Factor exposure inputs: {_md(section.get('factor_exposure_status'))}",
        f"Position contribution inputs: {_md(section.get('position_contribution_status'))}",
        f"Differentiation confidence: {_md(section.get('differentiation_confidence'))}",
        f"Blocker categories: {_md(section.get('blocker_categories'))}",
        f"Recommendation: {_md(section.get('recommendation'))}",
        f"Blockers: {_md(section.get('blockers'))}",
        f"Reason codes: {_md(section.get('reason_codes'))}",
        "",
    ]


def _render_execution_timing_study_md(section: dict[str, Any]) -> list[str]:
    best = section.get("best_offset_vs_baseline") or {}
    worst = section.get("worst_offset_vs_baseline") or {}
    return [
        "## Execution Timing Study",
        "",
        f"Available: {_md(section.get('available'))}",
        f"Baseline: {_md(section.get('baseline_time_et'))} ({_md(section.get('baseline_offset'))})",
        f"Coverage ratio: {_md(section.get('coverage_ratio'))}",
        f"Symbols evaluated: {_md(section.get('symbols_evaluated'))}",
        f"Missing bars: {_md(section.get('symbols_missing_bars'))}",
        f"Best offset vs baseline: {_md(best.get('execution_time_et'))} ({_md(best.get('total_estimated_bps_impact_vs_baseline'))} bps)",
        f"Worst offset vs baseline: {_md(worst.get('execution_time_et'))} ({_md(worst.get('total_estimated_bps_impact_vs_baseline'))} bps)",
        f"Confidence: {_md(section.get('confidence'))}",
        f"Reason codes: {_md(section.get('reason_codes'))}",
        "",
    ]


def _render_promotion_windows_md(section: dict[str, Any]) -> list[str]:
    lines = [
        "## Promotion Readiness Windows",
        "",
        f"Available: {_md(section.get('available'))}",
        f"Recommendation: {_md(section.get('promotion_recommendation'))}",
        f"Blockers: {_md(section.get('blockers'))}",
        f"Confidence: {_md(section.get('confidence'))}",
        f"Reason codes: {_md(section.get('reason_codes'))}",
        "",
        "| Strategy | 20d | 40d | 60d |",
        "|---|---|---|---|",
    ]
    for strategy, payload in sorted((section.get("strategies") or {}).items()):
        windows = payload.get("windows") or {}
        cells = []
        for window in ("20", "40", "60"):
            row = windows.get(window) or {}
            cells.append(f"{row.get('readiness_state', 'n/a')} ({row.get('observation_count', 'n/a')} obs)")
        lines.append(f"| {strategy} | {cells[0]} | {cells[1]} | {cells[2]} |")
    lines.append("")
    return lines


def _render_strategy_differentiation_md(section: dict[str, Any]) -> list[str]:
    lines = [
        "## Strategy Differentiation",
        "",
        f"Available: {_md(section.get('available'))}",
        f"Factor exposure inputs: {_md('available' if section.get('factor_exposure_available') else 'missing_or_unavailable')}",
        f"Position contribution inputs: {_md('available' if section.get('position_contributions_available') else 'missing_or_unavailable')}",
        f"Blockers: {_md(section.get('blockers'))}",
        f"Confidence: {_md(section.get('confidence'))}",
        f"Reason codes: {_md(section.get('reason_codes'))}",
        "",
        "| Pair | Holdings Overlap | Return Corr | Active Share | Score | Flag |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for row in section.get("pairs") or []:
        pair = f"{row.get('left_strategy')} vs {row.get('right_strategy')}"
        lines.append(
            f"| {pair} | {_md(row.get('holdings_overlap_percentage'))} | {_md(row.get('daily_return_correlation'))} | {_md(row.get('average_active_share_proxy'))} | {_md(row.get('behavioral_differentiation_score'))} | {_md(row.get('differentiation_readiness_flag'))} |"
        )
    lines.append("")
    return lines


def _render_tier2_md(section: dict[str, Any]) -> list[str]:
    return [
        "## Tier 2 Research Controls",
        "",
        f"Risk coverage: {_md(section.get('risk_coverage_status'))}",
        f"Deep differentiation verdict: {_md(section.get('deep_differentiation_verdict'))}",
        f"Position sizing research: {_md(section.get('position_sizing_status'))}",
        f"Universe governance: {_md(section.get('universe_governance_status'))}",
        f"Recommendation: {_md(section.get('recommendation'))}",
        f"Blocker categories: {_md(section.get('blocker_categories'))}",
        f"Blockers: {_md(section.get('blockers'))}",
        f"Reason codes: {_md(section.get('reason_codes'))}",
        "",
    ]


def _render_risk_coverage_md(section: dict[str, Any]) -> list[str]:
    lines = [
        "## Tier 2 Risk Coverage",
        "",
        f"Available: {_md(section.get('available'))}",
        f"Risk level: {_md(section.get('risk_level'))}",
        f"Holdings source date: {_md(section.get('holdings_source_date'))}",
        f"Gross exposure: {_md(section.get('gross_exposure'))}",
        f"Net exposure: {_md(section.get('net_exposure'))}",
        f"Top 3 / Top 5 / Top 10: {_md(section.get('top3_concentration'))} / {_md(section.get('top5_concentration'))} / {_md(section.get('top10_concentration'))}",
        f"Confidence: {_md(section.get('confidence'))}",
        f"Reason codes: {_md(section.get('reason_codes'))}",
        "",
        "| Strategy | Positions | Gross | Net | Top 3 | Top 5 | Top 10 | Max Name | Risk |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for strategy, row in sorted((section.get("strategies") or {}).items()):
        lines.append(f"| {strategy} | {_md(row.get('position_count'))} | {_md(row.get('gross_exposure'))} | {_md(row.get('net_exposure'))} | {_md(row.get('top3_concentration'))} | {_md(row.get('top5_concentration'))} | {_md(row.get('top10_concentration'))} | {_md(row.get('max_single_name_weight'))} | {_md(row.get('risk_level'))} |")
    lines.append("")
    return lines


def _render_strategy_differentiation_deep_md(section: dict[str, Any]) -> list[str]:
    lines = [
        "## Deep Strategy Differentiation",
        "",
        f"Available: {_md(section.get('available'))}",
        f"Aggregate verdict: {_md(section.get('aggregate_verdict'))}",
        f"Blockers: {_md(section.get('blockers'))}",
        f"Confidence: {_md(section.get('confidence'))}",
        f"Reason codes: {_md(section.get('reason_codes'))}",
        "",
        "| Pair | Verdict | Score | Shared Contributors | Shared Detractors |",
        "|---|---|---:|---|---|",
    ]
    for row in section.get("pairs") or []:
        pair = f"{row.get('left_strategy')} vs {row.get('right_strategy')}"
        lines.append(f"| {pair} | {_md(row.get('verdict'))} | {_md(row.get('behavioral_differentiation_score'))} | {_md(row.get('shared_top_contributors'))} | {_md(row.get('shared_top_detractors'))} |")
    lines.append("")
    return lines


def _render_position_sizing_md(section: dict[str, Any]) -> list[str]:
    lines = [
        "## Position Sizing Research",
        "",
        f"Available: {_md(section.get('available'))}",
        f"Holdings source date: {_md(section.get('holdings_source_date'))}",
        f"Returns source date: {_md(section.get('returns_source_date'))}",
        f"Confidence: {_md(section.get('confidence'))}",
        f"Reason codes: {_md(section.get('reason_codes'))}",
        "",
        "| Strategy | Best Research Alternative | Confidence | Reasons |",
        "|---|---|---|---|",
    ]
    for strategy, row in sorted((section.get("strategies") or {}).items()):
        lines.append(f"| {strategy} | {_md(row.get('best_research_alternative'))} | {_md(row.get('confidence'))} | {_md(row.get('reason_codes'))} |")
    lines.append("")
    return lines


def _render_universe_governance_md(section: dict[str, Any]) -> list[str]:
    lines = [
        "## Universe Governance",
        "",
        f"Available: {_md(section.get('available'))}",
        f"Security master as-of: {_md(section.get('security_master_asof_date'))}",
        f"Stale universe: {_md(section.get('stale_universe'))}",
        f"Blockers: {_md(section.get('blockers'))}",
        f"Confidence: {_md(section.get('confidence'))}",
        f"Reason codes: {_md(section.get('reason_codes'))}",
        "",
        f"Aliases resolved: {_md(section.get('alias_resolutions'))}",
        "",
    ]
    return lines


def _render_promotion_governance_md(section: dict[str, Any]) -> list[str]:
    lines = ["## Promotion Governance (Tier 3)", ""]
    if not section.get("available"):
        return lines + [f"Missing or unavailable. Reason codes: {_md(section.get('reason_codes'))}", ""]
    lines += [
        f"Current control: {_md(section.get('current_control_strategy'))}",
        f"Promotion recommendation: {_md(section.get('promotion_recommendation'))}",
        f"Demotion recommendation: {_md(section.get('demotion_recommendation'))}",
        f"Evidence strength: {_md(section.get('evidence_strength'))}",
        f"Confidence: {_md(section.get('confidence'))}",
        f"Blocker categories: {_md(section.get('blocker_categories'))}",
        "",
        "| Rank | Strategy | Decision | Evidence | Max Obs |",
        "|---:|---|---|---|---:|",
    ]
    for row in section.get("challenger_rankings") or []:
        lines.append(
            f"| {_md(row.get('rank'))} | {_md(row.get('strategy'))} | {_md(row.get('decision'))} | {_md(row.get('evidence_strength'))} | {_md(row.get('max_observation_count'))} |"
        )
    lines += ["", f"Reason codes: {_md(section.get('reason_codes'))}", ""]
    return lines


def _render_regime_attribution_md(section: dict[str, Any]) -> list[str]:
    lines = ["## Regime Attribution (Tier 3)", ""]
    if not section.get("available"):
        return lines + [f"Missing or unavailable. Reason codes: {_md(section.get('reason_codes'))}", ""]
    history = section.get("history_window") or {}
    lines += [
        f"History: {_md(history.get('first_date'))} → {_md(history.get('last_date'))}",
        f"Classified days: {_md(history.get('classified_days'))}",
        f"Confidence: {_md(section.get('confidence'))}",
        "",
        "| Regime | Days |",
        "|---|---:|",
    ]
    for regime, count in sorted((section.get("regime_distribution") or {}).items()):
        lines.append(f"| {regime} | {count} |")
    lines += ["", f"Reason codes: {_md(section.get('reason_codes'))}", ""]
    return lines


def _render_dynamic_allocation_md(section: dict[str, Any]) -> list[str]:
    lines = ["## Dynamic Strategy Allocation (Tier 3, Research Only)", ""]
    if not section.get("available"):
        return lines + [f"Missing or unavailable. Reason codes: {_md(section.get('reason_codes'))}", ""]
    lines += [
        f"Is research only: {_md(section.get('is_research_only'))}",
        f"Production weights modified: {_md(section.get('production_weights_modified'))}",
        f"Allocation recommendation: {_md(section.get('allocation_recommendation'))}",
        f"Promotion governance allows change: {_md(section.get('promotion_governance_allows_change'))}",
        f"Confidence: {_md(section.get('confidence'))}",
        "",
        "| Rank | Policy | Excess vs Polaris | Vol | MaxDD | Score |",
        "|---:|---|---:|---:|---:|---:|",
    ]
    for row in section.get("ranking") or []:
        lines.append(
            f"| {_md(row.get('rank'))} | {_md(row.get('policy'))} | {_md(row.get('excess_return_vs_polaris'))} | {_md(row.get('realized_volatility'))} | {_md(row.get('max_drawdown'))} | {_md(row.get('risk_adjusted_score'))} |"
        )
    lines += ["", f"Reason codes: {_md(section.get('reason_codes'))}", ""]
    return lines


def _render_tier3_md(section: dict[str, Any]) -> list[str]:
    lines = ["## Tier 3 Research Controls", ""]
    lines += [
        f"Promotion governance status: {_md(section.get('promotion_governance_status'))}",
        f"Regime attribution status: {_md(section.get('regime_attribution_status'))}",
        f"Dynamic allocation status: {_md(section.get('dynamic_strategy_allocation_status'))}",
        f"Promotion recommendation: {_md(section.get('promotion_recommendation'))}",
        f"Demotion recommendation: {_md(section.get('demotion_recommendation'))}",
        f"Allocation recommendation: {_md(section.get('allocation_recommendation'))}",
        f"Recommendation: {_md(section.get('recommendation'))}",
        f"Evidence strength: {_md(section.get('evidence_strength'))}",
        f"Blockers: {_md(section.get('blockers'))}",
        f"Reason codes: {_md(section.get('reason_codes'))}",
        "",
    ]
    return lines


def _render_final_control_summary_md(section: dict[str, Any]) -> list[str]:
    lines = [
        "## Final Control Summary",
        "",
        f"- **Current recommendation:** {_md(section.get('current_recommendation'))}",
        f"- **Promotion status:** {_md(section.get('promotion_status'))}",
        f"- **Demotion status:** {_md(section.get('demotion_status'))}",
        f"- **Allocation status:** {_md(section.get('allocation_status'))}",
        f"- **Polaris status:** {_md(section.get('polaris_status'))}",
        f"- **Orion status:** {_md(section.get('orion_status'))}",
        f"- **Lyra status:** {_md(section.get('lyra_status'))}",
        f"- **Top blockers:** {_md(section.get('top_blockers'))}",
        f"- **Current blockers:** {_md(section.get('current_blockers'))}",
        f"- **True blockers:** {_md(section.get('true_blockers'))}",
        f"- **Configuration blockers:** {_md(section.get('configuration_blockers'))}",
        f"- **Eliminated blockers:** {_md(section.get('eliminated_blockers'))}",
        f"- **Blockers eliminated:** {_md(section.get('blockers_eliminated'))}",
        f"- **Blockers remaining:** {_md(section.get('blockers_remaining'))}",
        f"- **Data quality issues:** {_md(section.get('data_quality_issues'))}",
        f"- **Actual strategy issues:** {_md(section.get('actual_strategy_issues'))}",
        f"- **Governance maturity tier:** {_md(section.get('governance_maturity_tier'))} (score {_md(section.get('governance_maturity_score'))})",
        f"- **Evidence maturity:** {_md(section.get('evidence_maturity'))}",
        f"- **Tier 1 recommendation:** {_md(section.get('tier1_recommendation'))}",
        f"- **Tier 2 recommendation:** {_md(section.get('tier2_recommendation'))}",
        f"- **Tier 3 recommendation:** {_md(section.get('tier3_recommendation'))}",
        f"- **Reason codes:** {_md(section.get('reason_codes'))}",
        "",
    ]
    strategy_statuses = section.get("strategy_statuses") if isinstance(section.get("strategy_statuses"), dict) else {}
    extra_statuses = [
        row for strategy_id, row in strategy_statuses.items()
        if strategy_id not in {"caerus_polaris", "caerus_orion", "caerus_lyra"}
    ]
    if extra_statuses:
        lines += [
            "Additional strategy statuses:",
            "",
            "| Strategy | Role | Decision | Status |",
            "|---|---|---|---|",
        ]
        for row in extra_statuses:
            lines.append(
                f"| {_md(row.get('display_name'))} | {_md(row.get('role'))} | {_md(row.get('decision'))} | {_md(row.get('status'))} |"
            )
        lines.append("")
    return lines


def _render_governance_blocker_audit_md(section: dict[str, Any]) -> list[str]:
    lines = ["## Governance Blocker Audit", ""]
    if not section.get("available"):
        return lines + [f"Missing or unavailable. Reason codes: {_md(section.get('reason_codes'))}", ""]
    lines += [
        f"Counts: {_md(section.get('classification_counts'))}",
        "",
        "| Blocker | Classification | Severity | Root Cause |",
        "|---|---|---|---|",
    ]
    for row in section.get("classifications") or []:
        lines.append(
            f"| {_md(row.get('blocker'))} | {_md(row.get('classification'))} | {_md(row.get('severity'))} | {_md(row.get('root_cause'))} |"
        )
    lines += ["", f"Reason codes: {_md(section.get('reason_codes'))}", ""]
    return lines


def _render_operational_drag_md(section: dict[str, Any]) -> list[str]:
    lines = ["## Operational Drag", ""]
    if not section.get("available"):
        return lines + [f"Missing or unavailable. Reason codes: {_md(section.get('reason_codes'))}", ""]
    lines += [
        f"- Requested date status: {_md(section.get('current_date_status'))}",
        f"- Decision grade: {_md(section.get('decision_grade'))} — {_md(section.get('decision_grade_explanation'))}",
        f"- Current cumulative operational drag: {_md(section.get('current_cumulative_operational_drag'))}",
        f"- Latest daily operational drag: {_md(section.get('latest_daily_operational_drag'))}",
        f"- Performance gap driver: {_md(section.get('performance_gap_driver'))}",
        f"- Data confidence: {_md(section.get('data_confidence'))}",
        f"- Current-date reason codes: {_md(section.get('current_date_reason_codes'))}",
        f"- Historical caveats: {_md(section.get('historical_reason_codes'))}",
        f"- Missing data warnings: {_md(section.get('missing_data_warnings'))}",
        "",
    ]
    contributors = section.get("main_drag_contributors") or []
    if contributors:
        lines += [
            "| Category | Date Range | Estimated Drag bps | Confidence | Explanation |",
            "|---|---|---:|---|---|",
        ]
        for row in contributors[:5]:
            if isinstance(row, dict):
                lines.append(
                    f"| {_md(row.get('category'))} | {_md(row.get('date_range'))} | {_md(row.get('estimated_drag_bps'))} | {_md(row.get('confidence'))} | {_md(row.get('explanation'))} |"
                )
        lines.append("")
    windows = section.get("stable_windows") or []
    if windows:
        lines += [
            "| Window | Available | Intended | Actual | SPY | Drag | Confidence |",
            "|---|---|---:|---:|---:|---:|---|",
        ]
        for row in windows:
            if isinstance(row, dict):
                lines.append(
                    f"| {_md(row.get('window'))} | {_md(row.get('available'))} | {_md(row.get('intended_cumulative_return'))} | {_md(row.get('actual_cumulative_return'))} | {_md(row.get('spy_cumulative_return'))} | {_md(row.get('operational_drag'))} | {_md(row.get('confidence'))} |"
                )
        lines.append("")
    lines += [f"Reason codes: {_md(section.get('reason_codes'))}", ""]
    return lines


def _render_security_master_reconciliation_md(section: dict[str, Any]) -> list[str]:
    lines = ["## Security Master Reconciliation", ""]
    if not section.get("available"):
        return lines + [f"Missing or unavailable. Reason codes: {_md(section.get('reason_codes'))}", ""]
    cov = section.get("coverage") or {}
    lines += [
        f"Security master as-of: {_md(section.get('security_master_asof_date'))}",
        f"Holdings symbols: {_md(cov.get('holdings_symbol_count'))}",
        f"Planned symbols: {_md(cov.get('planned_symbol_count'))}",
        f"Unknown symbols: {_md(cov.get('unknown_symbol_count'))}",
        f"Duplicates: {_md(cov.get('duplicate_count'))}",
        f"Inactive aliases: {_md(cov.get('inactive_alias_count'))}",
        f"Reason codes: {_md(section.get('reason_codes'))}",
        "",
    ]
    return lines


def _render_execution_payload_audit_md(section: dict[str, Any]) -> list[str]:
    lines = ["## Execution Payload Audit", ""]
    if not section.get("available"):
        return lines + [f"Missing or unavailable. Reason codes: {_md(section.get('reason_codes'))}", ""]
    lines += [
        f"Verdict: {_md(section.get('verdict'))}",
        f"Root cause: {_md(section.get('root_cause'))}",
        f"Remediation: {_md(section.get('remediation'))}",
        f"Target payload present: {_md(section.get('target_payload_present'))}",
        f"Most recent payload: {_md(section.get('most_recent_payload'))}",
        f"Reason codes: {_md(section.get('reason_codes'))}",
        "",
    ]
    return lines


def _render_differentiation_diagnostic_md(section: dict[str, Any]) -> list[str]:
    lines = ["## Differentiation Diagnostic", ""]
    if not section.get("available"):
        return lines + [f"Missing or unavailable. Reason codes: {_md(section.get('reason_codes'))}", ""]
    lines += [
        f"Aggregate verdict: {_md(section.get('aggregate_verdict'))}",
        f"Inputs complete: {_md(section.get('differentiation_inputs_complete'))}",
        f"Verdict counts: {_md(section.get('verdict_counts'))}",
        "",
        "| Pair | Verdict | Overlap | DailyCorr | Sector | Active | Regime | MaxObs |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in section.get("pairs") or []:
        lines.append(
            f"| {_md(row.get('left_strategy'))} vs {_md(row.get('right_strategy'))} | {_md(row.get('verdict'))} | {_md(row.get('holdings_overlap_percentage'))} | {_md(row.get('daily_return_correlation'))} | {_md(row.get('sector_overlap'))} | {_md(row.get('average_active_share_proxy'))} | {_md(row.get('regime_overlap'))} | {_md(row.get('max_observation_count'))} |"
        )
    lines += ["", f"Reason codes: {_md(section.get('reason_codes'))}", ""]
    return lines


def _render_concentration_diagnostic_md(section: dict[str, Any]) -> list[str]:
    lines = ["## Concentration Diagnostic", ""]
    if not section.get("available"):
        return lines + [f"Missing or unavailable. Reason codes: {_md(section.get('reason_codes'))}", ""]
    lines += [
        f"Counts: {_md(section.get('classification_counts'))}",
        "",
        "| Strategy | Classification | Positions | Max Name | Top3 | Top5 | Top10 |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for row in section.get("strategies") or []:
        lines.append(
            f"| {_md(row.get('strategy'))} | {_md(row.get('classification'))} | {_md(row.get('position_count'))} | {_md(row.get('max_single_name_weight'))} | {_md(row.get('top3_concentration'))} | {_md(row.get('top5_concentration'))} | {_md(row.get('top10_concentration'))} |"
        )
    lines += ["", f"Reason codes: {_md(section.get('reason_codes'))}", ""]
    return lines


def _render_governance_maturity_md(section: dict[str, Any]) -> list[str]:
    lines = ["## Governance Maturity", ""]
    if not section.get("available"):
        return lines + [f"Missing or unavailable. Reason codes: {_md(section.get('reason_codes'))}", ""]
    lines += [
        f"Total score: {_md(section.get('total_score'))}",
        f"Tier: {_md(section.get('tier'))}",
        f"Confidence: {_md(section.get('confidence'))}",
        "",
        "| Component | Score | Reason |",
        "|---|---:|---|",
    ]
    for row in section.get("components") or []:
        lines.append(f"| {_md(row.get('component'))} | {_md(row.get('score'))} | {_md(row.get('reason'))} |")
    lines += [
        "",
        f"Blockers (live): real={_md(section.get('blockers_real'))} configuration={_md(section.get('blockers_configuration'))} data_quality={_md(section.get('blockers_data_quality'))} observation_window={_md(section.get('blockers_observation_window'))}",
        f"Reason codes: {_md(section.get('reason_codes'))}",
        "",
    ]
    return lines


def _render_governance_calibration_md(section: dict[str, Any]) -> list[str]:
    lines = ["## Governance Calibration (FR-040)", ""]
    if not section.get("available"):
        return lines + [f"Missing or unavailable. Reason codes: {_md(section.get('reason_codes'))}", ""]
    lines += [
        f"Confidence: {_md(section.get('confidence'))}",
        f"Calibration status counts: {_md(section.get('calibration_status_counts'))}",
        "",
        "| Design class | Max Single Name | Top 3 | Top 5 |",
        "|---|---:|---:|---:|",
    ]
    for cls, row in (section.get("design_aware_thresholds") or {}).items():
        lines.append(
            f"| {cls} | {_md(row.get('max_single_name_allowed'))} | {_md(row.get('top3_allowed'))} | {_md(row.get('top5_allowed'))} |"
        )
    lines += [
        "",
        "| Strategy | Positions | Design | Expected EW | Actual Max | Top3 | Top5 | Calibrated Cap | Status |",
        "|---|---:|---|---:|---:|---:|---:|---:|---|",
    ]
    for row in section.get("strategies") or []:
        thresholds = row.get("calibrated_thresholds") or {}
        actual = row.get("actual_concentration_profile") or {}
        lines.append(
            f"| {_md(row.get('strategy'))} | {_md(row.get('position_count'))} | {_md(row.get('design_class'))} | "
            f"{_md(row.get('expected_equal_weight'))} | {_md(actual.get('max_single_name_weight'))} | "
            f"{_md(actual.get('top3_concentration'))} | {_md(actual.get('top5_concentration'))} | "
            f"{_md(thresholds.get('max_single_name_allowed'))} | {_md(row.get('calibration_status'))} |"
        )
    lines += ["", f"Reason codes: {_md(section.get('reason_codes'))}", ""]
    return lines


def _render_governance_reclassification_md(section: dict[str, Any]) -> list[str]:
    lines = ["## Governance Reclassification (OLD fixed vs NEW calibrated)", ""]
    if not section.get("available"):
        return lines + [f"Missing or unavailable. Reason codes: {_md(section.get('reason_codes'))}", ""]
    lines += [
        f"Change counts: {_md(section.get('change_counts'))}",
        "",
        "| Strategy | OLD Decision | NEW Decision | Changed | OLD Risk Reasons | NEW Risk Reasons |",
        "|---|---|---|---|---|---|",
    ]
    for row in section.get("comparisons") or []:
        lines.append(
            f"| {_md(row.get('strategy'))} | {_md(row.get('old_decision'))} | {_md(row.get('new_decision'))} | "
            f"{_md(row.get('decision_changed'))} | {_md(row.get('old_risk_reasons'))} | "
            f"{_md(row.get('new_risk_reasons'))} |"
        )
    lines += ["", f"Reason codes: {_md(section.get('reason_codes'))}", ""]
    return lines


def _render_freshness_md(section: dict[str, Any]) -> list[str]:
    lines = ["## Data Freshness & Completeness", "", "| Artifact | Status | Date | Confidence | Reason Codes |", "|---|---|---|---|---|"]
    for row in section.get("health_table") or []:
        lines.append(f"| {_md(row.get('artifact_name'))} | {_md(row.get('status'))} | {_md(row.get('date'))} | {_md(row.get('confidence'))} | {_md(row.get('reason_codes'))} |")
    lines += ["", f"Reason codes: {_md(section.get('reason_codes'))}", ""]
    return lines


def _render_actions_md(actions: list[str]) -> list[str]:
    lines = ["## Recommended Next Actions", ""]
    if not actions:
        lines.append("- No action generated.")
    else:
        for action in actions:
            lines.append(f"- {action}")
    lines.append("")
    return lines


def _render_sources_md(sources: dict[str, Any]) -> list[str]:
    lines = ["## Source Artifacts", "", "| Source | Status | Path |", "|---|---|---|"]
    for name, source in sorted(sources.items()):
        lines.append(f"| {name} | {_md(source.get('status'))} | `{_md(source.get('path'))}` |")
    lines.append("")
    return lines


def render_html(payload: dict[str, Any], markdown: str | None = None) -> str:
    del markdown
    title = "Caerus Research Review Packet"
    warnings = []
    for row in payload["sections"]["data_freshness"]["health_table"]:
        if row.get("status") not in {"PRESENT", "FRESH"} or row.get("reason_codes") != ["ok"]:
            warnings.append(row)
    warning_html = "".join(
        f"<div class='warn'><strong>{html.escape(str(row.get('artifact_name')))}</strong>: {html.escape(_fmt(row.get('reason_codes')))}</div>"
        for row in warnings
    )
    body = [
        f"<h1>{html.escape(title)}</h1>",
        f"<p><strong>Date:</strong> {html.escape(str(payload['date']))}<br><strong>Generated at:</strong> {html.escape(str(payload['generated_at']))}</p>",
        _html_summary(payload["overall"]),
        warning_html,
        _html_section_from_markdown(render_markdown(payload)),
    ]
    return """<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Caerus Research Review Packet</title>
  <style>
    body { font-family: Arial, sans-serif; margin: 28px; color: #1f2933; }
    h1, h2 { color: #102a43; }
    table { border-collapse: collapse; width: 100%; margin: 12px 0 22px; font-size: 13px; }
    th, td { border: 1px solid #d9e2ec; padding: 6px 8px; text-align: left; vertical-align: top; }
    th { background: #f0f4f8; }
    code { background: #f5f7fa; padding: 1px 3px; }
    .summary { border-left: 4px solid #486581; background: #f0f4f8; padding: 10px 14px; margin-bottom: 16px; }
    .warn { border-left: 4px solid #b7791f; background: #fffbea; padding: 8px 12px; margin: 8px 0; }
  </style>
</head>
<body>
""" + "\n".join(body) + "\n</body>\n</html>"


def _html_summary(overall: dict[str, Any]) -> str:
    return (
        "<div class='summary'>"
        f"<strong>Readiness:</strong> {html.escape(str(overall.get('readiness')))}<br>"
        f"<strong>Confidence:</strong> {html.escape(str(overall.get('confidence')))}<br>"
        f"<strong>Next action:</strong> {html.escape(str(overall.get('recommended_next_action')))}"
        "</div>"
    )


def _html_section_from_markdown(markdown: str) -> str:
    lines = markdown.splitlines()
    out: list[str] = []
    in_table = False
    for line in lines:
        if line.startswith("# "):
            out.append(f"<h1>{html.escape(line[2:])}</h1>")
        elif line.startswith("## "):
            if in_table:
                out.append("</tbody></table>")
                in_table = False
            out.append(f"<h2>{html.escape(line[3:])}</h2>")
        elif line.startswith("### "):
            if in_table:
                out.append("</tbody></table>")
                in_table = False
            out.append(f"<h3>{html.escape(line[4:])}</h3>")
        elif line.startswith("- "):
            out.append(f"<p>{html.escape(line[2:])}</p>")
        elif line.startswith("|") and "---" not in line:
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            if not in_table:
                out.append("<table><tbody>")
                in_table = True
                tag = "th"
            else:
                tag = "td"
            out.append("<tr>" + "".join(f"<{tag}>{html.escape(cell)}</{tag}>" for cell in cells) + "</tr>")
        elif line.startswith("|") and "---" in line:
            continue
        elif line.strip():
            if in_table:
                out.append("</tbody></table>")
                in_table = False
            out.append(f"<p>{html.escape(line)}</p>")
    if in_table:
        out.append("</tbody></table>")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build deterministic Caerus research review packet artifacts.")
    parser.add_argument("--date", default=None, help="Review date in YYYY-MM-DD format. Defaults to latest attribution/decision date.")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--output-root", default=None)
    parser.add_argument("--print-date", action="store_true", help="Print selected review date and exit.")
    args = parser.parse_args(argv)
    if args.print_date:
        selected, _reasons = select_review_date(Path(args.repo_root), args.date)
        print(selected)
        return 0
    result = build_research_review_packet(
        trade_date=args.date,
        repo_root=Path(args.repo_root),
        output_root=Path(args.output_root) if args.output_root else None,
    )
    print(json.dumps({"date": result["date"], "overall": result["overall"], "artifact_paths": result["artifact_paths"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
