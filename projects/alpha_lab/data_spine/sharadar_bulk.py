"""Credential-safe Nasdaq Data Link bulk exports for large Sharadar tables."""

from __future__ import annotations

import json
import os
import tempfile
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Mapping
from urllib.request import Request, urlopen

from . import http
from .http import Response
from .registry import SourceRegistry
from .storage import output_root, write_bundle_from_paths


Fetcher = Callable[..., Response]
Downloader = Callable[[str, Path], None]


def _download(url: str, path: Path) -> None:
    request = Request(url, headers={"Accept": "application/zip"}, method="GET")
    with urlopen(request, timeout=600) as response, path.open("wb") as stream:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            stream.write(chunk)
        stream.flush()
        os.fsync(stream.fileno())


def capture_sharadar_bulk(
    *,
    repo_root: Path,
    registry: SourceRegistry,
    table: str,
    columns: Iterable[str],
    start_date: str | None = None,
    end_date: str | None = None,
    tickers: Iterable[str] = (),
    poll_interval: float = 10.0,
    timeout_seconds: float = 1800.0,
    fetcher: Fetcher = http.get,
    downloader: Downloader = _download,
    sleeper: Callable[[float], None] = time.sleep,
    retrieved_at: datetime | None = None,
) -> Dict[str, Any]:
    config = registry.sources["sharadar"]
    table_name = str(table).upper()
    if table_name not in set(config["required_tables"]):
        raise ValueError("Sharadar table is outside the approved research contract")
    env_name = str(config["api_key_env"])
    api_key = os.environ.get(env_name)
    if not api_key:
        raise RuntimeError("{} is required for Sharadar bulk capture".format(env_name))
    column_values = tuple(dict.fromkeys(str(value).strip() for value in columns if str(value).strip()))
    if not column_values:
        raise ValueError("at least one export column is required")
    ticker_values = tuple(sorted({str(value).strip().upper() for value in tickers if str(value).strip()}))
    params: Dict[str, str] = {
        "api_key": api_key,
        "qopts.export": "true",
        "qopts.columns": ",".join(column_values),
    }
    if start_date:
        params["date.gte"] = start_date
    if end_date:
        params["date.lte"] = end_date
    if ticker_values:
        params["ticker"] = ",".join(ticker_values)
    endpoint = "https://data.nasdaq.com/api/v3/datatables/SHARADAR/{}.json?{}".format(
        table_name, urllib.parse.urlencode(params)
    )
    deadline = time.monotonic() + timeout_seconds
    status = None
    snapshot = None
    link = None
    while time.monotonic() < deadline:
        response = fetcher(endpoint, headers={"Accept": "application/json"}, timeout=120)
        payload = json.loads(response.body)
        bulk = payload.get("datatable_bulk_download") or {}
        file_info = bulk.get("file") or {}
        status = str(file_info.get("status") or "").lower()
        snapshot = file_info.get("data_snapshot_time")
        if status == "fresh" and file_info.get("link"):
            link = str(file_info["link"])
            break
        sleeper(poll_interval)
    if not link:
        raise TimeoutError("Sharadar bulk export did not become fresh: {}".format(status or "unknown"))

    staging_root = output_root(repo_root) / ".staging"
    staging_root.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix="sharadar_{}_".format(table_name.lower()), suffix=".zip", dir=staging_root)
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        downloader(link, temporary)
        if temporary.stat().st_size == 0:
            raise ValueError("Sharadar bulk export returned an empty archive")
        timestamp = retrieved_at or datetime.now(timezone.utc)
        return write_bundle_from_paths(
            repo_root=repo_root,
            source_id="sharadar_{}_bulk".format(table_name.lower()),
            files={"{}.zip".format(table_name.lower()): temporary},
            metadata={
                "table": "SHARADAR/{}".format(table_name),
                "columns": list(column_values),
                "start_date": start_date,
                "end_date": end_date,
                "ticker_count": len(ticker_values),
                "tickers_sha256": __import__("hashlib").sha256(
                    "\n".join(ticker_values).encode("utf-8")
                ).hexdigest() if ticker_values else None,
                "export_status": "fresh",
                "data_snapshot_time": snapshot,
                "signed_download_url_persisted": False,
                "credential_value_persisted": False,
                "license_rights_must_be_preserved": True,
            },
            retrieved_at=timestamp,
        )
    finally:
        temporary.unlink(missing_ok=True)
