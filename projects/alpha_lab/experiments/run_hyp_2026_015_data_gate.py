"""Build HYP-2026-015's causal no-return event/peer eligibility manifest.

The runner is deliberately outcome blind.  It may inspect filing metadata,
effective-dated identity, membership, contemporaneous price/liquidity gates,
and whether required market rows exist.  It never computes or persists a
reporter reaction, a forward return, or a challenge-period observation.
"""

from __future__ import annotations

import argparse
import bisect
import csv
import gzip
import hashlib
import io
import json
import math
import os
import re
import resource
import shutil
import sqlite3
import tarfile
import tempfile
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence
from zoneinfo import ZoneInfo

from projects.alpha_lab.factory import canonical_hash, canonical_json
from projects.alpha_lab.factory.canonical import parse_datetime


SCHEMA_VERSION = "caerus_alpha_lab_hyp_2026_015_no_return_gate_v2"
HYPOTHESIS_ID = "HYP-2026-015"
EXPERIMENT_ID = "EXP-2026-0015"
RUNNER_RELATIVE_PATH = "projects/alpha_lab/experiments/run_hyp_2026_015_data_gate.py"
SPEC_RELATIVE_PATH = (
    "projects/alpha_lab/hypotheses/"
    "HYP-2026-015_industry_earnings_information_diffusion.md"
)
SPEC_SHA256 = "3ca51f2f477c548d0b9ad266f004b4f61ba532f1d23961847c05db1e5fd033d6"
ADDENDUM_RELATIVE_PATH = (
    "projects/alpha_lab/hypotheses/"
    "HYP-2026-015-ADDENDUM-001_source_materiality_and_evaluator_determinism.md"
)
ADDENDUM_BODY_SHA256 = (
    "6a3747d98e89efdb3f73e0f7a3587992b38804789e43534a7ec03842ee5e3c8e"
)
ADDENDUM_FULL_FILE_SHA256 = (
    "8a327c8317a7cb4f78b877863eec805ec86a047900488806a751569269704820"
)
SOURCE_MANIFEST_RELATIVE_PATH = (
    "outputs/research/alpha_lab/data_spine/sec_original_filings_stream/"
    "20260722T212948Z-8bec6cab476f/manifest.json"
)
SOURCE_MANIFEST_SHA256 = (
    "90bd5b5d43da8d8e02b924308cbc049cee117535db597769a1c43a057036278f"
)
SOURCE_BUNDLE_SHA256 = (
    "25f7cdf591a1f80339309b0ca1a2c5abc18a01529fca9e3d7e3eb004dcfd7ad4"
)
EARNINGS_READINESS_RELATIVE_PATH = (
    "outputs/research/alpha_lab/provider_readiness/pit_earnings_events_v1.json"
)
EARNINGS_READINESS_SHA256 = (
    "44e9d240a34560794f70f29cf73ddee1ad569192529eacfadadf135ee60d89ac"
)
PRICES_READINESS_RELATIVE_PATH = (
    "outputs/research/alpha_lab/provider_readiness/pit_observed_prices_v1.json"
)
PRICES_READINESS_SHA256 = (
    "6a97d2ae3311ae3ad24ee289a37099afde70473b782f26e21c5487d7563af7d0"
)
PRICES_PANEL_SHA256 = (
    "7b6518bc30d84820b5113465fb23d54de36012195ed1672ed19aca9e216c99c0"
)
SECURITY_MASTER_RELATIVE_PATH = "data/pit_universe/security_master.csv"
MEMBERSHIP_RELATIVE_PATH = "data/pit_universe/membership_universe.csv"
CIK_MAPPING_RELATIVE_PATH = "cik_mapping_results.csv"
SECURITY_MASTER_SHA256 = "55f09af11065725dfa797169414a32e2e18ea6e3dea6c903f325d1b8bf8febc9"
MEMBERSHIP_SHA256 = "563109a4d8a5a516d49967e60b58f152dfe52cc7ce1c6b45333fa6160381187d"
CIK_MAPPING_SHA256 = "e3e093da41c619eab292003f7259ffe874a3e942db52bbf97578a714e2bd2ad5"
CANONICAL_GCP_REPO_ROOT = Path("/mnt/disks/alpha-lab/alpha-lab-project")

DISCOVERY_START = date(2012, 1, 1)
VALIDATION_END = date(2024, 12, 31)
VALIDATION_START = date(2019, 1, 1)
AGGREGATE_ORIGINAL_COVERAGE_MIN = 0.999
PEER_MAPPING_COVERAGE_MIN = 0.99
MIN_VALIDATION_CLUSTERS = 150
MIN_VALIDATION_PEERS = 100
MIN_VALIDATION_SICS = 20
PRICE_FLOOR = 5.0
ADV_FLOOR = 10_000_000.0
HEADER_LIMIT_BYTES = 512 * 1024
MARKET_SCAN_SECURITY_CHUNK = 128
MARKET_SCAN_BATCH_SIZE = 8_192
CALENDAR_SCAN_BATCH_SIZE = 65_536
MARKET_CLUSTER_CHUNK = 2
MAX_CLUSTER_CANDIDATES = 5_000
MAX_MARKET_REQUEST_PAIRS = 150_000
PROCESS_RSS_LIMIT_BYTES = 384 * 1024 * 1024
AGGREGATE_RSS_LIMIT_BYTES = 512 * 1024 * 1024
CHECKPOINT_SCHEMA_VERSION = "caerus_hyp015_gate_checkpoint_v2"

_SPEC_MARKER = "## Freeze record\n"
_ADDENDUM_MARKER = "## Addendum record\n"
_ACCESSION = re.compile(r"(?P<accession>\d{10}-\d{2}-\d{6})")
_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_HEADER_CIK = re.compile(r"CENTRAL INDEX KEY:\s*(\d+)", re.I)
_HEADER_SIC = re.compile(
    r"STANDARD INDUSTRIAL CLASSIFICATION:[^\r\n]*\[(\d{4})\]", re.I
)
_HEADER_ACCEPTANCE = re.compile(r"<ACCEPTANCE-DATETIME>(\d{14})", re.I)
_HEADER_FORM = re.compile(r"CONFORMED SUBMISSION TYPE:\s*([^\r\n]+)", re.I)
_HEADER_ITEM = re.compile(r"ITEM INFORMATION:\s*([^\r\n]+)", re.I)
_ET = ZoneInfo("America/New_York")


def _current_rss_bytes() -> int:
    """Return current resident bytes without adding a runtime dependency."""

    proc_statm = Path("/proc/self/statm")
    if proc_statm.is_file():
        resident_pages = int(proc_statm.read_text(encoding="ascii").split()[1])
        return resident_pages * os.sysconf("SC_PAGE_SIZE")
    value = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    # Linux reports KiB; Darwin reports bytes. /proc handles production Linux.
    return value if value > 16 * 1024 * 1024 else value * 1024


class _RssMonitor:
    """Fail the no-return preflight before it exceeds the VM memory envelope."""

    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []
        self.peak_rss_bytes = 0
        self.measurement_count = 0

    def record(self, phase: str) -> None:
        rss = _current_rss_bytes()
        self.measurement_count += 1
        self.peak_rss_bytes = max(self.peak_rss_bytes, rss)
        record = {"phase": phase, "rss_bytes": rss}
        if len(self.records) < 256:
            self.records.append(record)
        else:
            self.records[-1] = record
        if rss > PROCESS_RSS_LIMIT_BYTES:
            raise MemoryError(
                f"HYP-015 process RSS guard exceeded at {phase}: {rss} bytes"
            )
        if rss > AGGREGATE_RSS_LIMIT_BYTES:
            raise MemoryError(
                f"HYP-015 aggregate RSS guard exceeded at {phase}: {rss} bytes"
            )

    def audit(self) -> dict[str, Any]:
        return {
            "measurement": "CURRENT_PROCESS_RSS",
            "single_process_pipeline": True,
            "process_limit_bytes": PROCESS_RSS_LIMIT_BYTES,
            "aggregate_limit_bytes": AGGREGATE_RSS_LIMIT_BYTES,
            "peak_rss_bytes": self.peak_rss_bytes,
            "measurement_count": self.measurement_count,
            "retained_measurement_count": len(self.records),
            "process_limit_pass": self.peak_rss_bytes <= PROCESS_RSS_LIMIT_BYTES,
            "aggregate_limit_pass": self.peak_rss_bytes <= AGGREGATE_RSS_LIMIT_BYTES,
            "phase_records": self.records,
        }


class _HeaderIndex:
    """Disk-backed, resumable SEC-header index for the production gate path."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row

    def close(self) -> None:
        self.connection.close()

    def get(self, event_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            """
            SELECT * FROM headers
            WHERE event_id = ?
              AND event_id NOT IN (SELECT event_id FROM failed_accessions)
            """,
            (event_id,),
        ).fetchone()
        return _decode_header_row(row) if row is not None else None

    def __iter__(self) -> Iterator[dict[str, Any]]:
        cursor = self.connection.execute(
            """
            SELECT * FROM headers
            WHERE event_id NOT IN (SELECT event_id FROM failed_accessions)
            ORDER BY acceptance, event_id
            """
        )
        for row in cursor:
            yield _decode_header_row(row)

    def audit(self) -> dict[str, Any]:
        verified = self.connection.execute(
            """
            SELECT COUNT(*) FROM headers
            WHERE event_id NOT IN (SELECT event_id FROM failed_accessions)
            """
        ).fetchone()[0]
        sic_count = self.connection.execute(
            """
            SELECT COUNT(*) FROM headers
            WHERE event_id NOT IN (SELECT event_id FROM failed_accessions)
              AND sic_count = 1 AND sic IS NOT NULL AND sic != ''
            """
        ).fetchone()[0]
        failure_counts = dict(
            self.connection.execute(
                "SELECT reason, COUNT(*) FROM failures GROUP BY reason ORDER BY reason"
            ).fetchall()
        )
        failed_count = self.connection.execute(
            "SELECT COUNT(*) FROM failed_accessions"
        ).fetchone()[0]
        alias_groups = 0
        advertised_extra = 0
        actual_extra = 0
        feed_cik = 0
        feed_filed = 0
        for row in self.connection.execute(
            """
            SELECT source_aliases_json, actual_member_paths_json,
                   feed_cik_discrepancy_count,
                   feed_filed_date_discrepancy_count
            FROM headers
            WHERE event_id NOT IN (SELECT event_id FROM failed_accessions)
            """
        ):
            aliases = json.loads(row[0])
            paths = json.loads(row[1])
            alias_groups += len(aliases) > 1
            advertised_extra += max(0, len(aliases) - 1)
            actual_extra += max(0, len(paths) - 1)
            feed_cik += int(row[2])
            feed_filed += int(row[3])
        partition_records = [
            {
                "partition": row["partition"],
                "inventory_sha256": row["inventory_sha256"],
                "inventory_bytes": row["inventory_bytes"],
                "tar_sha256": row["tar_sha256"],
                "tar_bytes": row["tar_bytes"],
            }
            for row in self.connection.execute(
                "SELECT * FROM completed_partitions ORDER BY partition"
            )
        ]
        attempted = verified + failed_count
        return {
            "attempted_original_headers_through_2024": attempted,
            "verified_header_rows": verified,
            "failure_count": failed_count,
            "coverage": verified / attempted if attempted else 0.0,
            "single_four_digit_sic_rows": sic_count,
            "sic_coverage": sic_count / verified if verified else 0.0,
            "duplicate_alias_group_count": alias_groups,
            "advertised_alias_extra_row_count": advertised_extra,
            "actual_member_alias_extra_count": actual_extra,
            "feed_cik_discrepancy_count": feed_cik,
            "feed_filed_date_discrepancy_count": feed_filed,
            "failures_by_reason": failure_counts,
            "checkpoint_path": str(self.path),
            "checkpoint_resumable": True,
            "headers_retained_in_memory": 0,
            "completed_partition_count": len(partition_records),
            "completed_partition_hashes": partition_records,
            "completed_partition_inventory_sha256": canonical_hash(
                partition_records
            ),
        }


def _decode_header_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "event_id": row["event_id"],
        "cik": row["cik"],
        "sic": row["sic"],
        "sic_count": row["sic_count"],
        "acceptance": parse_datetime(row["acceptance"]),
        "form_type": row["form_type"],
        "item_2_02": bool(row["item_2_02"]),
        "source_sha256": row["source_sha256"],
        "source_path": row["source_path"],
        "source_aliases": json.loads(row["source_aliases_json"]),
        "actual_member_paths": json.loads(row["actual_member_paths_json"]),
        "feed_cik_discrepancy_count": row["feed_cik_discrepancy_count"],
        "feed_filed_date_discrepancy_count": row[
            "feed_filed_date_discrepancy_count"
        ],
    }


class _ClusterSpool:
    """Integrity-checked disk spool for structural and annotated clusters."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS spool_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS clusters (
                ordinal INTEGER PRIMARY KEY AUTOINCREMENT,
                reaction_session TEXT NOT NULL,
                sic TEXT NOT NULL,
                structural_json TEXT NOT NULL,
                structural_sha256 TEXT NOT NULL,
                annotated_json TEXT,
                annotated_sha256 TEXT,
                UNIQUE(reaction_session, sic)
            );
            """
        )
        bindings = {
            "schema_version": CHECKPOINT_SCHEMA_VERSION,
            "runner_sha256": _sha256_file(Path(__file__)),
            "source_bundle_sha256": SOURCE_BUNDLE_SHA256,
            "observed_prices_sha256": PRICES_PANEL_SHA256,
            "validation_end": VALIDATION_END.isoformat(),
        }
        existing = dict(self.connection.execute("SELECT key, value FROM spool_meta"))
        bound = {key: existing.get(key) for key in bindings}
        if any(value is not None for value in bound.values()) and bound != bindings:
            self.close()
            raise ValueError("HYP-015 structural spool binding mismatch")
        if not any(value is not None for value in bound.values()):
            self.connection.executemany(
                "INSERT INTO spool_meta(key, value) VALUES(?, ?)",
                sorted(bindings.items()),
            )
            self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def _meta(self, key: str) -> str | None:
        row = self.connection.execute(
            "SELECT value FROM spool_meta WHERE key = ?", (key,)
        ).fetchone()
        return str(row[0]) if row is not None else None

    def _set_meta(self, key: str, value: str) -> None:
        self.connection.execute(
            "INSERT OR REPLACE INTO spool_meta(key, value) VALUES(?, ?)",
            (key, value),
        )

    def structural_ready(self) -> bool:
        if self._meta("structural_complete") != "1":
            return False
        expected_count = int(self._meta("structural_cluster_count") or -1)
        observed_count = self.connection.execute(
            "SELECT COUNT(*) FROM clusters"
        ).fetchone()[0]
        if expected_count != observed_count:
            raise ValueError("HYP-015 structural spool count mismatch")
        for row in self.connection.execute(
            "SELECT structural_json, structural_sha256 FROM clusters ORDER BY ordinal"
        ):
            if hashlib.sha256(row[0].encode("utf-8")).hexdigest() != row[1]:
                raise ValueError("HYP-015 structural spool integrity mismatch")
        return True

    def begin_structural_rebuild(self) -> None:
        with self.connection:
            self.connection.execute("DELETE FROM clusters")
            for key in (
                "structural_complete",
                "structural_cluster_count",
                "structural_audit_json",
                "structural_audit_sha256",
                "annotated_complete",
                "path_audit_json",
                "path_audit_sha256",
            ):
                self.connection.execute("DELETE FROM spool_meta WHERE key = ?", (key,))

    def append_structural(self, cluster: Mapping[str, Any]) -> None:
        payload = canonical_json(cluster)
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO clusters(
                    reaction_session, sic, structural_json, structural_sha256
                ) VALUES(?, ?, ?, ?)
                """,
                (
                    cluster["reaction_session"].isoformat(),
                    cluster["sic"],
                    payload,
                    hashlib.sha256(payload.encode("utf-8")).hexdigest(),
                ),
            )

    def finish_structural(self, audit: Mapping[str, Any]) -> None:
        payload = canonical_json(audit)
        count = self.connection.execute("SELECT COUNT(*) FROM clusters").fetchone()[0]
        with self.connection:
            self._set_meta("structural_cluster_count", str(count))
            self._set_meta("structural_audit_json", payload)
            self._set_meta(
                "structural_audit_sha256",
                hashlib.sha256(payload.encode("utf-8")).hexdigest(),
            )
            self._set_meta("structural_complete", "1")

    def structural_audit(self) -> dict[str, Any]:
        payload = self._meta("structural_audit_json")
        expected = self._meta("structural_audit_sha256")
        if payload is None or expected is None:
            raise ValueError("HYP-015 structural audit absent from spool")
        if hashlib.sha256(payload.encode("utf-8")).hexdigest() != expected:
            raise ValueError("HYP-015 structural audit integrity mismatch")
        return json.loads(payload)

    def iter_structural_chunks(
        self, chunk_size: int
    ) -> Iterator[list[tuple[int, dict[str, Any]]]]:
        if chunk_size < 1:
            raise ValueError("structural spool chunk_size must be positive")
        chunk: list[tuple[int, dict[str, Any]]] = []
        for row in self.connection.execute(
            """
            SELECT ordinal, structural_json, structural_sha256
            FROM clusters ORDER BY ordinal
            """
        ):
            if hashlib.sha256(row[1].encode("utf-8")).hexdigest() != row[2]:
                raise ValueError("HYP-015 structural spool integrity mismatch")
            chunk.append((int(row[0]), _decode_market_checkpoint_cluster(json.loads(row[1]))))
            if len(chunk) == chunk_size:
                yield chunk
                chunk = []
        if chunk:
            yield chunk

    def write_annotated(self, ordinal: int, cluster: Mapping[str, Any]) -> None:
        payload = canonical_json(cluster)
        with self.connection:
            updated = self.connection.execute(
                """
                UPDATE clusters
                SET annotated_json = ?, annotated_sha256 = ?
                WHERE ordinal = ?
                """,
                (
                    payload,
                    hashlib.sha256(payload.encode("utf-8")).hexdigest(),
                    ordinal,
                ),
            ).rowcount
        if updated != 1:
            raise ValueError("HYP-015 annotated spool ordinal absent")

    def finish_annotated(self, audit: Mapping[str, Any]) -> None:
        missing = self.connection.execute(
            "SELECT COUNT(*) FROM clusters WHERE annotated_json IS NULL"
        ).fetchone()[0]
        if missing:
            raise ValueError("HYP-015 annotated spool is incomplete")
        payload = canonical_json(audit)
        with self.connection:
            self._set_meta("path_audit_json", payload)
            self._set_meta(
                "path_audit_sha256",
                hashlib.sha256(payload.encode("utf-8")).hexdigest(),
            )
            self._set_meta("annotated_complete", "1")

    def __iter__(self) -> Iterator[dict[str, Any]]:
        for row in self.connection.execute(
            """
            SELECT annotated_json, annotated_sha256
            FROM clusters ORDER BY ordinal
            """
        ):
            if row[0] is None or row[1] is None:
                raise ValueError("HYP-015 annotated spool row absent")
            if hashlib.sha256(row[0].encode("utf-8")).hexdigest() != row[1]:
                raise ValueError("HYP-015 annotated spool integrity mismatch")
            yield _decode_market_checkpoint_cluster(json.loads(row[0]))

    def __len__(self) -> int:
        return int(self.connection.execute("SELECT COUNT(*) FROM clusters").fetchone()[0])


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _file_stat_signature(path: Path) -> tuple[int, int]:
    status = path.stat()
    return status.st_size, status.st_mtime_ns


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _verified_file(repo_root: Path, relative_path: str, expected: str) -> dict[str, Any]:
    path = (repo_root / relative_path).resolve()
    if not path.is_file():
        raise ValueError(f"frozen input is absent: {relative_path}")
    actual = _sha256_file(path)
    if actual != expected:
        raise ValueError(f"frozen input hash mismatch: {relative_path}")
    return {"path": relative_path, "bytes": path.stat().st_size, "sha256": actual}


def _verify_spec(repo_root: Path) -> dict[str, Any]:
    path = repo_root / SPEC_RELATIVE_PATH
    text = path.read_text(encoding="utf-8")
    if _SPEC_MARKER not in text:
        raise ValueError("frozen hypothesis is missing its Freeze record")
    actual = hashlib.sha256(text.split(_SPEC_MARKER, 1)[0].encode("utf-8")).hexdigest()
    if actual != SPEC_SHA256:
        raise ValueError("frozen HYP-2026-015 specification hash mismatch")
    return {"path": SPEC_RELATIVE_PATH, "sha256": actual}


def _verify_addendum(repo_root: Path) -> dict[str, Any]:
    path = repo_root / ADDENDUM_RELATIVE_PATH
    text = path.read_text(encoding="utf-8")
    if _ADDENDUM_MARKER not in text:
        raise ValueError("owner addendum is missing its Addendum record")
    body_hash = hashlib.sha256(
        text.split(_ADDENDUM_MARKER, 1)[0].encode("utf-8")
    ).hexdigest()
    full_hash = _sha256_file(path)
    if body_hash != ADDENDUM_BODY_SHA256:
        raise ValueError("owner addendum frozen body hash mismatch")
    if full_hash != ADDENDUM_FULL_FILE_SHA256:
        raise ValueError("owner addendum full-file hash mismatch")
    required_terms = (
        "HYP-2026-015",
        "Addendum 001",
        "0.999",
        "deterministic",
        "no-return",
    )
    missing = [term for term in required_terms if term.lower() not in text.lower()]
    if missing:
        raise ValueError(f"owner addendum is missing required terms: {missing}")
    return {
        "path": ADDENDUM_RELATIVE_PATH,
        "bytes": path.stat().st_size,
        "frozen_body_sha256": body_hash,
        "full_file_sha256": full_hash,
    }


def _norm_cik(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or not text.isdigit():
        return None
    return text.zfill(10)


def _parse_date(value: object) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _accession(value: object) -> str | None:
    match = _ACCESSION.search(value) if isinstance(value, str) else None
    return match.group("accession") if match else None


def _status_errors(bundle_root: Path) -> list[dict[str, Any]]:
    status_paths = sorted((bundle_root / "data/status").glob("part_*_status.json"))
    if not status_paths:
        raise ValueError("source-bundle partition status files are absent")
    errors: list[dict[str, Any]] = []
    for status_path in status_paths:
        payload = _load_json(status_path)
        records = payload.get("errors", [])
        if payload.get("error_count") != len(records) or not isinstance(records, list):
            raise ValueError(f"partition error-count mismatch: {status_path}")
        for record in records:
            errors.append(
                {
                    "partition": status_path.stem,
                    "error_type": record.get("error_type"),
                    "source_filename": record.get("source_filename"),
                    "accession": _accession(record.get("source_filename")),
                }
            )
    return errors


def _bundle_unsigned_hash(manifest: Mapping[str, Any]) -> str:
    unsigned = dict(manifest)
    unsigned.pop("bundle_hash", None)
    return canonical_hash(unsigned)


def _source_preflight(repo_root: Path) -> dict[str, Any]:
    record = _verified_file(
        repo_root, SOURCE_MANIFEST_RELATIVE_PATH, SOURCE_MANIFEST_SHA256
    )
    source_path = repo_root / SOURCE_MANIFEST_RELATIVE_PATH
    manifest = _load_json(source_path)
    if manifest.get("bundle_hash") != SOURCE_BUNDLE_SHA256:
        raise ValueError("frozen original-filings bundle hash mismatch")
    if _bundle_unsigned_hash(manifest) != SOURCE_BUNDLE_SHA256:
        raise ValueError("original-filings bundle canonical hash mismatch")
    metadata = manifest.get("metadata")
    if not isinstance(metadata, dict):
        raise ValueError("source manifest metadata is absent")
    keys = (
        "candidate_count",
        "hydrated_count",
        "acceptance_timestamp_pass_count",
        "error_count",
    )
    counts = {key: metadata.get(key) for key in keys}
    if not all(isinstance(value, int) for value in counts.values()):
        raise ValueError("source manifest count fields are invalid")
    errors = _status_errors(source_path.parent)
    if len(errors) != counts["error_count"]:
        raise ValueError("bundle error count does not match partition statuses")
    census = _source_inventory_census(source_path.parent, errors)
    census_failures = []
    if census["raw_inventory_rows"] != counts["hydrated_count"]:
        census_failures.append("raw_inventory_rows_do_not_equal_hydrated_count")
    if census["valid_payload_sha256_count"] != counts["hydrated_count"]:
        census_failures.append("inventory_payload_hash_contract_incomplete")
    if census["exact_acceptance_pass_count"] != counts["acceptance_timestamp_pass_count"]:
        census_failures.append("acceptance_pass_count_mismatch")
    if census["raw_candidate_rows"] != counts["candidate_count"]:
        census_failures.append("candidate_row_count_mismatch")
    if census["ambiguous_duplicate_accessions"]:
        census_failures.append("ambiguous_duplicate_accessions")
    candidate_count = census["unique_candidate_accessions"]
    source_ready_count = census["unique_ready_accessions"]
    coverage = source_ready_count / candidate_count if candidate_count else 0.0
    raw_candidate_count = counts["candidate_count"]
    raw_source_ready_count = min(
        counts["hydrated_count"],
        counts["acceptance_timestamp_pass_count"],
        census["valid_payload_sha256_count"],
    )
    return {
        "record": record,
        "bundle_root": source_path.parent,
        "manifest": manifest,
        "counts": counts,
        "source_candidate_count": candidate_count,
        "source_hydrated_count": source_ready_count,
        "coverage": coverage,
        "raw_source_candidate_count": raw_candidate_count,
        "raw_source_hydrated_count": raw_source_ready_count,
        "raw_coverage": (
            raw_source_ready_count / raw_candidate_count
            if raw_candidate_count
            else 0.0
        ),
        "inventory_census": census,
        "inventory_census_failures": census_failures,
        "errors": errors,
        "excluded_accessions": census["unresolved_error_accessions"],
        "gate_pass": coverage >= AGGREGATE_ORIGINAL_COVERAGE_MIN
        and not census_failures,
    }


def _read_readiness_bound_file(
    repo_root: Path, readiness_relative_path: str, readiness_sha256: str
) -> tuple[dict[str, Any], Path, dict[str, Any]]:
    readiness_record = _verified_file(
        repo_root, readiness_relative_path, readiness_sha256
    )
    readiness = _load_json(repo_root / readiness_relative_path)
    files = readiness.get("data_files")
    if not isinstance(files, list) or len(files) != 1 or not isinstance(files[0], dict):
        raise ValueError(f"readiness must bind exactly one file: {readiness_relative_path}")
    relative_path = files[0].get("path")
    expected_hash = files[0].get("sha256")
    if not isinstance(relative_path, str) or not isinstance(expected_hash, str):
        raise ValueError("readiness file record is invalid")
    data_path = repo_root / relative_path
    if not data_path.is_file():
        raise ValueError(f"readiness-bound file is absent: {relative_path}")
    if _sha256_file(data_path) != expected_hash:
        raise ValueError(f"readiness-bound file hash mismatch: {relative_path}")
    return readiness_record, data_path, files[0]


def _load_earnings_events(
    tape_path: Path, excluded_accessions: set[str]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    events: list[dict[str, Any]] = []
    all_rows = 0
    excluded_rows: list[dict[str, Any]] = []
    duplicate_ids: list[str] = []
    seen: set[str] = set()
    challenge_boundary_encountered = False
    prior_acceptance: datetime | None = None
    with gzip.open(tape_path, "rt", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            payload = json.loads(line)
            if not isinstance(payload, dict):
                raise ValueError(f"invalid Item 2.02 row at line {line_number}")
            all_rows += 1
            accession = str(payload.get("event_id") or "")
            accepted = parse_datetime(str(payload.get("acceptance_datetime_utc")))
            if prior_acceptance is not None and accepted < prior_acceptance:
                raise ValueError("Item 2.02 tape is not ordered by acceptance time")
            prior_acceptance = accepted
            accepted_date = accepted.date()
            if accepted_date > VALIDATION_END:
                challenge_boundary_encountered = True
                break
            if accepted_date < DISCOVERY_START:
                continue
            if accession in excluded_accessions:
                excluded_rows.append(
                    {
                        "event_id": accession,
                        "issuer_cik": _norm_cik(payload.get("issuer_cik")),
                        "accepted_date": accepted_date.isoformat(),
                        "acceptance_datetime_utc": accepted,
                    }
                )
                continue
            if accession in seen:
                duplicate_ids.append(accession)
                continue
            seen.add(accession)
            events.append(
                {
                    "event_id": accession,
                    "issuer_cik": _norm_cik(payload.get("issuer_cik")),
                    "form_type": str(payload.get("form_type") or "").upper(),
                    "acceptance": accepted,
                    "items": str(payload.get("items") or ""),
                    "source_sha256": payload.get("source_sha256"),
                }
            )
    return events, {
        "all_discovery_rows": all_rows,
        "included_pre_outcome_rows": len(events),
        "deterministically_excluded_missing_original_rows": excluded_rows,
        "duplicate_event_ids": sorted(set(duplicate_ids)),
        "challenge_boundary_encountered_then_scan_stopped": challenge_boundary_encountered,
    }


def _inventory_rows(bundle_root: Path) -> Iterator[tuple[str, dict[str, Any]]]:
    for inventory_path in sorted((bundle_root / "data/inventory").glob("*.jsonl.gz")):
        partition = inventory_path.name.split("_inventory", 1)[0]
        with gzip.open(inventory_path, "rt", encoding="utf-8") as stream:
            for line in stream:
                payload = json.loads(line)
                yield partition, payload


def _source_inventory_census(
    bundle_root: Path, source_errors: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    raw_rows = 0
    accessions: Counter[str] = Counter()
    accession_signatures: dict[
        str, set[tuple[object, object, object, object]]
    ] = defaultdict(set)
    valid_hashes = 0
    exact_acceptance_passes = 0
    for _, record in _inventory_rows(bundle_root):
        raw_rows += 1
        accession = str(record.get("accession_number") or "")
        if accession:
            accessions[accession] += 1
            accession_signatures[accession].add(
                (
                    record.get("source_sha256"),
                    record.get("acceptance_datetime_utc"),
                    record.get("acceptance_parse_status"),
                    str(record.get("form_type") or "").upper(),
                )
            )
        source_hash = record.get("source_sha256")
        if isinstance(source_hash, str) and _SHA256.fullmatch(source_hash):
            valid_hashes += 1
        if (
            record.get("acceptance_parse_status") == "PASS"
            and record.get("acceptance_datetime_utc")
        ):
            exact_acceptance_passes += 1
    error_accessions = {
        str(item["accession"])
        for item in source_errors
        if item.get("accession")
    }
    duplicate_accessions = [
        accession for accession, count in accessions.items() if count > 1
    ]
    ambiguous_duplicate_accessions = sorted(
        accession
        for accession in duplicate_accessions
        if len(accession_signatures[accession]) != 1
    )
    unique_ready_accessions = {
        accession
        for accession, signatures in accession_signatures.items()
        if len(signatures) == 1
        and isinstance(next(iter(signatures))[0], str)
        and _SHA256.fullmatch(str(next(iter(signatures))[0]))
        and next(iter(signatures))[1]
        and next(iter(signatures))[2] == "PASS"
    }
    unique_candidate_accessions = set(accessions) | error_accessions
    unresolved_error_accessions = sorted(error_accessions - unique_ready_accessions)
    ready_error_overlap_accessions = sorted(error_accessions & unique_ready_accessions)
    return {
        "raw_inventory_rows": raw_rows,
        "unique_inventory_accessions": len(accessions),
        "duplicate_accession_count": len(duplicate_accessions),
        "duplicate_alias_extra_rows": raw_rows - len(accessions),
        "ambiguous_duplicate_accessions": ambiguous_duplicate_accessions,
        "valid_payload_sha256_count": valid_hashes,
        "exact_acceptance_pass_count": exact_acceptance_passes,
        "error_accession_count": len(error_accessions),
        "candidate_accession_count": len(set(accessions) | error_accessions),
        "unique_ready_accessions": len(unique_ready_accessions),
        "unique_candidate_accessions": len(unique_candidate_accessions),
        "unresolved_error_accessions": unresolved_error_accessions,
        "ready_error_overlap_accessions": ready_error_overlap_accessions,
        "raw_candidate_rows": raw_rows + len(source_errors),
    }


def _validate_event_inventory(
    events: Sequence[dict[str, Any]], bundle_root: Path
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Path]]:
    wanted = {event["event_id"]: event for event in events}
    inventory_by_accession: dict[str, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
    partition_paths: dict[str, Path] = {}
    failures: Counter[str] = Counter()
    for partition, record in _inventory_rows(bundle_root):
        accession = str(record.get("accession_number") or "")
        if accession in wanted:
            inventory_by_accession[accession].append((partition, record))
    found: dict[str, dict[str, Any]] = {}
    for accession, event in wanted.items():
        aliases = inventory_by_accession.get(accession, [])
        if not aliases:
            failures["missing_inventory_row"] += 1
            continue
        signatures = {
            (
                record.get("source_sha256"),
                record.get("acceptance_datetime_utc"),
                record.get("acceptance_parse_status"),
                str(record.get("form_type") or "").upper(),
            )
            for _, record in aliases
        }
        if len(signatures) != 1:
            failures["ambiguous_inventory_aliases"] += 1
            continue
        source_hash, accepted_text, acceptance_status, form_type = next(
            iter(signatures)
        )
        valid = True
        try:
            accepted = parse_datetime(str(accepted_text))
        except (TypeError, ValueError):
            failures["invalid_acceptance_timestamp"] += 1
            valid = False
            accepted = None
        if accepted != event["acceptance"]:
            failures["acceptance_mismatch"] += 1
            valid = False
        if event.get("source_sha256") and source_hash != event["source_sha256"]:
            failures["source_hash_mismatch"] += 1
            valid = False
        if form_type != event["form_type"]:
            failures["form_type_mismatch"] += 1
            valid = False
        if not isinstance(source_hash, str) or not _SHA256.fullmatch(source_hash):
            failures["invalid_source_hash"] += 1
            valid = False
        if acceptance_status != "PASS":
            failures["acceptance_parse_not_pass"] += 1
            valid = False
        if not valid:
            continue
        alias_records = sorted(
            (
                {
                    "partition": partition,
                    "source_filename": record.get("source_filename"),
                    "feed_cik": _norm_cik(record.get("cik")),
                    "feed_filed_date": (
                        _parse_date(record.get("filed_date")).isoformat()
                        if _parse_date(record.get("filed_date"))
                        else None
                    ),
                }
                for partition, record in aliases
            ),
            key=lambda item: (
                str(item.get("source_filename") or ""), item["partition"]
            ),
        )
        canonical_alias = alias_records[0]
        enriched = dict(event)
        enriched.update(
            {
                "inventory_source_sha256": source_hash,
                "inventory_partition": canonical_alias["partition"],
                "source_filename": canonical_alias["source_filename"],
                "source_aliases": alias_records,
            }
        )
        found[accession] = enriched
        for alias in alias_records:
            partition = alias["partition"]
            partition_paths[partition] = (
                bundle_root / "data/partitions" / f"{partition}.tar.gz"
            )
    included = [found[key] for key in sorted(found)]
    return included, {
        "attempted": len(events),
        "included": len(included),
        "failures": dict(sorted(failures.items())),
        "coverage": len(included) / len(events) if events else 0.0,
    }, partition_paths


def _parse_sec_header(header_bytes: bytes) -> dict[str, Any]:
    text = header_bytes.decode("latin-1", errors="replace")
    if "</SEC-HEADER>" in text:
        text = text.split("</SEC-HEADER>", 1)[0]
    accession = _accession(text)
    cik_match = _HEADER_CIK.search(text)
    sic_matches = sorted(set(_HEADER_SIC.findall(text)))
    accepted_match = _HEADER_ACCEPTANCE.search(text)
    form_match = _HEADER_FORM.search(text)
    items = [value.strip() for value in _HEADER_ITEM.findall(text)]
    return {
        "accession": accession,
        "cik": _norm_cik(cik_match.group(1)) if cik_match else None,
        "sic": sic_matches[0] if len(sic_matches) == 1 else None,
        "sic_count": len(sic_matches),
        "acceptance": (
            datetime.strptime(accepted_match.group(1), "%Y%m%d%H%M%S")
            .replace(tzinfo=_ET)
            .astimezone(timezone.utc)
            if accepted_match
            else None
        ),
        "form_type": form_match.group(1).strip().upper() if form_match else None,
        "items": items,
        "item_2_02": any(
            "2.02" in item or "results of operations and financial condition" in item.lower()
            for item in items
        ),
    }


def _scan_partition(args: tuple[str, str, str]) -> tuple[list[dict[str, Any]], list[str]]:
    tar_name, inventory_name, cutoff_text = args
    cutoff = date.fromisoformat(cutoff_text)
    partition = Path(inventory_name).name.split("_inventory", 1)[0]
    inventory_aliases: dict[str, list[dict[str, Any]]] = defaultdict(list)
    failures: list[str] = []
    with gzip.open(inventory_name, "rt", encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            accession = str(row.get("accession_number") or "")
            try:
                accepted = parse_datetime(str(row.get("acceptance_datetime_utc")))
            except (TypeError, ValueError):
                failures.append(f"{accession}:invalid_inventory_acceptance")
                continue
            if accession and accepted.date() <= cutoff:
                inventory_aliases[accession].append(row)
    inventory: dict[str, tuple[dict[str, Any], list[dict[str, Any]]]] = {}
    for accession, aliases in inventory_aliases.items():
        signatures = {
            (
                row.get("source_sha256"),
                row.get("acceptance_datetime_utc"),
                row.get("acceptance_parse_status"),
                str(row.get("form_type") or "").upper(),
            )
            for row in aliases
        }
        if len(signatures) != 1:
            failures.append(f"{accession}:ambiguous_inventory_aliases")
            continue
        canonical = min(
            aliases, key=lambda row: str(row.get("source_filename") or "")
        )
        inventory[accession] = (canonical, aliases)
    rows: list[dict[str, Any]] = []
    if not inventory:
        return rows, failures
    actual_by_accession: dict[str, list[dict[str, Any]]] = defaultdict(list)
    invalid_actual_accessions: set[str] = set()
    with tarfile.open(tar_name, mode="r|gz") as archive:
        for member in archive:
            accession = _accession(member.name)
            if not accession or accession not in inventory or not member.isfile():
                continue
            source = archive.extractfile(member)
            if source is None:
                failures.append(f"{accession}:member_unreadable")
                continue
            digest = hashlib.sha256()
            header = bytearray()
            while True:
                chunk = source.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
                if len(header) < HEADER_LIMIT_BYTES:
                    header.extend(chunk[: HEADER_LIMIT_BYTES - len(header)])
            parsed = _parse_sec_header(bytes(header))
            source_hash = digest.hexdigest()
            expected, _ = inventory[accession]
            checks = {
                "member_accession": parsed["accession"] == accession,
                "source_sha256": source_hash == expected.get("source_sha256"),
                "acceptance": parsed["acceptance"]
                == parse_datetime(str(expected.get("acceptance_datetime_utc"))),
                "form_type": parsed["form_type"]
                == str(expected.get("form_type") or "").upper(),
            }
            if not all(checks.values()):
                invalid_actual_accessions.add(accession)
                continue
            actual_by_accession[accession].append(
                {
                    "event_id": accession,
                    "cik": parsed["cik"],
                    "sic": parsed["sic"],
                    "sic_count": parsed["sic_count"],
                    "acceptance": parsed["acceptance"],
                    "form_type": parsed["form_type"],
                    "item_2_02": parsed["item_2_02"],
                    "source_sha256": source_hash,
                    "source_path": member.name,
                }
            )
    for accession, (expected, aliases) in inventory.items():
        actual_rows = actual_by_accession.get(accession, [])
        if accession in invalid_actual_accessions:
            failures.append(f"{accession}:conflicting_actual_payload_alias")
            continue
        if not actual_rows:
            failures.append(f"{accession}:missing_or_invalid_payload")
            continue
        actual_signatures = {
            (
                row["source_sha256"],
                row["cik"],
                row["sic"],
                row["sic_count"],
                row["acceptance"],
                row["form_type"],
                row["item_2_02"],
            )
            for row in actual_rows
        }
        if len(actual_signatures) != 1:
            failures.append(f"{accession}:conflicting_actual_payload_headers")
            continue
        canonical_actual = min(actual_rows, key=lambda row: row["source_path"])
        alias_lineage = sorted(
            (
                {
                    "partition": partition,
                    "source_filename": row.get("source_filename"),
                    "feed_cik": _norm_cik(row.get("cik")),
                    "feed_filed_date": (
                        _parse_date(row.get("filed_date")).isoformat()
                        if _parse_date(row.get("filed_date"))
                        else None
                    ),
                }
                for row in aliases
            ),
            key=lambda item: str(item.get("source_filename") or ""),
        )
        canonical_actual["source_aliases"] = alias_lineage
        canonical_actual["actual_member_paths"] = sorted(
            row["source_path"] for row in actual_rows
        )
        canonical_actual["feed_cik_discrepancy_count"] = sum(
            item["feed_cik"] != canonical_actual["cik"] for item in alias_lineage
        )
        canonical_actual["feed_filed_date_discrepancy_count"] = sum(
            item["feed_filed_date"] != canonical_actual["acceptance"].date().isoformat()
            for item in alias_lineage
        )
        canonical_actual["inventory_canonical_source_path"] = expected.get(
            "source_filename"
        )
        rows.append(canonical_actual)
    return rows, failures


def _scan_headers(
    bundle_root: Path, max_workers: int
) -> tuple[list[dict[str, Any]], list[str]]:
    tasks = []
    for inventory_path in sorted((bundle_root / "data/inventory").glob("*.jsonl.gz")):
        partition = inventory_path.name.split("_inventory", 1)[0]
        tar_path = bundle_root / "data/partitions" / f"{partition}.tar.gz"
        if tar_path.is_file():
            tasks.append((str(tar_path), str(inventory_path), VALIDATION_END.isoformat()))
    rows: list[dict[str, Any]] = []
    failures: list[str] = []
    if max_workers == 1:
        results = map(_scan_partition, tasks)
    else:
        executor = ProcessPoolExecutor(max_workers=max_workers)
        results = executor.map(_scan_partition, tasks)
    try:
        for partition_rows, partition_failures in results:
            rows.extend(partition_rows)
            failures.extend(partition_failures)
    finally:
        if max_workers != 1:
            executor.shutdown(wait=True)
    failed_accessions = {item.split(":", 1)[0] for item in failures}
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["event_id"]].append(row)
    canonical_rows: list[dict[str, Any]] = []
    for accession, values in grouped.items():
        if accession in failed_accessions:
            continue
        signatures = {
            (
                row["source_sha256"],
                row["cik"],
                row["sic"],
                row["sic_count"],
                row["acceptance"],
                row["form_type"],
                row["item_2_02"],
            )
            for row in values
        }
        if len(signatures) != 1:
            failures.append(f"{accession}:conflicting_cross_partition_payload")
            continue
        canonical = min(values, key=lambda row: row["source_path"])
        canonical["source_aliases"] = sorted(
            {
                canonical_json(alias)
                for row in values
                for alias in row.get("source_aliases", ())
            }
        )
        canonical["source_aliases"] = [
            json.loads(alias) for alias in canonical["source_aliases"]
        ]
        canonical["actual_member_paths"] = sorted(
            {
                path
                for row in values
                for path in row.get("actual_member_paths", ())
            }
        )
        canonical["feed_cik_discrepancy_count"] = sum(
            alias.get("feed_cik") != canonical["cik"]
            for alias in canonical["source_aliases"]
        )
        canonical["feed_filed_date_discrepancy_count"] = sum(
            alias.get("feed_filed_date")
            != canonical["acceptance"].date().isoformat()
            for alias in canonical["source_aliases"]
        )
        canonical_rows.append(canonical)
    canonical_rows.sort(key=lambda row: (row["acceptance"], row["event_id"]))
    return canonical_rows, sorted(set(failures))


def _initialize_header_checkpoint(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=FULL")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS checkpoint_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS completed_partitions (
            partition TEXT PRIMARY KEY,
            inventory_sha256 TEXT NOT NULL,
            inventory_bytes INTEGER NOT NULL,
            tar_sha256 TEXT NOT NULL,
            tar_bytes INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS headers (
            event_id TEXT PRIMARY KEY,
            cik TEXT,
            sic TEXT,
            sic_count INTEGER NOT NULL,
            acceptance TEXT NOT NULL,
            form_type TEXT,
            item_2_02 INTEGER NOT NULL,
            source_sha256 TEXT NOT NULL,
            source_path TEXT NOT NULL,
            source_aliases_json TEXT NOT NULL,
            actual_member_paths_json TEXT NOT NULL,
            feed_cik_discrepancy_count INTEGER NOT NULL,
            feed_filed_date_discrepancy_count INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_headers_acceptance
            ON headers(acceptance, event_id);
        CREATE TABLE IF NOT EXISTS failed_accessions (
            event_id TEXT PRIMARY KEY,
            reason TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS failures (
            partition TEXT NOT NULL,
            event_id TEXT NOT NULL,
            reason TEXT NOT NULL,
            PRIMARY KEY(partition, event_id, reason)
        );
        """
    )
    bindings = {
        "schema_version": CHECKPOINT_SCHEMA_VERSION,
        "runner_sha256": _sha256_file(Path(__file__)),
        "source_bundle_sha256": SOURCE_BUNDLE_SHA256,
        "validation_end": VALIDATION_END.isoformat(),
    }
    existing = dict(connection.execute("SELECT key, value FROM checkpoint_meta"))
    if existing and existing != bindings:
        connection.close()
        raise ValueError("HYP-015 header checkpoint binding mismatch")
    if not existing:
        connection.executemany(
            "INSERT INTO checkpoint_meta(key, value) VALUES(?, ?)",
            sorted(bindings.items()),
        )
        connection.commit()
    return connection


def _merge_header_row(
    connection: sqlite3.Connection, partition: str, row: Mapping[str, Any]
) -> None:
    event_id = str(row["event_id"])
    existing = connection.execute(
        "SELECT * FROM headers WHERE event_id = ?", (event_id,)
    ).fetchone()
    signature = (
        row.get("source_sha256"),
        row.get("cik"),
        row.get("sic"),
        row.get("sic_count"),
        row.get("acceptance"),
        row.get("form_type"),
        bool(row.get("item_2_02")),
    )
    if existing is not None:
        existing_signature = (
            existing["source_sha256"],
            existing["cik"],
            existing["sic"],
            existing["sic_count"],
            parse_datetime(existing["acceptance"]),
            existing["form_type"],
            bool(existing["item_2_02"]),
        )
        if signature != existing_signature:
            reason = "conflicting_cross_partition_payload"
            connection.execute(
                "INSERT OR REPLACE INTO failed_accessions(event_id, reason) VALUES(?, ?)",
                (event_id, reason),
            )
            connection.execute(
                "INSERT OR IGNORE INTO failures VALUES(?, ?, ?)",
                (partition, event_id, reason),
            )
            return
        aliases = {
            canonical_json(item): item
            for item in (
                *json.loads(existing["source_aliases_json"]),
                *row.get("source_aliases", ()),
            )
        }
        paths = sorted(
            set(json.loads(existing["actual_member_paths_json"]))
            | set(row.get("actual_member_paths", ()))
        )
        source_path = min(existing["source_path"], str(row["source_path"]))
        alias_values = [aliases[key] for key in sorted(aliases)]
    else:
        alias_values = list(row.get("source_aliases", ()))
        paths = sorted(set(row.get("actual_member_paths", ())))
        source_path = str(row["source_path"])
    feed_cik = sum(
        item.get("feed_cik") != row.get("cik") for item in alias_values
    )
    acceptance_date = row["acceptance"].date().isoformat()
    feed_filed = sum(
        item.get("feed_filed_date") != acceptance_date for item in alias_values
    )
    connection.execute(
        """
        INSERT OR REPLACE INTO headers(
            event_id, cik, sic, sic_count, acceptance, form_type, item_2_02,
            source_sha256, source_path, source_aliases_json,
            actual_member_paths_json, feed_cik_discrepancy_count,
            feed_filed_date_discrepancy_count
        ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_id,
            row.get("cik"),
            row.get("sic"),
            int(row.get("sic_count") or 0),
            row["acceptance"].isoformat(),
            row.get("form_type"),
            int(bool(row.get("item_2_02"))),
            row["source_sha256"],
            source_path,
            canonical_json(alias_values),
            canonical_json(paths),
            feed_cik,
            feed_filed,
        ),
    )


def _scan_headers_to_checkpoint(
    bundle_root: Path,
    checkpoint_path: Path,
    phase_observer: Callable[[str], None] | None = None,
) -> _HeaderIndex:
    """Scan one archive partition at a time and commit each atomically."""

    connection = _initialize_header_checkpoint(checkpoint_path)
    try:
        completed = {
            row["partition"]: dict(row)
            for row in connection.execute("SELECT * FROM completed_partitions")
        }
        tasks = []
        discovered_partitions: set[str] = set()
        for inventory_path in sorted(
            (bundle_root / "data/inventory").glob("*.jsonl.gz")
        ):
            partition = inventory_path.name.split("_inventory", 1)[0]
            discovered_partitions.add(partition)
            tar_path = bundle_root / "data/partitions" / f"{partition}.tar.gz"
            if partition in completed:
                record = completed[partition]
                if not tar_path.is_file():
                    raise ValueError(
                        f"completed SEC header partition source missing: {partition}"
                    )
                observed = {
                    "inventory_sha256": _sha256_file(inventory_path),
                    "inventory_bytes": inventory_path.stat().st_size,
                    "tar_sha256": _sha256_file(tar_path),
                    "tar_bytes": tar_path.stat().st_size,
                }
                expected = {
                    key: record[key]
                    for key in (
                        "inventory_sha256",
                        "inventory_bytes",
                        "tar_sha256",
                        "tar_bytes",
                    )
                }
                if observed != expected:
                    raise ValueError(
                        f"completed SEC header partition integrity mismatch: {partition}"
                    )
                if phase_observer is not None:
                    phase_observer(f"header_partition_reverified::{partition}")
                continue
            if not tar_path.is_file():
                raise ValueError(f"SEC header partition archive missing: {partition}")
            tasks.append((partition, tar_path, inventory_path))
        missing_completed = sorted(set(completed) - discovered_partitions)
        if missing_completed:
            raise ValueError(
                "completed SEC header inventory partition missing: "
                + ",".join(missing_completed)
            )
        for partition, tar_path, inventory_path in tasks:
            before = {
                "inventory_stat": _file_stat_signature(inventory_path),
                "tar_stat": _file_stat_signature(tar_path),
            }
            rows, failures = _scan_partition(
                (str(tar_path), str(inventory_path), VALIDATION_END.isoformat())
            )
            after = {
                "inventory_stat": _file_stat_signature(inventory_path),
                "tar_stat": _file_stat_signature(tar_path),
            }
            if after != before:
                raise ValueError(
                    f"SEC header partition changed during scan: {partition}"
                )
            committed_hashes = {
                "inventory_sha256": _sha256_file(inventory_path),
                "inventory_bytes": after["inventory_stat"][0],
                "tar_sha256": _sha256_file(tar_path),
                "tar_bytes": after["tar_stat"][0],
            }
            with connection:
                for failure in failures:
                    event_id, reason = failure.split(":", 1)
                    connection.execute(
                        "INSERT OR IGNORE INTO failures VALUES(?, ?, ?)",
                        (partition, event_id, reason),
                    )
                    connection.execute(
                        "INSERT OR REPLACE INTO failed_accessions VALUES(?, ?)",
                        (event_id, reason),
                    )
                for row in rows:
                    _merge_header_row(connection, partition, row)
                connection.execute(
                    """
                    INSERT INTO completed_partitions(
                        partition, inventory_sha256, inventory_bytes,
                        tar_sha256, tar_bytes
                    ) VALUES(?, ?, ?, ?, ?)
                    """,
                    (
                        partition,
                        committed_hashes["inventory_sha256"],
                        committed_hashes["inventory_bytes"],
                        committed_hashes["tar_sha256"],
                        committed_hashes["tar_bytes"],
                    ),
                )
            del rows, failures
            if phase_observer is not None:
                phase_observer(f"header_partition_committed::{partition}")
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        connection.close()
    return _HeaderIndex(checkpoint_path)


def _reaction_session(accepted: datetime, sessions: Sequence[date]) -> date | None:
    local = accepted.astimezone(_ET)
    index = bisect.bisect_left(sessions, local.date())
    if index >= len(sessions):
        return None
    if sessions[index] == local.date() and local.timetz().replace(tzinfo=None) < time(9, 30):
        return sessions[index]
    if sessions[index] == local.date():
        index += 1
    return sessions[index] if index < len(sessions) else None


def _quarter_start(value: date) -> date:
    return date(value.year, ((value.month - 1) // 3) * 3 + 1, 1)


def _load_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def _active(interval: Mapping[str, Any], when: date, start_key: str, end_key: str) -> bool:
    start = _parse_date(interval.get(start_key))
    end = _parse_date(interval.get(end_key))
    return bool(start and start <= when and (end is None or when <= end))


def _build_identity(repo_root: Path) -> dict[str, Any]:
    identity_inputs = [
        _verified_file(repo_root, SECURITY_MASTER_RELATIVE_PATH, SECURITY_MASTER_SHA256),
        _verified_file(repo_root, MEMBERSHIP_RELATIVE_PATH, MEMBERSHIP_SHA256),
        _verified_file(repo_root, CIK_MAPPING_RELATIVE_PATH, CIK_MAPPING_SHA256),
    ]
    security_rows = _load_csv(repo_root / SECURITY_MASTER_RELATIVE_PATH)
    membership_rows = _load_csv(repo_root / MEMBERSHIP_RELATIVE_PATH)
    mapping_rows = _load_csv(repo_root / CIK_MAPPING_RELATIVE_PATH)
    memberships: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in membership_rows:
        memberships[row["security_id"]].append(row)
    security_by_id = {row["security_id"]: row for row in security_rows}
    by_cik: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in mapping_rows:
        cik = _norm_cik(row.get("cik"))
        security = security_by_id.get(row.get("security_id", ""))
        if not cik or security is None or security.get("category") != "Domestic Common Stock":
            continue
        joined = dict(row)
        joined.update(
            {
                "firstpricedate": security.get("firstpricedate"),
                "lastpricedate": security.get("lastpricedate"),
            }
        )
        by_cik[cik].append(joined)
    for rows in by_cik.values():
        rows.sort(key=lambda row: (row.get("effective_start", ""), row["security_id"]))
    return {
        "by_cik": by_cik,
        "memberships": memberships,
        "security_by_id": security_by_id,
        "input_records": identity_inputs,
    }


def _unique_security(identity: Mapping[str, Any], cik: str, when: date) -> tuple[str | None, str]:
    matches = []
    for row in identity["by_cik"].get(cik, ()):
        if not _active(row, when, "effective_start", "effective_end"):
            continue
        security_id = row["security_id"]
        if any(
            _active(item, when, "membership_start_date", "membership_end_date")
            for item in identity["memberships"].get(security_id, ())
        ):
            matches.append(security_id)
    matches = sorted(set(matches))
    if len(matches) == 1:
        return matches[0], "UNIQUE"
    return None, "ABSENT" if not matches else "AMBIGUOUS"


def _calendar_from_panel(panel_path: Path) -> list[date]:
    import pyarrow.parquet as pq

    sessions: set[date] = set()
    parquet = pq.ParquetFile(panel_path)
    for batch in parquet.iter_batches(
        columns=["date"], batch_size=CALENDAR_SCAN_BATCH_SIZE
    ):
        sessions.update(value for value in batch.column(0).to_pylist() if value <= VALIDATION_END)
    return sorted(sessions)


def _latest_sic_histories(headers: Sequence[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    histories: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in headers:
        if row.get("cik") and row.get("sic"):
            histories[row["cik"]].append(row)
    return histories


def _reported_in_quarter(
    event_dates: Mapping[str, Sequence[datetime]], cik: str, quarter: date, cutoff: datetime
) -> bool:
    values = event_dates.get(cik, ())
    left = bisect.bisect_left(values, datetime.combine(quarter, time.min, tzinfo=timezone.utc))
    return left < len(values) and values[left] <= cutoff


def _header_get(
    headers: Sequence[dict[str, Any]] | _HeaderIndex, event_id: str
) -> dict[str, Any] | None:
    if isinstance(headers, _HeaderIndex):
        return headers.get(event_id)
    for row in headers:
        if row["event_id"] == event_id:
            return row
    return None


def _header_iter(
    headers: Sequence[dict[str, Any]] | _HeaderIndex,
) -> Iterator[dict[str, Any]]:
    if isinstance(headers, _HeaderIndex):
        yield from headers
    else:
        yield from sorted(headers, key=lambda row: (row["acceptance"], row["event_id"]))


def _build_structural_clusters(
    *,
    events: Sequence[dict[str, Any]],
    headers: Sequence[dict[str, Any]] | _HeaderIndex,
    identity: Mapping[str, Any],
    sessions: Sequence[date],
    excluded_event_metadata: Sequence[Mapping[str, Any]] = (),
    cluster_sink: Callable[[dict[str, Any]], None] | None = None,
    reporter_spool_path: Path | None = None,
    max_cluster_candidates: int | None = None,
    phase_observer: Callable[[str], None] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    event_dates: dict[str, list[datetime]] = defaultdict(list)
    reporter_failures: Counter[str] = Counter()
    reporter_metadata_discrepancies: Counter[str] = Counter()
    reporter_rows: list[dict[str, Any]] = []
    reporter_connection: sqlite3.Connection | None = None
    reporter_included = 0
    if reporter_spool_path is not None:
        reporter_spool_path.parent.mkdir(parents=True, exist_ok=True)
        reporter_spool_path.unlink(missing_ok=True)
        reporter_connection = sqlite3.connect(reporter_spool_path)
        reporter_connection.execute(
            """
            CREATE TABLE reporters (
                reaction_session TEXT NOT NULL,
                sic TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                event_id TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL
            )
            """
        )
        reporter_connection.execute(
            "CREATE INDEX idx_reporter_group ON reporters(reaction_session, sic, sequence)"
        )
    for event in events:
        header = _header_get(headers, event["event_id"])
        if header and header.get("cik") and header.get("acceptance"):
            event_dates[header["cik"]].append(header["acceptance"])
    for event in excluded_event_metadata:
        cik = event.get("issuer_cik")
        accepted = event.get("acceptance_datetime_utc")
        if isinstance(cik, str) and isinstance(accepted, datetime):
            event_dates[cik].append(accepted)
    for values in event_dates.values():
        values.sort()
    for event in events:
        header = _header_get(headers, event["event_id"])
        if header is None:
            reporter_failures["missing_verified_header"] += 1
            continue
        if header.get("acceptance") != event["acceptance"]:
            reporter_failures["header_acceptance_mismatch"] += 1
            continue
        if header.get("form_type") != event["form_type"]:
            reporter_failures["header_form_type_mismatch"] += 1
            continue
        if header.get("source_sha256") != event.get("inventory_source_sha256"):
            reporter_failures["header_source_hash_mismatch"] += 1
            continue
        if not header.get("cik"):
            reporter_failures["missing_header_cik"] += 1
            continue
        if event.get("issuer_cik") != header["cik"]:
            reporter_metadata_discrepancies["tape_cik"] += 1
        if not header.get("item_2_02"):
            reporter_failures["original_header_not_item_2_02"] += 1
            continue
        if header.get("sic_count") != 1 or not header.get("sic"):
            reporter_failures["nonunique_or_missing_sic"] += 1
            continue
        if header.get("form_type") == "8-K/A":
            reporter_failures["unresolved_amendment"] += 1
            continue
        reaction = _reaction_session(header["acceptance"], sessions)
        if reaction is None:
            reporter_failures["reaction_session_absent"] += 1
            continue
        security_id, mapping = _unique_security(identity, header["cik"], reaction)
        if security_id is None:
            reporter_failures[f"reporter_mapping_{mapping.lower()}"] += 1
            continue
        reporter_record = {
                **event,
                "issuer_cik": header["cik"],
                "acceptance": header["acceptance"],
                "form_type": header["form_type"],
                "sic": header["sic"],
                "reaction_session": reaction,
                "security_id": security_id,
                "header_source_sha256": header["source_sha256"],
                "header_source_path": header.get("source_path"),
                "header_source_aliases": header.get("source_aliases", ()),
                "header_actual_member_paths": header.get(
                    "actual_member_paths", ()
                ),
            }
        reporter_included += 1
        if reporter_connection is None:
            reporter_rows.append(reporter_record)
        else:
            reporter_connection.execute(
                "INSERT INTO reporters VALUES(?, ?, ?, ?, ?)",
                (
                    reporter_record["reaction_session"].isoformat(),
                    reporter_record["sic"],
                    reporter_included,
                    reporter_record["event_id"],
                    canonical_json(reporter_record),
                ),
            )
            if reporter_included % 1_000 == 0:
                reporter_connection.commit()
                if phase_observer is not None:
                    phase_observer(
                        f"structural_reporters_spooled::{reporter_included}"
                    )
    if reporter_connection is not None:
        reporter_connection.commit()

    def reporter_groups() -> Iterator[tuple[date, str, list[dict[str, Any]]]]:
        if reporter_connection is None:
            grouped: dict[tuple[date, str], list[dict[str, Any]]] = defaultdict(list)
            for row in reporter_rows:
                grouped[(row["reaction_session"], row["sic"])].append(row)
            for (reaction, sic), rows in sorted(grouped.items()):
                yield reaction, sic, rows
            return
        group_cursor = reporter_connection.execute(
            "SELECT DISTINCT reaction_session, sic FROM reporters ORDER BY 1, 2"
        )
        for reaction_text, sic in group_cursor:
            rows = []
            for (payload_text,) in reporter_connection.execute(
                """
                SELECT payload_json FROM reporters
                WHERE reaction_session = ? AND sic = ? ORDER BY sequence
                """,
                (reaction_text, sic),
            ):
                row = json.loads(payload_text)
                row["acceptance"] = parse_datetime(row["acceptance"])
                row["reaction_session"] = date.fromisoformat(
                    row["reaction_session"]
                )
                rows.append(row)
            yield date.fromisoformat(reaction_text), sic, rows
    if phase_observer is not None:
        phase_observer("structural_reporter_grouping_complete")

    headers_iterator = iter(_header_iter(headers))
    next_header = next(headers_iterator, None)
    current_sic: dict[str, str] = {}
    current_sic_source: dict[str, dict[str, Any]] = {}
    ciks_by_sic: dict[str, set[str]] = defaultdict(set)
    ciks_by_division: dict[str, set[str]] = defaultdict(set)
    peer_mapping: Counter[str] = Counter()
    control_mapping: Counter[str] = Counter()
    clusters: list[dict[str, Any]] = []
    for reaction, sic, reporters in reporter_groups():
        cutoff = datetime.combine(reaction, time(16, 0), tzinfo=_ET).astimezone(timezone.utc)
        while next_header is not None and next_header["acceptance"] <= cutoff:
            update = next_header
            next_header = next(headers_iterator, None)
            cik = update.get("cik")
            new_sic = update.get("sic")
            if not cik or not new_sic:
                continue
            old_sic = current_sic.get(cik)
            if old_sic:
                ciks_by_sic[old_sic].discard(cik)
                ciks_by_division[old_sic[:2]].discard(cik)
            current_sic[cik] = new_sic
            current_sic_source[cik] = {
                "event_id": update["event_id"],
                "source_path": update.get("source_path"),
                "source_sha256": update.get("source_sha256"),
                "acceptance": update.get("acceptance"),
                "source_aliases": update.get("source_aliases", ()),
                "actual_member_paths": update.get("actual_member_paths", ()),
            }
            ciks_by_sic[new_sic].add(cik)
            ciks_by_division[new_sic[:2]].add(cik)
        reporter_ciks = sorted({row["issuer_cik"] for row in reporters})
        reporter_set = set(reporter_ciks)

        candidate_count = len(reporters)
        if (
            max_cluster_candidates is not None
            and candidate_count > max_cluster_candidates
        ):
            raise MemoryError(
                "HYP-015 structural cluster exceeds deterministic pre-outcome "
                "participant guard: "
                f"{reaction.isoformat()}::{sic}::{candidate_count}>"
                f"{max_cluster_candidates}"
            )

        def map_potentials(
            ciks: Iterable[str], counter: Counter[str], relevance: str
        ) -> list[dict[str, Any]]:
            nonlocal candidate_count
            potentials = []
            for peer_cik in sorted(set(ciks) - reporter_set):
                if _reported_in_quarter(
                    event_dates, peer_cik, _quarter_start(reaction), cutoff
                ):
                    continue
                candidate_count += 1
                if (
                    max_cluster_candidates is not None
                    and candidate_count > max_cluster_candidates
                ):
                    raise MemoryError(
                        "HYP-015 structural cluster exceeds deterministic "
                        "pre-outcome participant guard: "
                        f"{reaction.isoformat()}::{sic}::{candidate_count}>"
                        f"{max_cluster_candidates}"
                    )
                counter["attempted"] += 1
                security_id, mapping = _unique_security(identity, peer_cik, reaction)
                if security_id is None:
                    counter[mapping.lower()] += 1
                else:
                    counter["mapped"] += 1
                potentials.append(
                    {
                        "cik": peer_cik,
                        "security_id": security_id,
                        "source_lineage": [
                            {
                                "event_id": row["event_id"],
                                "source_sha256": row["header_source_sha256"],
                                "canonical_source_path": row[
                                    "header_source_path"
                                ],
                                "advertised_aliases": row[
                                    "header_source_aliases"
                                ],
                                "actual_member_paths": row[
                                    "header_actual_member_paths"
                                ],
                            }
                            for row in reporters
                            if row["security_id"] == security_id
                        ],
                        "mapping_status": mapping,
                        "relevance": relevance,
                        "causal_sic_source": current_sic_source.get(peer_cik),
                    }
                )
            return potentials

        peers = map_potentials(
            ciks_by_sic.get(sic, ()), peer_mapping, "FOUR_DIGIT_PEER"
        )
        control_ciks = ciks_by_division.get(sic[:2], ()) - ciks_by_sic.get(sic, set())
        controls = map_potentials(
            control_ciks, control_mapping, "TWO_DIGIT_INDUSTRY_CONTROL"
        )
        index = bisect.bisect_right(sessions, reaction)
        if index + 4 >= len(sessions):
            reporter_failures["holding_calendar_incomplete"] += len(reporters)
            continue
        entry = sessions[index]
        exit_date = sessions[index + 4]
        exit_cutoff = datetime.combine(
            exit_date, time(16, 0), tzinfo=_ET
        ).astimezone(timezone.utc)
        peer_report_during_hold_security_ids = sorted(
            {
                item["security_id"]
                for item in peers
                if item.get("security_id")
                and any(
                    cutoff < accepted <= exit_cutoff
                    for accepted in event_dates.get(item["cik"], ())
                )
            }
        )
        cluster_id = hashlib.sha256(
            canonical_json(
                {
                    "reaction_session": reaction.isoformat(),
                    "sic": sic,
                    "reporter_event_ids": sorted(row["event_id"] for row in reporters),
                }
            ).encode()
        ).hexdigest()[:20]
        cluster = {
                "cluster_id": f"HYP015-{cluster_id}",
                "reaction_session": reaction,
                "entry_session": entry,
                "exit_session": exit_date,
                "sic": sic,
                "reporter_event_ids": sorted(row["event_id"] for row in reporters),
                "reporter_ciks": reporter_ciks,
                "reporter_security_ids": sorted({row["security_id"] for row in reporters}),
                "reporters": [
                    {
                        "accessions": sorted(
                            row["event_id"]
                            for row in reporters
                            if row["security_id"] == security_id
                        ),
                        "cik": next(
                            row["issuer_cik"]
                            for row in reporters
                            if row["security_id"] == security_id
                        ),
                        "security_id": security_id,
                    }
                    for security_id in sorted(
                        {row["security_id"] for row in reporters}
                    )
                ],
                "peers": peers,
                "controls": controls,
                "peer_report_during_hold_security_ids": (
                    peer_report_during_hold_security_ids
                ),
                "potential_cluster_key": (
                    f"{reaction.isoformat()}::{sic}::"
                    + ",".join(sorted(row["event_id"] for row in reporters))
                ),
            }
        if cluster_sink is None:
            clusters.append(cluster)
        else:
            cluster_sink(cluster)
    if reporter_connection is not None:
        reporter_connection.close()
    mapping_rate = (
        peer_mapping["mapped"] / peer_mapping["attempted"]
        if peer_mapping["attempted"]
        else 0.0
    )
    control_mapping_rate = (
        control_mapping["mapped"] / control_mapping["attempted"]
        if control_mapping["attempted"]
        else 0.0
    )
    return clusters, {
        "reporter_attempted": len(events),
        "reporter_included": reporter_included,
        "reporter_failures": dict(sorted(reporter_failures.items())),
        "reporter_metadata_discrepancies": dict(
            sorted(reporter_metadata_discrepancies.items())
        ),
        "peer_mapping": dict(sorted(peer_mapping.items())),
        "peer_mapping_rate": mapping_rate,
        "control_mapping": dict(sorted(control_mapping.items())),
        "control_mapping_rate": control_mapping_rate,
    }


def _required_dates(cluster: Mapping[str, Any], sessions: Sequence[date]) -> tuple[list[date], list[date]]:
    reaction = cluster["reaction_session"]
    index = bisect.bisect_left(sessions, reaction)
    reporter_dates = list(sessions[index - 20 : index + 1]) if index >= 20 else []
    entry_index = bisect.bisect_left(sessions, cluster["entry_session"])
    peer_dates = list(sessions[entry_index : entry_index + 5])
    return reporter_dates, peer_dates


def _scan_requested_market_rows(
    panel_path: Path,
    requests: Mapping[str, set[date]],
    reaction_pairs: set[tuple[str, date]],
) -> dict[tuple[str, date], dict[str, Any]]:
    import pyarrow as pa
    import pyarrow.dataset as ds

    challenge_start = date(2025, 1, 1)
    requested_pairs = {
        (security_id, row_date)
        for security_id, dates in requests.items()
        for row_date in dates
    }
    if any(row_date >= challenge_start for _, row_date in requested_pairs):
        raise ValueError("market-row request crosses the sealed challenge boundary")
    if not requested_pairs:
        return {}
    found: dict[tuple[str, date], dict[str, Any]] = {}
    columns = [
        "security_id",
        "date",
        "open",
        "close",
        "closeadj",
        "dollar_ADV_20",
        "corporate_action_id",
        "delisting_return",
        "terminal_return",
        "adjustment_available_at",
        "available_at",
    ]
    dataset = ds.dataset(panel_path, format="parquet")
    security_ids = sorted(requests)
    challenge_filter = ds.field("date") < pa.scalar(
        challenge_start, type=pa.date32()
    )

    def requested_batches() -> Iterator[Any]:
        for start in range(0, len(security_ids), MARKET_SCAN_SECURITY_CHUNK):
            pair_filter = None
            for security_id in security_ids[
                start : start + MARKET_SCAN_SECURITY_CHUNK
            ]:
                dates = sorted(requests[security_id])
                if not dates:
                    continue
                security_filter = (
                    ds.field("security_id") == security_id
                ) & ds.field("date").isin(dates)
                pair_filter = (
                    security_filter
                    if pair_filter is None
                    else pair_filter | security_filter
                )
            if pair_filter is not None:
                yield from dataset.to_batches(
                    columns=columns,
                    filter=challenge_filter & pair_filter,
                    batch_size=MARKET_SCAN_BATCH_SIZE,
                )

    for batch in requested_batches():
        security_values = batch.column(0).to_pylist()
        date_values = batch.column(1).to_pylist()
        validity = {
            name: batch.column(index).is_valid().to_pylist()
            for index, name in enumerate(columns)
            if index >= 2
        }
        corporate_action_values = batch.column(6).to_pylist()
        adjustment_times = batch.column(9).to_pylist()
        available_times = batch.column(10).to_pylist()
        for index, (security_id, row_date) in enumerate(
            zip(security_values, date_values)
        ):
            key = (security_id, row_date)
            if row_date >= challenge_start or key not in requested_pairs:
                raise ValueError(
                    "market scanner returned an unrequested or challenge row"
                )
            available_at = available_times[index]
            adjustment_at = adjustment_times[index]
            if available_at is not None and available_at.tzinfo is None:
                available_at = available_at.replace(tzinfo=timezone.utc)
            if adjustment_at is not None and adjustment_at.tzinfo is None:
                adjustment_at = adjustment_at.replace(tzinfo=timezone.utc)
            corporate_action_id = corporate_action_values[index]
            corporate_action_present = bool(
                isinstance(corporate_action_id, str) and corporate_action_id.strip()
            )
            record = {
                "row_present": True,
                "open_valid": validity["open"][index],
                "close_valid": validity["close"][index],
                "closeadj_valid": validity["closeadj"][index],
                "available_at": available_at,
                "adjustment_available_at": adjustment_at,
                "corporate_action_present": corporate_action_present,
                "corporate_action_lineage_present": bool(
                    not corporate_action_present
                    or (
                        validity["closeadj"][index]
                        and adjustment_at is not None
                    )
                ),
                "terminal_value_present": bool(
                    validity["delisting_return"][index]
                    or validity["terminal_return"][index]
                ),
            }
            if key in found:
                record["duplicate_row"] = True
            if key in reaction_pairs:
                close_value = batch.column(3)[index].as_py()
                adv_value = batch.column(5)[index].as_py()
                record.update(
                    {
                        "reaction_close_valid": close_value is not None
                        and math.isfinite(close_value),
                        "price_floor_pass": close_value is not None
                        and math.isfinite(close_value)
                        and close_value >= PRICE_FLOOR,
                        "adv_valid": adv_value is not None
                        and math.isfinite(adv_value),
                        "adv_floor_pass": adv_value is not None
                        and math.isfinite(adv_value)
                        and adv_value >= ADV_FLOOR,
                    }
                )
            found[key] = record
    return found


def _lineage_complete(
    found: Mapping[tuple[str, date], Mapping[str, Any]],
    security_id: str,
    required_dates: Sequence[date],
    *,
    require_entry_open: bool,
    selection_deadline: datetime | None = None,
) -> bool:
    if not required_dates:
        return False
    for offset, row_date in enumerate(required_dates):
        row = found.get((security_id, row_date), {})
        if (
            not row.get("row_present")
            or row.get("duplicate_row")
            or not row.get("closeadj_valid")
            or not row.get("corporate_action_lineage_present")
            or row.get("terminal_value_present")
            or (require_entry_open and offset == 0 and not row.get("open_valid"))
        ):
            return False
        if selection_deadline is not None:
            available_at = row.get("available_at")
            if not isinstance(available_at, datetime) or available_at > selection_deadline:
                return False
            if row.get("corporate_action_present"):
                adjustment_at = row.get("adjustment_available_at")
                if (
                    not isinstance(adjustment_at, datetime)
                    or adjustment_at > selection_deadline
                ):
                    return False
    return True


def _apply_path_liquidity_overlap_chunk(
    *,
    clusters: list[dict[str, Any]],
    sessions: Sequence[date],
    identity: Mapping[str, Any],
    panel_path: Path,
) -> dict[str, Any]:
    """Apply the no-return market gates to one bounded cluster chunk."""

    requests: dict[str, set[date]] = defaultdict(set)
    reaction_pairs: set[tuple[str, date]] = set()
    for cluster in clusters:
        reporter_dates, peer_dates = _required_dates(cluster, sessions)
        cluster["reporter_required_sessions"] = reporter_dates
        cluster["peer_required_sessions"] = peer_dates
        for security_id in cluster["reporter_security_ids"]:
            requests[security_id].update(reporter_dates)
            requests[security_id].update(peer_dates)
            reaction_pairs.add((security_id, cluster["reaction_session"]))
        for candidate in (*cluster["peers"], *cluster["controls"]):
            security_id = candidate.get("security_id")
            if not security_id:
                continue
            requests[security_id].update(peer_dates)
            requests[security_id].add(cluster["reaction_session"])
            reaction_pairs.add((security_id, cluster["reaction_session"]))
    requested_pair_count = sum(len(values) for values in requests.values())
    if requested_pair_count > MAX_MARKET_REQUEST_PAIRS:
        raise MemoryError(
            "HYP-015 market request shard exceeds deterministic pre-outcome guard: "
            f"{requested_pair_count}>{MAX_MARKET_REQUEST_PAIRS}"
        )
    found = _scan_requested_market_rows(panel_path, requests, reaction_pairs)
    path_failures: Counter[str] = Counter()
    included_peer_ids: set[str] = set()
    included_sics: set[str] = set()
    validation_clusters = 0
    mapping_path_counts: Counter[str] = Counter()
    for cluster in clusters:
        entry_decision = datetime.combine(
            cluster["entry_session"], time(9, 30), tzinfo=_ET
        ).astimezone(timezone.utc)
        reporter_lineage_ok = True
        reporter_universe_ok = True
        for security_id in cluster["reporter_security_ids"]:
            if not _lineage_complete(
                found,
                security_id,
                cluster["reporter_required_sessions"],
                require_entry_open=False,
                selection_deadline=entry_decision,
            ):
                path_failures["reporter_reaction_or_history_path_missing"] += 1
                reporter_lineage_ok = False
            if not _lineage_complete(
                found,
                security_id,
                cluster["peer_required_sessions"],
                require_entry_open=True,
            ):
                path_failures[
                    "reporter_holding_path_or_terminal_lineage_incomplete"
                ] += 1
                reporter_lineage_ok = False
            reaction_record = found.get(
                (security_id, cluster["reaction_session"]), {}
            )
            if not reaction_record.get("price_floor_pass") or not reaction_record.get("adv_floor_pass"):
                path_failures["reporter_contemporaneous_liquidity_fail"] += 1
                reporter_universe_ok = False

        def qualify_pool(
            rows: Sequence[dict[str, Any]], pool_name: str
        ) -> list[dict[str, Any]]:
            included = []
            for candidate in rows:
                mapping_path_counts[f"{pool_name}_potential"] += 1
                security_id = candidate.get("security_id")
                if not security_id:
                    candidate["exclusion_reason"] = (
                        f"{pool_name}_mapping_{candidate['mapping_status'].lower()}"
                    )
                    candidate["exclusion_class"] = "LINEAGE_MISSING"
                    path_failures[candidate["exclusion_reason"]] += 1
                    continue
                mapping_path_counts[f"{pool_name}_mapped"] += 1
                path_ok = _lineage_complete(
                    found,
                    security_id,
                    cluster["peer_required_sessions"],
                    require_entry_open=True,
                )
                if not path_ok:
                    terminal_required = any(
                        found.get((security_id, value), {}).get(
                            "terminal_value_present"
                        )
                        for value in cluster["peer_required_sessions"]
                    )
                    candidate["exclusion_reason"] = (
                        f"{pool_name}_terminal_outcome_required"
                        if terminal_required
                        else f"{pool_name}_holding_path_or_lineage_incomplete"
                    )
                    candidate["exclusion_class"] = "LINEAGE_MISSING"
                    path_failures[candidate["exclusion_reason"]] += 1
                    security = identity["security_by_id"].get(security_id, {})
                    last_price = _parse_date(security.get("lastpricedate"))
                    if last_price and last_price < cluster["exit_session"]:
                        candidate["terminal_disposition"] = "UNRESOLVED_INSIDE_WINDOW"
                        path_failures[
                            f"{pool_name}_unresolved_terminal_inside_holding_window"
                        ] += 1
                    continue
                mapping_path_counts[f"{pool_name}_path_complete"] += 1
                reaction_record = found.get(
                    (security_id, cluster["reaction_session"]), {}
                )
                if (
                    not isinstance(reaction_record.get("available_at"), datetime)
                    or reaction_record["available_at"] > entry_decision
                    or (
                        reaction_record.get("corporate_action_present")
                        and (
                            not isinstance(
                                reaction_record.get("adjustment_available_at"),
                                datetime,
                            )
                            or reaction_record["adjustment_available_at"]
                            > entry_decision
                        )
                    )
                ):
                    candidate["exclusion_reason"] = (
                        f"{pool_name}_selection_input_not_available_by_entry"
                    )
                    candidate["exclusion_class"] = "LINEAGE_MISSING"
                    path_failures[candidate["exclusion_reason"]] += 1
                    continue
                if not reaction_record.get("price_floor_pass"):
                    candidate["exclusion_reason"] = f"{pool_name}_price_floor_fail"
                    candidate["exclusion_class"] = "UNIVERSE_INELIGIBLE"
                    path_failures[candidate["exclusion_reason"]] += 1
                    continue
                if not reaction_record.get("adv_floor_pass"):
                    candidate["exclusion_reason"] = f"{pool_name}_adv_floor_fail"
                    candidate["exclusion_class"] = "UNIVERSE_INELIGIBLE"
                    path_failures[candidate["exclusion_reason"]] += 1
                    continue
                candidate["included"] = True
                candidate["terminal_disposition"] = "COMPLETE_THROUGH_EXIT"
                candidate["terminal_outcome_required"] = False
                candidate["overlap_key"] = security_id
                candidate["overlap_entry_session"] = cluster["entry_session"]
                candidate["overlap_exit_session"] = cluster["exit_session"]
                included.append(candidate)
            return included

        eligible_peers = qualify_pool(cluster["peers"], "peer")
        eligible_controls = qualify_pool(cluster["controls"], "control")
        cluster["reporter_lineage_pass"] = reporter_lineage_ok
        cluster["reporter_universe_eligible"] = reporter_universe_ok
        cluster["included_peers"] = eligible_peers if reporter_lineage_ok else []
        cluster["included_controls"] = eligible_controls if reporter_lineage_ok else []
        cluster["structural_breadth_pass"] = bool(
            reporter_lineage_ok
            and reporter_universe_ok
            and len(eligible_peers) >= 3
            and len(eligible_controls) >= 1
        )
        cluster["emitted_evaluator_eligible"] = cluster["structural_breadth_pass"]
        cluster["potential_overlap_inputs_complete"] = cluster[
            "emitted_evaluator_eligible"
        ] and all(
            candidate.get("overlap_key")
            and candidate.get("overlap_entry_session")
            and candidate.get("overlap_exit_session")
            for candidate in (*eligible_peers, *eligible_controls)
        )
        if cluster["emitted_evaluator_eligible"] and VALIDATION_START <= cluster["reaction_session"] <= VALIDATION_END:
            validation_clusters += 1
            included_peer_ids.update(peer["security_id"] for peer in eligible_peers)
            included_sics.add(cluster["sic"])
    peer_path_rate = (
        mapping_path_counts["peer_path_complete"] / mapping_path_counts["peer_mapped"]
        if mapping_path_counts["peer_mapped"]
        else 0.0
    )
    control_path_rate = (
        mapping_path_counts["control_path_complete"]
        / mapping_path_counts["control_mapped"]
        if mapping_path_counts["control_mapped"]
        else 0.0
    )
    return {
        "path_failures": dict(sorted(path_failures.items())),
        "mapping_path_counts": dict(sorted(mapping_path_counts.items())),
        "peer_path_coverage": peer_path_rate,
        "control_path_coverage": control_path_rate,
        "validation_structural_cluster_count": validation_clusters,
        "validation_structural_unique_peer_count": len(included_peer_ids),
        "validation_structural_four_digit_sic_count": len(included_sics),
        "structural_counts_are_pre_signal": True,
        "actual_qualifying_counts_rechecked_by_outcome_evaluator": True,
        "overlap_applied_pre_signal": False,
        "actual_overlap_deferred_until_qualifying_clusters_are_known": True,
        "requested_security_count": len(requests),
        "requested_pair_count": requested_pair_count,
        "market_rows_retained_after_chunk": 0,
    }


def _decode_market_checkpoint_cluster(value: Mapping[str, Any]) -> dict[str, Any]:
    """Restore canonical JSON date/timestamp fields from a market checkpoint."""

    cluster = dict(value)
    for key in ("reaction_session", "entry_session", "exit_session"):
        parsed = _parse_date(cluster.get(key))
        if parsed is None:
            raise ValueError(f"invalid market checkpoint {key}")
        cluster[key] = parsed
    for key in ("reporter_required_sessions", "peer_required_sessions"):
        dates = [_parse_date(item) for item in cluster.get(key, ())]
        if any(item is None for item in dates):
            raise ValueError(f"invalid market checkpoint {key}")
        cluster[key] = dates
    for pool_name in (
        "peers",
        "controls",
        "included_peers",
        "included_controls",
    ):
        restored = []
        for raw_candidate in cluster.get(pool_name, ()):
            candidate = dict(raw_candidate)
            for key in ("overlap_entry_session", "overlap_exit_session"):
                if candidate.get(key) is not None:
                    parsed = _parse_date(candidate[key])
                    if parsed is None:
                        raise ValueError(f"invalid market checkpoint {key}")
                    candidate[key] = parsed
            source = candidate.get("causal_sic_source")
            if isinstance(source, Mapping):
                restored_source = dict(source)
                acceptance = restored_source.get("acceptance")
                if isinstance(acceptance, str):
                    restored_source["acceptance"] = parse_datetime(acceptance)
                candidate["causal_sic_source"] = restored_source
            restored.append(candidate)
        cluster[pool_name] = restored
    return cluster


def _write_market_checkpoint(path: Path, payload: Mapping[str, Any]) -> dict[str, Any]:
    """Publish an atomic chunk directory with an external integrity record."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{path.name}.{os.getpid()}.", dir=path.parent)
    )
    try:
        payload_path = temporary / "chunk.json.gz"
        with payload_path.open("wb") as raw:
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as stream:
                stream.write((canonical_json(payload) + "\n").encode("utf-8"))
            raw.flush()
            os.fsync(raw.fileno())
        integrity = {
            "schema_version": CHECKPOINT_SCHEMA_VERSION,
            "file": payload_path.name,
            "bytes": payload_path.stat().st_size,
            "sha256": _sha256_file(payload_path),
            "bindings_sha256": canonical_hash(payload["bindings"]),
        }
        integrity_path = temporary / "integrity.json"
        integrity_path.write_text(canonical_json(integrity) + "\n", encoding="utf-8")
        with integrity_path.open("rb") as stream:
            os.fsync(stream.fileno())
        if path.exists():
            raise FileExistsError(f"market checkpoint already exists: {path}")
        os.replace(temporary, path)
        return integrity
    finally:
        shutil.rmtree(temporary, ignore_errors=True)


def _read_market_checkpoint(
    path: Path, *, start: int, input_hash: str
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    payload_path = path / "chunk.json.gz"
    integrity_path = path / "integrity.json"
    if not path.is_dir() or not payload_path.is_file() or not integrity_path.is_file():
        raise ValueError(f"HYP-015 market checkpoint integrity files missing: {path}")
    integrity = json.loads(integrity_path.read_text(encoding="utf-8"))
    observed_integrity = {
        "schema_version": CHECKPOINT_SCHEMA_VERSION,
        "file": payload_path.name,
        "bytes": payload_path.stat().st_size,
        "sha256": _sha256_file(payload_path),
    }
    if any(integrity.get(key) != value for key, value in observed_integrity.items()):
        raise ValueError(f"HYP-015 market checkpoint integrity mismatch: {path}")
    with gzip.open(payload_path, "rt", encoding="utf-8") as stream:
        payload = json.load(stream)
    bindings = payload.get("bindings", {})
    expected = {
        "schema_version": CHECKPOINT_SCHEMA_VERSION,
        "runner_sha256": _sha256_file(Path(__file__)),
        "start": start,
        "input_hash": input_hash,
        "observed_prices_sha256": PRICES_PANEL_SHA256,
        "validation_end": VALIDATION_END.isoformat(),
    }
    if bindings != expected:
        raise ValueError(f"HYP-015 market checkpoint binding mismatch: {path}")
    if integrity.get("bindings_sha256") != canonical_hash(bindings):
        raise ValueError(f"HYP-015 market checkpoint binding hash mismatch: {path}")
    rows = payload.get("clusters")
    audit = payload.get("audit")
    if not isinstance(rows, list) or not isinstance(audit, dict):
        raise ValueError(f"HYP-015 market checkpoint payload invalid: {path}")
    return [_decode_market_checkpoint_cluster(row) for row in rows], audit, integrity


def _apply_path_liquidity_overlap(
    *,
    clusters: list[dict[str, Any]],
    sessions: Sequence[date],
    identity: Mapping[str, Any],
    panel_path: Path,
    checkpoint_dir: Path | None = None,
    cluster_chunk_size: int = MARKET_CLUSTER_CHUNK,
    checkpoint_start_offset: int = 0,
    phase_observer: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Apply path gates in bounded, atomically resumable cluster chunks."""

    if cluster_chunk_size < 1:
        raise ValueError("cluster_chunk_size must be positive")
    path_failures: Counter[str] = Counter()
    mapping_path_counts: Counter[str] = Counter()
    included_peer_ids: set[str] = set()
    included_sics: set[str] = set()
    validation_clusters = 0
    checkpoint_chunks_written = 0
    checkpoint_chunks_reused = 0
    peak_cluster_chunk = 0
    peak_requested_security_count = 0
    peak_requested_pair_count = 0
    checkpoint_integrity_records: list[dict[str, Any]] = []

    for start in range(0, len(clusters), cluster_chunk_size):
        stop = min(start + cluster_chunk_size, len(clusters))
        checkpoint_start = checkpoint_start_offset + start
        chunk = clusters[start:stop]
        peak_cluster_chunk = max(peak_cluster_chunk, len(chunk))
        input_hash = canonical_hash(chunk)
        checkpoint_path = (
            checkpoint_dir / "market_chunks" / f"{checkpoint_start:08d}"
            if checkpoint_dir is not None
            else None
        )
        if checkpoint_path is not None and checkpoint_path.exists():
            completed, chunk_audit, checkpoint_integrity = _read_market_checkpoint(
                checkpoint_path, start=checkpoint_start, input_hash=input_hash
            )
            if len(completed) != len(chunk):
                raise ValueError("HYP-015 market checkpoint cluster count mismatch")
            clusters[start:stop] = completed
            chunk = completed
            checkpoint_chunks_reused += 1
        else:
            chunk_audit = _apply_path_liquidity_overlap_chunk(
                clusters=chunk,
                sessions=sessions,
                identity=identity,
                panel_path=panel_path,
            )
            clusters[start:stop] = chunk
            if checkpoint_path is not None:
                checkpoint_integrity = _write_market_checkpoint(
                    checkpoint_path,
                    {
                        "bindings": {
                            "schema_version": CHECKPOINT_SCHEMA_VERSION,
                            "runner_sha256": _sha256_file(Path(__file__)),
                            "start": checkpoint_start,
                            "input_hash": input_hash,
                            "observed_prices_sha256": PRICES_PANEL_SHA256,
                            "validation_end": VALIDATION_END.isoformat(),
                        },
                        "clusters": chunk,
                        "audit": chunk_audit,
                    },
                )
                checkpoint_chunks_written += 1
            else:
                checkpoint_integrity = {}
        if checkpoint_path is not None:
            checkpoint_integrity_records.append(
                {
                    "checkpoint_start": checkpoint_start,
                    "input_hash": input_hash,
                    **checkpoint_integrity,
                }
            )
        if phase_observer is not None:
            phase_observer(f"market_chunk_complete::{checkpoint_start:08d}")

        path_failures.update(chunk_audit.get("path_failures", {}))
        mapping_path_counts.update(chunk_audit.get("mapping_path_counts", {}))
        peak_requested_security_count = max(
            peak_requested_security_count,
            int(chunk_audit.get("requested_security_count", 0)),
        )
        peak_requested_pair_count = max(
            peak_requested_pair_count,
            int(chunk_audit.get("requested_pair_count", 0)),
        )
        for cluster in chunk:
            if not (
                cluster.get("emitted_evaluator_eligible")
                and VALIDATION_START
                <= cluster["reaction_session"]
                <= VALIDATION_END
            ):
                continue
            validation_clusters += 1
            included_peer_ids.update(
                peer["security_id"] for peer in cluster.get("included_peers", ())
            )
            included_sics.add(cluster["sic"])

    peer_path_rate = (
        mapping_path_counts["peer_path_complete"]
        / mapping_path_counts["peer_mapped"]
        if mapping_path_counts["peer_mapped"]
        else 0.0
    )
    control_path_rate = (
        mapping_path_counts["control_path_complete"]
        / mapping_path_counts["control_mapped"]
        if mapping_path_counts["control_mapped"]
        else 0.0
    )
    return {
        "path_failures": dict(sorted(path_failures.items())),
        "mapping_path_counts": dict(sorted(mapping_path_counts.items())),
        "peer_path_coverage": peer_path_rate,
        "control_path_coverage": control_path_rate,
        "validation_structural_cluster_count": validation_clusters,
        "validation_structural_unique_peer_count": len(included_peer_ids),
        "validation_structural_four_digit_sic_count": len(included_sics),
        "structural_counts_are_pre_signal": True,
        "actual_qualifying_counts_rechecked_by_outcome_evaluator": True,
        "overlap_applied_pre_signal": False,
        "actual_overlap_deferred_until_qualifying_clusters_are_known": True,
        "market_memory_model": "BOUNDED_CLUSTER_CHUNKS",
        "cluster_chunk_size": cluster_chunk_size,
        "peak_cluster_chunk": peak_cluster_chunk,
        "peak_requested_security_count": peak_requested_security_count,
        "peak_requested_pair_count": peak_requested_pair_count,
        "global_request_state_retained": False,
        "global_market_rows_retained": False,
        "checkpoint_chunks_written": checkpoint_chunks_written,
        "checkpoint_chunks_reused": checkpoint_chunks_reused,
        "checkpoint_resumable": checkpoint_dir is not None,
        "checkpoint_integrity_records": checkpoint_integrity_records,
    }


def _spool_structural_clusters(
    *,
    spool: _ClusterSpool,
    events: Sequence[dict[str, Any]],
    headers: Sequence[dict[str, Any]] | _HeaderIndex,
    identity: Mapping[str, Any],
    sessions: Sequence[date],
    excluded_event_metadata: Sequence[Mapping[str, Any]],
    reporter_spool_path: Path,
    phase_observer: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    if spool.structural_ready():
        if phase_observer is not None:
            phase_observer("structural_spool_reverified")
        return spool.structural_audit()
    spool.begin_structural_rebuild()

    def sink(cluster: dict[str, Any]) -> None:
        spool.append_structural(cluster)
        if phase_observer is not None:
            phase_observer(
                "structural_cluster_spooled::"
                f"{cluster['reaction_session'].isoformat()}::{cluster['sic']}"
            )

    retained, audit = _build_structural_clusters(
        events=events,
        headers=headers,
        identity=identity,
        sessions=sessions,
        excluded_event_metadata=excluded_event_metadata,
        cluster_sink=sink,
        reporter_spool_path=reporter_spool_path,
        max_cluster_candidates=MAX_CLUSTER_CANDIDATES,
        phase_observer=phase_observer,
    )
    if retained:
        raise ValueError("HYP-015 spooled structural builder retained clusters")
    spool.finish_structural(audit)
    reporter_spool_path.unlink(missing_ok=True)
    return audit


def _annotate_cluster_spool(
    *,
    spool: _ClusterSpool,
    sessions: Sequence[date],
    identity: Mapping[str, Any],
    panel_path: Path,
    checkpoint_dir: Path,
    phase_observer: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    path_failures: Counter[str] = Counter()
    mapping_path_counts: Counter[str] = Counter()
    peak_cluster_chunk = 0
    peak_requested_security_count = 0
    peak_requested_pair_count = 0
    written = 0
    reused = 0
    checkpoint_integrity_records: list[dict[str, Any]] = []
    for chunk in spool.iter_structural_chunks(MARKET_CLUSTER_CHUNK):
        ordinals = [item[0] for item in chunk]
        clusters = [item[1] for item in chunk]
        audit = _apply_path_liquidity_overlap(
            clusters=clusters,
            sessions=sessions,
            identity=identity,
            panel_path=panel_path,
            checkpoint_dir=checkpoint_dir,
            cluster_chunk_size=MARKET_CLUSTER_CHUNK,
            checkpoint_start_offset=ordinals[0] - 1,
            phase_observer=phase_observer,
        )
        for ordinal, cluster in zip(ordinals, clusters):
            spool.write_annotated(ordinal, cluster)
        path_failures.update(audit.get("path_failures", {}))
        mapping_path_counts.update(audit.get("mapping_path_counts", {}))
        peak_cluster_chunk = max(peak_cluster_chunk, audit["peak_cluster_chunk"])
        peak_requested_security_count = max(
            peak_requested_security_count,
            audit["peak_requested_security_count"],
        )
        peak_requested_pair_count = max(
            peak_requested_pair_count,
            audit["peak_requested_pair_count"],
        )
        written += audit["checkpoint_chunks_written"]
        reused += audit["checkpoint_chunks_reused"]
        checkpoint_integrity_records.extend(
            audit.get("checkpoint_integrity_records", ())
        )

    validation_clusters = 0
    included_peer_ids: set[str] = set()
    included_sics: set[str] = set()
    for cluster in spool:
        if not (
            cluster.get("emitted_evaluator_eligible")
            and VALIDATION_START <= cluster["reaction_session"] <= VALIDATION_END
        ):
            continue
        validation_clusters += 1
        included_peer_ids.update(
            item["security_id"] for item in cluster.get("included_peers", ())
        )
        included_sics.add(cluster["sic"])
    peer_path_rate = (
        mapping_path_counts["peer_path_complete"]
        / mapping_path_counts["peer_mapped"]
        if mapping_path_counts["peer_mapped"]
        else 0.0
    )
    control_path_rate = (
        mapping_path_counts["control_path_complete"]
        / mapping_path_counts["control_mapped"]
        if mapping_path_counts["control_mapped"]
        else 0.0
    )
    result = {
        "path_failures": dict(sorted(path_failures.items())),
        "mapping_path_counts": dict(sorted(mapping_path_counts.items())),
        "peer_path_coverage": peer_path_rate,
        "control_path_coverage": control_path_rate,
        "validation_structural_cluster_count": validation_clusters,
        "validation_structural_unique_peer_count": len(included_peer_ids),
        "validation_structural_four_digit_sic_count": len(included_sics),
        "structural_counts_are_pre_signal": True,
        "actual_qualifying_counts_rechecked_by_outcome_evaluator": True,
        "overlap_applied_pre_signal": False,
        "actual_overlap_deferred_until_qualifying_clusters_are_known": True,
        "market_memory_model": "DISK_SPOOLED_BOUNDED_CLUSTER_CHUNKS",
        "cluster_chunk_size": MARKET_CLUSTER_CHUNK,
        "peak_cluster_chunk": peak_cluster_chunk,
        "peak_requested_security_count": peak_requested_security_count,
        "peak_requested_pair_count": peak_requested_pair_count,
        "global_request_state_retained": False,
        "global_market_rows_retained": False,
        "checkpoint_chunks_written": written,
        "checkpoint_chunks_reused": reused,
        "checkpoint_resumable": True,
        "structural_clusters_retained_in_memory": 0,
        "annotated_clusters_retained_in_memory": 0,
        "checkpoint_integrity_records": checkpoint_integrity_records,
    }
    spool.finish_annotated(result)
    return result


def _build_exclusions_and_missingness(
    *,
    source_errors: Sequence[Mapping[str, Any]],
    event_audit: Mapping[str, Any],
    included_events: Sequence[Mapping[str, Any]],
    headers: Sequence[dict[str, Any]] | _HeaderIndex,
    clusters: Iterable[Mapping[str, Any]],
    checked_at: datetime,
    sessions: Sequence[date],
    exclusion_sink: Callable[[Mapping[str, Any]], None] | None = None,
    phase_observer: Callable[[str], None] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    exclusions: list[dict[str, Any]] = []
    dimensions: dict[str, dict[str, Counter[str]]] = {
        name: {"denominator": Counter(), "missing": Counter()}
        for name in ("year", "sic", "issuer_cik", "relevance")
    }
    total_denominator = 0
    total_missing = 0
    reporter_denominator = 0
    reporter_missing = 0
    exclusion_count = 0
    exclusion_contract_incomplete = False
    adverse_mapping_unproven = False
    selection_reasons: list[str] = []
    required_exclusion_fields = (
        "reason",
        "source_path",
        "source_status",
        "sealed_at",
        "potential_cluster_key",
        "adverse_sensitivity_eligible",
    )

    def observe_coverage(row: Mapping[str, Any]) -> None:
        nonlocal total_denominator, total_missing
        nonlocal reporter_denominator, reporter_missing
        total_denominator += 1
        missing = not bool(row["lineage_complete"])
        total_missing += int(missing)
        if row["relevance"] == "ITEM_2_02_REPORTER":
            reporter_denominator += 1
            reporter_missing += int(missing)
        for dimension in dimensions:
            key = str(row.get(dimension) or "UNKNOWN")
            dimensions[dimension]["denominator"][key] += 1
            if missing:
                dimensions[dimension]["missing"][key] += 1
        if phase_observer is not None and total_denominator % 10_000 == 0:
            phase_observer(f"missingness_rows_observed::{total_denominator}")

    def emit_exclusion(row: dict[str, Any]) -> None:
        nonlocal exclusion_count, exclusion_contract_incomplete
        nonlocal adverse_mapping_unproven
        exclusion_count += 1
        if any(field not in row or row[field] is None for field in required_exclusion_fields):
            exclusion_contract_incomplete = True
        if row.get("adverse_sensitivity_eligible") is True and (
            row.get("reaction_session") is None
            or row.get("reaction_quarter") is None
            or not row.get("potential_cluster_key")
        ):
            adverse_mapping_unproven = True
        if row.get("outcome_informed") is True:
            selection_reasons.append("selection_related_exclusion")
        if exclusion_sink is None:
            exclusions.append(row)
        else:
            exclusion_sink(row)
        if phase_observer is not None and exclusion_count % 1_000 == 0:
            phase_observer(f"exclusions_streamed::{exclusion_count}")
    included_reporter_ids = {
        event_id
        for cluster in clusters
        if cluster.get("reporter_lineage_pass") is True
        for event_id in cluster["reporter_event_ids"]
    }
    if phase_observer is not None:
        phase_observer("missingness_reporter_index_complete")
    source_error_by_accession = {
        item.get("accession"): item for item in source_errors if item.get("accession")
    }

    def base_exclusion(
        *,
        stage: str,
        reason: str,
        event_id: str | None,
        cluster_id: str | None,
        year: str,
        sic: str,
        issuer_cik: str,
        relevance: str,
        source_path: str | None,
        source_status: str,
        potential_cluster_key: str,
        adverse_sensitivity_eligible: bool,
        reaction_session: date | None = None,
    ) -> dict[str, Any]:
        return {
            "stage": stage,
            "reason": reason,
            "event_id": event_id,
            "cluster_id": cluster_id,
            "year": year,
            "sic": sic,
            "sic4": sic,
            "issuer_cik": issuer_cik,
            "relevance": relevance,
            "source_path": source_path or "UNAVAILABLE",
            "source_status": source_status,
            "sealed_at": checked_at,
            "potential_cluster_key": potential_cluster_key,
            "adverse_sensitivity_eligible": adverse_sensitivity_eligible,
            "reaction_session": reaction_session,
            "reaction_quarter": (
                _quarter_start(reaction_session) if reaction_session else None
            ),
            "outcome_informed": False,
        }

    for item in event_audit["deterministically_excluded_missing_original_rows"]:
        source_error = source_error_by_accession.get(item["event_id"], {})
        accepted_at = item.get("acceptance_datetime_utc")
        reaction_session = (
            _reaction_session(accepted_at, sessions)
            if isinstance(accepted_at, datetime)
            else None
        )
        adverse_eligible = bool(
            reaction_session
            and VALIDATION_START <= reaction_session <= VALIDATION_END
        )
        row = base_exclusion(
            stage="SOURCE",
            reason="MISSING_ORIGINAL_DETERMINISTIC_EXCLUSION",
            event_id=item["event_id"],
            cluster_id=None,
            year=str(item["accepted_date"])[:4],
            sic="UNKNOWN",
            issuer_cik=item.get("issuer_cik") or "UNKNOWN",
            relevance="ITEM_2_02_REPORTER",
            source_path=source_error.get("source_filename"),
            source_status=str(source_error.get("error_type") or "SOURCE_ABSENT"),
            potential_cluster_key=(
                f"{reaction_session.isoformat() if reaction_session else 'UNKNOWN'}::"
                f"UNKNOWN::{item['event_id']}"
            ),
            adverse_sensitivity_eligible=adverse_eligible,
            reaction_session=reaction_session,
        )
        emit_exclusion(row)
        observe_coverage({**row, "lineage_complete": False})

    for event in included_events:
        header = _header_get(headers, event["event_id"]) or {}
        lineage_complete = event["event_id"] in included_reporter_ids
        row = {
            "stage": "REPORTER",
            "event_id": event["event_id"],
            "year": str(event["acceptance"].year),
            "sic": header.get("sic") or "UNKNOWN",
            "issuer_cik": event.get("issuer_cik") or "UNKNOWN",
            "relevance": "ITEM_2_02_REPORTER",
            "lineage_complete": lineage_complete,
        }
        observe_coverage(row)
        if not lineage_complete:
            accepted_date = event["acceptance"].date()
            reaction_session = _reaction_session(event["acceptance"], sessions)
            exclusion = base_exclusion(
                stage="REPORTER",
                reason="REPORTER_CAUSAL_LINEAGE_EXCLUDED",
                event_id=event["event_id"],
                cluster_id=None,
                year=str(event["acceptance"].year),
                sic=header.get("sic") or "UNKNOWN",
                issuer_cik=event.get("issuer_cik") or "UNKNOWN",
                relevance="ITEM_2_02_REPORTER",
                source_path=(
                    event.get("source_filename") or header.get("source_path")
                ),
                source_status="ORIGINAL_PRESENT_LINEAGE_INCOMPLETE",
                potential_cluster_key=(
                    f"{reaction_session.isoformat() if reaction_session else 'UNKNOWN'}::"
                    f"{header.get('sic') or 'UNKNOWN'}::{event['event_id']}"
                ),
                adverse_sensitivity_eligible=(
                    VALIDATION_START <= accepted_date <= VALIDATION_END
                ),
                reaction_session=reaction_session,
            )
            emit_exclusion(exclusion)

    for cluster in clusters:
        year = str(cluster["reaction_session"].year)
        for pool_name, relevance in (
            ("peers", "POTENTIAL_PEER"),
            ("controls", "PRIMARY_CONTROL"),
        ):
            for candidate in cluster[pool_name]:
                lineage_complete = bool(
                    candidate.get("mapping_status") == "UNIQUE"
                    and candidate.get("exclusion_class") != "LINEAGE_MISSING"
                )
                row = {
                    "stage": pool_name[:-1].upper(),
                    "cluster_id": cluster["cluster_id"],
                    "year": year,
                    "sic": cluster["sic"],
                    "issuer_cik": candidate.get("cik") or "UNKNOWN",
                    "security_id": candidate.get("security_id"),
                    "relevance": relevance,
                    "lineage_complete": lineage_complete,
                }
                observe_coverage(row)
                if candidate.get("included") is not True:
                    is_lineage_missing = (
                        candidate.get("exclusion_class") == "LINEAGE_MISSING"
                    )
                    source = candidate.get("causal_sic_source") or {}
                    exclusion = base_exclusion(
                        stage=pool_name[:-1].upper(),
                        reason=candidate.get("exclusion_reason")
                        or "REPORTER_CLUSTER_LINEAGE_EXCLUDED",
                        event_id=source.get("event_id"),
                        cluster_id=cluster["cluster_id"],
                        year=year,
                        sic=cluster["sic"],
                        issuer_cik=candidate.get("cik") or "UNKNOWN",
                        relevance=relevance,
                        source_path=source.get("source_path"),
                        source_status=(
                            "LINEAGE_MISSING"
                            if is_lineage_missing
                            else "UNIVERSE_INELIGIBLE"
                        ),
                        potential_cluster_key=cluster["potential_cluster_key"],
                        adverse_sensitivity_eligible=bool(
                            is_lineage_missing
                            and VALIDATION_START
                            <= cluster["reaction_session"]
                            <= VALIDATION_END
                        ),
                        reaction_session=cluster["reaction_session"],
                    )
                    exclusion["security_id"] = candidate.get("security_id")
                    emit_exclusion(exclusion)

        if not cluster.get("emitted_evaluator_eligible"):
            emit_exclusion(
                base_exclusion(
                    stage="CLUSTER",
                    reason="STRUCTURAL_CLUSTER_NOT_EVALUATOR_ELIGIBLE",
                    event_id=None,
                    cluster_id=cluster["cluster_id"],
                    year=year,
                    sic=cluster["sic"],
                    issuer_cik=",".join(cluster["reporter_ciks"]),
                    relevance="POTENTIAL_CLUSTER",
                    source_path="NOT_APPLICABLE",
                    source_status="STRUCTURAL_INELIGIBILITY",
                    potential_cluster_key=cluster["potential_cluster_key"],
                    adverse_sensitivity_eligible=False,
                    reaction_session=cluster["reaction_session"],
                )
            )

    coverage_by_dimension: dict[str, list[dict[str, Any]]] = {}
    for dimension, counters in dimensions.items():
        records = []
        for key, denominator in sorted(counters["denominator"].items()):
            missing = counters["missing"][key]
            records.append(
                {
                    dimension: key,
                    "denominator": denominator,
                    "missing": missing,
                    "coverage": (denominator - missing) / denominator,
                    "missing_share": (
                        missing / total_missing if total_missing else 0.0
                    ),
                    "denominator_share": (
                        denominator / total_denominator if total_denominator else 0.0
                    ),
                }
            )
        coverage_by_dimension[dimension] = records
    reporter_coverage = (
        (reporter_denominator - reporter_missing) / reporter_denominator
        if reporter_denominator
        else 0.0
    )
    gate_reasons = []
    if not total_denominator:
        gate_reasons.append("coverage_denominator_absent")
    if exclusion_contract_incomplete:
        gate_reasons.append("exclusion_audit_contract_incomplete")
    if adverse_mapping_unproven:
        gate_reasons.append("adverse_sensitivity_mapping_unproven")
    if reporter_coverage < AGGREGATE_ORIGINAL_COVERAGE_MIN:
        gate_reasons.append("aggregate_reporter_coverage_below_99_9_percent")
    for dimension in ("year", "sic"):
        for record in coverage_by_dimension[dimension]:
            if record["denominator"] >= 100 and record["coverage"] < 0.99:
                gate_reasons.append(
                    f"material_{dimension}_stratum_below_99_percent::{record[dimension]}"
                )
    for record in coverage_by_dimension["issuer_cik"]:
        if record["denominator"] >= 20 and record["coverage"] < 0.95:
            gate_reasons.append(
                "material_issuer_stratum_below_95_percent::"
                + record["issuer_cik"]
            )
    concentration_status = (
        "LOW_COUNT_NOT_SEPARATELY_TESTABLE" if total_missing < 10 else "TESTED"
    )
    if total_missing >= 10:
        for dimension in ("year", "sic"):
            for record in coverage_by_dimension[dimension]:
                if (
                    record["missing_share"] >= 0.50
                    and record["missing_share"]
                    >= 2.0 * record["denominator_share"]
                ):
                    gate_reasons.append(
                        f"missingness_concentrated_by_{dimension}::{record[dimension]}"
                    )
        for record in coverage_by_dimension["issuer_cik"]:
            if record["missing"] >= 3 and record["missing_share"] >= 0.25:
                gate_reasons.append(
                    "missingness_concentrated_by_issuer::" + record["issuer_cik"]
                )
    gate_reasons.extend(selection_reasons)
    return exclusions, {
        "source_error_count": len(source_errors),
        "coverage_by_dimension": coverage_by_dimension,
        "reporter_relevance_denominator": reporter_denominator,
        "reporter_relevance_missing": reporter_missing,
        "reporter_relevance_coverage": reporter_coverage,
        "structural_denominator": total_denominator,
        "structural_missing": total_missing,
        "concentration_status": concentration_status,
        "exclusion_count": exclusion_count,
        "selection_relation_flags": selection_reasons,
        "selection_gate_pass": not gate_reasons,
        "selection_gate_reasons": sorted(set(gate_reasons)),
        "outcome_informed_exclusions": 0,
    }


def _gate_summary(
    source: Mapping[str, Any],
    inventory: Mapping[str, Any],
    structural: Mapping[str, Any],
    paths: Mapping[str, Any],
    missingness: Mapping[str, Any],
    clusters: Iterable[Mapping[str, Any]],
    event_audit: Mapping[str, Any],
    header_audit: Mapping[str, Any],
) -> list[dict[str, Any]]:
    reporter_coverage = (
        structural["reporter_included"] / structural["reporter_attempted"]
        if structural["reporter_attempted"]
        else 0.0
    )
    controls = [
        ("aggregate_original_coverage", source["coverage"] >= AGGREGATE_ORIGINAL_COVERAGE_MIN, source["coverage"], AGGREGATE_ORIGINAL_COVERAGE_MIN),
        ("source_inventory_count_hash_acceptance", not source.get("inventory_census_failures"), source.get("inventory_census_failures", ()), "no census failures"),
        ("aggregate_reporter_inventory_coverage", inventory["coverage"] >= AGGREGATE_ORIGINAL_COVERAGE_MIN, inventory["coverage"], AGGREGATE_ORIGINAL_COVERAGE_MIN),
        ("item_2_02_accession_uniqueness", not event_audit["duplicate_event_ids"], len(event_audit["duplicate_event_ids"]), 0),
        ("aggregate_original_header_integrity", header_audit["coverage"] >= AGGREGATE_ORIGINAL_COVERAGE_MIN, header_audit["coverage"], AGGREGATE_ORIGINAL_COVERAGE_MIN),
        ("aggregate_event_time_sic_coverage", header_audit["sic_coverage"] >= PEER_MAPPING_COVERAGE_MIN, header_audit["sic_coverage"], PEER_MAPPING_COVERAGE_MIN),
        ("aggregate_reporter_causal_coverage", reporter_coverage >= AGGREGATE_ORIGINAL_COVERAGE_MIN, reporter_coverage, AGGREGATE_ORIGINAL_COVERAGE_MIN),
        ("eligible_peer_mapping", structural["peer_mapping_rate"] >= PEER_MAPPING_COVERAGE_MIN, structural["peer_mapping_rate"], PEER_MAPPING_COVERAGE_MIN),
        ("potential_control_mapping", structural["control_mapping_rate"] >= PEER_MAPPING_COVERAGE_MIN, structural["control_mapping_rate"], PEER_MAPPING_COVERAGE_MIN),
        ("eligible_peer_path_coverage", paths["peer_path_coverage"] >= PEER_MAPPING_COVERAGE_MIN, paths["peer_path_coverage"], PEER_MAPPING_COVERAGE_MIN),
        ("potential_control_path_coverage", paths["control_path_coverage"] >= PEER_MAPPING_COVERAGE_MIN, paths["control_path_coverage"], PEER_MAPPING_COVERAGE_MIN),
        ("included_reporter_peer_control_lineage", all(
            cluster.get("reporter_lineage_pass")
            and all(
                item.get("included")
                and item.get("terminal_disposition") == "COMPLETE_THROUGH_EXIT"
                and item.get("terminal_outcome_required") is False
                for item in (
                    *cluster.get("included_peers", ()),
                    *cluster.get("included_controls", ()),
                )
            )
            for cluster in clusters if cluster.get("emitted_evaluator_eligible")
        ), "100% included rows", "100% included rows"),
        ("validation_structural_clusters", paths["validation_structural_cluster_count"] >= MIN_VALIDATION_CLUSTERS, paths["validation_structural_cluster_count"], MIN_VALIDATION_CLUSTERS),
        ("validation_structural_unique_peers", paths["validation_structural_unique_peer_count"] >= MIN_VALIDATION_PEERS, paths["validation_structural_unique_peer_count"], MIN_VALIDATION_PEERS),
        ("validation_structural_four_digit_sics", paths["validation_structural_four_digit_sic_count"] >= MIN_VALIDATION_SICS, paths["validation_structural_four_digit_sic_count"], MIN_VALIDATION_SICS),
        ("potential_overlap_inputs_complete", all(cluster.get("potential_overlap_inputs_complete") for cluster in clusters if cluster.get("emitted_evaluator_eligible")), "inventoried_not_applied", "apply only after signal qualification"),
        ("missingness_selection_diagnostics", missingness.get("selection_gate_pass") is True, missingness.get("selection_gate_reasons", ()), "complete and outcome-blind"),
    ]
    return [
        {"control": name, "status": "PASS" if passed else "FAIL", "observed": observed, "required": required}
        for name, passed, observed, required in controls
    ]


def _write_jsonl_gz(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    with path.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
            with io.TextIOWrapper(compressed, encoding="utf-8", newline="") as stream:
                for row in rows:
                    stream.write(canonical_json(row) + "\n")


class _JsonlGzipSink:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.raw: Any = None
        self.compressed: Any = None
        self.stream: Any = None

    def __enter__(self) -> "_JsonlGzipSink":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.raw = self.path.open("wb")
        self.compressed = gzip.GzipFile(
            filename="", mode="wb", fileobj=self.raw, mtime=0
        )
        self.stream = io.TextIOWrapper(
            self.compressed, encoding="utf-8", newline=""
        )
        return self

    def write(self, row: Mapping[str, Any]) -> None:
        if self.stream is None:
            raise RuntimeError("JSONL sink is not open")
        self.stream.write(canonical_json(row) + "\n")

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self.stream is not None:
            self.stream.close()
        elif self.compressed is not None:
            self.compressed.close()
        if self.raw is not None and not self.raw.closed:
            self.raw.close()


def _manifest_rows(clusters: Iterable[Mapping[str, Any]]) -> Iterator[dict[str, Any]]:
    for cluster in clusters:
        if not cluster.get("emitted_evaluator_eligible"):
            continue
        yield {
            "schema_version": "caerus_hyp015_structural_cluster_v2",
            "cluster_id": cluster["cluster_id"],
            "reaction_session": cluster["reaction_session"],
            "reaction_quarter": _quarter_start(cluster["reaction_session"]),
            "entry_session": cluster["entry_session"],
            "exit_session": cluster["exit_session"],
            "four_digit_sic": cluster["sic"],
            "contributing_reporter_event_ids": cluster["reporter_event_ids"],
            "reporter_ciks": cluster["reporter_ciks"],
            "reporter_security_ids": cluster["reporter_security_ids"],
            "reporters": cluster["reporters"],
            "structural_peer_attempt_count": len(cluster["peers"]),
            "potential_peer_security_ids": sorted(
                item["security_id"]
                for item in cluster["peers"]
                if item.get("security_id")
            ),
            "included_peer_security_ids": sorted(
                peer["security_id"] for peer in cluster.get("included_peers", ())
            ),
            "potential_control_attempt_count": len(cluster["controls"]),
            "industry_control_security_ids": sorted(
                item["security_id"]
                for item in cluster.get("included_controls", ())
            ),
            "peer_report_during_hold_security_ids": cluster[
                "peer_report_during_hold_security_ids"
            ],
            "potential_cluster_key": cluster["potential_cluster_key"],
            "overlap_inputs": [
                {
                    "security_id": item["security_id"],
                    "entry_session": item["overlap_entry_session"],
                    "exit_session": item["overlap_exit_session"],
                    "role": item["relevance"],
                }
                for item in (
                    *cluster.get("included_peers", ()),
                    *cluster.get("included_controls", ()),
                )
            ],
            "overlap_applied": False,
            "terminal_outcome_required": False,
            "reporter_lineage_pass": cluster.get("reporter_lineage_pass", False),
            "structural_breadth_pass": cluster.get("structural_breadth_pass", False),
            "reaction_value_accessed": False,
            "forward_return_accessed": False,
        }


def _write_append_only_bundle(
    *,
    repo_root: Path,
    run_id: str,
    result: Mapping[str, Any],
    clusters: Iterable[Mapping[str, Any]],
    exclusions: Sequence[Mapping[str, Any]] = (),
    exclusion_spool_path: Path | None = None,
) -> tuple[Path, dict[str, Any]]:
    research_root = (repo_root / "outputs/research/alpha_lab").resolve()
    hypothesis_root = research_root / HYPOTHESIS_ID
    final_dir = hypothesis_root / run_id
    if final_dir.exists():
        raise FileExistsError(f"research run already exists: {final_dir}")
    staging_root = hypothesis_root / ".staging"
    staging_root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f"{run_id}.", dir=staging_root))
    try:
        result_path = staging / "result.json"
        result_path.write_text(canonical_json(result) + "\n", encoding="utf-8")
        eligibility_path = staging / "eligibility_manifest.jsonl.gz"
        _write_jsonl_gz(eligibility_path, _manifest_rows(clusters))
        exclusion_path = staging / "exclusion_manifest.jsonl.gz"
        if exclusion_spool_path is None:
            _write_jsonl_gz(exclusion_path, exclusions)
        else:
            if not exclusion_spool_path.is_file():
                raise ValueError("streamed exclusion spool is absent")
            shutil.copyfile(exclusion_spool_path, exclusion_path)
        files = [
            {"name": path.name, "bytes": path.stat().st_size, "sha256": _sha256_file(path)}
            for path in (result_path, eligibility_path, exclusion_path)
        ]
        manifest = {
            "schema_version": "caerus_alpha_lab_hyp015_evidence_bundle_v2",
            "classification": "RESEARCH_ONLY_NON_EXECUTIONAL_NO_RETURN",
            "run_id": run_id,
            "hypothesis_id": HYPOTHESIS_ID,
            "files": files,
            "credentials_persisted": False,
            "trading_behavior_changed": False,
            "reporter_reaction_accessed": False,
            "forward_return_accessed": False,
            "challenge_accessed": False,
            "bundle_hash": canonical_hash(files),
        }
        (staging / "manifest.json").write_text(
            canonical_json(manifest) + "\n", encoding="utf-8"
        )
        hypothesis_root.mkdir(parents=True, exist_ok=True)
        os.replace(staging, final_dir)
        return final_dir, manifest
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def run_gate(
    *,
    repo_root: Path,
    run_id: str,
    checked_at: datetime,
    max_workers: int = 1,
    enforce_canonical_gcp: bool = True,
) -> dict[str, Any]:
    if not _RUN_ID.fullmatch(run_id):
        raise ValueError("run_id must be a path-safe research identifier")
    repo_root = repo_root.resolve()
    if enforce_canonical_gcp and repo_root != CANONICAL_GCP_REPO_ROOT:
        raise ValueError(
            "HYP-2026-015 evidence writes require the canonical GCP Alpha Lab root"
        )
    hypothesis_root = repo_root / "outputs/research/alpha_lab" / HYPOTHESIS_ID
    final_dir = hypothesis_root / run_id
    if final_dir.exists():
        raise FileExistsError(f"research run already exists: {final_dir}")
    checkpoint_root = hypothesis_root / ".staging" / "checkpoints" / run_id
    memory = _RssMonitor()
    memory.record("gate_start")
    spec = _verify_spec(repo_root)
    addendum = _verify_addendum(repo_root)
    source = _source_preflight(repo_root)
    memory.record("source_preflight_complete")
    if not source["gate_pass"]:
        raise ValueError("aggregate original coverage is below Addendum 001's 99.9% gate")
    earnings_record, tape_path, tape_file_record = _read_readiness_bound_file(
        repo_root, EARNINGS_READINESS_RELATIVE_PATH, EARNINGS_READINESS_SHA256
    )
    events, event_audit = _load_earnings_events(
        tape_path, set(source["excluded_accessions"])
    )
    included_events, inventory_audit, _ = _validate_event_inventory(
        events, source["bundle_root"]
    )
    memory.record("event_inventory_complete")
    headers = _scan_headers_to_checkpoint(
        source["bundle_root"],
        checkpoint_root / "headers.sqlite",
        phase_observer=memory.record,
    )
    header_audit = headers.audit()
    header_audit["production_scan_workers"] = 1
    header_audit["requested_max_workers_ignored_for_memory_safety"] = max_workers

    prices_record, panel_path, panel_file_record = _read_readiness_bound_file(
        repo_root, PRICES_READINESS_RELATIVE_PATH, PRICES_READINESS_SHA256
    )
    if panel_file_record["sha256"] != PRICES_PANEL_SHA256:
        raise ValueError("observed-price readiness does not bind the frozen panel hash")
    sessions = _calendar_from_panel(panel_path)
    memory.record("calendar_complete")
    identity = _build_identity(repo_root)
    memory.record("identity_complete")
    clusters = _ClusterSpool(checkpoint_root / "clusters.sqlite")
    structural_audit = _spool_structural_clusters(
        spool=clusters,
        events=included_events,
        headers=headers,
        identity=identity,
        sessions=sessions,
        excluded_event_metadata=event_audit[
            "deterministically_excluded_missing_original_rows"
        ],
        reporter_spool_path=checkpoint_root / "reporters.sqlite",
        phase_observer=memory.record,
    )
    memory.record("structural_spool_complete")
    path_audit = _annotate_cluster_spool(
        spool=clusters,
        sessions=sessions,
        identity=identity,
        panel_path=panel_path,
        checkpoint_dir=checkpoint_root,
        phase_observer=memory.record,
    )
    memory.record("market_annotation_complete")
    exclusion_spool_path = checkpoint_root / "exclusion_manifest.jsonl.gz"
    with _JsonlGzipSink(exclusion_spool_path) as exclusion_sink:
        exclusions, missingness = _build_exclusions_and_missingness(
            source_errors=source["errors"],
            event_audit=event_audit,
            included_events=included_events,
            headers=headers,
            clusters=clusters,
            checked_at=checked_at,
            sessions=sessions,
            exclusion_sink=exclusion_sink.write,
            phase_observer=memory.record,
        )
    if exclusions:
        raise ValueError("streamed HYP-015 exclusions were retained in memory")
    memory.record("exclusion_missingness_complete")
    headers.close()
    controls = _gate_summary(
        source,
        inventory_audit,
        structural_audit,
        path_audit,
        missingness,
        clusters,
        event_audit,
        header_audit,
    )
    ready = all(item["status"] == "PASS" for item in controls)
    result = {
        "schema_version": SCHEMA_VERSION,
        "hypothesis_id": HYPOTHESIS_ID,
        "experiment_id": EXPERIMENT_ID,
        "run_id": run_id,
        "checked_at": checked_at,
        "outcome": "READY_FOR_OUTCOME_REGISTRATION" if ready else "BLOCKED_DATA",
        "classification": "UNPROVEN",
        "spec": spec,
        "owner_addendum": addendum,
        "runner": {"path": RUNNER_RELATIVE_PATH, "sha256": _sha256_file(Path(__file__))},
        "source_manifest": source["record"],
        "source_bundle_sha256": SOURCE_BUNDLE_SHA256,
        "earnings_readiness": earnings_record,
        "earnings_tape": tape_file_record,
        "observed_prices_readiness": prices_record,
        "observed_prices_panel": panel_file_record,
        "identity_inputs": identity["input_records"],
        "source_audit": {
            "counts": source["counts"],
            "source_candidate_count": source["source_candidate_count"],
            "source_hydrated_count": source["source_hydrated_count"],
            "coverage": source["coverage"],
            "raw_source_candidate_count": source["raw_source_candidate_count"],
            "raw_source_hydrated_count": source["raw_source_hydrated_count"],
            "raw_coverage": source["raw_coverage"],
            "threshold": AGGREGATE_ORIGINAL_COVERAGE_MIN,
            "inventory_census": source["inventory_census"],
            "inventory_census_failures": source["inventory_census_failures"],
            "deterministically_excluded_accessions": source["excluded_accessions"],
        },
        "item_2_02_audit": event_audit,
        "included_event_inventory_audit": inventory_audit,
        "header_audit": header_audit,
        "structural_audit": structural_audit,
        "path_overlap_audit": path_audit,
        "runtime_memory_model": {
            "headers": "SQLITE_DISK_BACKED_PARTITION_CHECKPOINT",
            "headers_retained_in_memory": 0,
            "header_partition_scan_workers": 1,
            "structural_clusters": "SQLITE_DISK_SPOOL_REACTION_SESSION_SIC_ATOMIC",
            "structural_clusters_retained_in_memory": 0,
            "annotated_clusters_retained_in_memory": 0,
            "exclusions": "STREAMED_DETERMINISTIC_GZIP_SPOOL",
            "exclusions_retained_in_memory": 0,
            "market_rows": "PREDICATE_PUSHED_DISK_SPOOLED_CLUSTER_CHUNKS",
            "market_cluster_chunk_size": path_audit["cluster_chunk_size"],
            "market_arrow_batch_size": MARKET_SCAN_BATCH_SIZE,
            "calendar_arrow_batch_size": CALENDAR_SCAN_BATCH_SIZE,
            "peak_market_cluster_chunk": path_audit["peak_cluster_chunk"],
            "global_market_request_state_retained": False,
            "checkpoint_root": str(checkpoint_root),
            "checkpoint_retained_on_interruption": True,
            "checkpoint_removed_after_atomic_evidence_publish": True,
            "oversized_cluster_candidate_guard": MAX_CLUSTER_CANDIDATES,
            "oversized_market_request_pair_guard": MAX_MARKET_REQUEST_PAIRS,
            "rss_preflight": memory.audit(),
        },
        "missingness_concentration": missingness,
        "exclusion_manifest_row_count": missingness["exclusion_count"],
        "eligibility_manifest_row_count": sum(
            cluster.get("emitted_evaluator_eligible") is True for cluster in clusters
        ),
        "controls": controls,
        "structural_counts_are_not_qualifying_signal_counts": True,
        "outcome_evaluator_must_recheck_qualifying_cluster_peer_sic_floors": True,
        "same_sic_same_reaction_reporters_preserved_without_aggregation": True,
        "canonical_global_ledger_present": (
            repo_root / "outputs/research/alpha_lab/ledger/research_events.v1.jsonl"
        ).is_file(),
        "reporter_reaction_accessed": False,
        "forward_return_accessed": False,
        "validation_outcomes_accessed": False,
        "challenge_period_accessed": False,
        "statistical_trial_opened": False,
        "orders_submitted": False,
        "trading_behavior_changed": False,
        "next_executable_action": (
            "register the frozen family/wave/trial in the authenticated global ledger "
            "and run the separately governed outcome evaluator"
            if ready
            else "remediate the failed no-return controls without changing the frozen signal"
        ),
        "boundary_attestation": (
            "No reporter reaction, forward return, validation outcome, challenge input, "
            "broker, order, allocation, scheduler, cron, Paper, Live, or production surface "
            "was read or changed. Contemporaneous price/liquidity values were reduced to "
            "eligibility booleans and were not persisted."
        ),
    }
    run_dir, manifest = _write_append_only_bundle(
        repo_root=repo_root,
        run_id=run_id,
        result=result,
        clusters=clusters,
        exclusion_spool_path=exclusion_spool_path,
    )
    clusters.close()
    shutil.rmtree(checkpoint_root, ignore_errors=True)
    return {"run_dir": str(run_dir), "result": result, "manifest": manifest}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--run-id")
    parser.add_argument("--checked-at")
    parser.add_argument("--max-workers", type=int, default=1)
    return parser.parse_args()


def main() -> int:
    arguments = _parse_args()
    checked_at = (
        parse_datetime(arguments.checked_at)
        if arguments.checked_at
        else datetime.now(timezone.utc)
    )
    run_id = arguments.run_id or (
        checked_at.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        + "-hyp-2026-015-no-return-gate-v2"
    )
    packet = run_gate(
        repo_root=arguments.repo_root.expanduser(),
        run_id=run_id,
        checked_at=checked_at,
        max_workers=max(1, arguments.max_workers),
    )
    print(canonical_json(packet))
    return 0 if packet["result"]["outcome"] == "READY_FOR_OUTCOME_REGISTRATION" else 2


if __name__ == "__main__":
    raise SystemExit(main())
