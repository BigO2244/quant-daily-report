"""Prepare SEC original-filing candidates for free delisting settlement research."""

from __future__ import annotations

import csv
import gzip
import hashlib
import json
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict

from projects.alpha_lab.factory import canonical_json

from .storage import latest_manifest, sha256_file


_ACTIONS = {
    "acquisitionby",
    "mergerto",
    "delisted",
    "bankruptcyliquidation",
    "regulatorydelisting",
    "voluntarydelisting",
}


def prepare_delisting_hydration_index(repo_root: Path) -> Dict[str, Any]:
    """Join corporate-action dates to nearby issuer 8-K originals, without certification."""

    action_manifest_path = latest_manifest(repo_root, "sharadar_actions")
    event_manifest_path = latest_manifest(repo_root, "sec_event_index")
    if not action_manifest_path or not event_manifest_path:
        raise FileNotFoundError("Sharadar ACTIONS and SEC event index are required")
    action_manifest = json.loads(action_manifest_path.read_text(encoding="utf-8"))
    event_manifest = json.loads(event_manifest_path.read_text(encoding="utf-8"))
    actions_path = action_manifest_path.parent / "data" / action_manifest["files"][0]["name"]
    events_path = event_manifest_path.parent / "data" / next(
        row["name"] for row in event_manifest["files"] if row["name"] == "event_index.csv"
    )
    master_path = repo_root / "data/pit_universe/security_master.csv"
    histories: Dict[str, list[Dict[str, str]]] = defaultdict(list)
    with master_path.open("r", encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream):
            if row.get("category") != "Domestic Common Stock":
                continue
            ticker = str(row.get("ticker") or "").upper()
            if ticker and row.get("cik"):
                histories[ticker].append(dict(row))

    by_cik: Dict[str, list[Dict[str, Any]]] = defaultdict(list)
    action_count = 0
    mapped_action_count = 0
    with actions_path.open("r", encoding="utf-8") as stream:
        for line in stream:
            action = json.loads(line)
            action_type = str(action.get("action") or "").lower()
            action_date_text = str(action.get("date") or "")[:10]
            if action_type not in _ACTIONS or not ("2011-01-01" <= action_date_text <= "2026-06-30"):
                continue
            action_count += 1
            action_date = date.fromisoformat(action_date_text)
            ticker = str(action.get("ticker") or "").upper()
            matches = [
                row
                for row in histories.get(ticker, [])
                if str(row.get("effective_start") or "")[:10] <= action_date_text
                and (
                    not str(row.get("effective_end") or "")[:10]
                    or action_date_text <= str(row.get("effective_end") or "")[:10]
                )
            ]
            matches.sort(key=lambda row: str(row.get("security_id") or ""))
            if not matches:
                continue
            mapped_action_count += 1
            row = matches[0]
            action_id = hashlib.sha256(
                canonical_json(
                    {
                        "security_id": row.get("security_id"),
                        "action": action_type,
                        "date": action_date_text,
                        "contraticker": action.get("contraticker"),
                    }
                ).encode("utf-8")
            ).hexdigest()
            by_cik[str(row["cik"]).lstrip("0")].append(
                {
                    "action_id": action_id,
                    "security_id": row.get("security_id"),
                    "ticker": ticker,
                    "action": action_type,
                    "action_date": action_date_text,
                    "window_start": (action_date - timedelta(days=365)).isoformat(),
                    "window_end": (action_date + timedelta(days=120)).isoformat(),
                    "contraticker": action.get("contraticker"),
                    "contraname": action.get("contraname"),
                }
            )

    filing_rows: Dict[str, Dict[str, str]] = {}
    evidence_rows = []
    with events_path.open("r", encoding="utf-8", newline="") as stream:
        for filing in csv.DictReader(stream):
            if filing.get("form_type") not in {"8-K", "8-K/A"}:
                continue
            cik = str(filing.get("cik") or "").lstrip("0")
            filed_date = str(filing.get("filed_date") or "")[:10]
            matched = [
                action
                for action in by_cik.get(cik, [])
                if action["window_start"] <= filed_date <= action["window_end"]
            ]
            if not matched:
                continue
            filename = str(filing.get("filename") or "")
            filing_rows[filename] = dict(filing)
            for action in matched:
                evidence_rows.append(
                    {
                        **action,
                        "cik": cik.zfill(10),
                        "form_type": filing.get("form_type"),
                        "filed_date": filed_date,
                        "filename": filename,
                        "classification": "SEC_CANDIDATE_NOT_SETTLEMENT_CERTIFICATION",
                    }
                )

    shared = repo_root / "outputs/research/alpha_lab/shared"
    shared.mkdir(parents=True, exist_ok=True)
    index_path = shared / "delisting_8k_hydration_index.csv"
    evidence_path = shared / "delisting_sec_candidate_lineage.jsonl.gz"
    manifest_path = shared / "delisting_sec_candidate_manifest.json"
    index_tmp = index_path.with_name(".{}.tmp".format(index_path.name))
    fields = ("cik", "company_name", "form_type", "filed_date", "filename", "index_year", "index_quarter")
    with index_tmp.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for filename in sorted(filing_rows, key=lambda key: (filing_rows[key]["filed_date"], key)):
            writer.writerow({field: filing_rows[filename].get(field, "") for field in fields})
    index_tmp.replace(index_path)
    evidence_tmp = evidence_path.with_name(".{}.tmp".format(evidence_path.name))
    with gzip.open(evidence_tmp, "wt", encoding="utf-8", compresslevel=6) as stream:
        for row in sorted(evidence_rows, key=lambda value: (value["action_date"], value["action_id"], value["filename"])):
            stream.write(canonical_json(row) + "\n")
    evidence_tmp.replace(evidence_path)
    manifest = {
        "schema_version": "caerus_alpha_lab_delisting_sec_candidates_v1",
        "classification": "RESEARCH_ONLY_NON_EXECUTIONAL",
        "settlement_certified": False,
        "selection_window_days": {"before": 365, "after": 120},
        "eligible_action_types": sorted(_ACTIONS),
        "action_candidate_count": action_count,
        "mapped_action_count": mapped_action_count,
        "filing_candidate_count": len(filing_rows),
        "action_filing_edge_count": len(evidence_rows),
        "actions_source_sha256": sha256_file(actions_path),
        "event_index_source_sha256": sha256_file(events_path),
        "index_sha256": sha256_file(index_path),
        "evidence_sha256": sha256_file(evidence_path),
        "blockers": [
            "original_8K_bytes_not_yet_hydrated",
            "merger_consideration_and_terminal_settlement_not_parsed",
            "non_merger_delistings_need_case_specific_terminal_value_evidence",
        ],
        "trading_behavior_changed": False,
    }
    manifest_tmp = manifest_path.with_name(".{}.tmp".format(manifest_path.name))
    manifest_tmp.write_text(canonical_json(manifest) + "\n", encoding="utf-8")
    manifest_tmp.replace(manifest_path)
    return {
        "delisting_action_candidates": action_count,
        "delisting_mapped_actions": mapped_action_count,
        "delisting_hydration_candidate_rows": len(filing_rows),
        "delisting_action_filing_edges": len(evidence_rows),
        "delisting_hydration_index_path": str(index_path),
        "delisting_candidate_manifest_path": str(manifest_path),
    }


def prepare_combined_8k_hydration_index(repo_root: Path) -> Dict[str, Any]:
    """Union earnings and delisting 8-K requests to avoid duplicate SEC downloads."""

    shared = repo_root / "outputs/research/alpha_lab/shared"
    inputs = (
        shared / "earnings_8k_hydration_index.csv",
        shared / "delisting_8k_hydration_index.csv",
    )
    for path in inputs:
        if not path.is_file():
            raise FileNotFoundError(path)
    rows: Dict[str, Dict[str, str]] = {}
    input_counts = {}
    for path in inputs:
        count = 0
        with path.open("r", encoding="utf-8", newline="") as stream:
            for row in csv.DictReader(stream):
                count += 1
                rows[str(row.get("filename") or "")] = dict(row)
        input_counts[path.name] = count
    output = shared / "combined_8k_hydration_index.csv"
    temporary = output.with_name(".{}.tmp".format(output.name))
    fields = ("cik", "company_name", "form_type", "filed_date", "filename", "index_year", "index_quarter")
    with temporary.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for filename in sorted(rows, key=lambda key: (rows[key]["filed_date"], key)):
            writer.writerow({field: rows[filename].get(field, "") for field in fields})
    temporary.replace(output)
    manifest_path = shared / "combined_8k_hydration_manifest.json"
    payload = {
        "schema_version": "caerus_alpha_lab_combined_8k_hydration_v1",
        "classification": "RESEARCH_ONLY_NON_EXECUTIONAL",
        "input_rows": input_counts,
        "deduplicated_candidate_count": len(rows),
        "output_sha256": sha256_file(output),
        "original_filing_capture_complete": False,
        "trading_behavior_changed": False,
    }
    manifest_path.write_text(canonical_json(payload) + "\n", encoding="utf-8")
    return {
        "combined_8k_hydration_candidate_rows": len(rows),
        "combined_8k_hydration_index_path": str(output),
        "combined_8k_hydration_manifest_path": str(manifest_path),
    }
