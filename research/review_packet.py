from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import re
from pathlib import Path
from typing import Any

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


def _build_tier1_research_controls_section(sections: dict[str, Any]) -> dict[str, Any]:
    timing = sections["execution_timing_study"]
    promotion = sections["promotion_readiness_windows"]
    differentiation = sections["strategy_differentiation"]
    blockers: list[str] = []
    if not timing.get("available"):
        blockers.append("missing_timing_coverage")
    blockers.extend(str(code) for code in promotion.get("blockers") or [])
    blockers.extend(str(code) for code in differentiation.get("blockers") or [])
    if any(str(code).endswith("weak_differentiation") for code in blockers):
        blockers.append("weak_differentiation")
    if not blockers:
        blockers = ["no_tier1_blockers_detected"]
    promotion_recommendation = str(promotion.get("promotion_recommendation") or "NO_PROMOTION_RECOMMENDED")
    if promotion_recommendation.startswith("PROMOTION_REVIEW_READY") and differentiation.get("available") and "weak_differentiation" not in blockers:
        recommendation = promotion_recommendation
    else:
        recommendation = "No promotion recommended"
    return {
        "available": any(section.get("available") for section in (timing, promotion, differentiation)),
        "execution_timing_status": "available" if timing.get("available") else "missing_or_unavailable",
        "promotion_readiness_status": "available" if promotion.get("available") else "missing_or_unavailable",
        "strategy_differentiation_status": "available" if differentiation.get("available") else "missing_or_unavailable",
        "recommendation": recommendation,
        "blockers": sorted(set(blockers)),
        "reason_codes": sorted(set(
            list(timing.get("reason_codes") or [])
            + list(promotion.get("reason_codes") or [])
            + list(differentiation.get("reason_codes") or [])
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
        "data_freshness": data_freshness,
    }
    sections["tier1_research_controls"] = _build_tier1_research_controls_section(sections)
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
            "tier1_research_controls": sections["tier1_research_controls"],
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
