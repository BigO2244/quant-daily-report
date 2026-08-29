"""Page-streamed, compressed Sharadar captures for large verified table slices."""

from __future__ import annotations

import csv
import gzip
import json
import os
import shutil
import tempfile
import time
import urllib.parse
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable

from . import http
from .http import Response
from .registry import SourceRegistry
from .storage import output_root, write_bundle_from_paths


Fetcher = Callable[..., Response]


def _chunks(values: tuple[str, ...], size: int) -> list[tuple[str, ...]]:
    if not values:
        return [()]
    return [values[index : index + size] for index in range(0, len(values), size)]


def capture_sharadar_stream(
    *,
    repo_root: Path,
    registry: SourceRegistry,
    table: str,
    columns: Iterable[str],
    start_date: str | None = None,
    end_date: str | None = None,
    tickers: Iterable[str] = (),
    ticker_chunk_size: int = 200,
    fetcher: Fetcher = http.get,
    retrieved_at: datetime | None = None,
    sleeper: Callable[[float], None] = time.sleep,
    request_attempts: int = 5,
) -> Dict[str, Any]:
    config = registry.sources["sharadar"]
    table_name = str(table).upper()
    if table_name not in set(config["required_tables"]):
        raise ValueError("Sharadar table is outside the approved research contract")
    env_name = str(config["api_key_env"])
    api_key = os.environ.get(env_name)
    if not api_key:
        raise RuntimeError("{} is required for Sharadar stream capture".format(env_name))
    column_values = tuple(dict.fromkeys(str(value).strip() for value in columns if str(value).strip()))
    if not column_values:
        raise ValueError("at least one capture column is required")
    ticker_values = tuple(sorted({str(value).strip().upper() for value in tickers if str(value).strip()}))
    if ticker_chunk_size < 1 or ticker_chunk_size > 500:
        raise ValueError("ticker_chunk_size must be between 1 and 500")
    if request_attempts < 1:
        raise ValueError("request_attempts must be positive")

    staging_root = output_root(repo_root) / ".staging"
    staging_root.mkdir(parents=True, exist_ok=True)
    capture_key = hashlib.sha256(
        json.dumps(
            {
                "table": table_name,
                "columns": column_values,
                "start_date": start_date,
                "end_date": end_date,
                "tickers": ticker_values,
                "ticker_chunk_size": ticker_chunk_size,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()[:16]
    checkpoint_root = staging_root / "sharadar_{}_{}".format(table_name.lower(), capture_key)
    checkpoint_root.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix="sharadar_{}_".format(table_name.lower()), suffix=".csv.gz", dir=staging_root
    )
    os.close(fd)
    temporary = Path(temporary_name)
    row_count = 0
    page_count = 0
    observed_tickers: set[str] = set()
    first_date = None
    last_date = None
    complete = False
    try:
        chunk_paths = []
        for chunk_number, ticker_chunk in enumerate(_chunks(ticker_values, ticker_chunk_size)):
            chunk_path = checkpoint_root / "chunk_{:05d}.csv.gz".format(chunk_number)
            status_path = checkpoint_root / "chunk_{:05d}.json".format(chunk_number)
            if not chunk_path.is_file() or not status_path.is_file():
                chunk_temporary = chunk_path.with_suffix(".csv.gz.tmp")
                cursor = None
                chunk_rows = 0
                chunk_pages = 0
                chunk_tickers: set[str] = set()
                chunk_first_date = None
                chunk_last_date = None
                try:
                    with gzip.open(
                        chunk_temporary, "wt", encoding="utf-8", newline="", compresslevel=6
                    ) as stream:
                        writer = csv.writer(stream, lineterminator="\n")
                        if chunk_number == 0:
                            writer.writerow(column_values)
                        for _ in range(100000):
                            params: Dict[str, Any] = {
                                "api_key": api_key,
                                "qopts.per_page": 10000,
                                "qopts.columns": ",".join(column_values),
                            }
                            if start_date:
                                params["date.gte"] = start_date
                            if end_date:
                                params["date.lte"] = end_date
                            if ticker_chunk:
                                params["ticker"] = ",".join(ticker_chunk)
                            if cursor:
                                params["qopts.cursor_id"] = cursor
                            url = "https://data.nasdaq.com/api/v3/datatables/SHARADAR/{}.json?{}".format(
                                table_name, urllib.parse.urlencode(params)
                            )
                            for attempt in range(request_attempts):
                                try:
                                    response = fetcher(
                                        url, headers={"Accept": "application/json"}, timeout=180
                                    )
                                    break
                                except Exception as exc:
                                    if attempt + 1 >= request_attempts:
                                        raise RuntimeError(
                                            "Sharadar request failed after bounded retries: {}".format(
                                                type(exc).__name__
                                            )
                                        ) from None
                                    sleeper(min(2 ** attempt, 30))
                            payload = json.loads(response.body)
                            datatable = payload.get("datatable") or {}
                            page_columns = tuple(
                                str(item.get("name")) for item in datatable.get("columns") or []
                            )
                            if page_columns != column_values:
                                raise ValueError("Sharadar schema mismatch during streamed capture")
                            ticker_index = (
                                column_values.index("ticker") if "ticker" in column_values else None
                            )
                            date_index = column_values.index("date") if "date" in column_values else None
                            for values in datatable.get("data") or []:
                                writer.writerow(values)
                                chunk_rows += 1
                                if ticker_index is not None and ticker_index < len(values):
                                    chunk_tickers.add(str(values[ticker_index]))
                                if date_index is not None and date_index < len(values):
                                    date_value = str(values[date_index])[:10]
                                    chunk_first_date = (
                                        date_value
                                        if chunk_first_date is None
                                        else min(chunk_first_date, date_value)
                                    )
                                    chunk_last_date = (
                                        date_value
                                        if chunk_last_date is None
                                        else max(chunk_last_date, date_value)
                                    )
                            chunk_pages += 1
                            cursor = (payload.get("meta") or {}).get("next_cursor_id")
                            if not cursor:
                                break
                        else:
                            raise RuntimeError("Sharadar stream pagination safety cap reached")
                    chunk_temporary.replace(chunk_path)
                    status_temporary = status_path.with_suffix(".json.tmp")
                    status_temporary.write_text(
                        json.dumps(
                            {
                                "row_count": chunk_rows,
                                "page_count": chunk_pages,
                                "observed_tickers": sorted(chunk_tickers),
                                "first_date": chunk_first_date,
                                "last_date": chunk_last_date,
                            },
                            separators=(",", ":"),
                            sort_keys=True,
                        )
                        + "\n",
                        encoding="utf-8",
                    )
                    status_temporary.replace(status_path)
                except Exception:
                    chunk_temporary.unlink(missing_ok=True)
                    raise
            status = json.loads(status_path.read_text(encoding="utf-8"))
            row_count += int(status["row_count"])
            page_count += int(status["page_count"])
            observed_tickers.update(str(value) for value in status["observed_tickers"])
            if status.get("first_date"):
                first_date = (
                    status["first_date"] if first_date is None else min(first_date, status["first_date"])
                )
            if status.get("last_date"):
                last_date = (
                    status["last_date"] if last_date is None else max(last_date, status["last_date"])
                )
            chunk_paths.append(chunk_path)

        with gzip.open(
            temporary, "wt", encoding="utf-8", newline="", compresslevel=6
        ) as stream:
            for chunk_path in chunk_paths:
                with gzip.open(chunk_path, "rt", encoding="utf-8", newline="") as chunk_stream:
                    shutil.copyfileobj(chunk_stream, stream, length=1024 * 1024)
        timestamp = retrieved_at or datetime.now(timezone.utc)
        result = write_bundle_from_paths(
            repo_root=repo_root,
            source_id="sharadar_{}_stream".format(table_name.lower()),
            files={"{}.csv.gz".format(table_name.lower()): temporary},
            metadata={
                "table": "SHARADAR/{}".format(table_name),
                "columns": list(column_values),
                "start_date": start_date,
                "end_date": end_date,
                "requested_ticker_count": len(ticker_values),
                "observed_ticker_count": len(observed_tickers),
                "ticker_chunk_size": ticker_chunk_size,
                "row_count": row_count,
                "page_count": page_count,
                "observed_date_range": [first_date, last_date],
                "credential_value_persisted": False,
                "pagination_complete": True,
                "license_rights_must_be_preserved": True,
            },
            retrieved_at=timestamp,
        )
        complete = True
        return result
    finally:
        temporary.unlink(missing_ok=True)
        if complete:
            shutil.rmtree(checkpoint_root, ignore_errors=True)
