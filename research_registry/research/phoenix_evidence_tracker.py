from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

import pandas as pd

from research_registry.research.model_quality_common import (
    md_join,
    model_quality_dir,
    normalize_date,
    read_json,
    round_or_none,
    symbol,
    write_json,
    write_text,
)

SCHEMA_VERSION = "caerus_phoenix_evidence_tracker_v1"
DEFAULT_PRICE_PATHS = (
    Path("outputs/research/flow_detection_v1/price_panel.parquet"),
    Path("outputs/research/ma_vol_hypothesis/price_panel.parquet"),
    Path("alpha_stack_cache/csv_export/prices_matrix.csv"),
    Path("data/alpha_stack_cache/csv_export/prices_matrix.csv"),
)


def build_phoenix_evidence_tracker(
    *,
    trade_date: str,
    repo_root: Path | str = Path("."),
    output_root: Path | str | None = None,
    signal_date: str | None = None,
    as_of_date: str | None = None,
    observation_window_days: int = 5,
    price_path: Path | str | None = None,
    write: bool = True,
) -> dict[str, Any]:
    target = normalize_date(trade_date)
    signal = normalize_date(signal_date or target)
    asof = normalize_date(as_of_date or target)
    repo = Path(repo_root)
    phoenix_path = model_quality_dir(repo, signal, output_root) / "phoenix_research.json"
    phoenix = read_json(phoenix_path)
    sector_map = _load_sector_map(repo / "data" / "universe.csv")
    candidates = _candidate_rows(phoenix, sector_map=sector_map)
    realized = _realized_returns(
        repo=repo,
        candidates=candidates,
        signal_date=signal,
        as_of_date=asof,
        observation_window_days=observation_window_days,
        price_path=Path(price_path) if price_path is not None else None,
    )
    reason_codes = set()
    if phoenix is None:
        reason_codes.add("PHOENIX_RESEARCH_ARTIFACT_MISSING")
    else:
        reason_codes.update(code for code in (phoenix.get("reason_codes") or []) if code != "ok")
    reason_codes.update(code for code in realized.get("reason_codes") or [] if code != "ok")
    if not candidates:
        reason_codes.add("NO_PHOENIX_CANDIDATES")
    payload = {
        "schema_version": SCHEMA_VERSION,
        "date": target,
        "signal_date": signal,
        "as_of_date": asof,
        "governance_label": "RESEARCH_ONLY",
        "execution_impact": "NON_EXECUTIONAL",
        "status": "OK" if phoenix is not None else "PARTIAL",
        "no_tuning_confirmation": True,
        "regime": _regime_label(phoenix),
        "phoenix_active": bool((phoenix or {}).get("active")),
        "candidate_count": len(candidates),
        "top_candidates": candidates[:10],
        "candidate_sectors": _candidate_sector_counts(candidates),
        "realized_return_evidence": realized,
        "confidence": _confidence(phoenix=phoenix, realized=realized, candidates=candidates),
        "source_artifacts": {
            "phoenix_research": str(phoenix_path),
            "price_source": realized.get("price_source"),
        },
        "research_limits": [
            "tracker_does_not_change_phoenix_scoring_or_thresholds",
            "realized_return_evidence_is_attached_only_when_observable_as_of_run_date",
            "current_date_forward_returns_are_not_estimated",
        ],
        "reason_codes": sorted(reason_codes) or ["ok"],
    }
    if write:
        out_dir = model_quality_dir(repo, target, output_root)
        write_json(out_dir / "phoenix_evidence_tracker.json", payload)
        write_text(out_dir / "phoenix_evidence_tracker.md", render_markdown(payload))
    return payload


def _candidate_rows(phoenix: dict[str, Any] | None, *, sector_map: dict[str, str]) -> list[dict[str, Any]]:
    candidates = (phoenix or {}).get("target_candidates") or []
    rows = []
    for row in candidates:
        if not isinstance(row, dict):
            continue
        ticker = symbol(row.get("ticker") or row.get("symbol"))
        if not ticker:
            continue
        rows.append(
            {
                "ticker": ticker,
                "sector": str(row.get("sector") or sector_map.get(ticker) or "UNKNOWN"),
                "target_weight": round_or_none(row.get("target_weight"), 10),
                "phoenix_score": round_or_none(row.get("phoenix_score"), 10),
                "reason_codes": sorted(row.get("reason_codes") or ["ok"]),
            }
        )
    return sorted(rows, key=lambda item: (-(item.get("phoenix_score") or 0.0), item["ticker"]))


def _realized_returns(
    *,
    repo: Path,
    candidates: list[dict[str, Any]],
    signal_date: str,
    as_of_date: str,
    observation_window_days: int,
    price_path: Path | None,
) -> dict[str, Any]:
    signal_ts = pd.Timestamp(signal_date)
    asof_ts = pd.Timestamp(as_of_date)
    observation_ts = signal_ts + pd.Timedelta(days=int(observation_window_days))
    if not candidates:
        return {
            "available": False,
            "observation_window_days": int(observation_window_days),
            "observation_date_required": str(observation_ts.date()),
            "returns": [],
            "price_source": None,
            "reason_codes": ["NO_PHOENIX_CANDIDATES"],
        }
    if asof_ts < observation_ts:
        return {
            "available": False,
            "observation_window_days": int(observation_window_days),
            "observation_date_required": str(observation_ts.date()),
            "returns": [],
            "price_source": None,
            "reason_codes": ["FORWARD_RETURN_NOT_YET_OBSERVABLE"],
        }
    symbols = [row["ticker"] for row in candidates]
    panel, source, price_reasons = _load_price_panel(repo=repo, symbols=symbols, end_date=as_of_date, price_path=price_path)
    if panel.empty:
        return {
            "available": False,
            "observation_window_days": int(observation_window_days),
            "observation_date_required": str(observation_ts.date()),
            "returns": [],
            "price_source": source,
            "reason_codes": price_reasons or ["PRICE_DATA_MISSING"],
        }
    returns = []
    reasons = set(price_reasons)
    for ticker in sorted(symbols):
        rows = panel[panel["ticker"] == ticker].sort_values("date")
        start_rows = rows[(rows["date"] >= signal_ts) & (rows["date"] <= asof_ts)]
        end_rows = rows[(rows["date"] >= observation_ts) & (rows["date"] <= asof_ts)]
        if start_rows.empty or end_rows.empty:
            reasons.add("PRICE_WINDOW_MISSING")
            returns.append(
                {
                    "ticker": ticker,
                    "start_date": None,
                    "end_date": None,
                    "start_close": None,
                    "end_close": None,
                    "realized_return": None,
                    "reason_codes": ["PRICE_WINDOW_MISSING"],
                }
            )
            continue
        start = start_rows.iloc[0]
        end = end_rows.iloc[0]
        start_close = float(start["close"])
        end_close = float(end["close"])
        returns.append(
            {
                "ticker": ticker,
                "start_date": str(pd.Timestamp(start["date"]).date()),
                "end_date": str(pd.Timestamp(end["date"]).date()),
                "start_close": round(start_close, 10),
                "end_close": round(end_close, 10),
                "realized_return": round((end_close / start_close) - 1.0, 10) if start_close else None,
                "reason_codes": ["ok"],
            }
        )
    available = bool(returns) and all(row.get("realized_return") is not None for row in returns)
    return {
        "available": available,
        "observation_window_days": int(observation_window_days),
        "observation_date_required": str(observation_ts.date()),
        "returns": sorted(returns, key=lambda row: row["ticker"]),
        "price_source": source,
        "reason_codes": sorted(reasons) or ["ok"],
    }


def _load_price_panel(*, repo: Path, symbols: list[str], end_date: str, price_path: Path | None) -> tuple[pd.DataFrame, str | None, list[str]]:
    paths = [price_path] if price_path is not None else [repo / rel for rel in DEFAULT_PRICE_PATHS]
    for path in paths:
        if path is None:
            continue
        candidate = path if path.is_absolute() else repo / path
        if not candidate.exists():
            continue
        try:
            if candidate.suffix.lower() == ".parquet":
                frame = pd.read_parquet(candidate)
            else:
                frame = _read_price_csv(candidate)
            if frame.empty:
                continue
            frame = _standardize_price_frame(frame)
            frame = frame[(frame["ticker"].isin(symbols)) & (frame["date"] <= pd.Timestamp(end_date))]
            return frame.sort_values(["ticker", "date"]).reset_index(drop=True), str(candidate), ["ok"]
        except Exception:
            return pd.DataFrame(columns=["date", "ticker", "close"]), str(candidate), ["PRICE_SOURCE_READ_FAILED"]
    return pd.DataFrame(columns=["date", "ticker", "close"]), None, ["PRICE_SOURCE_MISSING"]


def _read_price_csv(path: Path) -> pd.DataFrame:
    raw = pd.read_csv(path)
    if {"date", "ticker", "close"}.issubset(set(raw.columns)):
        return raw[["date", "ticker", "close"]].copy()
    if "Date" in raw.columns:
        rows = []
        for _, row in raw.iterrows():
            for column in raw.columns:
                if column == "Date":
                    continue
                if pd.notna(row[column]):
                    rows.append({"date": row["Date"], "ticker": column, "close": row[column]})
        return pd.DataFrame(rows)
    return pd.DataFrame(columns=["date", "ticker", "close"])


def _standardize_price_frame(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    columns = {str(col).lower(): col for col in out.columns}
    rename = {}
    for canonical in ("date", "ticker", "close"):
        if canonical in columns and columns[canonical] != canonical:
            rename[columns[canonical]] = canonical
    if rename:
        out = out.rename(columns=rename)
    missing = {"date", "ticker", "close"} - set(out.columns)
    if missing:
        return pd.DataFrame(columns=["date", "ticker", "close"])
    out["date"] = pd.to_datetime(out["date"], errors="coerce")
    out["ticker"] = out["ticker"].astype(str).str.upper().str.strip()
    out["close"] = pd.to_numeric(out["close"], errors="coerce")
    return out.dropna(subset=["date", "ticker", "close"])[["date", "ticker", "close"]]


def _load_sector_map(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    out: dict[str, str] = {}
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(line for line in handle if line.strip())
            for row in reader:
                ticker = symbol(row.get("ticker") or row.get("symbol"))
                if ticker:
                    out[ticker] = str(row.get("sector") or "").strip() or "UNKNOWN"
    except Exception:
        return {}
    return out


def _candidate_sector_counts(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for row in candidates:
        sector = str(row.get("sector") or "UNKNOWN")
        counts[sector] = counts.get(sector, 0) + 1
    return [{"sector": sector, "count": count} for sector, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))]


def _regime_label(phoenix: dict[str, Any] | None) -> str:
    if phoenix is None:
        return "UNKNOWN"
    if phoenix.get("active"):
        return "PHOENIX_ACTIVE_CRISIS_REVERSAL"
    reasons = set(phoenix.get("reason_codes") or [])
    if "NO_CRISIS_REGIME" in reasons:
        return "NO_CRISIS_REGIME"
    return "INACTIVE"


def _confidence(*, phoenix: dict[str, Any] | None, realized: dict[str, Any], candidates: list[dict[str, Any]]) -> str:
    if phoenix is None:
        return "LOW"
    if realized.get("available") and candidates:
        return "MEDIUM"
    if candidates:
        return "WATCH"
    return "LOW"


def render_markdown(payload: dict[str, Any]) -> str:
    realized = payload.get("realized_return_evidence") or {}
    lines = [
        f"# Phoenix Evidence Tracker - {payload.get('date')}",
        "",
        f"- Signal date: {payload.get('signal_date')}",
        f"- As-of date: {payload.get('as_of_date')}",
        f"- Active: {payload.get('phoenix_active')}",
        f"- Candidate count: {payload.get('candidate_count')}",
        f"- Realized return evidence available: {realized.get('available')}",
        f"- Confidence: {payload.get('confidence')}",
        f"- No tuning confirmation: {payload.get('no_tuning_confirmation')}",
        f"- Reason codes: {md_join(payload.get('reason_codes') or [])}",
        "",
        "## Top Candidates",
        "",
        "| Ticker | Sector | Weight | Score | Reasons |",
        "|---|---|---:|---:|---|",
    ]
    for row in payload.get("top_candidates") or []:
        lines.append(
            f"| {row.get('ticker')} | {row.get('sector')} | {row.get('target_weight')} | "
            f"{row.get('phoenix_score')} | {md_join(row.get('reason_codes') or [])} |"
        )
    if not payload.get("top_candidates"):
        lines.append("| none | n/a | 0 | n/a | no candidates |")
    lines.extend(["", "## Realized Return Evidence", "", "| Ticker | Start | End | Return | Reasons |", "|---|---|---|---:|---|"])
    for row in realized.get("returns") or []:
        lines.append(
            f"| {row.get('ticker')} | {row.get('start_date')} | {row.get('end_date')} | "
            f"{row.get('realized_return')} | {md_join(row.get('reason_codes') or [])} |"
        )
    if not realized.get("returns"):
        lines.append("| none | n/a | n/a | n/a | not observable |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Track passive Phoenix evidence without tuning Phoenix.")
    parser.add_argument("--date", required=True, help="Artifact/output date.")
    parser.add_argument("--signal-date", default=None, help="Phoenix signal date to evaluate; defaults to --date.")
    parser.add_argument("--as-of-date", default=None, help="Evidence observation date; defaults to --date.")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--output-root", default=None)
    parser.add_argument("--price-path", default=None)
    parser.add_argument("--observation-window-days", type=int, default=5)
    args = parser.parse_args(argv)
    payload = build_phoenix_evidence_tracker(
        trade_date=args.date,
        signal_date=args.signal_date,
        as_of_date=args.as_of_date,
        repo_root=Path(args.repo_root),
        output_root=Path(args.output_root) if args.output_root else None,
        price_path=Path(args.price_path) if args.price_path else None,
        observation_window_days=int(args.observation_window_days),
    )
    print(
        json.dumps(
            {
                "date": payload["date"],
                "phoenix_active": payload["phoenix_active"],
                "candidate_count": payload["candidate_count"],
                "reason_codes": payload["reason_codes"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
