"""Streaming SEC bulk archives with immutable, credential-free lineage."""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict
from urllib.request import Request, urlopen

from .registry import SourceRegistry
from .storage import output_root, write_bundle_from_paths


Downloader = Callable[[str, Path, str], Dict[str, Any]]


def _download(url: str, path: Path, user_agent: str) -> Dict[str, Any]:
    request = Request(url, headers={"User-Agent": user_agent, "Accept": "application/zip"})
    with urlopen(request, timeout=1800) as response, path.open("wb") as stream:
        headers = {str(key).lower(): str(value) for key, value in response.headers.items()}
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            stream.write(chunk)
        stream.flush()
        os.fsync(stream.fileno())
    return {
        "content_length_header": headers.get("content-length"),
        "last_modified": headers.get("last-modified"),
        "etag": headers.get("etag"),
    }


def collect_sec_companyfacts_bulk(
    *,
    repo_root: Path,
    registry: SourceRegistry,
    user_agent: str | None = None,
    downloader: Downloader = _download,
    retrieved_at: datetime | None = None,
) -> Dict[str, Any]:
    config = registry.sources["sec"]
    agent = user_agent or os.environ.get(str(config["user_agent_env"]))
    if not agent or "@" not in agent:
        raise RuntimeError("SEC_USER_AGENT must identify the research client and a contact email")
    url = "https://www.sec.gov/Archives/edgar/daily-index/xbrl/companyfacts.zip"
    staging_root = output_root(repo_root) / ".staging"
    staging_root.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix="sec_companyfacts_", suffix=".zip", dir=staging_root)
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        response_metadata = downloader(url, temporary, agent)
        if temporary.stat().st_size == 0:
            raise ValueError("SEC companyfacts archive is empty")
        timestamp = retrieved_at or datetime.now(timezone.utc)
        return write_bundle_from_paths(
            repo_root=repo_root,
            source_id="sec_companyfacts",
            files={"companyfacts.zip": temporary},
            metadata={
                "source_url": url,
                "bulk_archive": True,
                "historical_point_in_time_source": True,
                "availability_rule": "facts_available_no_earlier_than_filed_timestamp",
                "user_agent_persisted": False,
                **response_metadata,
            },
            retrieved_at=timestamp,
        )
    finally:
        temporary.unlink(missing_ok=True)


def collect_sec_submissions_bulk(
    *,
    repo_root: Path,
    registry: SourceRegistry,
    user_agent: str | None = None,
    downloader: Downloader = _download,
    retrieved_at: datetime | None = None,
) -> Dict[str, Any]:
    """Capture the SEC bulk submissions archive used for exact filing metadata.

    Unlike the quarterly master index, the submissions payload includes EDGAR
    acceptance timestamps and 8-K item codes.  The archive is retained intact
    so later parsers can be independently reproduced without another download.
    """

    config = registry.sources["sec"]
    agent = user_agent or os.environ.get(str(config["user_agent_env"]))
    if not agent or "@" not in agent:
        raise RuntimeError("SEC_USER_AGENT must identify the research client and a contact email")
    url = "https://www.sec.gov/Archives/edgar/daily-index/bulkdata/submissions.zip"
    staging_root = output_root(repo_root) / ".staging"
    staging_root.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix="sec_submissions_", suffix=".zip", dir=staging_root
    )
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        response_metadata = downloader(url, temporary, agent)
        if temporary.stat().st_size == 0:
            raise ValueError("SEC submissions archive is empty")
        timestamp = retrieved_at or datetime.now(timezone.utc)
        return write_bundle_from_paths(
            repo_root=repo_root,
            source_id="sec_submissions",
            files={"submissions.zip": temporary},
            metadata={
                "source_url": url,
                "bulk_archive": True,
                "historical_point_in_time_source": True,
                "contains_acceptance_datetime": True,
                "contains_8k_item_codes": True,
                "user_agent_persisted": False,
                **response_metadata,
            },
            retrieved_at=timestamp,
        )
    finally:
        temporary.unlink(missing_ok=True)
