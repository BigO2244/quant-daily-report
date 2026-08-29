"""Resumable USAspending government-customer relationship proxy."""

from __future__ import annotations

import csv
import gzip
import hashlib
import json
import os
import re
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict
import requests

from projects.alpha_lab.factory import canonical_json

from .storage import output_root, sha256_file, write_bundle_from_paths


Poster = Callable[[str, Dict[str, Any]], Dict[str, Any]]
_SUFFIX = {
    "CORPORATION": "CORP",
    "INCORPORATED": "INC",
    "COMPANY": "CO",
    "LIMITED": "LTD",
}


def _normalize_name(value: str) -> str:
    tokens = re.findall(r"[A-Z0-9]+", str(value or "").upper())
    if tokens and tokens[0] == "THE":
        tokens = tokens[1:]
    return " ".join(_SUFFIX.get(token, token) for token in tokens)


def _post(url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    last_error = None
    for attempt in range(5):
        try:
            response = requests.post(
                url,
                data=canonical_json(payload).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "Caerus Alpha Lab research-data-client",
                },
                timeout=(10, 60),
            )
            response.raise_for_status()
            return response.json()
        except requests.RequestException as exc:
            last_error = exc
            if attempt < 4:
                time.sleep(min(2 ** attempt, 8))
    raise RuntimeError("USAspending request failed after bounded retries") from last_error


def capture_usaspending_government_customer_proxy(
    *,
    repo_root: Path,
    partition_size: int = 100,
    max_new_partitions: int | None = None,
    max_pages_per_issuer: int = 100,
    poster: Poster = _post,
    sleeper: Callable[[float], None] = time.sleep,
    retrieved_at: datetime | None = None,
) -> Dict[str, Any]:
    """Capture exact-name federal contract awards for PIT security histories."""

    if partition_size < 1 or partition_size > 1000:
        raise ValueError("partition_size must be between 1 and 1000")
    if max_new_partitions is not None and max_new_partitions < 1:
        raise ValueError("max_new_partitions must be positive")
    if max_pages_per_issuer < 1:
        raise ValueError("max_pages_per_issuer must be positive")
    master_path = repo_root / "data/pit_universe/security_master.csv"
    names: Dict[str, list[Dict[str, str]]] = {}
    with master_path.open("r", encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream):
            if str(row.get("category") or "") != "Domestic Common Stock":
                continue
            start = str(row.get("effective_start") or "")[:10]
            end = str(row.get("effective_end") or "")[:10]
            if end and end < "2011-01-01" or start > "2026-06-30":
                continue
            normalized = _normalize_name(str(row.get("name") or ""))
            if normalized:
                names.setdefault(normalized, []).append(dict(row))
    name_values = sorted(names)
    master_hash = sha256_file(master_path)
    capture_key = hashlib.sha256(
        canonical_json(
            {
                "master_sha256": master_hash,
                "partition_size": partition_size,
                "date_range": ["2011-01-01", "2026-06-30"],
                "exact_name_policy": "normalized_legal_name_v1",
                "collector_version": "autocomplete_unique_exact_v3",
            }
        ).encode("utf-8")
    ).hexdigest()[:16]
    checkpoint_root = output_root(repo_root) / ".staging" / "usaspending_{}".format(capture_key)
    checkpoint_root.mkdir(parents=True, exist_ok=True)
    endpoint = "https://api.usaspending.gov/api/v2/search/spending_by_award/"
    autocomplete_endpoint = "https://api.usaspending.gov/api/v2/autocomplete/recipient/"
    fields = [
        "Award ID",
        "Recipient Name",
        "Recipient UEI",
        "Start Date",
        "End Date",
        "Award Amount",
        "Awarding Agency",
    ]
    total_partitions = (len(name_values) + partition_size - 1) // partition_size
    new_partitions = 0
    files: Dict[str, Path] = {}
    total_edges = 0
    total_issuers = 0
    total_errors = 0
    total_truncated = 0
    total_resolved_names = 0
    total_ambiguous_names = 0

    for partition_number in range(total_partitions):
        part_names = name_values[
            partition_number * partition_size : (partition_number + 1) * partition_size
        ]
        prefix = "part_{:05d}".format(partition_number)
        data_path = checkpoint_root / "{}_edges.jsonl.gz".format(prefix)
        status_path = checkpoint_root / "{}_status.json".format(prefix)
        complete = data_path.is_file() and status_path.is_file()
        if complete:
            prior_status = json.loads(status_path.read_text(encoding="utf-8"))
            complete = int(prior_status.get("error_count") or 0) == 0
            if (
                int(prior_status.get("truncated_issuer_count") or 0) > 0
                and int(prior_status.get("max_pages_per_issuer") or 0) < max_pages_per_issuer
            ):
                complete = False
        if not complete and max_new_partitions is not None and new_partitions >= max_new_partitions:
            break
        if not complete:
            data_path.unlink(missing_ok=True)
            status_path.unlink(missing_ok=True)
            data_tmp = data_path.with_suffix(".jsonl.gz.tmp")
            edges = []
            errors = []
            matched_issuers = set()
            resolved_recipient_names = set()
            ambiguous_recipient_names = set()
            truncated_issuers = set()
            for normalized_name in part_names:
                query_name = str(names[normalized_name][0].get("name") or "")
                try:
                    autocomplete = poster(
                        autocomplete_endpoint,
                        {"search_text": query_name, "limit": 100},
                    )
                    exact_recipients = [
                        row for row in (autocomplete.get("results") or [])
                        if _normalize_name(str(row.get("recipient_name") or "")) == normalized_name
                    ]
                    if not exact_recipients:
                        sleeper(0.30)
                        continue
                    resolved_recipient_names.add(normalized_name)
                    search_terms = sorted(
                        {
                            str(row.get("uei") or "").strip() or str(row.get("recipient_name") or "").strip()
                            for row in exact_recipients
                            if str(row.get("uei") or "").strip() or str(row.get("recipient_name") or "").strip()
                        }
                    )
                    if len(search_terms) != 1:
                        ambiguous_recipient_names.add(normalized_name)
                        sleeper(0.30)
                        continue
                    for search_term in search_terms:
                        for page in range(1, max_pages_per_issuer + 1):
                            payload = {
                                "subawards": False,
                                "limit": 100,
                                "page": page,
                                "fields": fields,
                                "filters": {
                                    "time_period": [
                                        {"start_date": "2011-01-01", "end_date": "2026-06-30"}
                                    ],
                                    "award_type_codes": ["A", "B", "C", "D"],
                                    "recipient_search_text": [search_term],
                                },
                            }
                            response = poster(endpoint, payload)
                            for award in response.get("results") or []:
                                if _normalize_name(str(award.get("Recipient Name") or "")) != normalized_name:
                                    continue
                                award_start = str(award.get("Start Date") or "")[:10]
                                candidates = [
                                    row for row in names[normalized_name]
                                    if (
                                        not award_start
                                        or str(row.get("effective_start") or "")[:10] <= award_start
                                    )
                                    and (
                                        not award_start
                                        or not str(row.get("effective_end") or "")[:10]
                                        or award_start <= str(row.get("effective_end") or "")[:10]
                                    )
                                ]
                                candidates.sort(key=lambda row: str(row.get("security_id") or ""))
                                if not candidates:
                                    continue
                                security_id = str(candidates[0].get("security_id") or "")
                                award_id = str(award.get("Award ID") or "")
                                uei = str(award.get("Recipient UEI") or "")
                                edge_key = canonical_json(
                                    {"security_id": security_id, "award_id": award_id, "uei": uei}
                                )
                                edges.append(
                                    {
                                        "edge_id": hashlib.sha256(edge_key.encode("utf-8")).hexdigest(),
                                        "customer_entity_id": "US_FEDERAL_GOVERNMENT",
                                        "supplier_security_id": security_id,
                                        "supplier_cik": str(candidates[0].get("cik") or "").zfill(10),
                                        "recipient_name": award.get("Recipient Name"),
                                        "recipient_uei": uei,
                                        "award_id": award_id,
                                        "effective_start": award_start,
                                        "effective_end": str(award.get("End Date") or "")[:10],
                                        "award_amount": award.get("Award Amount"),
                                        "awarding_agency": award.get("Awarding Agency"),
                                        "available_at": award_start,
                                        "relationship_confidence": "EXACT_NORMALIZED_LEGAL_NAME_PROXY",
                                        "source_document": endpoint,
                                        "source_sha256": hashlib.sha256(
                                            canonical_json(award).encode("utf-8")
                                        ).hexdigest(),
                                        "proxy_classification": "FREE_GOVERNMENT_CUSTOMER_SUBGRAPH_ONLY",
                                    }
                                )
                                matched_issuers.add(security_id)
                            metadata = response.get("page_metadata") or {}
                            if not metadata.get("hasNext"):
                                break
                            if page == max_pages_per_issuer:
                                truncated_issuers.add(normalized_name)
                            sleeper(0.30)
                except Exception as exc:
                    errors.append({"issuer_name": query_name, "error_type": type(exc).__name__})
                sleeper(0.30)
            deduplicated = {row["edge_id"]: row for row in edges}
            with gzip.open(data_tmp, "wt", encoding="utf-8", compresslevel=6) as stream:
                for edge_id in sorted(deduplicated):
                    stream.write(canonical_json(deduplicated[edge_id]) + "\n")
            data_tmp.replace(data_path)
            status_tmp = status_path.with_suffix(".json.tmp")
            status_tmp.write_text(
                canonical_json(
                    {
                        "partition_number": partition_number,
                        "issuer_name_count": len(part_names),
                        "matched_security_count": len(matched_issuers),
                        "resolved_recipient_name_count": len(resolved_recipient_names),
                        "ambiguous_recipient_name_count": len(ambiguous_recipient_names),
                        "edge_count": len(deduplicated),
                        "error_count": len(errors),
                        "truncated_issuer_count": len(truncated_issuers),
                        "max_pages_per_issuer": max_pages_per_issuer,
                        "errors": errors,
                    }
                ) + "\n",
                encoding="utf-8",
            )
            status_tmp.replace(status_path)
            new_partitions += 1
        status = json.loads(status_path.read_text(encoding="utf-8"))
        total_edges += int(status["edge_count"])
        total_issuers += int(status["matched_security_count"])
        total_errors += int(status["error_count"])
        total_truncated += int(status["truncated_issuer_count"])
        total_resolved_names += int(status.get("resolved_recipient_name_count") or 0)
        total_ambiguous_names += int(status.get("ambiguous_recipient_name_count") or 0)
        files["edges/{}".format(data_path.name)] = data_path
        files["status/{}".format(status_path.name)] = status_path

    completed_partitions = len(files) // 2
    if completed_partitions < total_partitions:
        return {
            "capture_status": "IN_PROGRESS",
            "checkpoint_path": str(checkpoint_root),
            "issuer_name_count": len(name_values),
            "partition_count": total_partitions,
            "completed_partition_count": completed_partitions,
            "government_customer_edge_count": total_edges,
            "matched_security_count": total_issuers,
            "error_count": total_errors,
            "truncated_issuer_count": total_truncated,
            "resolved_recipient_name_count": total_resolved_names,
            "ambiguous_recipient_name_count": total_ambiguous_names,
            "trading_behavior_changed": False,
        }
    timestamp = retrieved_at or datetime.now(timezone.utc)
    result = write_bundle_from_paths(
        repo_root=repo_root,
        source_id="usaspending_government_customer_proxy",
        files=files,
        metadata={
            "issuer_name_count": len(name_values),
            "partition_count": total_partitions,
            "government_customer_edge_count": total_edges,
            "matched_security_count": total_issuers,
            "error_count": total_errors,
            "truncated_issuer_count": total_truncated,
            "resolved_recipient_name_count": total_resolved_names,
            "ambiguous_recipient_name_count": total_ambiguous_names,
            "master_sha256": master_hash,
            "date_range": ["2011-01-01", "2026-06-30"],
            "api_authorization_required": False,
            "proxy_only_not_full_supply_chain_graph": True,
            "exact_normalized_legal_name_policy": True,
            "ambiguous_exact_recipient_names_rejected": True,
        },
        retrieved_at=timestamp,
    )
    shutil.rmtree(checkpoint_root, ignore_errors=True)
    return result
