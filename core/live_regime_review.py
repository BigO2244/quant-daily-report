from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import pandas as pd

from core.portfolio_alloc import SleeveOutput, WEIGHT_TOLERANCE


# -------------------------------------------------------------------- #
# Ticker -> sleeve snapshot persistence                                 #
# -------------------------------------------------------------------- #
# The shadow-vs-live comparator needs to classify broker positions by
# sleeve. Broker positions reflect YESTERDAY's executed picks, but the
# in-process sleeve_outputs reflect TODAY's fresh picks. When picks
# churn (the norm with daily cadence + fast signals), today's
# sleeve_outputs may not contain any of the tickers the broker still
# holds, so the comparator silently returned all zeros and the
# promotion gate always failed with total_abs_gap_live_vs_target = 1.0.
#
# Fix: write a persisted ticker -> sleeve mapping after each run so the
# next run can classify held positions against the mapping that was
# live when those positions were established. We merge yesterday's
# persisted map with today's fresh sleeve_outputs, preferring the
# persisted map for any ticker currently held by the broker.
# -------------------------------------------------------------------- #

TICKER_SLEEVE_SNAPSHOT_DIR = Path("outputs/ticker_sleeve_snapshots")


def persist_ticker_sleeve_map(
    *,
    snapshot_dir: str | Path | None,
    asof_date: str,
    ticker_to_sleeve_weights: dict[str, dict[str, float]],
) -> Path | None:
    """Persist today's ticker -> {sleeve: weight} map so tomorrow's run
    can correctly attribute broker positions back to sleeves."""
    if not asof_date:
        return None
    base = Path(snapshot_dir) if snapshot_dir else TICKER_SLEEVE_SNAPSHOT_DIR
    base.mkdir(parents=True, exist_ok=True)
    out_path = base / f"ticker_sleeve_map_{asof_date}.json"
    clean = {
        str(ticker).upper(): {
            str(sleeve): float(weight or 0.0)
            for sleeve, weight in (sleeves or {}).items()
        }
        for ticker, sleeves in (ticker_to_sleeve_weights or {}).items()
        if ticker
    }
    payload = {
        "asof_date": asof_date,
        "generated_at": _now_utc(),
        "ticker_sleeves": clean,
    }
    out_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return out_path


def load_latest_ticker_sleeve_map(
    *,
    snapshot_dir: str | Path | None,
    before_date: str | None = None,
) -> dict[str, dict[str, float]]:
    """Load the most recent ticker -> sleeve map strictly before `before_date`.

    Returns an empty dict if nothing is found. We pick the most recent
    file by lexicographic sort because filenames contain ISO dates.
    """
    base = Path(snapshot_dir) if snapshot_dir else TICKER_SLEEVE_SNAPSHOT_DIR
    if not base.exists():
        return {}
    candidates = sorted(base.glob("ticker_sleeve_map_*.json"))
    if not candidates:
        return {}
    chosen: Path | None = None
    for candidate in reversed(candidates):
        stem_date = candidate.stem.replace("ticker_sleeve_map_", "")
        if before_date and stem_date >= before_date:
            continue
        chosen = candidate
        break
    if chosen is None:
        return {}
    try:
        payload = json.loads(chosen.read_text(encoding="utf-8"))
    except Exception:
        return {}
    raw = payload.get("ticker_sleeves") or {}
    out: dict[str, dict[str, float]] = {}
    for ticker, sleeves in raw.items():
        if not isinstance(sleeves, dict):
            continue
        clean_sleeves: dict[str, float] = {}
        for name, weight in sleeves.items():
            try:
                clean_sleeves[str(name)] = float(weight or 0.0)
            except Exception:
                continue
        if clean_sleeves:
            out[str(ticker).upper()] = clean_sleeves
    return out


DEFAULT_OBJECTIVE_CONTRACT: dict[str, Any] = {
    "benchmark": "SPY",
    "north_star": "Significantly outperform SPY with 20%+ annualized returns and strong risk-adjusted performance.",
    "cash_ceiling": 0.05,
}

DEFAULT_PROMOTION_GATES: dict[str, float] = {
    "min_active_sleeves": 2.0,
    "min_benchmark_overlap_days": 20.0,
    "max_total_allocation_gap": 0.30,
    "max_single_sleeve_gap": 0.15,
    "max_overlap_weight": 0.35,
}


def _now_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace("$", "").replace(",", "")
    try:
        return float(text)
    except Exception:
        return None


def _load_objective_contract(path: str | Path | None) -> dict[str, Any]:
    contract = dict(DEFAULT_OBJECTIVE_CONTRACT)
    if path is None:
        return contract
    contract_path = Path(path)
    if not contract_path.exists():
        return contract
    try:
        payload = json.loads(contract_path.read_text(encoding="utf-8"))
    except Exception:
        return contract
    if isinstance(payload, dict):
        contract.update(payload)
    return contract


def _group_sleeve_ticker_weights(sleeve_output: SleeveOutput | None) -> dict[str, float]:
    if sleeve_output is None or sleeve_output.positions_df is None or sleeve_output.positions_df.empty:
        return {}
    df = sleeve_output.positions_df.copy()
    if "ticker" not in df.columns or "target_weight" not in df.columns:
        return {}
    df["ticker"] = df["ticker"].astype(str).str.upper()
    grouped = (
        df.groupby("ticker", as_index=False)["target_weight"]
        .sum()
    )
    out: dict[str, float] = {}
    for _, row in grouped.iterrows():
        ticker = str(row.get("ticker") or "").strip().upper()
        weight = abs(float(row.get("target_weight") or 0.0))
        if ticker and weight > WEIGHT_TOLERANCE:
            out[ticker] = weight
    return out


def build_ticker_sleeve_target_weights(
    sleeve_outputs: list[SleeveOutput],
    sleeve_budgets: dict[str, float] | None,
) -> dict[str, dict[str, float]]:
    ticker_to_weights: dict[str, dict[str, float]] = {}
    for sleeve_output in sleeve_outputs or []:
        if sleeve_output is None or sleeve_output.meta is None:
            continue
        sleeve_name = str(sleeve_output.meta.sleeve_name or "").strip()
        if not sleeve_name:
            continue
        sleeve_budget = max(
            0.0,
            float((sleeve_budgets or {}).get(sleeve_name, getattr(sleeve_output.meta, "strength", 0.0)) or 0.0),
        )
        if sleeve_budget <= WEIGHT_TOLERANCE:
            continue
        for ticker, local_weight in _group_sleeve_ticker_weights(sleeve_output).items():
            ticker_to_weights.setdefault(ticker, {})[sleeve_name] = local_weight * sleeve_budget
    return ticker_to_weights


def compute_live_sleeve_weights(
    *,
    broker_positions: list[dict[str, Any]] | None,
    broker_equity: float | None,
    sleeve_outputs: list[SleeveOutput],
    sleeve_budgets: dict[str, float] | None,
    persisted_ticker_sleeves: dict[str, dict[str, float]] | None = None,
) -> dict[str, float]:
    """Compute live sleeve weights by attributing broker positions to sleeves.

    persisted_ticker_sleeves: optional ticker -> {sleeve: weight} map loaded
        from yesterday's snapshot. When provided, it is used as the
        authoritative classifier for any ticker currently held by the
        broker, because the broker's positions reflect yesterday's execution
        (not today's fresh sleeve picks). This is the fix for the silent
        attribution failure where the shadow_vs_live comparator returned
        all zeros.
    """
    sleeve_names = {
        str(getattr(sleeve_output.meta, "sleeve_name", "")).strip()
        for sleeve_output in sleeve_outputs or []
        if sleeve_output is not None and getattr(sleeve_output, "meta", None) is not None
    }
    sleeve_names |= set((sleeve_budgets or {}).keys())
    # Also pick up any sleeves that appear in the persisted map — broker
    # may be holding tickers from a sleeve that's inactive today.
    for sleeves in (persisted_ticker_sleeves or {}).values():
        if isinstance(sleeves, dict):
            sleeve_names |= {str(name) for name in sleeves.keys()}
    sleeve_names = {name for name in sleeve_names if name}
    if not sleeve_names:
        return {}

    out = {name: 0.0 for name in sleeve_names}
    if not broker_positions or broker_equity is None or float(broker_equity) <= 0:
        return out

    # Merge: start with today's sleeve_outputs, then overlay persisted map.
    # The persisted map wins for any ticker that appears in both because
    # broker holdings were established based on the mapping that was live
    # when the position was opened (i.e. yesterday or earlier).
    today_weights = build_ticker_sleeve_target_weights(sleeve_outputs, sleeve_budgets)
    ticker_to_weights: dict[str, dict[str, float]] = {
        ticker: dict(sleeves) for ticker, sleeves in today_weights.items()
    }
    for ticker, sleeves in (persisted_ticker_sleeves or {}).items():
        if isinstance(sleeves, dict) and sleeves:
            ticker_to_weights[str(ticker).upper()] = {
                str(name): float(weight or 0.0) for name, weight in sleeves.items()
            }

    ticker_to_shares: dict[str, dict[str, float]] = {}
    for ticker, sleeve_weights in ticker_to_weights.items():
        positive = {
            name: max(0.0, float(weight))
            for name, weight in sleeve_weights.items()
            if max(0.0, float(weight)) > WEIGHT_TOLERANCE
        }
        if not positive:
            continue
        total = sum(positive.values())
        if total <= WEIGHT_TOLERANCE:
            share = 1.0 / len(positive)
            ticker_to_shares[ticker] = {name: share for name in positive}
        else:
            ticker_to_shares[ticker] = {name: value / total for name, value in positive.items()}

    for position in broker_positions:
        symbol = str((position or {}).get("symbol") or "").strip().upper()
        market_value = _to_float((position or {}).get("market_value")) or 0.0
        if not symbol or market_value == 0.0:
            continue
        for sleeve_name, share in ticker_to_shares.get(symbol, {}).items():
            out[sleeve_name] = out.get(sleeve_name, 0.0) + market_value * share / float(broker_equity)
    return out


def build_returns_by_ticker(
    previous_prices: pd.DataFrame | dict[str, float] | None,
    current_prices: pd.DataFrame | dict[str, float] | None,
) -> dict[str, float]:
    def _to_map(payload: pd.DataFrame | dict[str, float] | None) -> dict[str, float]:
        if payload is None:
            return {}
        if isinstance(payload, dict):
            out: dict[str, float] = {}
            for key, value in payload.items():
                number = _to_float(value)
                if number is not None:
                    out[str(key).strip().upper()] = number
            return out
        if payload.empty or "ticker" not in payload.columns:
            return {}
        out = {}
        for _, row in payload.iterrows():
            ticker = str(row.get("ticker") or "").strip().upper()
            price = _to_float(row.get("prev_close"))
            if ticker and price not in (None, 0.0):
                out[ticker] = float(price)
        return out

    prev_map = _to_map(previous_prices)
    curr_map = _to_map(current_prices)
    returns_by_ticker: dict[str, float] = {}
    for ticker, prev_price in prev_map.items():
        curr_price = curr_map.get(ticker)
        if prev_price in (None, 0.0) or curr_price in (None,):
            continue
        returns_by_ticker[ticker] = float(curr_price) / float(prev_price) - 1.0
    return returns_by_ticker


def _build_overlap_diagnostics(
    ticker_to_target_weights: dict[str, dict[str, float]],
) -> dict[str, Any]:
    overlapping = []
    for ticker, sleeve_weights in ticker_to_target_weights.items():
        if len(sleeve_weights) <= 1:
            continue
        total_weight = sum(float(value) for value in sleeve_weights.values())
        overlapping.append(
            {
                "ticker": ticker,
                "target_weight": total_weight,
                "sleeves": dict(sorted(sleeve_weights.items(), key=lambda item: item[0])),
                "sleeve_count": len(sleeve_weights),
            }
        )
    overlapping.sort(key=lambda item: (-float(item["target_weight"]), item["ticker"]))
    return {
        "overlapping_ticker_count": len(overlapping),
        "overlapped_target_weight": sum(float(item["target_weight"]) for item in overlapping),
        "max_ticker_sleeve_count": max((int(item["sleeve_count"]) for item in overlapping), default=0),
        "overlapping_tickers": overlapping[:10],
    }


def _build_sleeve_rows(
    *,
    sleeve_outputs: list[SleeveOutput],
    regime_strengths: dict[str, float],
    drift_flags: dict[str, bool],
    final_target_allocations: dict[str, float],
    live_broker_weights: dict[str, float],
    returns_by_ticker: dict[str, float],
    ticker_to_target_weights: dict[str, dict[str, float]],
) -> list[dict[str, Any]]:
    overlapping_tickers = {
        ticker for ticker, sleeve_weights in ticker_to_target_weights.items() if len(sleeve_weights) > 1
    }
    rows: list[dict[str, Any]] = []
    for sleeve_output in sleeve_outputs or []:
        if sleeve_output is None or sleeve_output.meta is None:
            continue
        sleeve_name = str(sleeve_output.meta.sleeve_name or "").strip()
        if not sleeve_name:
            continue
        local_weights = _group_sleeve_ticker_weights(sleeve_output)
        local_total = sum(local_weights.values())
        weighted_return = None
        if local_total > WEIGHT_TOLERANCE:
            weighted_return = sum(
                local_weights[ticker] * float(returns_by_ticker.get(ticker, 0.0))
                for ticker in local_weights
            ) / local_total
        final_allocation = float(final_target_allocations.get(sleeve_name, 0.0) or 0.0)
        overlap_weight = sum(
            float(local_weights.get(ticker, 0.0)) * final_allocation
            for ticker in overlapping_tickers
            if ticker in local_weights
        )
        rows.append(
            {
                "sleeve_name": sleeve_name,
                "target_regime_weight": float(regime_strengths.get(sleeve_name, 0.0) or 0.0),
                "drift_rebalance": bool(drift_flags.get(sleeve_name, True)),
                "applied_strength": float(getattr(sleeve_output.meta, "strength", 0.0) or 0.0),
                "final_target_allocation": final_allocation,
                "live_broker_weight": float(live_broker_weights.get(sleeve_name, 0.0) or 0.0),
                "allocation_gap_live_minus_target": float(live_broker_weights.get(sleeve_name, 0.0) or 0.0) - final_allocation,
                "positions_count": len(local_weights),
                "avg_name_return": weighted_return,
                "daily_contribution_target_book": (
                    final_allocation * weighted_return if weighted_return is not None else None
                ),
                "overlap_target_weight": overlap_weight,
                "top_tickers": [
                    ticker
                    for ticker, _ in sorted(local_weights.items(), key=lambda item: (-item[1], item[0]))[:5]
                ],
            }
        )
    rows.sort(key=lambda item: (-float(item["final_target_allocation"]), item["sleeve_name"]))
    return rows


def _build_promotion_gate(
    *,
    regime_summary: dict[str, Any] | None,
    final_target_allocations: dict[str, float],
    live_broker_weights: dict[str, float],
    overlap_diagnostics: dict[str, Any],
    alpha_attribution: dict[str, Any] | None,
    signal_snapshot_path: str | Path | None,
    contract: dict[str, Any],
) -> dict[str, Any]:
    gates = dict(DEFAULT_PROMOTION_GATES)
    checks: list[dict[str, Any]] = []

    def _add_check(
        *,
        name: str,
        status: str,
        current: Any = None,
        threshold: Any = None,
        note: str = "",
        blocking: bool = False,
    ) -> None:
        checks.append(
            {
                "name": name,
                "status": status,
                "current": current,
                "threshold": threshold,
                "note": note,
                "blocking": blocking,
            }
        )

    regime_available = bool(regime_summary) and str((regime_summary or {}).get("composite_regime") or "").strip().lower() not in {"", "unknown"}
    _add_check(
        name="regime_data_available",
        status="pass" if regime_available else "fail",
        current=(regime_summary or {}).get("composite_regime"),
        threshold="known composite regime",
        note="The allocator needs a real regime state before its sleeve budgets mean anything.",
        blocking=True,
    )

    active_sleeves = sum(1 for value in final_target_allocations.values() if float(value or 0.0) > WEIGHT_TOLERANCE)
    min_active = int(gates["min_active_sleeves"])
    _add_check(
        name="multi_sleeve_participation",
        status="pass" if active_sleeves >= min_active else "fail",
        current=active_sleeves,
        threshold=min_active,
        note="Stage 1B requires more than a nominal single-sleeve book.",
        blocking=True,
    )

    signal_exists = bool(signal_snapshot_path) and Path(signal_snapshot_path).exists()
    _add_check(
        name="signal_snapshot_present",
        status="pass" if signal_exists else "fail",
        current=str(signal_snapshot_path or ""),
        threshold="existing signal snapshot artifact",
        note="If the signal snapshot is missing, the allocator decision is not auditable.",
        blocking=True,
    )

    abs_gaps = [
        abs(float(live_broker_weights.get(sleeve_name, 0.0) or 0.0) - float(target or 0.0))
        for sleeve_name, target in final_target_allocations.items()
    ]
    total_gap = sum(abs_gaps)
    max_gap = max(abs_gaps, default=0.0)
    if total_gap <= gates["max_total_allocation_gap"] and max_gap <= gates["max_single_sleeve_gap"]:
        alignment_status = "pass"
    elif total_gap <= gates["max_total_allocation_gap"] * 1.5 and max_gap <= gates["max_single_sleeve_gap"] * 1.5:
        alignment_status = "warn"
    else:
        alignment_status = "fail"
    _add_check(
        name="shadow_vs_live_alignment",
        status=alignment_status,
        current={"total_abs_gap": round(total_gap, 6), "max_abs_gap": round(max_gap, 6)},
        threshold={
            "max_total_allocation_gap": gates["max_total_allocation_gap"],
            "max_single_sleeve_gap": gates["max_single_sleeve_gap"],
        },
        note="Compares the model target book to the live broker book at the decision point.",
        blocking=True,
    )

    overlap_weight = float(overlap_diagnostics.get("overlapped_target_weight", 0.0) or 0.0)
    overlap_status = "pass" if overlap_weight <= gates["max_overlap_weight"] else "warn"
    _add_check(
        name="overlap_complexity",
        status=overlap_status,
        current=overlap_weight,
        threshold=gates["max_overlap_weight"],
        note="Heavy overlap across sleeves makes attribution harder even when allocation math is correct.",
        blocking=False,
    )

    alpha_payload = alpha_attribution or {}
    overlap_days = int(alpha_payload.get("overlap_days") or 0)
    if overlap_days >= int(gates["min_benchmark_overlap_days"]):
        benchmark_status = "pass"
    elif overlap_days >= 5:
        benchmark_status = "warn"
    else:
        benchmark_status = "fail"
    _add_check(
        name="benchmark_evidence_window",
        status=benchmark_status,
        current=overlap_days,
        threshold=int(gates["min_benchmark_overlap_days"]),
        note="A short benchmark window is directionally useful but not promotion-grade evidence.",
        blocking=False,
    )

    cumulative_alpha = _to_float(((alpha_payload.get("summary") or {}).get("cumulative_alpha")))
    if overlap_days < int(gates["min_benchmark_overlap_days"]) or cumulative_alpha is None:
        alpha_status = "warn"
    else:
        alpha_status = "pass" if cumulative_alpha >= 0 else "fail"
    _add_check(
        name="benchmark_relative_alpha",
        status=alpha_status,
        current=cumulative_alpha,
        threshold=">= 0.0 over validated window",
        note="The allocator should not be promoted further while it is persistently losing relative ground.",
        blocking=False,
    )

    invested_ratio = sum(float(value or 0.0) for value in live_broker_weights.values())
    cash_ratio = max(0.0, 1.0 - invested_ratio)
    cash_ceiling = _to_float(contract.get("cash_ceiling"))
    cash_status = "pass"
    if cash_ceiling is not None:
        if cash_ratio <= cash_ceiling:
            cash_status = "pass"
        elif cash_ratio <= cash_ceiling + 0.05:
            cash_status = "warn"
        else:
            cash_status = "fail"
    _add_check(
        name="cash_discipline",
        status=cash_status,
        current=cash_ratio,
        threshold=cash_ceiling,
        note="Idle cash should be a deliberate risk-off decision, not a quiet participation failure.",
        blocking=False,
    )

    blockers = [check["name"] for check in checks if check["blocking"] and check["status"] == "fail"]
    if blockers:
        overall_status = "not_ready"
    elif any(check["status"] == "fail" for check in checks):
        overall_status = "watch"
    elif any(check["status"] == "warn" for check in checks):
        overall_status = "watch"
    else:
        overall_status = "ready"

    return {
        "overall_status": overall_status,
        "checks": checks,
        "blockers": blockers,
        "cash_ratio": cash_ratio,
    }


def build_live_regime_review(
    *,
    trade_date: str,
    asof_date: str | None,
    regime_summary: dict[str, Any] | None,
    regime_strengths: dict[str, float] | None,
    drift_flags: dict[str, bool] | None,
    sleeve_outputs: list[SleeveOutput],
    final_target_allocations: dict[str, float] | None,
    broker_positions: list[dict[str, Any]] | None,
    broker_equity: float | None,
    returns_by_ticker: dict[str, float] | None = None,
    alpha_attribution: dict[str, Any] | None = None,
    signal_snapshot_path: str | Path | None = None,
    objective_contract_path: str | Path | None = Path("config/engine_objectives.json"),
    persisted_ticker_sleeves: dict[str, dict[str, float]] | None = None,
) -> dict[str, Any]:
    regime_strengths = dict(regime_strengths or {})
    drift_flags = dict(drift_flags or {})
    final_target_allocations = {
        str(key): float(value or 0.0)
        for key, value in dict(final_target_allocations or {}).items()
    }
    returns_by_ticker = {
        str(key).strip().upper(): float(value)
        for key, value in dict(returns_by_ticker or {}).items()
        if _to_float(value) is not None
    }
    contract = _load_objective_contract(objective_contract_path)

    ticker_to_target_weights = build_ticker_sleeve_target_weights(
        sleeve_outputs,
        final_target_allocations,
    )
    live_broker_weights = compute_live_sleeve_weights(
        broker_positions=broker_positions,
        broker_equity=broker_equity,
        sleeve_outputs=sleeve_outputs,
        sleeve_budgets=final_target_allocations,
        persisted_ticker_sleeves=persisted_ticker_sleeves,
    )
    overlap_diagnostics = _build_overlap_diagnostics(ticker_to_target_weights)
    sleeve_rows = _build_sleeve_rows(
        sleeve_outputs=sleeve_outputs,
        regime_strengths=regime_strengths,
        drift_flags=drift_flags,
        final_target_allocations=final_target_allocations,
        live_broker_weights=live_broker_weights,
        returns_by_ticker=returns_by_ticker,
        ticker_to_target_weights=ticker_to_target_weights,
    )
    promotion_gate = _build_promotion_gate(
        regime_summary=regime_summary,
        final_target_allocations=final_target_allocations,
        live_broker_weights=live_broker_weights,
        overlap_diagnostics=overlap_diagnostics,
        alpha_attribution=alpha_attribution,
        signal_snapshot_path=signal_snapshot_path,
        contract=contract,
    )

    return {
        "generated_at": _now_utc(),
        "trade_date": trade_date,
        "asof_date": asof_date,
        "benchmark": contract.get("benchmark", "SPY"),
        "north_star": contract.get("north_star"),
        "signal_snapshot_path": str(signal_snapshot_path) if signal_snapshot_path else "",
        "regime": {
            "composite_regime": (regime_summary or {}).get("composite_regime"),
            "trend_state": (regime_summary or {}).get("trend_state"),
            "volatility_state": (regime_summary or {}).get("volatility_state"),
            "breadth_state": (regime_summary or {}).get("breadth_state"),
            "macro_state": (regime_summary or {}).get("macro_state"),
            "target_weights": dict((regime_summary or {}).get("target_weights") or {}),
        },
        "shadow_vs_live": {
            "target_allocations": final_target_allocations,
            "live_broker_weights": live_broker_weights,
            "drift_flags": drift_flags,
            "persisted_ticker_sleeves_count": int(len(persisted_ticker_sleeves or {})),
            "total_abs_gap_live_vs_target": sum(
                abs(float(live_broker_weights.get(name, 0.0) or 0.0) - float(target or 0.0))
                for name, target in final_target_allocations.items()
            ),
            "max_abs_gap_live_vs_target": max(
                (
                    abs(float(live_broker_weights.get(name, 0.0) or 0.0) - float(target or 0.0))
                    for name, target in final_target_allocations.items()
                ),
                default=0.0,
            ),
        },
        "overlap_diagnostics": overlap_diagnostics,
        "sleeves": sleeve_rows,
        "returns_by_ticker": dict(sorted(returns_by_ticker.items(), key=lambda item: item[0])),
        "alpha_attribution": alpha_attribution or {},
        "promotion_gate": promotion_gate,
    }


def _format_pct(value: float | None, *, signed: bool = False) -> str:
    if value is None:
        return "N/A"
    return f"{value:+.2%}" if signed else f"{value:.2%}"


def build_live_regime_review_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Live Regime Review",
        "",
        f"- Trade date: {payload.get('trade_date') or 'N/A'}",
        f"- As of: {payload.get('asof_date') or 'N/A'}",
        f"- Benchmark: {payload.get('benchmark') or 'SPY'}",
        f"- Promotion gate: {str(((payload.get('promotion_gate') or {}).get('overall_status') or '')).upper()}",
        "",
        "## Regime",
        "",
        f"- Composite: {((payload.get('regime') or {}).get('composite_regime') or 'unknown')}",
        f"- Trend / Vol / Breadth / Macro: "
        f"{((payload.get('regime') or {}).get('trend_state') or 'unknown')} / "
        f"{((payload.get('regime') or {}).get('volatility_state') or 'unknown')} / "
        f"{((payload.get('regime') or {}).get('breadth_state') or 'unknown')} / "
        f"{((payload.get('regime') or {}).get('macro_state') or 'unknown')}",
        "",
        "## Shadow Vs Live",
        "",
        f"- Total absolute gap: {_format_pct((payload.get('shadow_vs_live') or {}).get('total_abs_gap_live_vs_target'))}",
        f"- Max single-sleeve gap: {_format_pct((payload.get('shadow_vs_live') or {}).get('max_abs_gap_live_vs_target'))}",
        "",
        "## Sleeves",
        "",
    ]

    for row in payload.get("sleeves") or []:
        lines.append(
            f"- {row['sleeve_name']}: target={_format_pct(row.get('final_target_allocation'))}, "
            f"live={_format_pct(row.get('live_broker_weight'))}, "
            f"gap={_format_pct(row.get('allocation_gap_live_minus_target'), signed=True)}, "
            f"contribution={_format_pct(row.get('daily_contribution_target_book'), signed=True)}, "
            f"rebalance={'YES' if row.get('drift_rebalance') else 'HOLD'}"
        )

    overlap = payload.get("overlap_diagnostics") or {}
    lines.extend(
        [
            "",
            "## Overlap",
            "",
            f"- Overlapping tickers: {int(overlap.get('overlapping_ticker_count') or 0)}",
            f"- Overlapped target weight: {_format_pct(overlap.get('overlapped_target_weight'))}",
            "",
            "## Promotion Gate",
            "",
        ]
    )
    for check in (payload.get("promotion_gate") or {}).get("checks") or []:
        lines.append(
            f"- {check['name']}: {str(check.get('status') or '').upper()} "
            f"(current={check.get('current')}, threshold={check.get('threshold')})"
        )
    return "\n".join(lines) + "\n"


def write_live_regime_review(
    *,
    run_root: str | Path,
    output_dir: str | Path,
    trade_date: str,
    asof_date: str | None,
    regime_summary: dict[str, Any] | None,
    regime_strengths: dict[str, float] | None,
    drift_flags: dict[str, bool] | None,
    sleeve_outputs: list[SleeveOutput],
    final_target_allocations: dict[str, float] | None,
    broker_positions: list[dict[str, Any]] | None,
    broker_equity: float | None,
    returns_by_ticker: dict[str, float] | None = None,
    alpha_attribution: dict[str, Any] | None = None,
    signal_snapshot_path: str | Path | None = None,
    objective_contract_path: str | Path | None = Path("config/engine_objectives.json"),
    ticker_sleeve_snapshot_dir: str | Path | None = None,
) -> dict[str, Any]:
    # Load yesterday's ticker -> sleeve map (if any) so we can correctly
    # attribute broker positions to the sleeves that established them.
    # Without this, churn-driven picks make today's sleeve_outputs miss
    # every ticker the broker actually holds, and every shadow-vs-live
    # comparison silently returns zero.
    persisted_ticker_sleeves = load_latest_ticker_sleeve_map(
        snapshot_dir=ticker_sleeve_snapshot_dir,
        before_date=asof_date or trade_date,
    )

    payload = build_live_regime_review(
        trade_date=trade_date,
        asof_date=asof_date,
        regime_summary=regime_summary,
        regime_strengths=regime_strengths,
        drift_flags=drift_flags,
        sleeve_outputs=sleeve_outputs,
        final_target_allocations=final_target_allocations,
        broker_positions=broker_positions,
        broker_equity=broker_equity,
        returns_by_ticker=returns_by_ticker,
        alpha_attribution=alpha_attribution,
        signal_snapshot_path=signal_snapshot_path,
        objective_contract_path=objective_contract_path,
        persisted_ticker_sleeves=persisted_ticker_sleeves,
    )

    # Persist today's ticker -> sleeve map for tomorrow's run. We compute
    # it fresh from today's sleeve_outputs, since those are what will be
    # executed and therefore what should classify tomorrow's positions.
    todays_ticker_sleeves = build_ticker_sleeve_target_weights(
        sleeve_outputs,
        final_target_allocations,
    )
    try:
        persist_ticker_sleeve_map(
            snapshot_dir=ticker_sleeve_snapshot_dir,
            asof_date=asof_date or trade_date,
            ticker_to_sleeve_weights=todays_ticker_sleeves,
        )
    except Exception:  # pragma: no cover - persistence failure is non-fatal
        pass
    markdown = build_live_regime_review_markdown(payload)

    run_root_path = Path(run_root)
    out_dir = Path(output_dir)
    run_root_path.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    run_json_path = run_root_path / "live_regime_review.json"
    dated_json_path = out_dir / f"live_regime_review_{trade_date}.json"
    dated_md_path = out_dir / f"live_regime_review_{trade_date}.md"
    latest_json_path = out_dir / "live_regime_review_latest.json"

    run_json_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    dated_json_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    dated_md_path.write_text(markdown, encoding="utf-8")
    latest_json_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    payload["artifact_paths"] = {
        "run_json": str(run_json_path),
        "dated_json": str(dated_json_path),
        "dated_markdown": str(dated_md_path),
        "latest_json": str(latest_json_path),
    }
    return payload
