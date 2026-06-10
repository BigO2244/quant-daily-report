"""FR-068 Phase 3 — Polaris PIT rebaseline scaffolding (RESEARCH_ONLY).

The faithful priced rebaseline (Legacy Polaris vs PIT Polaris with identical
ranking/costs/sizing/risk, swapping ONLY the universe source) is BLOCKED on two
data dependencies that do not exist in this environment:

  1. Adjusted prices for delisted securities (Sharadar SEP bulk download; needs
     the API key). The local price matrix is current-names-only, so the
     delisted-loser channel cannot be priced here.
  2. A PIT large-cap *membership family* mirroring Polaris's selection rule.
     `Universe(as_of_date)` is the whole ~20.6k-name market; using it directly
     would convert Polaris from large-cap to all-cap, which is NOT an
     apples-to-apples universe swap.

What IS computable now (no prices, deterministic): the **membership-level**
look-ahead correction — which of Polaris's static 201 universe names were not yet
tradable on each historical rebalance date (IPO-after-date) or already delisted
(delisted-before-date). This isolates the IPO-timing channel for the existing
universe and is the runnable portion of the rebaseline.

This module changes no production Polaris, execution, cron, model, ranking, cost,
sizing, or risk behavior. It only reads the PIT security master + the legacy
universe list.
"""
from __future__ import annotations

import csv
import io
from datetime import date
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_DIR = REPO_ROOT / "data" / "pit_universe"
DEFAULT_LEGACY_UNIVERSE = REPO_ROOT / "data" / "universe.csv"


def _parse_date(value: Any) -> date | None:
    text = str(value or "").strip()[:10]
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def load_legacy_universe_tickers(path: Path | str = DEFAULT_LEGACY_UNIVERSE) -> list[str]:
    """Legacy static universe tickers (tolerates a leading blank line)."""
    raw = "\n".join(line for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip())
    return [r["ticker"].strip().upper() for r in csv.DictReader(io.StringIO(raw)) if r.get("ticker", "").strip()]


def load_security_master(data_dir: Path | str = DEFAULT_DATA_DIR) -> list[dict[str, str]]:
    path = Path(data_dir) / "security_master.csv"
    if not path.exists():
        raise FileNotFoundError(
            f"PIT security master not found at {path}; run "
            "scripts/research/build_pit_universe_from_sharadar.py first."
        )
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def build_resolver(master_rows: list[dict[str, Any]]) -> Any:
    """Map a (possibly historical) ticker to its PIT security row, preferring a
    direct current-ticker match, then the active/latest relatedticker match."""
    ticker_to: dict[str, dict[str, Any]] = {}
    related: dict[str, list[dict[str, Any]]] = {}
    for r in master_rows:
        tk = str(r.get("ticker") or "").strip().upper()
        ticker_to.setdefault(tk, r)
        for rel in str(r.get("relatedtickers") or "").split():
            related.setdefault(rel.strip().upper(), []).append(r)

    def _best(cands: list[dict[str, Any]]) -> dict[str, Any]:
        return sorted(
            cands,
            key=lambda r: (str(r.get("isdelisted")).upper() == "N", _parse_date(r.get("lastpricedate")) or date.min),
        )[-1]

    def resolve(ticker: str) -> dict[str, Any] | None:
        tk = ticker.strip().upper()
        if tk in ticker_to:
            return ticker_to[tk]
        cands = related.get(tk)
        return _best(cands) if cands else None

    return resolve


def _eligible(sec: dict[str, Any], d: date) -> bool:
    first = _parse_date(sec.get("firstpricedate"))
    if first is None or d < first:
        return False
    if str(sec.get("isdelisted")).upper() == "N":
        return True
    last = _parse_date(sec.get("lastpricedate"))
    return last is not None and d <= last


def membership_diff(
    dates: list[str], legacy_tickers: list[str], master_rows: list[dict[str, Any]]
) -> dict[str, Any]:
    """Per-date legacy-vs-PIT membership diff for the legacy universe (pure)."""
    resolve = build_resolver(master_rows)
    per_date = []
    excluded_name_dates: dict[str, list[str]] = {}
    for ds in dates:
        d = _parse_date(ds)
        if d is None:
            continue
        kept, excluded = [], []
        for tk in legacy_tickers:
            sec = resolve(tk)
            if sec is None:
                excluded.append({"ticker": tk, "reason": "no_pit_match",
                                 "security_id": None, "firstpricedate": None, "lastpricedate": None})
                excluded_name_dates.setdefault(tk, []).append(ds)
                continue
            if _eligible(sec, d):
                kept.append(tk)
            else:
                first = _parse_date(sec.get("firstpricedate"))
                last = _parse_date(sec.get("lastpricedate"))
                reason = ("ipo_after_date" if (first and d < first)
                          else "delisted_before_date" if (last and d > last)
                          else "ineligible_other")
                excluded.append({
                    "ticker": tk, "reason": reason, "security_id": sec.get("security_id"),
                    "firstpricedate": sec.get("firstpricedate"), "lastpricedate": sec.get("lastpricedate"),
                })
                excluded_name_dates.setdefault(tk, []).append(ds)
        per_date.append({
            "date": ds, "legacy_count": len(legacy_tickers), "pit_eligible_count": len(kept),
            "excluded_count": len(excluded),
            "lookahead_pct": round(len(excluded) / len(legacy_tickers), 4) if legacy_tickers else None,
            "excluded": sorted(excluded, key=lambda e: e["ticker"]),
        })
    return {"by_date": per_date, "excluded_name_dates": excluded_name_dates}


def run(
    dates: list[str],
    *,
    legacy_path: Path | str = DEFAULT_LEGACY_UNIVERSE,
    data_dir: Path | str = DEFAULT_DATA_DIR,
) -> dict[str, Any]:
    legacy = load_legacy_universe_tickers(legacy_path)
    master = load_security_master(data_dir)
    diff = membership_diff(dates, legacy, master)
    return {"legacy_universe_size": len(legacy), "pit_master_size": len(master), **diff}
