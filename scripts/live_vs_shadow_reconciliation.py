from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from core.strategy_identity import strategy_identity_metadata


BENCHMARK_SYMBOL = "SPY"
SHADOW_POLARIS = "caerus_polaris"
SPY_SLUG = "spy_benchmark"

RECONCILED_RETURN_THRESHOLD = 0.005
MINOR_DRIFT_RETURN_THRESHOLD = 0.015
RECONCILED_OVERLAP_THRESHOLD = 0.80
MINOR_DRIFT_OVERLAP_THRESHOLD = 0.60
STRATEGY_MISMATCH_STATUS = "NOT_ALIGNED"


@dataclass(frozen=True)
class LoadedPositions:
    weights: dict[str, float]
    source_path: str | None
    missing: bool


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text())
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _fmt_pct(value: Any) -> str:
    number = _to_float(value)
    return "N/A" if number is None else f"{number:.2%}"


def _fmt_signed_pct(value: Any) -> str:
    number = _to_float(value)
    return "N/A" if number is None else f"{number:+.2%}"


def _fmt_decimal(value: Any) -> str:
    number = _to_float(value)
    return "N/A" if number is None else f"{number:.4f}"


def _date_dirs(shadow_dir: Path, trade_date: str) -> list[str]:
    dates: list[str] = []
    if not shadow_dir.exists():
        return dates
    for child in shadow_dir.iterdir():
        if not child.is_dir():
            continue
        try:
            normalized = pd.Timestamp(child.name).strftime("%Y-%m-%d")
        except Exception:
            continue
        if normalized == child.name and child.name <= trade_date:
            dates.append(child.name)
    return sorted(dates)


def _compound(returns: list[float]) -> float | None:
    if not returns:
        return None
    value = 1.0
    for item in returns:
        value *= 1.0 + float(item)
    return round(value - 1.0, 10)


def _load_shadow_daily_returns(shadow_dir: Path, trade_date: str) -> tuple[pd.DataFrame, list[str]]:
    rows: list[dict[str, Any]] = []
    reason_codes: list[str] = []
    for date in _date_dirs(shadow_dir, trade_date):
        payload = _read_json(shadow_dir / date / "shadow_performance.json")
        if not payload:
            continue
        if payload.get("data_status") != "OK":
            continue
        strategies = payload.get("strategies") or {}
        polaris = strategies.get(SHADOW_POLARIS) or {}
        spy = strategies.get(SPY_SLUG) or {}
        polaris_return = _to_float(polaris.get("daily_return"))
        spy_return = _to_float(spy.get("daily_return"))
        if polaris_return is None:
            continue
        if spy_return is None:
            reason_codes.append("BENCHMARK_MISSING")
        rows.append(
            {
                "date": date,
                "shadow_polaris_daily_return": polaris_return,
                "spy_daily_return": spy_return,
                "shadow_status": payload.get("status"),
                "shadow_data_status": payload.get("data_status"),
            }
        )
    if not rows:
        reason_codes.append("MISSING_SHADOW_DATA")
    return pd.DataFrame(rows), sorted(set(reason_codes))


def _load_live_returns(path: Path) -> tuple[pd.DataFrame, list[str]]:
    if not path.exists():
        return pd.DataFrame(), ["MISSING_LIVE_DATA"]
    try:
        frame = pd.read_csv(path)
    except Exception:
        return pd.DataFrame(), ["MISSING_LIVE_DATA"]
    if frame.empty or "date" not in frame.columns:
        return pd.DataFrame(), ["MISSING_LIVE_DATA"]
    if "return_1d" not in frame.columns:
        if "equity" not in frame.columns:
            return pd.DataFrame(), ["MISSING_LIVE_DATA"]
        frame["return_1d"] = pd.to_numeric(frame["equity"], errors="coerce").pct_change()
    frame = frame[["date", "return_1d"]].copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    frame["live_daily_return"] = pd.to_numeric(frame["return_1d"], errors="coerce")
    frame = frame.dropna(subset=["date", "live_daily_return"])
    if frame.empty:
        return pd.DataFrame(), ["MISSING_LIVE_DATA"]
    return frame[["date", "live_daily_return"]], []


def _run_root_from_latest() -> Path | None:
    payload = _read_json(Path("outputs/latest_run.json"))
    if not payload:
        return None
    run_root = payload.get("run_root")
    return Path(str(run_root)) if run_root else None


def _position_candidate_paths(trade_date: str, explicit_path: Path | None = None) -> list[Path]:
    candidates: list[Path] = []
    if explicit_path is not None:
        candidates.append(explicit_path)
    for run_root in sorted(Path("outputs/runs").glob(f"{trade_date}T*"), reverse=True):
        candidates.extend(
            [
                run_root / "broker" / "posttrade_positions.json",
                run_root / "broker" / "pretrade_positions.json",
            ]
        )
    candidates.append(Path(f"outputs/broker_snapshot/broker_snapshot_{trade_date}.json"))
    run_root = _run_root_from_latest()
    latest_payload = _read_json(Path("outputs/latest_run.json")) or {}
    if run_root and latest_payload.get("trade_date") == trade_date:
        candidates.extend(
            [
                run_root / "broker" / "posttrade_positions.json",
                run_root / "broker" / "pretrade_positions.json",
            ]
        )
    candidates.extend(
        [
            Path("outputs/broker/posttrade_positions.json"),
            Path("outputs/paper_state/canonical_positions.json"),
        ]
    )
    return candidates


def _extract_positions(payload: dict[str, Any]) -> list[dict[str, Any]]:
    positions = payload.get("positions")
    if isinstance(positions, list):
        return [item for item in positions if isinstance(item, dict)]
    if isinstance(payload.get("broker_positions"), list):
        return [item for item in payload["broker_positions"] if isinstance(item, dict)]
    if isinstance(payload, dict) and all(isinstance(v, (int, float, str, dict)) for v in payload.values()):
        rows = []
        for symbol, value in payload.items():
            if isinstance(value, dict):
                row = dict(value)
                row.setdefault("symbol", symbol)
                rows.append(row)
        return rows
    return []


def _load_live_positions(trade_date: str, explicit_path: Path | None = None) -> LoadedPositions:
    for path in _position_candidate_paths(trade_date, explicit_path):
        payload = _read_json(path)
        if not payload:
            continue
        positions = _extract_positions(payload)
        if not positions:
            continue
        market_values: dict[str, float] = {}
        for position in positions:
            symbol = str(position.get("symbol") or position.get("ticker") or "").upper().strip()
            if not symbol:
                continue
            value = _to_float(position.get("market_value") or position.get("value") or position.get("notional"))
            if value is None:
                qty = _to_float(position.get("qty") or position.get("quantity") or position.get("shares"))
                price = _to_float(position.get("current_price") or position.get("price") or position.get("close"))
                if qty is not None and price is not None:
                    value = qty * price
            if value is not None and value > 0:
                market_values[symbol] = market_values.get(symbol, 0.0) + float(value)
        total = sum(market_values.values())
        if total <= 0:
            continue
        weights = {symbol: round(value / total, 10) for symbol, value in sorted(market_values.items())}
        return LoadedPositions(weights=weights, source_path=str(path), missing=False)
    return LoadedPositions(weights={}, source_path=None, missing=True)


def _load_shadow_weights(shadow_dir: Path, trade_date: str) -> tuple[dict[str, float], str | None]:
    path = shadow_dir / trade_date / "caerus_polaris.json"
    payload = _read_json(path)
    if not payload:
        return {}, None
    weights = {
        str(symbol).upper(): float(weight)
        for symbol, weight in (payload.get("target_weights") or {}).items()
        if _to_float(weight) is not None and float(weight) > 0
    }
    return weights, str(path)


def _load_live_target_weights(precompute_dir: Path, trade_date: str) -> tuple[dict[str, float], str | None, dict[str, Any]]:
    path = precompute_dir / trade_date / "signals.json"
    payload = _read_json(path)
    if not payload:
        return {}, None, {}
    rows = payload.get("signals") or payload.get("weights") or []
    weights: dict[str, float] = {}
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                continue
            symbol = str(row.get("ticker") or row.get("symbol") or "").upper().strip()
            weight = _to_float(row.get("target_weight") or row.get("weight"))
            if symbol and symbol != "CASH" and weight is not None and float(weight) > 0:
                weights[symbol] = weights.get(symbol, 0.0) + float(weight)
    return {symbol: round(weight, 10) for symbol, weight in sorted(weights.items())}, str(path), dict(payload.get("strategy_identity") or {})


def _holdings_reconciliation(live_weights: dict[str, float], shadow_weights: dict[str, float]) -> dict[str, Any]:
    live_symbols = set(live_weights)
    shadow_symbols = set(shadow_weights)
    shared = sorted(live_symbols & shadow_symbols)
    live_only = sorted(live_symbols - shadow_symbols)
    shadow_only = sorted(shadow_symbols - live_symbols)
    all_symbols = sorted(live_symbols | shadow_symbols)
    rows = []
    for symbol in all_symbols:
        live = float(live_weights.get(symbol, 0.0))
        shadow = float(shadow_weights.get(symbol, 0.0))
        category = "SHARED" if symbol in shared else "LIVE_ONLY" if symbol in live_only else "SHADOW_ONLY"
        rows.append(
            {
                "symbol": symbol,
                "live_weight": round(live, 10),
                "shadow_weight": round(shadow, 10),
                "difference": round(live - shadow, 10),
                "category": category,
            }
        )
    overlap_weight = round(sum(min(float(live_weights[s]), float(shadow_weights[s])) for s in shared), 10)
    largest = sorted(rows, key=lambda item: abs(float(item["difference"])), reverse=True)[:10]
    return {
        "live_positions_count": len(live_symbols),
        "shadow_positions_count": len(shadow_symbols),
        "overlap_count": len(shared),
        "overlap_weight": overlap_weight,
        "live_only_symbols": live_only,
        "shadow_only_symbols": shadow_only,
        "shared_symbols": shared,
        "largest_weight_differences": largest,
        "rows": rows,
    }


def _classify(*, reason_codes: list[str], live_minus_shadow: float | None, overlap_weight: float | None, valid_days: int) -> str:
    blocking = {"MISSING_LIVE_DATA", "MISSING_SHADOW_DATA", "MISSING_BROKER_POSITIONS", "INSUFFICIENT_HISTORY"}
    if blocking & set(reason_codes) or valid_days < 2:
        return "NOT_COMPARABLE"
    if "DIFFERENT_STRATEGY_PATH" in set(reason_codes):
        return STRATEGY_MISMATCH_STATUS
    if live_minus_shadow is None or overlap_weight is None:
        return "NOT_COMPARABLE"
    abs_delta = abs(live_minus_shadow)
    if abs_delta <= RECONCILED_RETURN_THRESHOLD and overlap_weight >= RECONCILED_OVERLAP_THRESHOLD:
        return "RECONCILED"
    if abs_delta <= MINOR_DRIFT_RETURN_THRESHOLD and overlap_weight >= MINOR_DRIFT_OVERLAP_THRESHOLD:
        return "MINOR_DRIFT"
    return "MAJOR_DRIFT"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def build_reconciliation(
    *,
    trade_date: str,
    shadow_dir: Path = Path("outputs/shadow_candidates"),
    precompute_dir: Path = Path("outputs/precompute"),
    live_nav_path: Path = Path("outputs/perf/live_overlay_nav_series.csv"),
    broker_positions_path: Path | None = None,
) -> dict[str, Any]:
    shadow_returns, shadow_reasons = _load_shadow_daily_returns(shadow_dir, trade_date)
    live_returns, live_reasons = _load_live_returns(live_nav_path)
    reason_codes = list(shadow_reasons + live_reasons)

    if not shadow_returns.empty and not live_returns.empty:
        aligned = shadow_returns.merge(live_returns, on="date", how="inner")
        aligned = aligned.dropna(subset=["live_daily_return", "shadow_polaris_daily_return"])
    else:
        aligned = pd.DataFrame()
    if not aligned.empty and "spy_daily_return" in aligned.columns:
        aligned = aligned.dropna(subset=["spy_daily_return"])
    if aligned.empty:
        valid_days = 0
        start_date = None
        end_date = None
    else:
        valid_days = int(len(aligned))
        start_date = str(aligned["date"].iloc[0])
        end_date = str(aligned["date"].iloc[-1])
    if valid_days < 2:
        reason_codes.append("INSUFFICIENT_HISTORY")

    live_cum = _compound(aligned["live_daily_return"].astype(float).tolist()) if valid_days else None
    shadow_cum = _compound(aligned["shadow_polaris_daily_return"].astype(float).tolist()) if valid_days else None
    spy_cum = _compound(aligned["spy_daily_return"].astype(float).tolist()) if valid_days and "spy_daily_return" in aligned else None
    if spy_cum is None:
        reason_codes.append("BENCHMARK_MISSING")
    live_excess = round(live_cum - spy_cum, 10) if live_cum is not None and spy_cum is not None else None
    shadow_excess = round(shadow_cum - spy_cum, 10) if shadow_cum is not None and spy_cum is not None else None
    live_minus_shadow = round(live_cum - shadow_cum, 10) if live_cum is not None and shadow_cum is not None else None

    live_positions = _load_live_positions(trade_date, broker_positions_path)
    if live_positions.missing:
        reason_codes.append("MISSING_BROKER_POSITIONS")
    shadow_weights, shadow_weight_source = _load_shadow_weights(shadow_dir, trade_date)
    if not shadow_weights:
        reason_codes.append("MISSING_SHADOW_DATA")
    holdings = _holdings_reconciliation(live_positions.weights, shadow_weights)
    live_target_weights, live_target_source, identity_from_signals = _load_live_target_weights(precompute_dir, trade_date)
    target_comparison = _holdings_reconciliation(live_target_weights, shadow_weights)
    identity = strategy_identity_metadata(trade_date)
    identity.update(identity_from_signals)
    if live_target_source:
        identity["execution_target_source"] = live_target_source
    if shadow_weight_source:
        identity["shadow_baseline_source"] = shadow_weight_source
    live_strategy_id = str(identity.get("live_strategy_id") or "")
    shadow_baseline_strategy = str(identity.get("shadow_baseline_strategy") or SHADOW_POLARIS)
    live_tracks_shadow_baseline = bool(identity.get("live_tracks_shadow_baseline"))
    strategy_mismatch = bool(
        live_target_source
        and live_strategy_id
        and shadow_baseline_strategy
        and (live_strategy_id != shadow_baseline_strategy or not live_tracks_shadow_baseline)
    )
    if strategy_mismatch:
        alignment_status = "STRATEGY_MISMATCH"
        alignment_message = "Live execution is driven by precompute/signals and is not intended to track Shadow Polaris."
    elif live_target_source:
        alignment_status = "ALIGNED"
        alignment_message = "Live strategy identity is aligned with the shadow baseline metadata."
    else:
        alignment_status = "UNKNOWN"
        alignment_message = (
            "Live target source is unavailable; default strategy metadata says live does not track the shadow baseline, "
            "so this artifact should not be read as Polaris tracking evidence."
        )
    if strategy_mismatch:
        reason_codes.append("DIFFERENT_STRATEGY_PATH")

    if live_minus_shadow is not None and abs(live_minus_shadow) <= RECONCILED_RETURN_THRESHOLD:
        reason_codes.append("RETURNS_RECONCILED")
    elif live_minus_shadow is not None:
        reason_codes.append("RETURNS_DRIFT")
    if holdings["overlap_weight"] >= RECONCILED_OVERLAP_THRESHOLD:
        reason_codes.append("HOLDINGS_RECONCILED")
    else:
        reason_codes.append("HOLDINGS_DRIFT")
    if holdings["live_only_symbols"]:
        reason_codes.append("LIVE_ONLY_POSITIONS")
    if holdings["shadow_only_symbols"]:
        reason_codes.append("SHADOW_ONLY_POSITIONS")
    reason_codes = sorted(set(reason_codes))

    status = _classify(
        reason_codes=reason_codes,
        live_minus_shadow=live_minus_shadow,
        overlap_weight=holdings["overlap_weight"],
        valid_days=valid_days,
    )
    daily = aligned.iloc[-1].to_dict() if valid_days else {}
    return {
        "schema_version": "live_vs_shadow_reconciliation_v1",
        "trade_date": trade_date,
        "generated_at": _utc_now_iso(),
        "live_strategy_id": live_strategy_id,
        "shadow_baseline_strategy": shadow_baseline_strategy,
        "classification": status,
        "comparison_start_date": start_date,
        "comparison_end_date": end_date,
        "number_of_valid_days": valid_days,
        "benchmark_symbol": BENCHMARK_SYMBOL,
        "strategy_identity": identity,
        "strategy_alignment": {
            "live_strategy_id": live_strategy_id,
            "shadow_baseline_strategy": shadow_baseline_strategy,
            "live_tracks_shadow_baseline": live_tracks_shadow_baseline,
            "status": alignment_status,
            "message": alignment_message,
        },
        "status": status,
        "reason_codes": reason_codes,
        "sources": {
            "shadow_dir": str(shadow_dir),
            "live_nav_path": str(live_nav_path),
            "broker_positions_path": live_positions.source_path,
            "shadow_polaris_path": shadow_weight_source,
            "live_target_path": live_target_source,
        },
        "returns": {
            "live_daily_return": _to_float(daily.get("live_daily_return")),
            "shadow_polaris_daily_return": _to_float(daily.get("shadow_polaris_daily_return")),
            "spy_daily_return": _to_float(daily.get("spy_daily_return")),
            "live_cumulative_return": live_cum,
            "shadow_polaris_cumulative_return": shadow_cum,
            "spy_cumulative_return": spy_cum,
            "live_excess_vs_spy": live_excess,
            "shadow_polaris_excess_vs_spy": shadow_excess,
            "live_minus_shadow_polaris": live_minus_shadow,
        },
        "holdings": {key: value for key, value in holdings.items() if key != "rows"},
        "holdings_rows": holdings["rows"],
        "target_vs_target": {key: value for key, value in target_comparison.items() if key != "rows"},
        "target_vs_target_rows": target_comparison["rows"],
    }


def _operator_conclusion(payload: dict[str, Any]) -> str:
    status = payload.get("status")
    reasons = set(payload.get("reason_codes") or [])
    if status == "NOT_COMPARABLE":
        return "NOT_COMPARABLE; missing or insufficient artifacts prevent a live-vs-shadow judgment."
    if status == STRATEGY_MISMATCH_STATUS or "DIFFERENT_STRATEGY_PATH" in reasons:
        return "NOT_ALIGNED; live execution and Shadow Polaris are different strategy paths, so return differences are observational and not tracking underperformance."
    if status == "RECONCILED":
        return "RECONCILED; live paper behavior is tracking Shadow Polaris within configured return and holdings thresholds."
    if status == "MINOR_DRIFT":
        return "MINOR_DRIFT; live paper behavior is close to Shadow Polaris but outside the strict reconciled band."
    if "RETURNS_DRIFT" in reasons and "HOLDINGS_DRIFT" in reasons:
        return "MAJOR_DRIFT; both returns and holdings diverge from Shadow Polaris."
    if "RETURNS_DRIFT" in reasons:
        return "MAJOR_DRIFT; return path diverges from Shadow Polaris."
    if "HOLDINGS_DRIFT" in reasons:
        return "MAJOR_DRIFT; live holdings diverge from Shadow Polaris."
    return f"{status}; review reason codes."


def render_markdown(payload: dict[str, Any]) -> str:
    returns = payload.get("returns") or {}
    holdings = payload.get("holdings") or {}
    alignment = payload.get("strategy_alignment") or {}
    lines = [
        "# Live vs Shadow Reconciliation",
        "",
        "## Executive Summary",
        f"- Status: {payload.get('classification') or payload.get('status')}",
        f"- Generated at: {payload.get('generated_at') or 'N/A'}",
        f"- Live strategy: {payload.get('live_strategy_id') or alignment.get('live_strategy_id') or 'N/A'}",
        f"- Shadow baseline: {payload.get('shadow_baseline_strategy') or alignment.get('shadow_baseline_strategy') or 'N/A'}",
        f"- Strategy alignment: {alignment.get('status') or 'N/A'}",
        f"- Live return: {_fmt_pct(returns.get('live_cumulative_return'))}",
        f"- Shadow Polaris return: {_fmt_pct(returns.get('shadow_polaris_cumulative_return'))}",
        f"- SPY return: {_fmt_pct(returns.get('spy_cumulative_return'))}",
        f"- Live minus Shadow: {_fmt_signed_pct(returns.get('live_minus_shadow_polaris'))}",
        f"- Holdings overlap: {_fmt_pct(holdings.get('overlap_weight'))}",
        f"- Diagnosis: {', '.join(payload.get('reason_codes') or []) or 'N/A'}",
        f"- Operator conclusion: {_operator_conclusion(payload)}",
        "",
        "## Strategy Identity",
        f"- Live execution target source: {(payload.get('sources') or {}).get('live_target_path') or 'N/A'}",
        f"- Live execution target type: {(payload.get('strategy_identity') or {}).get('execution_target_type') or 'N/A'}",
        f"- Shadow baseline source: {(payload.get('sources') or {}).get('shadow_polaris_path') or 'N/A'}",
        f"- Live tracks Shadow baseline: {'YES' if alignment.get('live_tracks_shadow_baseline') else 'NO'}",
        f"- Interpretation: {alignment.get('message') or 'N/A'}",
        "",
        "## Return Comparison",
        "| Metric | Live | Shadow Polaris | SPY | Live minus Shadow |",
        "|---|---:|---:|---:|---:|",
        f"| Daily Return | {_fmt_pct(returns.get('live_daily_return'))} | {_fmt_pct(returns.get('shadow_polaris_daily_return'))} | {_fmt_pct(returns.get('spy_daily_return'))} | N/A |",
        f"| Cumulative Return | {_fmt_pct(returns.get('live_cumulative_return'))} | {_fmt_pct(returns.get('shadow_polaris_cumulative_return'))} | {_fmt_pct(returns.get('spy_cumulative_return'))} | {_fmt_signed_pct(returns.get('live_minus_shadow_polaris'))} |",
        f"| Excess vs SPY | {_fmt_pct(returns.get('live_excess_vs_spy'))} | {_fmt_pct(returns.get('shadow_polaris_excess_vs_spy'))} | 0.00% | {_fmt_signed_pct(returns.get('live_minus_shadow_polaris'))} |",
        "",
        "## Holdings Reconciliation",
        "| Symbol | Live Weight | Shadow Weight | Difference | Category |",
        "|---|---:|---:|---:|---|",
    ]
    for row in payload.get("holdings_rows") or []:
        lines.append(
            f"| {row['symbol']} | {_fmt_pct(row.get('live_weight'))} | {_fmt_pct(row.get('shadow_weight'))} | {_fmt_signed_pct(row.get('difference'))} | {row.get('category')} |"
        )
    if not payload.get("holdings_rows"):
        lines.append("| N/A | N/A | N/A | N/A | N/A |")
    lines.extend(
        [
            "",
            "## Target vs Target Comparison",
            "| Symbol | Live Target Weight | Shadow Polaris Weight | Difference | Category |",
            "|---|---:|---:|---:|---|",
        ]
    )
    for row in payload.get("target_vs_target_rows") or []:
        lines.append(
            f"| {row['symbol']} | {_fmt_pct(row.get('live_weight'))} | {_fmt_pct(row.get('shadow_weight'))} | {_fmt_signed_pct(row.get('difference'))} | {row.get('category')} |"
        )
    if not payload.get("target_vs_target_rows"):
        lines.append("| N/A | N/A | N/A | N/A | N/A |")
    lines.extend(
        [
            "",
            "## Diagnosis",
            f"- Trade date: {payload.get('trade_date')}",
            f"- Comparison window: {payload.get('comparison_start_date') or 'N/A'} to {payload.get('comparison_end_date') or 'N/A'}",
            f"- Valid days: {payload.get('number_of_valid_days')}",
            f"- Live positions: {holdings.get('live_positions_count', 'N/A')}",
            f"- Shadow positions: {holdings.get('shadow_positions_count', 'N/A')}",
            f"- Overlap count: {holdings.get('overlap_count', 'N/A')}",
            f"- Target overlap weight: {_fmt_pct((payload.get('target_vs_target') or {}).get('overlap_weight'))}",
            f"- Reason codes: {', '.join(payload.get('reason_codes') or []) or 'N/A'}",
            f"- Deterministic conclusion: {_operator_conclusion(payload)}",
        ]
    )
    return "\n".join(lines) + "\n"


def _latest_dated_artifact_dir(output_dir: Path) -> Path | None:
    candidates: list[tuple[str, Path]] = []
    if not output_dir.exists():
        return None
    for child in output_dir.iterdir():
        if not child.is_dir() or child.name == "latest":
            continue
        try:
            normalized = pd.Timestamp(child.name).strftime("%Y-%m-%d")
        except Exception:
            continue
        if normalized != child.name:
            continue
        json_path = child / "live_vs_shadow_reconciliation.json"
        md_path = child / "live_vs_shadow_reconciliation.md"
        if json_path.exists() and md_path.exists():
            candidates.append((child.name, child))
    if not candidates:
        return None
    return sorted(candidates, key=lambda item: item[0])[-1][1]


def publish_latest_artifacts(output_dir: Path) -> tuple[Path, Path] | None:
    dated_dir = _latest_dated_artifact_dir(output_dir)
    if dated_dir is None:
        return None
    latest_dir = output_dir / "latest"
    latest_dir.mkdir(parents=True, exist_ok=True)
    latest_json = latest_dir / "live_vs_shadow_reconciliation.json"
    latest_md = latest_dir / "live_vs_shadow_reconciliation.md"
    latest_json.write_text((dated_dir / "live_vs_shadow_reconciliation.json").read_text())
    latest_md.write_text((dated_dir / "live_vs_shadow_reconciliation.md").read_text())
    return latest_json, latest_md


def write_artifacts(payload: dict[str, Any], output_dir: Path) -> tuple[Path, Path]:
    trade_date = str(payload["trade_date"])
    dated_dir = output_dir / trade_date
    dated_dir.mkdir(parents=True, exist_ok=True)
    json_path = dated_dir / "live_vs_shadow_reconciliation.json"
    md_path = dated_dir / "live_vs_shadow_reconciliation.md"
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True))
    md_path.write_text(render_markdown(payload))
    publish_latest_artifacts(output_dir)
    return json_path, md_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Artifact-only live paper vs Shadow Polaris reconciliation.")
    parser.add_argument("--trade-date", required=True)
    parser.add_argument("--shadow-dir", default="outputs/shadow_candidates")
    parser.add_argument("--precompute-dir", default="outputs/precompute")
    parser.add_argument("--output-dir", default="outputs/reconciliation/live_vs_shadow")
    parser.add_argument("--live-nav-path", default="outputs/perf/live_overlay_nav_series.csv")
    parser.add_argument("--broker-positions-path", default=None)
    args = parser.parse_args(argv)

    payload = build_reconciliation(
        trade_date=str(pd.Timestamp(args.trade_date).strftime("%Y-%m-%d")),
        shadow_dir=Path(args.shadow_dir),
        precompute_dir=Path(args.precompute_dir),
        live_nav_path=Path(args.live_nav_path),
        broker_positions_path=Path(args.broker_positions_path) if args.broker_positions_path else None,
    )
    output_dir = Path(args.output_dir)
    json_path, md_path = write_artifacts(payload, output_dir)
    latest_paths = publish_latest_artifacts(output_dir)
    print(f"[LIVE_VS_SHADOW] wrote {json_path}")
    print(f"[LIVE_VS_SHADOW] wrote {md_path}")
    if latest_paths is not None:
        latest_json, latest_md = latest_paths
        print(f"[LIVE_VS_SHADOW] latest_json={latest_json}")
        print(f"[LIVE_VS_SHADOW] latest_md={latest_md}")
    print(f"[LIVE_VS_SHADOW] status={payload['status']} reasons={','.join(payload['reason_codes'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
