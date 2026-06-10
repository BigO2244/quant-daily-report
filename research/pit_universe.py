"""FR-068 Phase 1 — Point-in-Time universe reader (RESEARCH_ONLY / NON_EXECUTIONAL).

`Universe(as_of_date)` is the canonical research universe interface. Phase 1
implements the `sharadar_security_existence` family: a security is eligible on a
date when it existed and traded on that date (firstpricedate <= as_of <=
lastpricedate, lastpricedate open for active names), filtered to common stock.
Identity is the stable `security_id` (rooted in Sharadar `permaticker`); ticker
changes preserve identity.

This is **security-existence** PIT, not historical *index* membership — index
families (sp500_proxy, small_cap_band) are added in later phases.

Hard rules: this module never falls back to `data/universe.csv`. If the PIT
artifacts are missing it raises `PITUniverseUnavailable` with a clear message.
Results are deterministic (sorted by security_id, then ticker). No execution,
broker, cron, registry, or production behavior is touched.
"""
from __future__ import annotations

import csv
from datetime import date
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_DIR = REPO_ROOT / "data" / "pit_universe"
DEFAULT_FAMILY = "sharadar_security_existence"
SECURITY_MASTER_FILE = "security_master.csv"
MEMBERSHIP_FILE = "membership_universe.csv"

_CONFIDENCE_RANK = {"LOW": 0, "DEMO": 0, "MEDIUM": 1, "HIGH": 2}


class PITUniverseUnavailable(RuntimeError):
    """Raised when canonical PIT artifacts are missing or unreadable.

    Never silently fall back to the static current universe (data/universe.csv);
    missing PIT data must fail loudly so survivorship bias cannot re-enter.
    """


def _parse_date(value: Any) -> date | None:
    text = str(value or "").strip()[:10]
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _is_common_stock(category: str) -> bool:
    return "common stock" in str(category or "").lower()


def load_security_master(data_dir: Path | str = DEFAULT_DATA_DIR) -> list[dict[str, str]]:
    path = Path(data_dir) / SECURITY_MASTER_FILE
    if not path.exists():
        raise PITUniverseUnavailable(
            f"PIT security master not found at {path}. Build it first with "
            "`python3 scripts/research/build_pit_universe_from_sharadar.py`. "
            "Refusing to fall back to data/universe.csv (survivorship-biased)."
        )
    return _read_csv(path)


def Universe(
    as_of_date: str,
    membership_family: str = DEFAULT_FAMILY,
    *,
    data_dir: Path | str = DEFAULT_DATA_DIR,
    min_confidence: str = "DEMO",
    common_stock_only: bool = True,
) -> list[dict[str, Any]]:
    """Return PIT-eligible securities for `as_of_date`, deterministically ordered.

    Eligibility (sharadar_security_existence): membership_start_date <= as_of_date
    and (membership_end_date is empty/open or as_of_date <= membership_end_date),
    filtered to common stock when `common_stock_only`. Enriched with security
    master identity fields. Raises PITUniverseUnavailable if artifacts are missing.
    """
    as_of = _parse_date(as_of_date)
    if as_of is None:
        raise ValueError(f"invalid as_of_date: {as_of_date!r}")

    root = Path(data_dir)
    master_rows = load_security_master(root)
    master_by_id = {str(r.get("security_id")): r for r in master_rows}

    membership_path = root / MEMBERSHIP_FILE
    if membership_path.exists():
        membership_rows = [r for r in _read_csv(membership_path)
                           if str(r.get("membership_family")) == membership_family]
    else:
        # Derive the security-existence family from the master if the materialized
        # membership view is absent (still no universe.csv fallback).
        if membership_family != DEFAULT_FAMILY:
            raise PITUniverseUnavailable(
                f"membership_universe.csv not found at {membership_path} and family "
                f"{membership_family!r} cannot be derived from the security master."
            )
        membership_rows = [
            {
                "security_id": r.get("security_id"),
                "ticker": r.get("ticker"),
                "membership_family": DEFAULT_FAMILY,
                "membership_start_date": r.get("firstpricedate"),
                "membership_end_date": "" if str(r.get("isdelisted")).upper().startswith("N")
                else r.get("lastpricedate"),
                "confidence": r.get("confidence"),
            }
            for r in master_rows
        ]

    min_rank = _CONFIDENCE_RANK.get(str(min_confidence).upper(), 0)
    out: list[dict[str, Any]] = []
    for row in membership_rows:
        start = _parse_date(row.get("membership_start_date"))
        end = _parse_date(row.get("membership_end_date"))  # None => open/active
        if start is None or as_of < start:
            continue
        if end is not None and as_of > end:
            continue
        sid = str(row.get("security_id"))
        master = master_by_id.get(sid, {})
        if common_stock_only and not _is_common_stock(master.get("category", "")):
            continue
        if _CONFIDENCE_RANK.get(str(row.get("confidence") or master.get("confidence") or "").upper(), 0) < min_rank:
            continue
        out.append(
            {
                "security_id": sid,
                "permaticker": master.get("permaticker"),
                "ticker": row.get("ticker") or master.get("ticker"),
                "name": master.get("name"),
                "exchange": master.get("exchange"),
                "category": master.get("category"),
                "membership_family": membership_family,
                "membership_start_date": row.get("membership_start_date"),
                "membership_end_date": row.get("membership_end_date") or None,
                "is_active_on_date": end is None,
                "relatedtickers": master.get("relatedtickers"),
                "source": master.get("source"),
                "confidence": row.get("confidence") or master.get("confidence"),
            }
        )
    out.sort(key=lambda r: (str(r["security_id"]), str(r["ticker"])))
    return out
