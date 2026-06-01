from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = "position_pnl_attribution_phase_a_v1"
DEFAULT_PRICE_PATHS = (
    Path("outputs/research/flow_detection_v1/price_panel.parquet"),
    Path("alpha_stack_cache/csv_export/prices_matrix.csv"),
    Path("data/alpha_stack_cache/csv_export/prices_matrix.csv"),
)
STRATEGY_FILE_NAMES = {
    "caerus_polaris.json",
    "caerus_orion.json",
    "caerus_lyra.json",
}


@dataclass(frozen=True)
class PriceLoadResult:
    points: dict[str, dict[str, Any]]
    source: str | None
    source_max_date: str | None
    attribution_date: str
    is_fresh: bool
    freshness_lag_days: int | None
    reason_codes: list[str]

    def metadata(self) -> dict[str, Any]:
        return {
            "price_source": self.source,
            "price_source_max_date": self.source_max_date,
            "attribution_date": self.attribution_date,
            "is_price_source_fresh": bool(self.is_fresh),
            "freshness_lag_days": self.freshness_lag_days,
            "freshness_reason_codes": list(self.reason_codes),
        }


def _read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"json_payload_not_object:{path}")
    return payload


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


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


def _symbol(value: Any) -> str:
    return str(value or "").strip().upper()


def _date(value: str) -> dt.date:
    return dt.date.fromisoformat(str(value))


def _confidence(complete: int, total: int) -> str:
    if total <= 0:
        return "LOW"
    if complete == total:
        return "HIGH"
    if complete > 0:
        return "MEDIUM"
    return "LOW"


def _record_confidence(reason_codes: list[str]) -> str:
    if reason_codes == ["ok"]:
        return "HIGH"
    if any(code in reason_codes for code in ("missing_start_price", "missing_end_price")):
        return "MEDIUM"
    return "LOW"


def _find_price_path(repo_root: Path, override: Path | None = None) -> Path | None:
    if override is not None:
        path = override if override.is_absolute() else repo_root / override
        return path if path.exists() else None
    for rel in DEFAULT_PRICE_PATHS:
        path = repo_root / rel
        if path.exists():
            return path
    return None


def _freshness_metadata(
    *,
    trade_date: str,
    source: str | None,
    source_max_date: str | None,
    reason_codes: list[str] | None = None,
) -> dict[str, Any]:
    reasons = list(reason_codes or [])
    lag_days: int | None = None
    fresh = False
    if source_max_date:
        try:
            lag_days = max(0, int((_date(trade_date) - _date(source_max_date)).days))
            fresh = _date(source_max_date) >= _date(trade_date)
        except Exception:
            reasons.append("price_source_max_date_invalid")
    if source is None and "price_source_missing" not in reasons:
        reasons.append("price_source_missing")
    if source and source_max_date is None and "price_source_empty" not in reasons:
        reasons.append("price_source_empty")
    if source and source_max_date and not fresh:
        reasons.append("price_source_stale")
    if fresh and not reasons:
        reasons.append("ok")
    return {
        "price_source": source,
        "price_source_max_date": source_max_date,
        "attribution_date": trade_date,
        "is_price_source_fresh": fresh,
        "freshness_lag_days": lag_days,
        "freshness_reason_codes": sorted(set(reasons)) if reasons else ["ok"],
    }


def _empty_price_result(
    *,
    trade_date: str,
    wanted: list[str],
    source: str | None,
    source_max_date: str | None,
    reason_codes: list[str],
) -> PriceLoadResult:
    meta = _freshness_metadata(
        trade_date=trade_date,
        source=source,
        source_max_date=source_max_date,
        reason_codes=reason_codes,
    )
    return PriceLoadResult(
        points={symbol: {"start_price": None, "end_price": None} for symbol in wanted},
        source=meta["price_source"],
        source_max_date=meta["price_source_max_date"],
        attribution_date=meta["attribution_date"],
        is_fresh=bool(meta["is_price_source_fresh"]),
        freshness_lag_days=meta["freshness_lag_days"],
        reason_codes=list(meta["freshness_reason_codes"]),
    )


def load_price_points(
    *,
    trade_date: str,
    symbols: Iterable[str],
    repo_root: Path,
    price_path: Path | None = None,
) -> PriceLoadResult:
    resolved_price_path = _find_price_path(repo_root, price_path)
    wanted = sorted({_symbol(symbol) for symbol in symbols if _symbol(symbol)})
    if resolved_price_path is None:
        return _empty_price_result(
            trade_date=trade_date,
            wanted=wanted,
            source=None,
            source_max_date=None,
            reason_codes=["price_source_missing"],
        )
    if resolved_price_path.suffix.lower() == ".parquet":
        return _load_price_points_parquet(
            trade_date=trade_date,
            symbols=wanted,
            price_path=resolved_price_path,
        )
    return _load_price_points_csv(
        trade_date=trade_date,
        symbols=wanted,
        price_path=resolved_price_path,
    )


def _load_price_points_csv(
    *,
    trade_date: str,
    symbols: list[str],
    price_path: Path,
) -> PriceLoadResult:
    wanted = list(symbols)

    target = _date(trade_date)
    rows: list[dict[str, str]] = []
    source_max_date: str | None = None
    with price_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or "Date" not in reader.fieldnames:
            return _empty_price_result(
                trade_date=trade_date,
                wanted=wanted,
                source=str(price_path),
                source_max_date=None,
                reason_codes=["price_source_missing_date_column"],
            )
        for row in reader:
            raw_date = str(row.get("Date") or row.get("date") or "").strip()
            if not raw_date:
                continue
            try:
                row_date = _date(raw_date)
            except Exception:
                continue
            if source_max_date is None or row_date > _date(source_max_date):
                source_max_date = row_date.isoformat()
            if row_date <= target:
                rows.append(row)

    start_row: dict[str, str] | None = None
    end_row: dict[str, str] | None = None
    for row in rows:
        row_date = _date(str(row.get("Date") or row.get("date")))
        if row_date < target:
            start_row = row
        elif row_date == target:
            end_row = row

    out: dict[str, dict[str, Any]] = {}
    for symbol in wanted:
        out[symbol] = {
            "start_price": _safe_float((start_row or {}).get(symbol)),
            "end_price": _safe_float((end_row or {}).get(symbol)),
        }
    meta = _freshness_metadata(
        trade_date=trade_date,
        source=str(price_path),
        source_max_date=source_max_date,
    )
    return PriceLoadResult(
        points=out,
        source=meta["price_source"],
        source_max_date=meta["price_source_max_date"],
        attribution_date=meta["attribution_date"],
        is_fresh=bool(meta["is_price_source_fresh"]),
        freshness_lag_days=meta["freshness_lag_days"],
        reason_codes=list(meta["freshness_reason_codes"]),
    )


def _load_price_points_parquet(
    *,
    trade_date: str,
    symbols: list[str],
    price_path: Path,
) -> PriceLoadResult:
    wanted = list(symbols)
    try:
        import pandas as pd
    except Exception:
        return _empty_price_result(
            trade_date=trade_date,
            wanted=wanted,
            source=str(price_path),
            source_max_date=None,
            reason_codes=["price_source_parquet_reader_unavailable"],
        )
    try:
        frame = pd.read_parquet(price_path, columns=["date", "ticker", "close"])
    except Exception as exc:
        return _empty_price_result(
            trade_date=trade_date,
            wanted=wanted,
            source=str(price_path),
            source_max_date=None,
            reason_codes=[f"price_source_read_failed:{type(exc).__name__}"],
        )
    if frame.empty or not {"date", "ticker", "close"}.issubset(set(frame.columns)):
        return _empty_price_result(
            trade_date=trade_date,
            wanted=wanted,
            source=str(price_path),
            source_max_date=None,
            reason_codes=["price_source_empty"],
        )

    frame = frame.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.date
    frame["ticker"] = frame["ticker"].astype(str).str.upper().str.strip()
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    frame = frame.dropna(subset=["date", "ticker"])
    source_max_date = frame["date"].max().isoformat() if not frame.empty else None
    target = _date(trade_date)
    frame = frame[frame["ticker"].isin(wanted) & (frame["date"] <= target)].copy()

    out: dict[str, dict[str, Any]] = {}
    for symbol in wanted:
        symbol_frame = frame[frame["ticker"] == symbol].sort_values("date")
        start_rows = symbol_frame[symbol_frame["date"] < target]
        end_rows = symbol_frame[symbol_frame["date"] == target]
        start_price = None
        end_price = None
        if not start_rows.empty:
            start_price = _safe_float(start_rows.iloc[-1]["close"])
        if not end_rows.empty:
            end_price = _safe_float(end_rows.iloc[-1]["close"])
        out[symbol] = {"start_price": start_price, "end_price": end_price}

    meta = _freshness_metadata(
        trade_date=trade_date,
        source=str(price_path),
        source_max_date=source_max_date,
    )
    return PriceLoadResult(
        points=out,
        source=meta["price_source"],
        source_max_date=meta["price_source_max_date"],
        attribution_date=meta["attribution_date"],
        is_fresh=bool(meta["is_price_source_fresh"]),
        freshness_lag_days=meta["freshness_lag_days"],
        reason_codes=list(meta["freshness_reason_codes"]),
    )


def _weight_from_holding(row: dict[str, Any]) -> float | None:
    for key in ("target_weight", "weight", "weight_start", "allocation_weight"):
        value = _safe_float(row.get(key))
        if value is not None:
            return value
    return None


def _normalize_strategy_holdings(
    *,
    strategy: str,
    strategy_payload: dict[str, Any],
    source_artifact: str,
) -> list[dict[str, Any]]:
    holdings = strategy_payload.get("holdings") or []
    if not isinstance(holdings, list):
        holdings = []
    out: list[dict[str, Any]] = []
    for row in holdings:
        if not isinstance(row, dict):
            continue
        symbol = _symbol(row.get("ticker") or row.get("symbol"))
        if not symbol or symbol == "CASH":
            continue
        out.append(
            {
                "strategy": strategy,
                "symbol": symbol,
                "weight": _weight_from_holding(row),
                "source_artifacts": [source_artifact],
            }
        )
    if not out and isinstance(strategy_payload.get("target_weights"), dict):
        for symbol, weight in sorted(strategy_payload["target_weights"].items()):
            symbol_norm = _symbol(symbol)
            if not symbol_norm or symbol_norm == "CASH":
                continue
            out.append(
                {
                    "strategy": strategy,
                    "symbol": symbol_norm,
                    "weight": _safe_float(weight),
                    "source_artifacts": [source_artifact],
                }
            )
    return out


def load_strategy_holdings(repo_root: Path, trade_date: str) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    rows: list[dict[str, Any]] = []
    sources: list[str] = []
    reason_codes: list[str] = []
    seen: set[str] = set()

    portfolio_path = repo_root / "outputs" / "portfolio_history" / trade_date / "holdings_snapshot.json"
    if portfolio_path.exists():
        payload = _read_json(portfolio_path)
        strategies = payload.get("strategies") or {}
        if isinstance(strategies, dict):
            for strategy, strategy_payload in sorted(strategies.items()):
                if not isinstance(strategy_payload, dict):
                    continue
                rows.extend(
                    _normalize_strategy_holdings(
                        strategy=str(strategy),
                        strategy_payload=strategy_payload,
                        source_artifact=str(portfolio_path),
                    )
                )
                seen.add(str(strategy))
            sources.append(str(portfolio_path))

    shadow_dir = repo_root / "outputs" / "shadow_candidates" / trade_date
    if shadow_dir.exists():
        for path in sorted(shadow_dir.iterdir(), key=lambda item: item.name):
            if path.name not in STRATEGY_FILE_NAMES or not path.is_file():
                continue
            payload = _read_json(path)
            strategy = str(payload.get("strategy_slug") or path.stem)
            if strategy in seen:
                continue
            rows.extend(
                _normalize_strategy_holdings(
                    strategy=strategy,
                    strategy_payload=payload,
                    source_artifact=str(path),
                )
            )
            seen.add(strategy)
            sources.append(str(path))

    if not sources:
        reason_codes.append("holdings_source_missing")
    if sources and not rows:
        reason_codes.append("no_holdings")
    return rows, sorted(set(sources)), reason_codes


def _build_position_records(
    *,
    trade_date: str,
    holdings: list[dict[str, Any]],
    price_points: dict[str, dict[str, Any]],
    price_source: str | None,
    price_reason_codes: list[str],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for row in holdings:
        symbol = _symbol(row.get("symbol"))
        weight = _safe_float(row.get("weight"))
        prices = price_points.get(symbol, {})
        start_price = _safe_float(prices.get("start_price"))
        end_price = _safe_float(prices.get("end_price"))
        reason_codes: list[str] = []
        if weight is None:
            reason_codes.append("missing_weight")
        if start_price is None:
            reason_codes.append("missing_start_price")
        if end_price is None:
            reason_codes.append("missing_end_price")
        reason_codes.extend(price_reason_codes)
        reason_codes = sorted(set(reason_codes)) if reason_codes else ["ok"]

        return_pct = None
        pnl_contribution_pct = None
        if weight is not None and start_price not in (None, 0.0) and end_price is not None:
            return_pct = round((end_price / start_price) - 1.0, 10)
            pnl_contribution_pct = round(weight * return_pct, 10)

        data_completeness = "COMPLETE" if reason_codes == ["ok"] else "PARTIAL"
        source_artifacts = list(row.get("source_artifacts") or [])
        if price_source:
            source_artifacts.append(price_source)
        records.append(
            {
                "date": trade_date,
                "strategy": str(row.get("strategy") or ""),
                "symbol": symbol,
                "weight": weight,
                "start_price": start_price,
                "end_price": end_price,
                "return_pct": return_pct,
                "pnl_contribution_pct": pnl_contribution_pct,
                "rank": None,
                "data_completeness": data_completeness,
                "confidence": _record_confidence(reason_codes),
                "reason_codes": reason_codes,
                "source_artifacts": sorted(set(source_artifacts)),
            }
        )
    return records


def _rank_records(records: list[dict[str, Any]]) -> None:
    by_strategy: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        by_strategy.setdefault(str(record.get("strategy") or ""), []).append(record)
    for strategy, group in by_strategy.items():
        del strategy
        ranked = sorted(
            group,
            key=lambda row: (
                row.get("pnl_contribution_pct") is None,
                -float(row.get("pnl_contribution_pct") or 0.0),
                str(row.get("symbol") or ""),
            ),
        )
        for idx, record in enumerate(ranked, start=1):
            record["rank"] = idx


def _top_by_strategy(records: list[dict[str, Any]], *, reverse: bool) -> list[dict[str, Any]]:
    complete = [row for row in records if row.get("pnl_contribution_pct") is not None]
    return sorted(
        complete,
        key=lambda row: (
            str(row.get("strategy") or ""),
            -float(row.get("pnl_contribution_pct") or 0.0) if reverse else float(row.get("pnl_contribution_pct") or 0.0),
            str(row.get("symbol") or ""),
        ),
    )


def _strategy_extremes(records: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    contributors: dict[str, Any] = {}
    detractors: dict[str, Any] = {}
    for strategy in sorted({str(row.get("strategy") or "") for row in records}):
        complete = [
            row for row in records
            if row.get("strategy") == strategy and row.get("pnl_contribution_pct") is not None
        ]
        if not complete:
            contributors[strategy] = None
            detractors[strategy] = None
            continue
        contributors[strategy] = sorted(
            complete,
            key=lambda row: (-float(row["pnl_contribution_pct"]), str(row["symbol"])),
        )[0]
        detractors[strategy] = sorted(
            complete,
            key=lambda row: (float(row["pnl_contribution_pct"]), str(row["symbol"])),
        )[0]
    return contributors, detractors


def build_position_attribution(
    *,
    trade_date: str,
    repo_root: Path | str = Path("."),
    price_path: Path | None = None,
    output_root: Path | str | None = None,
) -> dict[str, Any]:
    repo = Path(repo_root)
    holdings, holding_sources, holding_reason_codes = load_strategy_holdings(repo, trade_date)
    symbols = [str(row.get("symbol") or "") for row in holdings]
    price_load = load_price_points(
        trade_date=trade_date,
        symbols=symbols,
        repo_root=repo,
        price_path=price_path,
    )
    records = _build_position_records(
        trade_date=trade_date,
        holdings=holdings,
        price_points=price_load.points,
        price_source=price_load.source,
        price_reason_codes=[
            code for code in price_load.reason_codes if code != "ok"
        ],
    )
    _rank_records(records)
    records = sorted(
        records,
        key=lambda row: (
            str(row.get("strategy") or ""),
            row.get("rank") if row.get("rank") is not None else 999999,
            str(row.get("symbol") or ""),
        ),
    )
    top_contributors = _top_by_strategy(records, reverse=True)
    top_detractors = _top_by_strategy(records, reverse=False)
    complete_count = sum(1 for row in records if row.get("data_completeness") == "COMPLETE")
    missing_count = len(records) - complete_count
    top_by_strategy, bottom_by_strategy = _strategy_extremes(records)
    source_artifacts = sorted(set(holding_sources + ([price_load.source] if price_load.source else [])))
    position_reason_codes = [
        code
        for row in records
        for code in list(row.get("reason_codes") or [])
        if code != "ok"
    ]
    freshness_reason_codes = [
        code for code in price_load.reason_codes if code != "ok"
    ]
    reason_codes = sorted(set(holding_reason_codes + freshness_reason_codes + position_reason_codes))
    if not records and "no_holdings" not in reason_codes and "holdings_source_missing" not in reason_codes:
        reason_codes.append("no_positions_analyzed")
    if not reason_codes:
        reason_codes = ["ok"]

    summary = {
        "schema_version": SCHEMA_VERSION,
        "date": trade_date,
        "strategies_covered": sorted({str(row.get("strategy") or "") for row in records}),
        "total_positions_analyzed": len(records),
        "positions_with_complete_price_data": complete_count,
        "positions_missing_price_data": missing_count,
        "top_contributor_per_strategy": {
            strategy: _summary_position(row)
            for strategy, row in sorted(top_by_strategy.items())
        },
        "top_detractor_per_strategy": {
            strategy: _summary_position(row)
            for strategy, row in sorted(bottom_by_strategy.items())
        },
        "aggregate_confidence": _confidence(complete_count, len(records)),
        "reason_codes": reason_codes,
        "source_artifacts": source_artifacts,
        **price_load.metadata(),
    }

    payload = {
        "summary": summary,
        "position_attribution": {
            "schema_version": SCHEMA_VERSION,
            "date": trade_date,
            "positions": records,
        },
        "top_contributors": {
            "schema_version": SCHEMA_VERSION,
            "date": trade_date,
            "positions": top_contributors,
        },
        "top_detractors": {
            "schema_version": SCHEMA_VERSION,
            "date": trade_date,
            "positions": top_detractors,
        },
    }

    out_root = Path(output_root) if output_root is not None else repo / "outputs" / "attribution"
    out_dir = out_root / trade_date
    _write_json(out_dir / "attribution_summary.json", summary)
    _write_json(out_dir / "position_attribution.json", payload["position_attribution"])
    _write_json(out_dir / "top_contributors.json", payload["top_contributors"])
    _write_json(out_dir / "top_detractors.json", payload["top_detractors"])
    payload["artifact_paths"] = {
        "attribution_summary": str(out_dir / "attribution_summary.json"),
        "position_attribution": str(out_dir / "position_attribution.json"),
        "top_contributors": str(out_dir / "top_contributors.json"),
        "top_detractors": str(out_dir / "top_detractors.json"),
    }
    return payload


def _summary_position(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    return {
        "strategy": row.get("strategy"),
        "symbol": row.get("symbol"),
        "weight": row.get("weight"),
        "return_pct": row.get("return_pct"),
        "pnl_contribution_pct": row.get("pnl_contribution_pct"),
        "confidence": row.get("confidence"),
        "reason_codes": row.get("reason_codes"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build deterministic position-level PnL attribution artifacts.")
    parser.add_argument("--date", required=True, help="Attribution date in YYYY-MM-DD format.")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--price-path", default=None)
    parser.add_argument("--output-root", default=None)
    args = parser.parse_args(argv)
    result = build_position_attribution(
        trade_date=args.date,
        repo_root=Path(args.repo_root),
        price_path=Path(args.price_path) if args.price_path else None,
        output_root=Path(args.output_root) if args.output_root else None,
    )
    print(json.dumps(result["summary"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
