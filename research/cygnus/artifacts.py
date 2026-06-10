"""FR-051 Cygnus — research artifact writers (RESEARCH_ONLY / NON_EXECUTIONAL)."""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from research.cygnus import (
    EXECUTION_IMPACT,
    GOVERNANCE_LABEL,
    SCHEMA_VERSION_EVENT_TAPE,
    STRATEGY_ID,
)

EVENT_TAPE_FIELDS = [
    "ticker",
    "cik10",
    "fiscal_period",
    "announcement_date",
    "announcement_time",
    "availability_date",
    "acceptance_datetime_utc",
    "acceptance_datetime_et",
    "acceptance_timestamp_present",
    "filing_date",
    "items",
    "has_financial_exhibit_item",
    "accession_number",
    "primary_document",
    "reported_eps",
    "consensus_eps",
    "reported_revenue",
    "consensus_revenue",
    "guidance_signal",
    "event_class",
    "source",
    "ingested_at",
]


def cygnus_output_dir(repo_root: Path | str, trade_date: str) -> Path:
    return Path(repo_root) / "outputs" / "research" / "cygnus" / trade_date


def _coerce_cell(value: Any) -> Any:
    if isinstance(value, list):
        return "|".join(str(v) for v in value)
    return value


def write_event_tape(
    events: list[dict[str, Any]],
    *,
    repo_root: Path | str,
    trade_date: str,
    fetch_errors: list[dict[str, Any]] | None = None,
) -> dict[str, str]:
    """Write the event tape as parquet (if pandas available) + CSV, plus meta."""
    out_dir = cygnus_output_dir(repo_root, trade_date)
    out_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, str] = {}

    csv_path = out_dir / "cygnus_event_tape.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=EVENT_TAPE_FIELDS)
        writer.writeheader()
        for event in events:
            writer.writerow({k: _coerce_cell(event.get(k)) for k in EVENT_TAPE_FIELDS})
    written["csv"] = str(csv_path)

    try:
        import pandas as pd

        frame = pd.DataFrame([{k: event.get(k) for k in EVENT_TAPE_FIELDS} for event in events])
        parquet_path = out_dir / "cygnus_event_tape.parquet"
        frame.to_parquet(parquet_path, index=False)
        written["parquet"] = str(parquet_path)
    except Exception as exc:  # parquet is best-effort; CSV is canonical for review
        written["parquet_error"] = f"{type(exc).__name__}: {exc}"

    meta = {
        "schema_version": SCHEMA_VERSION_EVENT_TAPE,
        "strategy_id": STRATEGY_ID,
        "strategy_slug": STRATEGY_ID,
        "governance_label": GOVERNANCE_LABEL,
        "execution_impact": EXECUTION_IMPACT,
        "trade_date": trade_date,
        "event_count": len(events),
        "unique_tickers": len({e["ticker"] for e in events}),
        "event_source": "sec_edgar_submissions",
        "availability_rule": "FR-051 addendum A2 (ET 09:00/16:00 thresholds)",
        "fetch_errors": fetch_errors or [],
    }
    meta_path = out_dir / "cygnus_event_tape_meta.json"
    meta_path.write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    written["meta"] = str(meta_path)
    return written


def write_acceptance_audit(audit: dict[str, Any], *, repo_root: Path | str, trade_date: str) -> str:
    out_dir = cygnus_output_dir(repo_root, trade_date)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "cygnus_acceptance_timestamp_audit.json"
    path.write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return str(path)
