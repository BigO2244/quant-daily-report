from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any


LOW_SAMPLE_OBSERVATION_THRESHOLD = 30


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _is_date_text(value: str) -> bool:
    try:
        dt.date.fromisoformat(value)
    except Exception:
        return False
    return True


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


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _pct(value: Any) -> str:
    numeric = _safe_float(value)
    if numeric is None:
        return "n/a"
    return f"{numeric * 100:+.1f}%"


def _plain(value: Any) -> str:
    if value is None:
        return "n/a"
    return str(value)


def _human_reason(code: str) -> str:
    mapping = {
        "missing_attribution": "missing position attribution",
        "missing_decision_attribution": "missing decision attribution",
        "missing_execution": "missing execution summary",
        "missing_execution_summary": "missing execution summary",
        "missing_model_review": "missing model review",
        "missing_regime": "missing regime summary",
        "missing_regime_summary": "missing regime summary",
        "missing_risk": "missing risk/concentration summary",
        "missing_risk_summary": "missing risk/concentration summary",
        "price_source_stale": "stale price source",
        "signal_evidence_sample_size_low": "limited signal evidence sample size",
    }
    return mapping.get(code, code.replace("_", " "))


def _reason_codes(packet: dict[str, Any]) -> list[str]:
    sections = packet.get("sections") if isinstance(packet.get("sections"), dict) else {}
    data_freshness = sections.get("data_freshness") if isinstance(sections.get("data_freshness"), dict) else {}
    overall = packet.get("overall") if isinstance(packet.get("overall"), dict) else {}
    codes = list(overall.get("reason_codes") or []) + list(data_freshness.get("reason_codes") or [])
    return sorted(set(str(code) for code in codes if code and code != "ok" and not str(code).startswith("date_")))


def _find_prior_packet(repo_root: Path, trade_date: str) -> dict[str, Any] | None:
    root = repo_root / "outputs" / "research_review"
    if not root.exists():
        return None
    candidates: list[tuple[str, Path]] = []
    for child in root.iterdir():
        if not child.is_dir() or not _is_date_text(child.name) or child.name >= trade_date:
            continue
        summary = child / "research_review_summary.json"
        review = child / "research_review.json"
        if summary.exists() or review.exists():
            candidates.append((child.name, review if review.exists() else summary))
    if not candidates:
        return None
    _date, path = sorted(candidates, key=lambda item: item[0])[-1]
    payload = _read_json(path)
    if payload is None and path.name == "research_review_summary.json":
        return None
    return payload


def _sections(packet: dict[str, Any]) -> dict[str, Any]:
    return packet.get("sections") if isinstance(packet.get("sections"), dict) else {}


def _overall(packet: dict[str, Any]) -> dict[str, Any]:
    return packet.get("overall") if isinstance(packet.get("overall"), dict) else {}


def _get_path(payload: dict[str, Any], path: tuple[str, ...]) -> Any:
    current: Any = payload
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _dict_candidates(packet: dict[str, Any], paths: tuple[tuple[str, ...], ...]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen: set[int] = set()
    for path in paths:
        value = _get_path(packet, path)
        if isinstance(value, dict) and id(value) not in seen:
            candidates.append(value)
            seen.add(id(value))
    return candidates


def _first_value(row: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if row.get(key) is not None:
            return row.get(key)
    return None


def _position_symbol(row: dict[str, Any]) -> str | None:
    value = _first_value(row, ("symbol", "ticker", "asset", "name"))
    return str(value).upper() if value else None


def _strategy_name(row: dict[str, Any], fallback: str | None = None) -> str | None:
    value = _first_value(row, ("strategy", "strategy_id", "strategy_slug", "strategy_name"))
    if value is None:
        value = fallback
    return str(value) if value else None


def _return_value(row: dict[str, Any]) -> float | None:
    return _safe_float(
        _first_value(
            row,
            (
                "return_pct",
                "realized_return",
                "average_realized_return",
                "average_return",
                "avg_return",
            ),
        )
    )


def _pnl_value(row: dict[str, Any]) -> float | None:
    return _safe_float(
        _first_value(
            row,
            (
                "pnl_contribution_pct",
                "pnl_contribution",
                "average_pnl_contribution",
                "average_contribution",
                "avg_pnl_contribution",
                "contribution_pct",
            ),
        )
    )


def _normalize_position_row(row: dict[str, Any], strategy: str | None = None) -> dict[str, Any]:
    normalized = dict(row)
    strategy_name = _strategy_name(row, strategy)
    symbol = _position_symbol(row)
    if strategy_name is not None:
        normalized["strategy"] = strategy_name
    if symbol is not None:
        normalized["symbol"] = symbol
    return_value = _return_value(row)
    pnl_value = _pnl_value(row)
    if return_value is not None:
        normalized["return_pct"] = return_value
    if pnl_value is not None:
        normalized["pnl_contribution_pct"] = pnl_value
    return normalized


def _position_rows(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [_normalize_position_row(row) for row in value if isinstance(row, dict)]
    if isinstance(value, dict):
        nested = value.get("positions") or value.get("rows") or value.get("items")
        if isinstance(nested, list):
            return [_normalize_position_row(row) for row in nested if isinstance(row, dict)]
        if _position_symbol(value):
            return [_normalize_position_row(value)]
        rows: list[dict[str, Any]] = []
        for strategy, row in sorted(value.items()):
            if isinstance(row, dict):
                rows.append(_normalize_position_row(row, str(strategy)))
        return rows
    return []


def _strategy_extreme(rows: list[dict[str, Any]], *, reverse: bool) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        strategy = str(row.get("strategy") or "")
        if not strategy:
            continue
        grouped.setdefault(strategy, []).append(row)
    out: dict[str, dict[str, Any]] = {}
    for strategy, group in sorted(grouped.items()):
        out[strategy] = sorted(
            group,
            key=lambda row: (
                _pnl_value(row) is None,
                -float(_pnl_value(row) or 0.0) if reverse else float(_pnl_value(row) or 0.0),
                str(row.get("symbol") or ""),
            ),
        )[0]
    return out


def _position_count(section: dict[str, Any]) -> int:
    explicit = _first_value(
        section,
        (
            "total_positions_analyzed",
            "positions_analyzed",
            "position_count",
            "total_positions",
        ),
    )
    explicit_count = _safe_int(explicit) if explicit is not None else None
    if explicit_count and explicit_count > 0:
        return explicit_count
    positions = section.get("positions")
    if isinstance(positions, list):
        derived = len(positions)
        if derived > 0:
            return derived
    contributors = _position_rows(section.get("top_contributors") or section.get("top_contributor_per_strategy"))
    detractors = _position_rows(section.get("top_detractors") or section.get("top_detractor_per_strategy"))
    derived = len({(row.get("strategy"), row.get("symbol")) for row in contributors + detractors})
    if derived > 0:
        return derived
    return explicit_count or 0


def _normalize_position_section(raw: dict[str, Any]) -> dict[str, Any]:
    section = dict(raw)
    section["total_positions_analyzed"] = _position_count(section)

    contributors = section.get("top_contributor_per_strategy") or section.get("top_contributors_per_strategy")
    detractors = section.get("top_detractor_per_strategy") or section.get("top_detractors_per_strategy")
    if isinstance(contributors, dict):
        section["top_contributor_per_strategy"] = {
            str(strategy): _normalize_position_row(row, str(strategy))
            for strategy, row in sorted(contributors.items())
            if isinstance(row, dict)
        }
    else:
        contributor_rows = _position_rows(
            section.get("top_contributors")
            or section.get("top_contributor")
            or section.get("contributors")
        )
        section["top_contributor_per_strategy"] = _strategy_extreme(contributor_rows, reverse=True)

    if isinstance(detractors, dict):
        section["top_detractor_per_strategy"] = {
            str(strategy): _normalize_position_row(row, str(strategy))
            for strategy, row in sorted(detractors.items())
            if isinstance(row, dict)
        }
    else:
        detractor_rows = _position_rows(
            section.get("top_detractors")
            or section.get("top_detractor")
            or section.get("detractors")
        )
        section["top_detractor_per_strategy"] = _strategy_extreme(detractor_rows, reverse=False)
    return section


def _position_section(packet: dict[str, Any]) -> dict[str, Any]:
    candidates = _dict_candidates(
        packet,
        (
            ("sections", "position_attribution"),
            ("sections", "attribution"),
            ("position_attribution",),
            ("attribution",),
            ("attribution_summary",),
            ("summary", "position_attribution"),
            ("summary", "attribution"),
            ("research_review", "sections", "position_attribution"),
        ),
    )
    normalized = [_normalize_position_section(candidate) for candidate in candidates]
    if not normalized:
        return {}
    return sorted(
        normalized,
        key=lambda section: (
            -_position_count(section),
            not bool(section.get("top_contributor_per_strategy")),
            not bool(section.get("top_detractor_per_strategy")),
        ),
    )[0]


def _strategy_rows(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        nested = value.get("strategies") or value.get("strategy_summaries") or value.get("rows")
        if isinstance(nested, list):
            value = nested
        elif _strategy_name(value):
            value = [value]
        else:
            rows = []
            for strategy, row in sorted(value.items()):
                if isinstance(row, dict):
                    candidate = dict(row)
                    candidate.setdefault("strategy", str(strategy))
                    rows.append(candidate)
            value = rows
    if not isinstance(value, list):
        return []
    normalized: list[dict[str, Any]] = []
    for row in value:
        if not isinstance(row, dict):
            continue
        strategy = _strategy_name(row)
        if not strategy:
            continue
        decisions = _first_value(row, ("decisions_analyzed", "decisions", "decision_count", "observations"))
        avg_return = _first_value(row, ("average_realized_return", "average_return", "avg_return"))
        avg_pnl = _first_value(
            row,
            (
                "average_pnl_contribution",
                "avg_pnl_contribution",
                "average_contribution",
                "average_pnl_contribution_pct",
            ),
        )
        normalized.append(
            {
                **row,
                "strategy": strategy,
                "decisions_analyzed": _safe_int(decisions),
                "average_realized_return": _safe_float(avg_return),
                "average_pnl_contribution": _safe_float(avg_pnl),
                "hit_rate": _safe_float(_first_value(row, ("hit_rate", "win_rate"))),
                "top_decision": row.get("top_decision") or row.get("best_decision") or row.get("top_contributor"),
                "worst_decision": row.get("worst_decision") or row.get("bottom_decision") or row.get("top_detractor"),
                "confidence": row.get("confidence") or "LOW",
            }
        )
    return normalized


def _signal_rows(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        nested = value.get("signals") or value.get("signal_summaries") or value.get("rows")
        if isinstance(nested, list):
            value = nested
        elif value.get("signal_name") or value.get("name"):
            value = [value]
    if not isinstance(value, list):
        return []
    out: list[dict[str, Any]] = []
    for row in value:
        if not isinstance(row, dict):
            continue
        signal_name = row.get("signal_name") or row.get("name")
        if not signal_name:
            continue
        out.append({**row, "signal_name": str(signal_name)})
    return out


def _decision_count(section: dict[str, Any]) -> int:
    explicit = _first_value(
        section,
        (
            "decisions_analyzed",
            "total_decisions_analyzed",
            "decision_count",
            "total_decisions",
        ),
    )
    explicit_count = _safe_int(explicit) if explicit is not None else None
    if explicit_count and explicit_count > 0:
        return explicit_count
    strategies = _strategy_rows(
        section.get("strategies")
        or section.get("strategy_summaries")
        or section.get("strategy_decision_summary")
    )
    if strategies:
        derived = sum(_safe_int(row.get("decisions_analyzed")) for row in strategies)
        if derived > 0:
            return derived
    decisions = section.get("decisions")
    if isinstance(decisions, list):
        derived = len(decisions)
        if derived > 0:
            return derived
    return explicit_count or 0


def _normalize_decision_section(raw: dict[str, Any]) -> dict[str, Any]:
    section = dict(raw)
    strategies = _strategy_rows(
        section.get("strategies")
        or section.get("strategy_summaries")
        or section.get("strategy_decision_summary")
    )
    signals = _signal_rows(
        section.get("signals")
        or section.get("signal_summaries")
        or section.get("signal_outcome_summary")
    )
    section["strategies"] = strategies
    section["signals"] = signals
    section["decisions_analyzed"] = _decision_count(section)
    return section


def _decision_section(packet: dict[str, Any]) -> dict[str, Any]:
    candidates = _dict_candidates(
        packet,
        (
            ("sections", "decision_attribution"),
            ("decision_attribution",),
            ("decision_summary",),
            ("strategy_decision_summary",),
            ("summary", "decision_attribution"),
            ("summary", "strategy_decision_summary"),
            ("research_review", "sections", "decision_attribution"),
        ),
    )
    normalized = [_normalize_decision_section(candidate) for candidate in candidates]
    if not normalized:
        return {}
    return sorted(
        normalized,
        key=lambda section: (
            -_decision_count(section),
            not bool(section.get("strategies")),
            not bool(section.get("signals")),
        ),
    )[0]


def _signal_section(packet: dict[str, Any]) -> dict[str, Any]:
    candidates = _dict_candidates(
        packet,
        (
            ("sections", "signal_quality"),
            ("signal_quality",),
            ("signal_outcome_summary",),
            ("summary", "signal_quality"),
            ("summary", "signal_outcome_summary"),
            ("research_review", "sections", "signal_quality"),
        ),
    )
    normalized: list[dict[str, Any]] = []
    for candidate in candidates:
        section = dict(candidate)
        section["signals"] = _signal_rows(section.get("signals") or section.get("signal_summaries") or section)
        normalized.append(section)
    decision = _decision_section(packet)
    if decision.get("signals"):
        normalized.append(
            {
                "signals": decision.get("signals"),
                "confidence": decision.get("confidence"),
                "reason_codes": decision.get("reason_codes"),
            }
        )
    if not normalized:
        return {}
    return sorted(normalized, key=lambda section: -len(list(section.get("signals") or [])))[0]


def _position_label(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    row = _normalize_position_row(row)
    ret = row.get("return_pct")
    pnl = row.get("pnl_contribution_pct")
    return {
        "symbol": row.get("symbol"),
        "strategy": row.get("strategy"),
        "return_pct": _safe_float(ret),
        "pnl_contribution": _safe_float(pnl),
        "summary": f"{row.get('symbol')} ({_pct(ret)} return, {_pct(pnl)} contribution)",
    }


def _decision_label(row: dict[str, Any] | None) -> str:
    label = _position_label(row)
    return label["summary"] if label else "n/a"


def _strategy_leaderboard(packet: dict[str, Any]) -> list[dict[str, Any]]:
    decision = _decision_section(packet)
    strategies = list(decision.get("strategies") or [])
    ranked = sorted(
        [row for row in strategies if isinstance(row, dict)],
        key=lambda row: (
            -(_safe_float(row.get("average_pnl_contribution")) if _safe_float(row.get("average_pnl_contribution")) is not None else -999999.0),
            -(_safe_float(row.get("average_realized_return")) if _safe_float(row.get("average_realized_return")) is not None else -999999.0),
            -(_safe_float(row.get("hit_rate")) if _safe_float(row.get("hit_rate")) is not None else -999999.0),
            str(row.get("strategy") or ""),
        ),
    )
    out: list[dict[str, Any]] = []
    for idx, row in enumerate(ranked, start=1):
        avg_pnl = _safe_float(row.get("average_pnl_contribution"))
        avg_return = _safe_float(row.get("average_realized_return"))
        hit_rate = _safe_float(row.get("hit_rate"))
        strategy = str(row.get("strategy") or "")
        out.append(
            {
                "rank": idx,
                "strategy": strategy,
                "decisions_analyzed": _safe_int(row.get("decisions_analyzed")),
                "hit_rate": hit_rate,
                "average_realized_return": avg_return,
                "average_pnl_contribution": avg_pnl,
                "top_decision": row.get("top_decision"),
                "worst_decision": row.get("worst_decision"),
                "confidence": row.get("confidence") or "LOW",
                "interpretation": (
                    f"{strategy} leads on average PnL contribution at {_pct(avg_pnl)} "
                    f"with a {_pct(hit_rate)} hit rate across {_safe_int(row.get('decisions_analyzed'))} decisions."
                ),
            }
        )
    return out


def _leader(packet: dict[str, Any]) -> str | None:
    leaderboard = _strategy_leaderboard(packet)
    if not leaderboard:
        leaderboard = packet.get("cio_briefing", {}).get("strategy_leaderboard")
    return str(leaderboard[0].get("strategy")) if leaderboard else None


def _attribution_interpretation(packet: dict[str, Any]) -> dict[str, Any]:
    position = _position_section(packet)
    contributors = position.get("top_contributor_per_strategy") if isinstance(position.get("top_contributor_per_strategy"), dict) else {}
    detractors = position.get("top_detractor_per_strategy") if isinstance(position.get("top_detractor_per_strategy"), dict) else {}
    contrib_rows = [row for row in contributors.values() if isinstance(row, dict)]
    detract_rows = [row for row in detractors.values() if isinstance(row, dict)]
    strongest = sorted(
        contrib_rows,
        key=lambda row: (-(_safe_float(row.get("pnl_contribution_pct")) or 0.0), str(row.get("symbol") or "")),
    )[0] if contrib_rows else None
    weakest = sorted(
        detract_rows,
        key=lambda row: ((_safe_float(row.get("pnl_contribution_pct")) or 0.0), str(row.get("symbol") or "")),
    )[0] if detract_rows else None
    repeated_contributors = _repeated_symbols(contrib_rows)
    repeated_detractors = _repeated_symbols(detract_rows)
    confidence = str(position.get("aggregate_confidence") or position.get("confidence") or "LOW")
    strongest_label = _position_label(strongest)
    weakest_label = _position_label(weakest)
    notes: list[str] = []
    if strongest_label:
        notes.append(f"{strongest_label['symbol']} was the strongest contributor in the packet.")
    if weakest_label:
        notes.append(f"{weakest_label['symbol']} was the largest drag in the packet.")
    if repeated_contributors:
        notes.append(f"{', '.join(repeated_contributors)} appeared as repeated top contributors across strategies.")
    if repeated_detractors:
        notes.append(f"{', '.join(repeated_detractors)} appeared as repeated detractors across strategies.")
    if not notes:
        notes.append("Attribution evidence is not available yet.")
    return {
        "strongest_contributor_overall": strongest_label,
        "weakest_detractor_overall": weakest_label,
        "repeated_contributors": repeated_contributors,
        "repeated_detractors": repeated_detractors,
        "confidence": confidence,
        "narrative": " ".join(notes) + f" Attribution confidence is {confidence}.",
        "reason_codes": list(position.get("reason_codes") or ["ok"]),
    }


def _repeated_symbols(rows: list[dict[str, Any]]) -> list[str]:
    counts: dict[str, int] = {}
    for row in rows:
        symbol = str(row.get("symbol") or "")
        if symbol:
            counts[symbol] = counts.get(symbol, 0) + 1
    return [
        symbol
        for symbol, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
        if count > 1
    ]


def _signal_assessment(packet: dict[str, Any]) -> dict[str, Any]:
    signal = _signal_section(packet)
    signals = list(signal.get("signals") or [])
    if not signals:
        return {
            "strongest_observed_signal": None,
            "weakest_observed_signal": None,
            "observations_count": 0,
            "confidence": "LOW",
            "sample_size_warning": True,
            "conclusion": "Signal outcome evidence is not available yet.",
            "reason_codes": ["missing_signal_outcome_summary"],
        }
    complete = [row for row in signals if _safe_float(row.get("average_realized_return")) is not None]
    strongest = sorted(
        complete,
        key=lambda row: (
            -(_safe_float(row.get("average_realized_return")) or 0.0),
            -(_safe_float(row.get("hit_rate")) or 0.0),
            str(row.get("signal_name") or ""),
        ),
    )[0] if complete else None
    weakest = sorted(
        complete,
        key=lambda row: (
            (_safe_float(row.get("average_realized_return")) or 0.0),
            (_safe_float(row.get("hit_rate")) or 0.0),
            str(row.get("signal_name") or ""),
        ),
    )[0] if complete else None
    observation_counts = [_safe_int(row.get("observations")) for row in signals if isinstance(row, dict)]
    observations = min(observation_counts) if observation_counts else 0
    degenerate = not strongest or not weakest or strongest.get("signal_name") == weakest.get("signal_name")
    sample_low = observations < LOW_SAMPLE_OBSERVATION_THRESHOLD
    if degenerate:
        conclusion = "Signal evidence is not yet differentiated; sample size remains too small to identify a durable signal edge."
    elif sample_low:
        conclusion = (
            f"{strongest.get('signal_name')} is the strongest observed signal so far, "
            "but the sample is still too small to treat the edge as durable."
        )
    else:
        conclusion = (
            f"{strongest.get('signal_name')} currently shows the strongest realized outcome association, "
            f"while {weakest.get('signal_name')} is weakest."
        )
    reasons = list(signal.get("reason_codes") or [])
    if sample_low and "signal_evidence_sample_size_low" not in reasons:
        reasons.append("signal_evidence_sample_size_low")
    if degenerate and "signal_evidence_not_differentiated" not in reasons:
        reasons.append("signal_evidence_not_differentiated")
    return {
        "strongest_observed_signal": strongest,
        "weakest_observed_signal": weakest,
        "observations_count": observations,
        "confidence": signal.get("confidence") or "LOW",
        "sample_size_warning": sample_low,
        "conclusion": conclusion,
        "reason_codes": sorted(set(reasons)) if reasons else ["ok"],
    }


def _risk_blocker_assessment(packet: dict[str, Any]) -> dict[str, Any]:
    sections = _sections(packet)
    risk = sections.get("risk_concentration") or {}
    freshness = sections.get("data_freshness") or {}
    codes = _reason_codes(packet)
    health_rows = list(freshness.get("health_table") or [])
    missing_artifacts = sorted({
        str(row.get("artifact_name") or "")
        for row in health_rows
        if str(row.get("status") or "") == "MISSING"
    })
    stale_artifacts = sorted({
        str(row.get("artifact_name") or "")
        for row in health_rows
        if "STALE" in str(row.get("status") or "") or any("stale" in str(code).lower() for code in list(row.get("reason_codes") or []))
    })
    if not risk.get("available") and "risk" not in missing_artifacts:
        missing_artifacts.append("risk")
    blocker = _biggest_blocker(packet, missing_artifacts, stale_artifacts, codes)
    prevents_upgrade = bool(missing_artifacts or stale_artifacts or _overall(packet).get("confidence") == "LOW")
    remediation = _remediation_for_blocker(blocker)
    return {
        "biggest_blocker": blocker,
        "missing_artifacts": sorted(set(missing_artifacts)),
        "stale_artifacts": sorted(set(stale_artifacts)),
        "prevents_confidence_upgrade": prevents_upgrade,
        "recommended_remediation": remediation,
        "narrative": (
            f"The primary blocker is {_human_reason(blocker)}, which "
            f"{'prevents' if prevents_upgrade else 'does not prevent'} a confidence upgrade. {remediation}"
        ),
        "reason_codes": sorted(set(codes)) if codes else ["ok"],
    }


def _biggest_blocker(packet: dict[str, Any], missing: list[str], stale: list[str], codes: list[str]) -> str:
    priority = [
        "missing_risk_summary",
        "missing_risk",
        "missing_attribution",
        "missing_decision_attribution",
        "price_source_stale",
        "missing_execution_summary",
        "missing_execution",
        "missing_model_review",
        "missing_regime_summary",
        "missing_regime",
    ]
    expanded = set(codes)
    for item in missing:
        expanded.add(f"missing_{item}")
    for item in stale:
        expanded.add(f"{item}_stale")
    overall_blocker = str(_overall(packet).get("biggest_blocker") or "")
    if overall_blocker and overall_blocker != "No blocking artifact gaps detected.":
        expanded.add(overall_blocker)
    for code in priority:
        if code in expanded:
            return code
    return sorted(expanded)[0] if expanded else "none"


def _remediation_for_blocker(blocker: str) -> str:
    if blocker in {"missing_risk_summary", "missing_risk"}:
        return "Regenerate or build the canonical risk/concentration summary artifacts."
    if blocker == "missing_attribution":
        return "Run position attribution before reviewing model outcomes."
    if blocker == "missing_decision_attribution":
        return "Run decision attribution after Phase A exists."
    if blocker == "price_source_stale":
        return "Refresh the canonical price cache, then rebuild attribution and the packet."
    if blocker in {"missing_execution_summary", "missing_execution"}:
        return "Build a canonical execution telemetry summary for order lifecycle health."
    if blocker == "missing_model_review":
        return "Run or preserve the weekly model review scorecard."
    if blocker in {"missing_regime_summary", "missing_regime"}:
        return "Regenerate regime context artifacts."
    return "Continue accumulating review packets and address the highest-confidence missing artifact."


def _cio_recommendation(packet: dict[str, Any], risk_blocker: dict[str, Any], signal: dict[str, Any]) -> dict[str, Any]:
    blocker = str(risk_blocker.get("biggest_blocker") or "")
    actions = list(_sections(packet).get("recommended_next_actions") or [])
    if blocker in {"missing_risk_summary", "missing_risk"}:
        primary = "Regenerate or build canonical risk/concentration summary artifacts before adding new signals."
    elif blocker == "missing_attribution":
        primary = "Run position attribution so the packet can explain what drove returns."
    elif blocker == "missing_decision_attribution":
        primary = "Run decision attribution so the packet can connect selections to outcomes."
    elif blocker in {"missing_execution_summary", "missing_execution"}:
        primary = "Build canonical execution telemetry so confidence can include order lifecycle health."
    elif signal.get("sample_size_warning"):
        primary = "Continue collecting decision attribution observations; do not promote signal changes from this sample yet."
    elif _overall(packet).get("confidence") in {"MEDIUM", "HIGH"}:
        primary = "Move next to signal IC and rank IC analysis across a longer decision history."
    else:
        primary = actions[0] if actions else "Continue building review history before changing model logic."
    secondary: list[str] = []
    for action in actions:
        if action != primary and action not in secondary:
            secondary.append(action)
        if len(secondary) >= 3:
            break
    return {
        "primary": primary,
        "secondary": secondary,
        "reason_codes": [blocker] if blocker and blocker != "none" else ["ok"],
    }


def _what_changed(repo_root: Path, packet: dict[str, Any]) -> dict[str, Any]:
    trade_date = str(packet.get("date") or "")
    prior = _find_prior_packet(repo_root, trade_date)
    if prior is None:
        return {
            "prior_date": None,
            "readiness_change": None,
            "confidence_change": None,
            "positions_analyzed_change": None,
            "decisions_analyzed_change": None,
            "price_freshness_change": None,
            "new_blockers": [],
            "resolved_blockers": [],
            "strategy_leader_change": None,
            "narrative": "No prior review packet available for comparison.",
            "reason_codes": ["prior_review_missing"],
        }
    current_metrics = _comparison_metrics(packet)
    prior_metrics = _comparison_metrics(prior)
    current_blockers = set(current_metrics["blockers"])
    prior_blockers = set(prior_metrics["blockers"])
    leader_change = None
    if current_metrics["leader"] != prior_metrics["leader"]:
        leader_change = {
            "from": prior_metrics["leader"],
            "to": current_metrics["leader"],
        }
    new_blockers = sorted(current_blockers - prior_blockers)
    resolved_blockers = sorted(prior_blockers - current_blockers)
    narrative_parts = [
        f"Readiness moved from {prior_metrics['readiness']} to {current_metrics['readiness']}.",
        f"Confidence moved from {prior_metrics['confidence']} to {current_metrics['confidence']}.",
        f"Positions analyzed changed by {current_metrics['positions'] - prior_metrics['positions']}.",
        f"Decisions analyzed changed by {current_metrics['decisions'] - prior_metrics['decisions']}.",
    ]
    if leader_change:
        narrative_parts.append(
            f"Strategy leadership changed from {_display_value(leader_change['from'])} to {_display_value(leader_change['to'])}."
        )
    if new_blockers:
        narrative_parts.append(f"New blockers: {_human_reason_list(new_blockers)}.")
    if resolved_blockers:
        narrative_parts.append(f"Resolved blockers: {_human_reason_list(resolved_blockers)}.")
    return {
        "prior_date": prior.get("date"),
        "readiness_change": {"from": prior_metrics["readiness"], "to": current_metrics["readiness"]},
        "confidence_change": {"from": prior_metrics["confidence"], "to": current_metrics["confidence"]},
        "positions_analyzed_change": current_metrics["positions"] - prior_metrics["positions"],
        "decisions_analyzed_change": current_metrics["decisions"] - prior_metrics["decisions"],
        "price_freshness_change": {"from": prior_metrics["price_fresh"], "to": current_metrics["price_fresh"]},
        "new_blockers": new_blockers,
        "resolved_blockers": resolved_blockers,
        "strategy_leader_change": leader_change,
        "narrative": " ".join(narrative_parts),
        "reason_codes": ["ok"],
    }


def _comparison_metrics(packet: dict[str, Any]) -> dict[str, Any]:
    position = _position_section(packet)
    decision = _decision_section(packet)
    return {
        "readiness": _overall(packet).get("readiness"),
        "confidence": _overall(packet).get("confidence"),
        "positions": _position_count(position),
        "decisions": _decision_count(decision),
        "price_fresh": position.get("is_price_source_fresh"),
        "blockers": _reason_codes(packet),
        "leader": _leader(packet),
    }


def _display_value(value: Any) -> str:
    if value is None or value == "":
        return "no current leader"
    return str(value)


def _human_reason_list(codes: list[str]) -> str:
    labels = []
    for code in codes:
        label = _human_reason(code)
        if label not in labels:
            labels.append(label)
    return ", ".join(labels)


def build_cio_briefing(packet: dict[str, Any], repo_root: Path | str = Path(".")) -> dict[str, Any]:
    repo = Path(repo_root)
    leaderboard = _strategy_leaderboard(packet)
    attribution = _attribution_interpretation(packet)
    signal = _signal_assessment(packet)
    risk_blocker = _risk_blocker_assessment(packet)
    recommendation = _cio_recommendation(packet, risk_blocker, signal)
    changed = _what_changed(repo, packet)
    thirty = {
        "readiness": _overall(packet).get("readiness"),
        "confidence": _overall(packet).get("confidence"),
        "leading_strategy": leaderboard[0]["strategy"] if leaderboard else None,
        "main_contributor": (attribution.get("strongest_contributor_overall") or {}).get("symbol"),
        "main_detractor": (attribution.get("weakest_detractor_overall") or {}).get("symbol"),
        "biggest_blocker": _human_reason(str(risk_blocker.get("biggest_blocker") or "none")),
        "recommended_action": recommendation["primary"],
    }
    takeaway = _takeaway(packet, leaderboard, attribution, signal, risk_blocker, recommendation)
    reason_codes = sorted(set(
        list(changed.get("reason_codes") or [])
        + list(attribution.get("reason_codes") or [])
        + list(signal.get("reason_codes") or [])
        + list(risk_blocker.get("reason_codes") or [])
        + list(recommendation.get("reason_codes") or [])
    ))
    if not reason_codes:
        reason_codes = ["ok"]
    return {
        "schema_version": "caerus_cio_briefing_v1",
        "date": packet.get("date"),
        "confidence": "LOW" if any(code != "ok" for code in reason_codes) else "MEDIUM",
        "cio_takeaway": takeaway,
        "what_changed_since_prior_review": changed,
        "strategy_leaderboard": leaderboard,
        "attribution_interpretation": attribution,
        "signal_evidence_assessment": signal,
        "risk_blocker_assessment": risk_blocker,
        "cio_recommendation": recommendation,
        "thirty_second_read": thirty,
        "reason_codes": reason_codes,
    }


def _takeaway(
    packet: dict[str, Any],
    leaderboard: list[dict[str, Any]],
    attribution: dict[str, Any],
    signal: dict[str, Any],
    risk_blocker: dict[str, Any],
    recommendation: dict[str, Any],
) -> str:
    overall = _overall(packet)
    position = _position_section(packet)
    decision = _decision_section(packet)
    leader = leaderboard[0]["strategy"] if leaderboard else "No strategy"
    contributor = (attribution.get("strongest_contributor_overall") or {}).get("symbol") or "No contributor"
    detractor = (attribution.get("weakest_detractor_overall") or {}).get("symbol") or "no clear detractor"
    signal_conclusion = str(signal.get("conclusion") or "Signal evidence remains limited.")
    positions_count = _position_count(position)
    decisions_count = _decision_count(decision)
    if positions_count > 0 and decisions_count > 0:
        attribution_sentence = (
            f"Attribution and decision attribution are operational: {positions_count} positions "
            f"and {decisions_count} decisions were analyzed with price freshness "
            f"{'confirmed' if position.get('is_price_source_fresh') else 'not confirmed'}."
        )
    else:
        attribution_sentence = (
            f"Attribution artifacts exist, but only {positions_count} positions and {decisions_count} decisions "
            "were analyzable, so outcome confidence remains constrained."
        )
    return " ".join(
        [
            f"Research readiness is {overall.get('readiness')}, with {overall.get('confidence')} confidence.",
            attribution_sentence,
            f"{contributor} was the strongest performance driver, while {detractor} was the main drag.",
            f"{leader} currently leads the strategy stack. {signal_conclusion}",
            f"The main blocker is {_human_reason(str(risk_blocker.get('biggest_blocker') or 'none'))}; {recommendation.get('primary')}",
        ]
    )
