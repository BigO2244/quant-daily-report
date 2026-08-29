"""SEC earnings-event hydration indexes and original-exhibit lineage."""

from __future__ import annotations

import csv
import gzip
import json
from datetime import date
from pathlib import Path
from typing import Any, Dict


def prepare_earnings_hydration_index(repo_root: Path) -> Dict[str, Any]:
    """Convert the frozen-window Item 2.02 tape into SEC original-file requests."""

    tape = repo_root / "outputs/research/cygnus/alpha_lab_sec_earnings_event_tape.jsonl.gz"
    if not tape.is_file():
        raise FileNotFoundError("SEC earnings-event tape is absent")
    output = repo_root / "outputs/research/alpha_lab/shared/earnings_8k_hydration_index.csv"
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(".{}.tmp".format(output.name))
    fields = (
        "cik",
        "company_name",
        "form_type",
        "filed_date",
        "filename",
        "index_year",
        "index_quarter",
    )
    rows = {}
    with gzip.open(tape, "rt", encoding="utf-8") as stream:
        for line in stream:
            event = json.loads(line)
            accession = str(event.get("event_id") or "")
            cik = str(event.get("issuer_cik") or "").lstrip("0")
            accepted = str(event.get("acceptance_datetime_utc") or "")[:10]
            source = str(event.get("source_document") or "")
            marker = "/Archives/"
            filename = source.split(marker, 1)[1] if marker in source else ""
            if not accession or not cik or not accepted or not filename:
                continue
            accepted_date = date.fromisoformat(accepted)
            rows[accession] = {
                "cik": cik,
                "company_name": "",
                "form_type": str(event.get("form_type") or "8-K"),
                "filed_date": accepted,
                "filename": filename,
                "index_year": accepted_date.year,
                "index_quarter": ((accepted_date.month - 1) // 3) + 1,
            }
    with temporary.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for accession in sorted(rows, key=lambda key: (rows[key]["filed_date"], key)):
            writer.writerow(rows[accession])
    temporary.replace(output)
    return {
        "earnings_hydration_candidate_rows": len(rows),
        "earnings_hydration_index_path": str(output),
        "selection_method": "SEC_Item_2_02_exact_acceptance_frozen_window",
    }
