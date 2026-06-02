from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import pandas as pd


SCHEMA_VERSION = "caerus_strategy_differentiation_v1"
PAIRS = (
    ("caerus_lyra", "caerus_orion"),
    ("caerus_lyra", "caerus_polaris"),
    ("caerus_orion", "caerus_polaris"),
)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _read_json_with_reason(path: Path, prefix: str) -> tuple[dict[str, Any] | None, list[str]]:
    if not path.exists():
        return None, []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None, [f"{prefix}_parser_error"]
    if not isinstance(payload, dict):
        return None, [f"{prefix}_bad_schema"]
    return payload, []


def _round(value: Any, digits: int = 10) -> float | None:
    if value is None:
        return None
    try:
        f = float(value)
    except Exception:
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return round(f, digits)


def _date_from_path(path: Path) -> str | None:
    for part in reversed(path.parts):
        try:
            pd.Timestamp(part)
        except Exception:
            continue
        return part
    return None


def _payload_date(payload: dict[str, Any], path: Path) -> str | None:
    for key in ("date", "trade_date", "as_of_date", "data_through_date"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            try:
                return pd.Timestamp(value).date().isoformat()
            except Exception:
                continue
    return _date_from_path(path)


def _is_on_or_before(left: str | None, right: str) -> bool:
    if not left:
        return True
    try:
        return pd.Timestamp(left) <= pd.Timestamp(right)
    except Exception:
        return False


def _dated_artifact_candidates(base: Path, filename: str, trade_date: str) -> list[Path]:
    candidates: list[Path] = []
    exact = base / trade_date / filename
    if exact.exists():
        candidates.append(exact)
    if base.exists():
        dated = []
        for child in base.iterdir():
            if not child.is_dir() or child.name == trade_date:
                continue
            try:
                pd.Timestamp(child.name)
            except Exception:
                continue
            if child.name <= trade_date and (child / filename).exists():
                dated.append(child / filename)
        candidates.extend(sorted(dated, key=lambda path: path.parent.name, reverse=True))
        latest = base / "latest" / filename
        if latest.exists():
            candidates.append(latest)
    out: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        key = str(path)
        if key not in seen:
            seen.add(key)
            out.append(path)
    return out


def _latest_shadow_date(repo: Path, trade_date: str) -> str | None:
    root = repo / "outputs" / "shadow_candidates"
    if not root.exists():
        return None
    dates = []
    for child in root.iterdir():
        if not child.is_dir():
            continue
        try:
            pd.Timestamp(child.name)
        except Exception:
            continue
        if child.name > trade_date or not (child / "comparison.json").exists():
            continue
        payload = _read_json(child / "comparison.json") or {}
        strategies = payload.get("strategies") if isinstance(payload.get("strategies"), dict) else {}
        if any(isinstance(row, dict) and row.get("holdings") for row in strategies.values()):
            dates.append(child.name)
    return sorted(dates)[-1] if dates else None


def _load_holdings(repo: Path, trade_date: str) -> tuple[dict[str, dict[str, float]], list[str], list[str]]:
    source_date = _latest_shadow_date(repo, trade_date)
    if source_date is None:
        return {}, [], ["shadow_comparison_missing"]
    path = repo / "outputs" / "shadow_candidates" / source_date / "comparison.json"
    payload = _read_json(path) or {}
    strategies = payload.get("strategies") if isinstance(payload.get("strategies"), dict) else {}
    holdings: dict[str, dict[str, float]] = {}
    for strategy, row in sorted(strategies.items()):
        if not isinstance(row, dict):
            continue
        rows = row.get("holdings") if isinstance(row.get("holdings"), list) else []
        weights: dict[str, float] = {}
        for holding in rows:
            if not isinstance(holding, dict):
                continue
            symbol = str(holding.get("ticker") or holding.get("symbol") or "").upper()
            if not symbol:
                continue
            weights[symbol] = _round(holding.get("target_weight") or holding.get("weight")) or 0.0
        if weights:
            holdings[strategy] = weights
    return holdings, [str(path)], [] if holdings else ["holdings_missing"]


def _load_nav_returns(repo: Path, trade_date: str, lookback: int = 60) -> tuple[pd.DataFrame | None, list[str]]:
    path = repo / "outputs" / "shadow_candidates" / "performance" / "shadow_nav_series.csv"
    if not path.exists():
        return None, ["shadow_nav_series_missing"]
    frame = pd.read_csv(path)
    if "date" not in frame.columns:
        return None, ["shadow_nav_series_bad_schema"]
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame = frame.dropna(subset=["date"])
    frame = frame[frame["date"] <= pd.Timestamp(trade_date)].sort_values("date", kind="mergesort").tail(lookback + 1)
    returns = frame.set_index("date").pct_change().dropna()
    return returns, [] if not returns.empty else ["return_history_missing"]


def _load_factor_exposure(repo: Path, trade_date: str) -> tuple[dict[str, Any], list[str], list[str]]:
    reasons: list[str] = []
    candidates = _dated_artifact_candidates(repo / "outputs" / "attribution", "factor_exposure.json", trade_date)
    candidates.extend(_dated_artifact_candidates(repo / "outputs" / "research" / "factor_exposure", "factor_exposure.json", trade_date))
    candidates.extend(_dated_artifact_candidates(repo / "outputs" / "risk_summary", "risk_summary.json", trade_date))
    for path in candidates:
        payload, parse_reasons = _read_json_with_reason(path, "factor_exposure")
        reasons.extend(parse_reasons)
        if not payload:
            continue
        artifact_date = _payload_date(payload, path)
        if "latest" in path.parts and not artifact_date:
            reasons.append("factor_exposure_latest_artifact_date_missing")
            continue
        if not _is_on_or_before(artifact_date, trade_date):
            reasons.append("factor_exposure_future_artifact_ignored")
            continue
        strategies = payload.get("strategies")
        if not isinstance(strategies, dict):
            reasons.append("factor_exposure_bad_schema")
            continue
        usable = {str(strategy): value for strategy, value in sorted(strategies.items()) if isinstance(value, dict) and value}
        if not usable:
            reasons.append("empty_factor_exposure")
            continue
        selected_reasons = [reason for reason in sorted(set(reasons)) if reason not in {"empty_factor_exposure"}]
        if artifact_date and artifact_date != trade_date:
            selected_reasons.append("factor_exposure_date_differs_from_target")
        if "risk_summary" in path.parts:
            selected_reasons.append("factor_exposure_source_risk_summary")
        return usable, [str(path)], sorted(set(selected_reasons))
    return {}, [], sorted(set(reasons + ["factor_exposure_missing"]))


def _parse_position_attribution(payload: dict[str, Any]) -> tuple[dict[str, dict[str, float]], list[str]]:
    rows = payload.get("positions") or payload.get("position_attribution") or []
    if not isinstance(rows, list):
        return {}, ["position_attribution_bad_schema"]
    out: dict[str, dict[str, float]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        strategy = str(row.get("strategy") or row.get("strategy_id") or "")
        symbol = str(row.get("symbol") or row.get("ticker") or "").upper()
        contribution = row.get("pnl_contribution_pct")
        if contribution is None:
            contribution = row.get("pnl_contribution")
        if contribution is None:
            contribution = row.get("contribution")
        value = _round(contribution)
        if strategy and symbol and value is not None:
            out.setdefault(strategy, {})[symbol] = value
    return out, [] if out else ["position_contributions_empty"]


def _ordered_windows(windows: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    def _key(item: tuple[str, Any]) -> tuple[int, str]:
        name, _payload = item
        digits = "".join(ch for ch in str(name) if ch.isdigit())
        return (int(digits) if digits else 9999, str(name))

    return [
        (str(name), payload)
        for name, payload in sorted(windows.items(), key=_key)
        if isinstance(payload, dict)
    ]


def _parse_contribution_report(payload: dict[str, Any]) -> tuple[dict[str, dict[str, float]], list[str]]:
    strategies = payload.get("strategies")
    if not isinstance(strategies, dict):
        return {}, ["contribution_report_bad_schema"]
    out: dict[str, dict[str, float]] = {}
    for strategy, row in sorted(strategies.items()):
        if not isinstance(row, dict):
            continue
        windows = row.get("windows")
        if not isinstance(windows, dict):
            continue
        for _window_name, window_payload in _ordered_windows(windows):
            positions = window_payload.get("positions")
            if not isinstance(positions, list):
                continue
            values: dict[str, float] = {}
            for position in positions:
                if not isinstance(position, dict):
                    continue
                symbol = str(position.get("symbol") or position.get("ticker") or "").upper()
                contribution = position.get("pnl_contribution_pct")
                if contribution is None:
                    contribution = position.get("contribution")
                if contribution is None:
                    contribution = position.get("pnl_contribution")
                value = _round(contribution)
                if symbol and value is not None:
                    values[symbol] = value
            if values:
                out[str(strategy)] = values
                break
    return out, [] if out else ["position_contributions_empty"]


def _load_contributions(repo: Path, trade_date: str) -> tuple[dict[str, dict[str, float]], list[str], list[str]]:
    reasons: list[str] = []
    attribution_root = repo / "outputs" / "attribution"
    candidates = [
        (path, "position_attribution")
        for path in _dated_artifact_candidates(attribution_root, "position_attribution.json", trade_date)
    ]
    candidates.extend(
        (path, "contribution_report")
        for path in _dated_artifact_candidates(attribution_root, "contribution_report.json", trade_date)
    )
    for path, kind in candidates:
        payload, parse_reasons = _read_json_with_reason(path, "position_contribution")
        reasons.extend(parse_reasons)
        if not payload:
            continue
        artifact_date = _payload_date(payload, path)
        if "latest" in path.parts and not artifact_date:
            reasons.append("position_contribution_latest_artifact_date_missing")
            continue
        if not _is_on_or_before(artifact_date, trade_date):
            reasons.append("position_contribution_future_artifact_ignored")
            continue
        if kind == "position_attribution":
            out, parse_reasons = _parse_position_attribution(payload)
        else:
            out, parse_reasons = _parse_contribution_report(payload)
        reasons.extend(parse_reasons)
        if not out:
            continue
        selected_reasons = [reason for reason in sorted(set(reasons)) if reason not in {"position_contributions_empty"}]
        if artifact_date and artifact_date != trade_date:
            selected_reasons.append("position_contribution_date_differs_from_target")
        if kind == "contribution_report":
            selected_reasons.append("position_contribution_source_contribution_report")
        return out, [str(path)], sorted(set(selected_reasons))
    return {}, [], sorted(set(reasons + ["position_contributions_missing"]))


def _weighted_overlap(left: dict[str, float], right: dict[str, float]) -> float | None:
    if not left or not right:
        return None
    return _round(sum(min(left.get(symbol, 0.0), right.get(symbol, 0.0)) for symbol in sorted(set(left) | set(right))))


def _active_share(left: dict[str, float], right: dict[str, float]) -> float | None:
    if not left or not right:
        return None
    return _round(0.5 * sum(abs(left.get(symbol, 0.0) - right.get(symbol, 0.0)) for symbol in sorted(set(left) | set(right))))


def _top_overlap(left: dict[str, float], right: dict[str, float], n: int = 10) -> float | None:
    if not left or not right:
        return None
    left_top = {symbol for symbol, _ in sorted(left.items(), key=lambda item: (-item[1], item[0]))[:n]}
    right_top = {symbol for symbol, _ in sorted(right.items(), key=lambda item: (-item[1], item[0]))[:n]}
    denominator = max(len(left_top), len(right_top), 1)
    return _round(len(left_top & right_top) / denominator)


def _sector_weights(factor: dict[str, Any]) -> dict[str, float]:
    sector = factor.get("sector_exposure") if isinstance(factor, dict) else {}
    if isinstance(sector, dict):
        weights = sector.get("weights") if isinstance(sector.get("weights"), dict) else sector
        return {str(k): float(v) for k, v in weights.items() if _round(v) is not None and not str(k).endswith("_count")}
    return {}


def _sector_overlap(left: dict[str, Any], right: dict[str, Any]) -> float | None:
    lw = _sector_weights(left)
    rw = _sector_weights(right)
    if not lw or not rw:
        return None
    return _round(sum(min(lw.get(sec, 0.0), rw.get(sec, 0.0)) for sec in sorted(set(lw) | set(rw))))


def _factor_similarity(left: dict[str, Any], right: dict[str, Any]) -> float | None:
    if not left or not right:
        return None
    components: list[float] = []
    for key, scale in (("market_beta", 2.0), ("market_correlation", 1.0), ("realized_volatility_ann_current_book", 1.0), ("market_beta_current_book", 2.0), ("market_beta_proxy", 2.0)):
        lv = _round(left.get(key))
        rv = _round(right.get(key))
        if lv is not None and rv is not None:
            components.append(max(0.0, 1.0 - min(abs(lv - rv) / scale, 1.0)))
    sector = _sector_overlap(left, right)
    if sector is not None:
        components.append(sector)
    return _round(sum(components) / len(components)) if components else None


def _corr_from_vectors(left: dict[str, float], right: dict[str, float]) -> float | None:
    symbols = sorted(set(left) | set(right))
    if len(symbols) < 2:
        return None
    frame = pd.DataFrame({"left": [left.get(s, 0.0) for s in symbols], "right": [right.get(s, 0.0) for s in symbols]})
    return _round(frame["left"].corr(frame["right"]))


def _top_common(contribs: dict[str, float], other: dict[str, float], positive: bool) -> list[str]:
    filt = [
        (symbol, value)
        for symbol, value in contribs.items()
        if symbol in other and ((value > 0 and other[symbol] > 0) if positive else (value < 0 and other[symbol] < 0))
    ]
    return [symbol for symbol, _ in sorted(filt, key=lambda item: (-abs(item[1]), item[0]))[:3]]


def _classify(holdings_overlap: float | None, return_corr: float | None, factor_similarity: float | None, active_share: float | None, contribution_corr: float | None) -> tuple[float | None, str, list[str]]:
    reasons: list[str] = []
    if holdings_overlap is None:
        reasons.append("holdings_overlap_missing")
    if return_corr is None:
        reasons.append("return_correlation_missing")
    if factor_similarity is None:
        reasons.append("factor_similarity_missing")
    if contribution_corr is None:
        reasons.append("contribution_correlation_missing")
    if holdings_overlap is not None and return_corr is not None and holdings_overlap >= 0.8 and return_corr >= 0.9:
        return 0.15, "WEAK", sorted(set(reasons + ["high_overlap_high_correlation"]))
    components: list[float] = []
    if active_share is not None:
        components.append(active_share)
    if return_corr is not None:
        components.append(1.0 - max(min(return_corr, 1.0), -1.0))
    if contribution_corr is not None:
        components.append(1.0 - max(min(contribution_corr, 1.0), -1.0))
    if factor_similarity is not None:
        components.append(1.0 - factor_similarity)
    score = _round(sum(components) / len(components)) if components else None
    if score is None:
        return None, "UNKNOWN", sorted(set(reasons))
    if holdings_overlap is not None and factor_similarity is not None and holdings_overlap >= 0.5 and factor_similarity >= 0.85:
        return score, "WATCH", sorted(set(reasons + ["moderate_overlap_high_factor_similarity"]))
    if score >= 0.55:
        return score, "READY", sorted(set(reasons)) or ["ok"]
    if score >= 0.35:
        return score, "WATCH", sorted(set(reasons)) or ["ok"]
    return score, "WEAK", sorted(set(reasons + ["weak_behavioral_differentiation"]))


def _verdict_from_flag(flag: str) -> str:
    if flag == "READY":
        return "STRONG_DIFFERENTIATION"
    if flag == "WATCH":
        return "MODERATE_DIFFERENTIATION"
    return "WEAK_DIFFERENTIATION"


def _load_shadow_holdings_by_date(repo: Path, trade_date: str) -> tuple[dict[str, dict[str, dict[str, float]]], list[str], list[str]]:
    root = repo / "outputs" / "shadow_candidates"
    if not root.exists():
        return {}, [], ["shadow_comparison_missing"]
    by_date: dict[str, dict[str, dict[str, float]]] = {}
    sources: list[str] = []
    for child in sorted(root.iterdir(), key=lambda path: path.name):
        if not child.is_dir():
            continue
        try:
            pd.Timestamp(child.name)
        except Exception:
            continue
        if child.name > trade_date:
            continue
        path = child / "comparison.json"
        payload = _read_json(path)
        if payload is None or not isinstance(payload.get("strategies"), dict):
            continue
        date_rows: dict[str, dict[str, float]] = {}
        for strategy, row in sorted(payload["strategies"].items()):
            if not isinstance(row, dict):
                continue
            holdings = row.get("holdings") if isinstance(row.get("holdings"), list) else []
            weights: dict[str, float] = {}
            for holding in holdings:
                if not isinstance(holding, dict):
                    continue
                symbol = str(holding.get("ticker") or holding.get("symbol") or "").upper()
                weight = _round(holding.get("target_weight") or holding.get("weight"))
                if symbol and weight is not None:
                    weights[symbol] = weight
            if weights:
                date_rows[strategy] = weights
        if date_rows:
            by_date[child.name] = date_rows
            sources.append(str(path))
    return by_date, sources, [] if by_date else ["shadow_comparison_missing"]


def _corr_for_window(returns: pd.DataFrame | None, left: str, right: str, window: int) -> tuple[float | None, int]:
    if returns is None or left not in returns.columns or right not in returns.columns:
        return None, 0
    aligned = returns[[left, right]].dropna().tail(window)
    if aligned.shape[0] < 2:
        return None, int(aligned.shape[0])
    return _round(aligned[left].corr(aligned[right])), int(aligned.shape[0])


def _window_score(overlap: float | None, corr: float | None, active_share: float | None, factor_similarity: float | None, contribution_corr: float | None) -> float | None:
    components: list[float] = []
    if active_share is not None:
        components.append(active_share)
    if overlap is not None:
        components.append(1.0 - min(max(overlap, 0.0), 1.0))
    if corr is not None:
        components.append(1.0 - min(max(corr, -1.0), 1.0))
    if factor_similarity is not None:
        components.append(1.0 - min(max(factor_similarity, 0.0), 1.0))
    if contribution_corr is not None:
        components.append(1.0 - min(max(contribution_corr, -1.0), 1.0))
    return _round(sum(components) / len(components)) if components else None


def build_strategy_differentiation_deep(
    *,
    trade_date: str,
    repo_root: Path | str = Path("."),
    output_root: Path | str | None = None,
) -> dict[str, Any]:
    repo = Path(repo_root)
    out_dir = Path(output_root) if output_root is not None else repo / "outputs" / "research" / "strategy_differentiation" / trade_date
    holdings_by_date, holding_sources, holding_reasons = _load_shadow_holdings_by_date(repo, trade_date)
    returns, return_reasons = _load_nav_returns(repo, trade_date, lookback=60)
    factors, factor_sources, factor_reasons = _load_factor_exposure(repo, trade_date)
    contributions, contribution_sources, contribution_reasons = _load_contributions(repo, trade_date)
    dates = sorted(holdings_by_date)
    pair_rows: list[dict[str, Any]] = []
    for left, right in PAIRS:
        left_c = contributions.get(left, {})
        right_c = contributions.get(right, {})
        contribution_corr = _corr_from_vectors(left_c, right_c)
        factor_similarity = _factor_similarity(factors.get(left, {}), factors.get(right, {}))
        sector_overlap = _sector_overlap(factors.get(left, {}), factors.get(right, {}))
        window_rows: dict[str, Any] = {}
        scores: list[float] = []
        pair_reasons: list[str] = []
        for window in (20, 40, 60):
            window_dates = dates[-window:]
            overlaps: list[float] = []
            active_shares: list[float] = []
            for source_date in window_dates:
                left_h = holdings_by_date.get(source_date, {}).get(left, {})
                right_h = holdings_by_date.get(source_date, {}).get(right, {})
                overlap = _weighted_overlap(left_h, right_h)
                active = _active_share(left_h, right_h)
                if overlap is not None:
                    overlaps.append(overlap)
                if active is not None:
                    active_shares.append(active)
            avg_overlap = _round(sum(overlaps) / len(overlaps)) if overlaps else None
            avg_active = _round(sum(active_shares) / len(active_shares)) if active_shares else None
            return_corr, return_obs = _corr_for_window(returns, left, right, window)
            score = _window_score(avg_overlap, return_corr, avg_active, factor_similarity, contribution_corr)
            reasons: list[str] = []
            if len(window_dates) < min(window, 20):
                reasons.append(f"insufficient_observations_{window}d")
            if avg_overlap is None:
                reasons.append("holdings_overlap_missing")
            if return_corr is None:
                reasons.append("return_correlation_missing")
            if contribution_corr is None:
                reasons.append("contribution_correlation_missing")
            if factor_similarity is None:
                reasons.append("factor_similarity_missing")
            if avg_overlap is not None and return_corr is not None and avg_overlap >= 0.8 and return_corr >= 0.9:
                reasons.append("high_overlap_high_correlation")
            if score is not None:
                scores.append(score)
            pair_reasons.extend(reasons)
            window_rows[str(window)] = {
                "window_days": window,
                "observation_count": len(window_dates),
                "return_observation_count": return_obs,
                "holdings_overlap": avg_overlap,
                "return_correlation": return_corr,
                "contribution_correlation": contribution_corr,
                "active_share_proxy": avg_active,
                "sector_overlap": sector_overlap,
                "factor_similarity": factor_similarity,
                "behavioral_differentiation_score": score,
                "reason_codes": sorted(set(reasons)) or ["ok"],
            }
        overall_score = _round(sum(scores) / len(scores)) if scores else None
        max_obs = max((row.get("observation_count") or 0 for row in window_rows.values()), default=0)
        if any("high_overlap_high_correlation" in reason for reason in pair_reasons) or overall_score is None or overall_score < 0.35:
            verdict = "WEAK_DIFFERENTIATION"
        elif max_obs < 20 or any("insufficient_observations" in reason for reason in pair_reasons):
            verdict = "MODERATE_DIFFERENTIATION"
            pair_reasons.append("insufficient_observations_block_strong_verdict")
        elif overall_score >= 0.55:
            verdict = "STRONG_DIFFERENTIATION"
        else:
            verdict = "MODERATE_DIFFERENTIATION"
        pair_rows.append(
            {
                "left_strategy": left,
                "right_strategy": right,
                "windows": window_rows,
                "active_share_proxy": window_rows.get("60", {}).get("active_share_proxy") or window_rows.get("40", {}).get("active_share_proxy") or window_rows.get("20", {}).get("active_share_proxy"),
                "shared_top_contributors": _top_common(left_c, right_c, True),
                "shared_top_detractors": _top_common(left_c, right_c, False),
                "sector_overlap": sector_overlap,
                "factor_similarity": factor_similarity,
                "concentration_similarity": _round(1.0 - abs((window_rows.get("60", {}).get("active_share_proxy") or 0.0) - (window_rows.get("20", {}).get("active_share_proxy") or 0.0))),
                "behavioral_differentiation_score": overall_score,
                "verdict": verdict,
                "reason_codes": sorted(set(pair_reasons + holding_reasons + return_reasons + factor_reasons + contribution_reasons)) or ["ok"],
            }
        )
    verdict_rank = {"STRONG_DIFFERENTIATION": 2, "MODERATE_DIFFERENTIATION": 1, "WEAK_DIFFERENTIATION": 0}
    aggregate_verdict = min((str(row.get("verdict")) for row in pair_rows), default="WEAK_DIFFERENTIATION", key=lambda item: verdict_rank.get(item, 0))
    blockers = sorted(
        f"{row['left_strategy']}_vs_{row['right_strategy']}:{str(row.get('verdict')).lower()}"
        for row in pair_rows
        if row.get("verdict") != "STRONG_DIFFERENTIATION"
    )
    reason_codes = sorted(
        {
            str(code)
            for row in pair_rows
            for code in list(row.get("reason_codes") or [])
            if code != "ok"
        }
    ) or ["ok"]
    payload = {
        "schema_version": "caerus_strategy_differentiation_deep_v1",
        "date": trade_date,
        "available": bool(pair_rows) and bool(holdings_by_date),
        "confidence": "LOW" if any("missing" in code or "insufficient" in code for code in reason_codes) else "MEDIUM",
        "aggregate_verdict": aggregate_verdict,
        "pairs": pair_rows,
        "blockers": blockers,
        "reason_codes": reason_codes,
        "source_artifacts": sorted(set(holding_sources + factor_sources + contribution_sources + [str(repo / "outputs" / "shadow_candidates" / "performance" / "shadow_nav_series.csv")])),
    }
    _write_json(out_dir / "strategy_differentiation_deep.json", payload)
    _write_text(out_dir / "strategy_differentiation_deep.md", render_deep_markdown(payload))
    return payload


def _coverage_reason_present(reasons: list[str]) -> bool:
    return any(
        token in str(reason)
        for reason in reasons
        for token in ("missing", "bad_schema", "parser_error", "empty", "date_differs_from_target")
    )


def build_strategy_differentiation(
    *,
    trade_date: str,
    repo_root: Path | str = Path("."),
    output_root: Path | str | None = None,
) -> dict[str, Any]:
    repo = Path(repo_root)
    out_dir = Path(output_root) if output_root is not None else repo / "outputs" / "research" / "strategy_differentiation" / trade_date
    holdings, holding_sources, holding_reasons = _load_holdings(repo, trade_date)
    returns, return_reasons = _load_nav_returns(repo, trade_date)
    factors, factor_sources, factor_reasons = _load_factor_exposure(repo, trade_date)
    contributions, contribution_sources, contribution_reasons = _load_contributions(repo, trade_date)
    pair_rows: list[dict[str, Any]] = []
    for left, right in PAIRS:
        left_h = holdings.get(left, {})
        right_h = holdings.get(right, {})
        overlap = _weighted_overlap(left_h, right_h)
        top10 = _top_overlap(left_h, right_h)
        active = _active_share(left_h, right_h)
        ret_corr = None
        if returns is not None and left in returns.columns and right in returns.columns:
            aligned = returns[[left, right]].dropna()
            if aligned.shape[0] >= 2:
                ret_corr = _round(aligned[left].corr(aligned[right]))
        sector = _sector_overlap(factors.get(left, {}), factors.get(right, {}))
        factor_sim = _factor_similarity(factors.get(left, {}), factors.get(right, {}))
        left_c = contributions.get(left, {})
        right_c = contributions.get(right, {})
        contrib_corr = _corr_from_vectors(left_c, right_c)
        score, flag, reasons = _classify(overlap, ret_corr, factor_sim, active, contrib_corr)
        reasons = sorted(set(reasons + holding_reasons + return_reasons + factor_reasons + contribution_reasons))
        pair_rows.append(
            {
                "left_strategy": left,
                "right_strategy": right,
                "holdings_overlap_percentage": overlap,
                "top10_overlap": top10,
                "sector_overlap": sector,
                "factor_exposure_similarity": factor_sim,
                "daily_return_correlation": ret_corr,
                "contribution_correlation": contrib_corr,
                "average_active_share_proxy": active,
                "common_top_contributors": _top_common(left_c, right_c, True),
                "common_top_detractors": _top_common(left_c, right_c, False),
                "behavioral_differentiation_score": score,
                "differentiation_readiness_flag": flag,
                "reason_codes": reasons or ["ok"],
            }
        )
    blockers = sorted({
        f"{row['left_strategy']}_vs_{row['right_strategy']}:weak_differentiation"
        for row in pair_rows
        if row.get("differentiation_readiness_flag") == "WEAK"
    })
    input_reasons = sorted(set(factor_reasons + contribution_reasons))
    payload = {
        "schema_version": SCHEMA_VERSION,
        "date": trade_date,
        "available": bool(holdings) and returns is not None,
        "factor_exposure_available": bool(factors),
        "position_contributions_available": bool(contributions),
        "factor_exposure_source_artifacts": sorted(set(factor_sources)),
        "position_contribution_source_artifacts": sorted(set(contribution_sources)),
        "confidence": "LOW" if any(row["differentiation_readiness_flag"] == "UNKNOWN" for row in pair_rows) or _coverage_reason_present(input_reasons) else "MEDIUM",
        "pairs": pair_rows,
        "blockers": blockers,
        "reason_codes": sorted(set(holding_reasons + return_reasons + factor_reasons + contribution_reasons + blockers)) or ["ok"],
        "source_artifacts": sorted(set(holding_sources + factor_sources + contribution_sources + [str(repo / "outputs" / "shadow_candidates" / "performance" / "shadow_nav_series.csv")])),
    }
    _write_json(out_dir / "strategy_differentiation.json", payload)
    _write_text(out_dir / "strategy_differentiation.md", render_markdown(payload))
    build_strategy_differentiation_deep(
        trade_date=trade_date,
        repo_root=repo,
        output_root=out_dir,
    )
    return payload


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        f"# Strategy Differentiation - {payload.get('date')}",
        "",
        f"- Available: {payload.get('available')}",
        f"- Confidence: {payload.get('confidence')}",
        f"- Reason codes: {', '.join(payload.get('reason_codes') or [])}",
        "",
        "| Pair | Holdings Overlap | Return Corr | Contribution Corr | Active Share | Score | Flag | Reasons |",
        "|---|---:|---:|---:|---:|---:|---|---|",
    ]
    for row in payload.get("pairs") or []:
        pair = f"{row.get('left_strategy')} vs {row.get('right_strategy')}"
        lines.append(
            f"| {pair} | {row.get('holdings_overlap_percentage')} | {row.get('daily_return_correlation')} | {row.get('contribution_correlation')} | {row.get('average_active_share_proxy')} | {row.get('behavioral_differentiation_score')} | {row.get('differentiation_readiness_flag')} | {', '.join(row.get('reason_codes') or [])} |"
        )
    return "\n".join(lines)


def render_deep_markdown(payload: dict[str, Any]) -> str:
    lines = [
        f"# Deep Strategy Differentiation - {payload.get('date')}",
        "",
        f"- Available: {payload.get('available')}",
        f"- Confidence: {payload.get('confidence')}",
        f"- Aggregate verdict: {payload.get('aggregate_verdict')}",
        f"- Reason codes: {', '.join(payload.get('reason_codes') or [])}",
        "",
        "| Pair | Verdict | Score | 20d overlap | 20d corr | 40d overlap | 40d corr | 60d overlap | 60d corr | Reasons |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in payload.get("pairs") or []:
        pair = f"{row.get('left_strategy')} vs {row.get('right_strategy')}"
        windows = row.get("windows") or {}
        w20 = windows.get("20") or {}
        w40 = windows.get("40") or {}
        w60 = windows.get("60") or {}
        lines.append(
            f"| {pair} | {row.get('verdict')} | {row.get('behavioral_differentiation_score')} | {w20.get('holdings_overlap')} | {w20.get('return_correlation')} | {w40.get('holdings_overlap')} | {w40.get('return_correlation')} | {w60.get('holdings_overlap')} | {w60.get('return_correlation')} | {', '.join(row.get('reason_codes') or [])} |"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build read-only strategy differentiation artifacts.")
    parser.add_argument("--date", required=True)
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--output-root", default=None)
    args = parser.parse_args(argv)
    payload = build_strategy_differentiation(
        trade_date=args.date,
        repo_root=Path(args.repo_root),
        output_root=Path(args.output_root) if args.output_root else None,
    )
    print(json.dumps({"date": args.date, "available": payload["available"], "confidence": payload["confidence"], "reason_codes": payload["reason_codes"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
