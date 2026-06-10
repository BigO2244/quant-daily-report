"""FR-068 Phase 2.5 — caerus_large_cap PIT membership family (RESEARCH_ONLY).

Defines a transparent, reason-coded large-cap membership family on top of the PIT
security master. Identity is the stable `security_id` (SHARADAR:permaticker); the
family never falls back to the static `data/universe.csv`.

Large-cap eligibility (transparent filters, all must hold):
  1. common stock (category contains "Common Stock")
  2. US-listed (exchange in the US equity venues)
  3. price history available on the as-of date (firstpricedate <= as_of <= last)
  4. large-cap scale — EITHER Sharadar `scalemarketcap` in {"5 - Large",
     "6 - Mega"} (cheap, but **current** scale — approximate for history), OR a
     PIT numeric market cap >= `min_marketcap` (exact PIT, from SHARADAR/DAILY).

Market-cap data is NOT in the base security master; it must be supplied (TICKERS
`scalemarketcap` column or a DAILY market-cap snapshot). When absent, securities
are reason-coded `market_cap_unavailable` and the family is BLOCKED — never
silently approximated.

No execution/model/cron/registry behavior is touched.
"""
from __future__ import annotations

from datetime import date
from typing import Any

US_EQUITY_EXCHANGES = {"NYSE", "NASDAQ", "NYSEMKT", "NYSEARCA", "BATS", "AMEX", "ARCA"}
LARGE_CAP_SCALES = {"5 - Large", "6 - Mega"}
DEFAULT_MIN_MARKETCAP = 10_000_000_000.0  # $10B large-cap floor (used with numeric mktcap)


def normalize_ticker(ticker: str) -> str:
    """Normalize a ticker to the Sharadar convention.

    Sharadar uses a dot for share classes (BRK.B, BF.B); the legacy universe and
    some feeds use a hyphen (BRK-B). Normalize hyphen-class suffixes to dots so
    legacy names resolve instead of becoming `no_pit_match`.
    """
    t = str(ticker or "").strip().upper()
    if "-" in t:
        head, _, tail = t.rpartition("-")
        if head and len(tail) <= 2 and tail.isalpha():
            return f"{head}.{tail}"
    return t


def _parse_date(value: Any) -> date | None:
    text = str(value or "").strip()[:10]
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _is_common_stock(category: str) -> bool:
    return "common stock" in str(category or "").lower()


def classify_large_cap(
    security: dict[str, Any],
    *,
    as_of: date,
    scalemarketcap: str | None = None,
    marketcap: float | None = None,
    min_marketcap: float = DEFAULT_MIN_MARKETCAP,
    us_exchanges: set[str] = US_EQUITY_EXCHANGES,
) -> tuple[bool, list[str]]:
    """Return (included, reason_codes) for one security on `as_of`."""
    reasons: list[str] = []
    if not _is_common_stock(security.get("category", "")):
        reasons.append("not_common_stock")
    if str(security.get("exchange") or "").strip().upper() not in us_exchanges:
        reasons.append("non_us_exchange")
    first = _parse_date(security.get("firstpricedate"))
    last = _parse_date(security.get("lastpricedate"))
    active = str(security.get("isdelisted")).upper() == "N"
    if first is None or as_of < first:
        reasons.append("no_price_history_on_date")
    elif not active and (last is None or as_of > last):
        reasons.append("delisted_before_date")

    # Scale filter — scalemarketcap (current, approximate) or numeric PIT mktcap.
    if scalemarketcap is not None:
        if str(scalemarketcap).strip() not in LARGE_CAP_SCALES:
            reasons.append("below_large_cap_scale")
    elif marketcap is not None:
        if float(marketcap) < min_marketcap:
            reasons.append("below_market_cap")
    else:
        reasons.append("market_cap_unavailable")

    included = not reasons
    return included, (["ok"] if included else sorted(set(reasons)))


def build_large_cap_membership(
    master_rows: list[dict[str, Any]],
    as_of_dates: list[str],
    *,
    scalemarketcap_by_id: dict[str, str] | None = None,
    marketcap_by_id: dict[str, float] | None = None,
    min_marketcap: float = DEFAULT_MIN_MARKETCAP,
) -> dict[str, Any]:
    """Build caerus_large_cap membership rows + per-date reason summaries (pure).

    Scale data is keyed by security_id. If neither scale source is provided, every
    security is reason-coded `market_cap_unavailable` (family BLOCKED).
    """
    scalemarketcap_by_id = scalemarketcap_by_id or {}
    marketcap_by_id = marketcap_by_id or {}
    membership: list[dict[str, Any]] = []
    per_date: list[dict[str, Any]] = []
    have_scale = bool(scalemarketcap_by_id or marketcap_by_id)

    for ds in as_of_dates:
        d = _parse_date(ds)
        if d is None:
            continue
        included = 0
        reason_counts: dict[str, int] = {}
        for sec in master_rows:
            sid = str(sec.get("security_id"))
            ok, reasons = classify_large_cap(
                sec, as_of=d,
                scalemarketcap=scalemarketcap_by_id.get(sid),
                marketcap=marketcap_by_id.get(sid),
                min_marketcap=min_marketcap,
            )
            for rc in reasons:
                reason_counts[rc] = reason_counts.get(rc, 0) + 1
            if ok:
                included += 1
                membership.append({
                    "security_id": sid, "ticker": sec.get("ticker"),
                    "membership_family": "caerus_large_cap",
                    "membership_start_date": sec.get("firstpricedate"),
                    "membership_end_date": "" if str(sec.get("isdelisted")).upper() == "N"
                    else sec.get("lastpricedate"),
                    "scale_source": "scalemarketcap" if sid in scalemarketcap_by_id
                    else ("marketcap" if sid in marketcap_by_id else "none"),
                    "source": sec.get("source"), "confidence": sec.get("confidence"),
                })
        per_date.append({"date": ds, "included": included,
                         "reason_counts": dict(sorted(reason_counts.items()))})

    # de-dup membership rows by (security_id, family) — keep widest interval
    dedup: dict[str, dict[str, Any]] = {}
    for m in membership:
        dedup.setdefault(m["security_id"], m)
    membership_rows = sorted(dedup.values(), key=lambda m: m["security_id"])
    return {
        "family": "caerus_large_cap",
        "scale_source_available": have_scale,
        "blocked": not have_scale,
        "block_reason": None if have_scale else "market_cap_unavailable: supply TICKERS scalemarketcap or DAILY marketcap",
        "by_date": per_date,
        "membership": membership_rows,
        "scale_caveat": "scalemarketcap is CURRENT scale (approximate for history); "
                        "DAILY numeric marketcap is the PIT-exact source.",
    }
