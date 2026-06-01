from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from research.attribution.position_pnl import (
    STRATEGY_FILE_NAMES,
    _read_json,
    _safe_float,
    _symbol,
    _weight_from_holding,
    _write_json,
)


SCHEMA_VERSION = "decision_attribution_phase_b_v1"
SIGNAL_FIELDS = (
    "momentum_score",
    "momentum_rank",
    "estimated_holding_period_days",
)


def _safe_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None or value == "":
        return None
    text = str(value).strip().lower()
    if text in {"true", "1", "yes"}:
        return True
    if text in {"false", "0", "no"}:
        return False
    return None


def _decision_rank(row: dict[str, Any]) -> float | None:
    for key in ("momentum_rank", "rank", "selection_rank"):
        value = _safe_float(row.get(key))
        if value is not None:
            return value
    return None


def _signal_snapshot(row: dict[str, Any], rank_row: dict[str, Any] | None = None) -> dict[str, Any]:
    source = dict(row)
    if rank_row:
        source.update(rank_row)
    snapshot: dict[str, Any] = {}
    for key in SIGNAL_FIELDS:
        value = _safe_float(source.get(key))
        if value is not None:
            snapshot[key] = value
    selected = _safe_bool(source.get("is_selected"))
    if selected is not None:
        snapshot["is_selected"] = selected
    sector = source.get("sector")
    if sector:
        snapshot["sector"] = str(sector)
    return snapshot


def _normalize_decision_rows(
    *,
    strategy: str,
    payload: dict[str, Any],
    source_artifact: str,
) -> list[dict[str, Any]]:
    rank_by_symbol: dict[str, dict[str, Any]] = {}
    rank_table = payload.get("rank_table") or []
    if isinstance(rank_table, list):
        for row in rank_table:
            if not isinstance(row, dict):
                continue
            symbol = _symbol(row.get("ticker") or row.get("symbol"))
            if symbol:
                rank_by_symbol[symbol] = row

    rows: list[dict[str, Any]] = []
    holdings = payload.get("holdings") or []
    if isinstance(holdings, list):
        for row in holdings:
            if not isinstance(row, dict):
                continue
            symbol = _symbol(row.get("ticker") or row.get("symbol"))
            if not symbol or symbol == "CASH":
                continue
            rank_row = rank_by_symbol.get(symbol)
            snapshot = _signal_snapshot(row, rank_row)
            rows.append(
                {
                    "date": payload.get("trade_date") or payload.get("effective_trade_date"),
                    "strategy": strategy,
                    "symbol": symbol,
                    "rank": _decision_rank(rank_row or row),
                    "weight": _weight_from_holding(row),
                    "signal_snapshot": snapshot,
                    "source_artifacts": [source_artifact],
                }
            )

    if not rows and isinstance(payload.get("target_weights"), dict):
        for symbol, weight in sorted(payload["target_weights"].items()):
            symbol_norm = _symbol(symbol)
            if not symbol_norm or symbol_norm == "CASH":
                continue
            rank_row = rank_by_symbol.get(symbol_norm)
            rows.append(
                {
                    "date": payload.get("trade_date") or payload.get("effective_trade_date"),
                    "strategy": strategy,
                    "symbol": symbol_norm,
                    "rank": _decision_rank(rank_row or {}),
                    "weight": _safe_float(weight),
                    "signal_snapshot": _signal_snapshot({}, rank_row),
                    "source_artifacts": [source_artifact],
                }
            )
    return rows


def load_decision_snapshots(repo_root: Path, trade_date: str) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    rows: list[dict[str, Any]] = []
    sources: list[str] = []
    reason_codes: list[str] = []
    seen: set[str] = set()

    shadow_dir = repo_root / "outputs" / "shadow_candidates" / trade_date
    if shadow_dir.exists():
        for path in sorted(shadow_dir.iterdir(), key=lambda item: item.name):
            if path.name not in STRATEGY_FILE_NAMES or not path.is_file():
                continue
            payload = _read_json(path)
            strategy = str(payload.get("strategy_slug") or path.stem)
            rows.extend(
                _normalize_decision_rows(
                    strategy=strategy,
                    payload=payload,
                    source_artifact=str(path),
                )
            )
            seen.add(strategy)
            sources.append(str(path))

    portfolio_path = repo_root / "outputs" / "portfolio_history" / trade_date / "holdings_snapshot.json"
    if portfolio_path.exists():
        payload = _read_json(portfolio_path)
        strategies = payload.get("strategies") or {}
        if isinstance(strategies, dict):
            for strategy, strategy_payload in sorted(strategies.items()):
                if strategy in seen or not isinstance(strategy_payload, dict):
                    continue
                strategy_payload = dict(strategy_payload)
                strategy_payload.setdefault("trade_date", payload.get("trade_date") or trade_date)
                rows.extend(
                    _normalize_decision_rows(
                        strategy=str(strategy),
                        payload=strategy_payload,
                        source_artifact=str(portfolio_path),
                    )
                )
                seen.add(str(strategy))
            sources.append(str(portfolio_path))

    if not sources:
        reason_codes.append("decision_source_missing")
    if sources and not rows:
        reason_codes.append("no_decisions")
    return rows, sorted(set(sources)), reason_codes


def _load_position_attribution(repo_root: Path, trade_date: str) -> tuple[dict[tuple[str, str], dict[str, Any]], list[str], list[str]]:
    path = repo_root / "outputs" / "attribution" / trade_date / "position_attribution.json"
    if not path.exists():
        return {}, [], ["attribution_source_missing"]
    payload = _read_json(path)
    positions = payload.get("positions") or []
    if not isinstance(positions, list):
        return {}, [str(path)], ["attribution_positions_invalid"]
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for row in positions:
        if not isinstance(row, dict):
            continue
        strategy = str(row.get("strategy") or "")
        symbol = _symbol(row.get("symbol"))
        if strategy and symbol:
            record = dict(row)
            record["source_artifacts"] = sorted(set(list(row.get("source_artifacts") or []) + [str(path)]))
            by_key[(strategy, symbol)] = record
    reason_codes = [] if by_key else ["attribution_positions_empty"]
    return by_key, [str(path)], reason_codes


def _record_confidence(reason_codes: list[str]) -> str:
    if reason_codes == ["ok"]:
        return "MEDIUM"
    return "LOW"


def _build_decision_records(
    *,
    trade_date: str,
    decisions: list[dict[str, Any]],
    attribution: dict[tuple[str, str], dict[str, Any]],
    attribution_reason_codes: list[str],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for decision in decisions:
        strategy = str(decision.get("strategy") or "")
        symbol = _symbol(decision.get("symbol"))
        attr = attribution.get((strategy, symbol))
        signal_snapshot = dict(decision.get("signal_snapshot") or {})
        reason_codes: list[str] = []
        if not signal_snapshot:
            reason_codes.append("signal_snapshot_missing")
        if decision.get("rank") is None:
            reason_codes.append("missing_rank")
        if attr is None:
            reason_codes.extend(attribution_reason_codes or ["missing_realized_outcome"])
            reason_codes.append("missing_realized_outcome")
            realized_return = None
            pnl_contribution = None
            attr_sources: list[str] = []
        else:
            realized_return = _safe_float(attr.get("return_pct"))
            pnl_contribution = _safe_float(attr.get("pnl_contribution_pct"))
            attr_sources = list(attr.get("source_artifacts") or [])
            if realized_return is None:
                reason_codes.append("missing_realized_return")
            if pnl_contribution is None:
                reason_codes.append("missing_pnl_contribution")
            reason_codes.extend([code for code in list(attr.get("reason_codes") or []) if code != "ok"])
        reason_codes = sorted(set(reason_codes)) if reason_codes else ["ok"]
        records.append(
            {
                "date": trade_date,
                "strategy": strategy,
                "symbol": symbol,
                "rank": decision.get("rank"),
                "weight": _safe_float(decision.get("weight")),
                "signal_snapshot": signal_snapshot,
                "realized_return": realized_return,
                "pnl_contribution": pnl_contribution,
                "confidence": _record_confidence(reason_codes),
                "reason_codes": reason_codes,
                "source_artifacts": sorted(set(list(decision.get("source_artifacts") or []) + attr_sources)),
            }
        )
    return sorted(
        records,
        key=lambda row: (
            str(row.get("strategy") or ""),
            row.get("rank") is None,
            float(row.get("rank") or 0.0),
            str(row.get("symbol") or ""),
        ),
    )


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 10)


def _hit_rate(values: list[float]) -> float | None:
    if not values:
        return None
    return round(sum(1 for value in values if value > 0.0) / len(values), 10)


def _summary_confidence(observations: int, reason_codes: list[str]) -> str:
    if observations <= 0:
        return "LOW"
    if reason_codes == ["ok"] and observations >= 3:
        return "MEDIUM"
    return "LOW"


def _build_signal_outcome_summary(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    signal_names = sorted({
        key
        for record in records
        for key, value in dict(record.get("signal_snapshot") or {}).items()
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    })
    summaries: list[dict[str, Any]] = []
    for signal_name in signal_names:
        values: list[float] = []
        returns: list[float] = []
        missing_outcomes = 0
        for record in records:
            value = _safe_float(dict(record.get("signal_snapshot") or {}).get(signal_name))
            if value is None:
                continue
            realized_return = _safe_float(record.get("realized_return"))
            if realized_return is None:
                missing_outcomes += 1
                continue
            values.append(value)
            returns.append(realized_return)
        reason_codes: list[str] = []
        if not returns:
            reason_codes.append("no_signal_outcomes")
        if missing_outcomes:
            reason_codes.append("missing_realized_outcomes")
        if len(returns) < 3:
            reason_codes.append("insufficient_observations")
        if not reason_codes:
            reason_codes = ["ok"]
        summaries.append(
            {
                "signal_name": signal_name,
                "observations": len(returns),
                "average_score": _mean(values),
                "average_realized_return": _mean(returns),
                "hit_rate": _hit_rate(returns),
                "confidence": _summary_confidence(len(returns), reason_codes),
                "reason_codes": sorted(set(reason_codes)) if reason_codes != ["ok"] else ["ok"],
            }
        )
    return summaries


def _decision_stub(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    return {
        "strategy": row.get("strategy"),
        "symbol": row.get("symbol"),
        "rank": row.get("rank"),
        "weight": row.get("weight"),
        "realized_return": row.get("realized_return"),
        "pnl_contribution": row.get("pnl_contribution"),
        "confidence": row.get("confidence"),
        "reason_codes": row.get("reason_codes"),
    }


def _build_strategy_decision_summary(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for strategy in sorted({str(row.get("strategy") or "") for row in records}):
        group = [row for row in records if row.get("strategy") == strategy]
        returns = [_safe_float(row.get("realized_return")) for row in group]
        returns = [value for value in returns if value is not None]
        pnls = [_safe_float(row.get("pnl_contribution")) for row in group]
        pnls = [value for value in pnls if value is not None]
        complete = [
            row for row in group
            if row.get("realized_return") is not None and row.get("pnl_contribution") is not None
        ]
        top = sorted(
            complete,
            key=lambda row: (-float(row.get("pnl_contribution") or 0.0), str(row.get("symbol") or "")),
        )[0] if complete else None
        worst = sorted(
            complete,
            key=lambda row: (float(row.get("pnl_contribution") or 0.0), str(row.get("symbol") or "")),
        )[0] if complete else None
        reason_codes = sorted({
            code
            for row in group
            for code in list(row.get("reason_codes") or [])
            if code != "ok"
        })
        if not group:
            reason_codes.append("no_decisions")
        if not complete:
            reason_codes.append("no_realized_outcomes")
        if not reason_codes:
            reason_codes = ["ok"]
        summaries.append(
            {
                "strategy": strategy,
                "decisions_analyzed": len(group),
                "average_realized_return": _mean(returns),
                "average_pnl_contribution": _mean(pnls),
                "hit_rate": _hit_rate(returns),
                "top_decision": _decision_stub(top),
                "worst_decision": _decision_stub(worst),
                "confidence": _summary_confidence(len(complete), reason_codes),
                "reason_codes": sorted(set(reason_codes)) if reason_codes != ["ok"] else ["ok"],
            }
        )
    return summaries


def build_decision_attribution(
    *,
    trade_date: str,
    repo_root: Path | str = Path("."),
    output_root: Path | str | None = None,
) -> dict[str, Any]:
    repo = Path(repo_root)
    decisions, decision_sources, decision_reason_codes = load_decision_snapshots(repo, trade_date)
    attribution, attribution_sources, attribution_reason_codes = _load_position_attribution(repo, trade_date)
    records = _build_decision_records(
        trade_date=trade_date,
        decisions=decisions,
        attribution=attribution,
        attribution_reason_codes=attribution_reason_codes,
    )
    if not records and "no_decisions" not in decision_reason_codes and "decision_source_missing" not in decision_reason_codes:
        decision_reason_codes.append("no_decisions")

    signal_summary = _build_signal_outcome_summary(records)
    strategy_summary = _build_strategy_decision_summary(records)
    all_record_reasons = sorted({
        code
        for row in records
        for code in list(row.get("reason_codes") or [])
        if code != "ok"
    })
    summary_reason_codes = sorted(set(decision_reason_codes + attribution_reason_codes + all_record_reasons))
    if not summary_reason_codes:
        summary_reason_codes = ["ok"]

    out_root = Path(output_root) if output_root is not None else repo / "outputs" / "decision_attribution"
    out_dir = out_root / trade_date
    payload = {
        "decision_attribution": {
            "schema_version": SCHEMA_VERSION,
            "date": trade_date,
            "decisions": records,
            "reason_codes": summary_reason_codes,
            "source_artifacts": sorted(set(decision_sources + attribution_sources)),
        },
        "signal_outcome_summary": {
            "schema_version": SCHEMA_VERSION,
            "date": trade_date,
            "signals": signal_summary,
            "reason_codes": summary_reason_codes,
            "source_artifacts": sorted(set(decision_sources + attribution_sources)),
        },
        "strategy_decision_summary": {
            "schema_version": SCHEMA_VERSION,
            "date": trade_date,
            "strategies": strategy_summary,
            "reason_codes": summary_reason_codes,
            "source_artifacts": sorted(set(decision_sources + attribution_sources)),
        },
    }
    _write_json(out_dir / "decision_attribution.json", payload["decision_attribution"])
    _write_json(out_dir / "signal_outcome_summary.json", payload["signal_outcome_summary"])
    _write_json(out_dir / "strategy_decision_summary.json", payload["strategy_decision_summary"])
    payload["artifact_paths"] = {
        "decision_attribution": str(out_dir / "decision_attribution.json"),
        "signal_outcome_summary": str(out_dir / "signal_outcome_summary.json"),
        "strategy_decision_summary": str(out_dir / "strategy_decision_summary.json"),
    }
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build deterministic decision attribution artifacts.")
    parser.add_argument("--date", required=True, help="Attribution date in YYYY-MM-DD format.")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--output-root", default=None)
    args = parser.parse_args(argv)
    result = build_decision_attribution(
        trade_date=args.date,
        repo_root=Path(args.repo_root),
        output_root=Path(args.output_root) if args.output_root else None,
    )
    print(json.dumps(result["strategy_decision_summary"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
