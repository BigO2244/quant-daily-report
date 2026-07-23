"""Resumable, partitioned capture of original SEC submissions."""

from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import os
import shutil
import sqlite3
import tarfile
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Iterator
from zoneinfo import ZoneInfo

from projects.alpha_lab.factory import canonical_json

from . import http
from .registry import SourceRegistry
from .sources import _SEC_ACCEPTANCE, _SEC_ACCESSION
from .storage import output_root, sha256_file, write_bundle_from_paths


Fetcher = Callable[..., http.Response]
_CANDIDATE_FIELDS = (
    "cik",
    "company_name",
    "form_type",
    "filed_date",
    "filename",
    "index_year",
    "index_quarter",
)


def _build_candidate_index(
    *,
    source_path: Path,
    database_path: Path,
    requested_forms: tuple[str, ...],
) -> int:
    """Build a deterministic, disk-backed candidate index with bounded memory."""

    temporary = database_path.with_name(".{}.tmp.{}".format(database_path.name, os.getpid()))
    temporary.unlink(missing_ok=True)
    connection = sqlite3.connect(temporary)
    count = 0
    try:
        connection.execute("PRAGMA journal_mode=OFF")
        connection.execute("PRAGMA synchronous=OFF")
        connection.execute("PRAGMA temp_store=FILE")
        connection.execute("PRAGMA cache_size=-8192")
        connection.execute(
            """
            CREATE TABLE candidates (
                source_ordinal INTEGER NOT NULL,
                cik TEXT NOT NULL,
                company_name TEXT NOT NULL,
                form_type TEXT NOT NULL,
                filed_date TEXT NOT NULL,
                filename TEXT NOT NULL,
                index_year TEXT NOT NULL,
                index_quarter TEXT NOT NULL
            )
            """
        )
        insert = (
            "INSERT INTO candidates "
            "(source_ordinal, cik, company_name, form_type, filed_date, filename, index_year, index_quarter) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
        )
        batch = []
        with source_path.open("r", encoding="utf-8", newline="") as stream:
            for source_ordinal, row in enumerate(csv.DictReader(stream)):
                if str(row.get("form_type") or "") not in requested_forms:
                    continue
                batch.append(
                    (
                        source_ordinal,
                        *(str(row.get(field) or "") for field in _CANDIDATE_FIELDS),
                    )
                )
                if len(batch) >= 1000:
                    connection.executemany(insert, batch)
                    count += len(batch)
                    batch.clear()
            if batch:
                connection.executemany(insert, batch)
                count += len(batch)
        connection.execute(
            """
            CREATE INDEX candidate_order
            ON candidates(index_year, filed_date, cik, filename, source_ordinal)
            """
        )
        connection.commit()
    except Exception:
        connection.close()
        temporary.unlink(missing_ok=True)
        raise
    else:
        connection.close()
    os.replace(temporary, database_path)
    return count


def _candidate_partitions(database_path: Path, size: int) -> Iterator[list[Dict[str, str]]]:
    """Read deterministic partitions from SQLite without materializing the full index."""

    connection = sqlite3.connect(database_path)
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA temp_store=FILE")
    connection.execute("PRAGMA cache_size=-8192")
    cursor = connection.execute(
        """
        SELECT cik, company_name, form_type, filed_date, filename, index_year, index_quarter
        FROM candidates
        ORDER BY index_year, filed_date, cik, filename, source_ordinal
        """
    )
    try:
        while True:
            rows = cursor.fetchmany(size)
            if not rows:
                return
            yield [dict(zip(_CANDIDATE_FIELDS, row)) for row in rows]
    finally:
        cursor.close()
        connection.close()


def capture_sec_original_stream(
    *,
    repo_root: Path,
    registry: SourceRegistry,
    index_path: Path,
    forms: Iterable[str] = ("4", "4/A"),
    partition_size: int = 1000,
    max_new_partitions: int | None = None,
    user_agent: str | None = None,
    fetcher: Fetcher = http.get,
    sleeper: Callable[[float], None] = time.sleep,
    request_attempts: int = 5,
    request_workers: int = 4,
    retrieved_at: datetime | None = None,
) -> Dict[str, Any]:
    """Capture every selected original submission without holding filings in memory."""

    config = registry.sources["sec"]
    agent = user_agent or os.environ.get(str(config["user_agent_env"]))
    if not agent or "@" not in agent:
        raise RuntimeError("SEC_USER_AGENT must identify the research client and a contact email")
    path = Path(index_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    if partition_size < 1 or partition_size > 10000:
        raise ValueError("partition_size must be between 1 and 10000")
    if request_attempts < 1:
        raise ValueError("request_attempts must be positive")
    if request_workers < 1 or request_workers > 6:
        raise ValueError("request_workers must be between 1 and 6")
    if max_new_partitions is not None and max_new_partitions < 1:
        raise ValueError("max_new_partitions must be positive")
    requested_forms = tuple(sorted({str(value).strip() for value in forms if str(value).strip()}))
    index_sha256 = sha256_file(path)
    capture_key = hashlib.sha256(
        canonical_json(
            {
                "index_sha256": index_sha256,
                "forms": requested_forms,
                "partition_size": partition_size,
            }
        ).encode("utf-8")
    ).hexdigest()[:16]
    checkpoint_root = output_root(repo_root) / ".staging" / "sec_original_{}".format(capture_key)
    checkpoint_root.mkdir(parents=True, exist_ok=True)
    candidate_database = checkpoint_root / "candidate_index.sqlite3"
    if not candidate_database.is_file():
        candidate_count = _build_candidate_index(
            source_path=path,
            database_path=candidate_database,
            requested_forms=requested_forms,
        )
    else:
        with sqlite3.connect(candidate_database) as connection:
            candidate_count = int(connection.execute("SELECT COUNT(*) FROM candidates").fetchone()[0])
    if not candidate_count:
        raise ValueError("SEC original stream contains no selected candidates")

    total_hydrated = 0
    total_errors = 0
    total_acceptance = 0
    files: Dict[str, Path] = {}
    new_partitions = 0
    total_partition_count = (candidate_count + partition_size - 1) // partition_size

    def fetch_candidate(row: Dict[str, str]) -> tuple[Dict[str, str], bytes | None, Dict[str, Any] | None]:
        filename = str(row.get("filename") or "")
        url = "https://www.sec.gov/Archives/" + filename
        try:
            for attempt in range(request_attempts):
                try:
                    response = fetcher(url, headers={"User-Agent": agent}, timeout=120)
                    break
                except Exception as exc:
                    if attempt + 1 >= request_attempts:
                        raise RuntimeError(
                            "SEC request failed after bounded retries: {}".format(type(exc).__name__)
                        ) from None
                    sleeper(min(float(2 ** attempt), 30.0))
            # Detach the payload from the response object before the worker
            # publishes its result.  Some fetchers expose a bytes subclass or
            # another response-owned buffer; retaining that object in a Future
            # can let completed responses outlive the configured worker bound.
            body = bytes(response.body)
            del response
            return row, body, None
        except Exception as exc:
            return row, None, {
                "source_filename": filename,
                "error_type": type(exc).__name__,
            }

    for partition_number, partition in enumerate(_candidate_partitions(candidate_database, partition_size)):
        prefix = "part_{:05d}".format(partition_number)
        archive_path = checkpoint_root / "{}.tar.gz".format(prefix)
        inventory_path = checkpoint_root / "{}_inventory.jsonl.gz".format(prefix)
        status_path = checkpoint_root / "{}_status.json".format(prefix)
        complete_checkpoint = archive_path.is_file() and inventory_path.is_file() and status_path.is_file()
        if complete_checkpoint:
            prior_status = json.loads(status_path.read_text(encoding="utf-8"))
            complete_checkpoint = int(prior_status.get("error_count") or 0) == 0
        if not complete_checkpoint and max_new_partitions is not None and new_partitions >= max_new_partitions:
            break
        if not complete_checkpoint:
            archive_path.unlink(missing_ok=True)
            inventory_path.unlink(missing_ok=True)
            status_path.unlink(missing_ok=True)
            archive_tmp = archive_path.with_suffix(".tar.gz.tmp")
            inventory_tmp = inventory_path.with_suffix(".jsonl.gz.tmp")
            errors = []
            hydrated_count = 0
            acceptance_count = 0
            try:
                with (
                    ThreadPoolExecutor(max_workers=request_workers) as executor,
                    tarfile.open(archive_tmp, "w:gz") as archive,
                    gzip.open(inventory_tmp, "wt", encoding="utf-8", compresslevel=6) as inventory_stream,
                ):
                    pending = deque()
                    next_row = 0
                    while pending or next_row < len(partition):
                        while next_row < len(partition) and len(pending) < request_workers:
                            pending.append(executor.submit(fetch_candidate, partition[next_row]))
                            next_row += 1
                            if next_row < len(partition):
                                # 0.13 seconds caps request starts below the SEC's
                                # published 10-request/second fair-access ceiling.
                                sleeper(0.13)
                        row, body, error = pending.popleft().result()
                        filename = str(row.get("filename") or "")
                        if error is not None or body is None:
                            errors.append(error or {"source_filename": filename, "error_type": "EMPTY_RESPONSE"})
                            continue
                        try:
                            acceptance_match = _SEC_ACCEPTANCE.search(body)
                            accession_match = _SEC_ACCESSION.search(body)
                            accession = (
                                accession_match.group(1).decode("ascii")
                                if accession_match else Path(filename).stem
                            )
                            member = tarfile.TarInfo("filings/{}.txt".format(accession.replace("/", "-")))
                            member.size = len(body)
                            member.mtime = 0
                            member.mode = 0o600
                            archive.addfile(member, io.BytesIO(body))
                            accepted_local = (
                                datetime.strptime(acceptance_match.group(1).decode("ascii"), "%Y%m%d%H%M%S")
                                .replace(tzinfo=ZoneInfo("America/New_York"))
                                if acceptance_match else None
                            )
                            record = {
                                "accession_number": accession,
                                "cik": row.get("cik"),
                                "form_type": row.get("form_type"),
                                "filed_date": row.get("filed_date"),
                                "acceptance_datetime_utc": accepted_local.astimezone(timezone.utc).isoformat() if accepted_local else None,
                                "acceptance_datetime_et": accepted_local.isoformat() if accepted_local else None,
                                "source_filename": filename,
                                "source_sha256": hashlib.sha256(body).hexdigest(),
                                "acceptance_parse_status": "PASS" if accepted_local else "MISSING",
                            }
                            inventory_stream.write(canonical_json(record) + "\n")
                            hydrated_count += 1
                            acceptance_count += int(accepted_local is not None)
                        except Exception as exc:
                            errors.append(
                                {
                                    "source_filename": filename,
                                    "error_type": type(exc).__name__,
                                }
                            )
                        finally:
                            del body
                archive_tmp.replace(archive_path)
                inventory_tmp.replace(inventory_path)
                status_tmp = status_path.with_suffix(".json.tmp")
                status_tmp.write_text(
                    canonical_json(
                        {
                            "partition_number": partition_number,
                            "candidate_count": len(partition),
                            "hydrated_count": hydrated_count,
                            "error_count": len(errors),
                            "acceptance_timestamp_pass_count": acceptance_count,
                            "errors": errors,
                        }
                    ) + "\n",
                    encoding="utf-8",
                )
                status_tmp.replace(status_path)
                new_partitions += 1
            except Exception:
                archive_tmp.unlink(missing_ok=True)
                inventory_tmp.unlink(missing_ok=True)
                raise
        status = json.loads(status_path.read_text(encoding="utf-8"))
        total_hydrated += int(status["hydrated_count"])
        total_errors += int(status["error_count"])
        total_acceptance += int(status["acceptance_timestamp_pass_count"])
        files["partitions/{}".format(archive_path.name)] = archive_path
        files["inventory/{}".format(inventory_path.name)] = inventory_path
        files["status/{}".format(status_path.name)] = status_path

    completed_partition_count = len(files) // 3
    if completed_partition_count < total_partition_count:
        return {
            "capture_status": "IN_PROGRESS",
            "checkpoint_path": str(checkpoint_root),
            "candidate_count": candidate_count,
            "partition_count": total_partition_count,
            "completed_partition_count": completed_partition_count,
            "hydrated_count": total_hydrated,
            "error_count": total_errors,
            "acceptance_timestamp_pass_count": total_acceptance,
            "trading_behavior_changed": False,
        }

    timestamp = retrieved_at or datetime.now(timezone.utc)
    result = write_bundle_from_paths(
        repo_root=repo_root,
        source_id="sec_original_filings_stream",
        files=files,
        metadata={
            "index_path": str(path.relative_to(repo_root.resolve())),
            "index_sha256": index_sha256,
            "forms": list(requested_forms),
            "candidate_count": candidate_count,
            "partition_size": partition_size,
            "partition_count": total_partition_count,
            "hydrated_count": total_hydrated,
            "error_count": total_errors,
            "acceptance_timestamp_pass_count": total_acceptance,
            "original_submission_preserved": True,
            "resumable_partitions": True,
            "user_agent_persisted": False,
        },
        retrieved_at=timestamp,
    )
    shutil.rmtree(checkpoint_root, ignore_errors=True)
    return result
