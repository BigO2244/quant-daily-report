from __future__ import annotations

import datetime as dt
from typing import Any


DEFAULT_MAX_AGE_DAYS = {
    "critical": 45,
    "high": 90,
    "medium": 180,
    "low": 365,
}


def build_freshness_report(
    inventory: dict[str, Any],
    *,
    today: dt.date | None = None,
) -> dict[str, Any]:
    today = today or dt.date.today()
    stale: list[dict[str, Any]] = []
    missing_metadata: list[str] = []
    for record in inventory.get("records", []):
        path = record["path"]
        if not record.get("has_metadata"):
            missing_metadata.append(path)
            continue
        reviewed = _parse_date(record.get("last_reviewed"))
        criticality = str(record.get("criticality") or "medium").lower()
        max_age = DEFAULT_MAX_AGE_DAYS.get(criticality, DEFAULT_MAX_AGE_DAYS["medium"])
        if reviewed is None:
            stale.append({"path": path, "reason": "missing_last_reviewed", "age_days": None})
            continue
        age_days = (today - reviewed).days
        if age_days > max_age:
            stale.append({"path": path, "reason": "review_age_exceeded", "age_days": age_days, "max_age_days": max_age})
    return {
        "stale_docs": stale,
        "missing_metadata": sorted(missing_metadata),
        "stale_count": len(stale),
        "missing_metadata_count": len(missing_metadata),
    }


def _parse_date(value: object) -> dt.date | None:
    if not value:
        return None
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except ValueError:
        return None

