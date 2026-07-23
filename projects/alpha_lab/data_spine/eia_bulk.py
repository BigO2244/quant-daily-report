"""Streaming capture for large public EIA bulk archives."""

from __future__ import annotations

import os
import tempfile
import csv
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict
from urllib.request import Request, urlopen

from .registry import SourceRegistry
from .storage import latest_manifest, output_root, sha256_file, write_bundle_from_paths


Downloader = Callable[[str, Path], Dict[str, Any]]


def _download(url: str, path: Path) -> Dict[str, Any]:
    request = Request(
        url,
        headers={
            "Accept": "application/zip,*/*",
            "User-Agent": "Caerus Alpha Lab research-data-client",
        },
    )
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


def collect_eia_large_bulk(
    *,
    repo_root: Path,
    registry: SourceRegistry,
    dataset: str,
    downloader: Downloader = _download,
    retrieved_at: datetime | None = None,
) -> Dict[str, Any]:
    """Capture one EIA archive larger than the in-memory normalization gate."""

    config = registry.sources["eia"]
    name = str(dataset).strip().lower()
    if name not in config["bulk"]:
        raise ValueError("unknown EIA bulk dataset: {}".format(name))
    staging_root = output_root(repo_root) / ".staging"
    staging_root.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix="eia_{}_".format(name), suffix=".zip", dir=staging_root)
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        response_metadata = downloader(str(config["bulk"][name]), temporary)
        if temporary.stat().st_size == 0:
            raise ValueError("EIA bulk archive is empty")
        timestamp = retrieved_at or datetime.now(timezone.utc)
        return write_bundle_from_paths(
            repo_root=repo_root,
            source_id="eia_{}_bulk".format(name),
            files={"{}.zip".format(name): temporary},
            metadata={
                "dataset": name,
                "source_url": str(config["bulk"][name]),
                "api_key_required": False,
                "bulk_archive": True,
                "raw_current_vintage": True,
                "streamed_to_disk": True,
                **response_metadata,
            },
            retrieved_at=timestamp,
        )
    finally:
        temporary.unlink(missing_ok=True)


_ELECTRICITY_CONTROLS = {
    "ELEC.PRICE.US-IND.M",
    "ELEC.PRICE.US-ALL.M",
    "ELEC.GEN.ALL-US-99.M",
    "ELEC.GEN.NG-US-99.M",
    "ELEC.GEN.COW-US-99.M",
    "ELEC.GEN.NUC-US-99.M",
    "ELEC.GEN.WND-US-99.M",
    "ELEC.GEN.SUN-US-99.M",
}


def materialize_eia_electricity_controls(repo_root: Path) -> Dict[str, Any]:
    """Extract a compact monthly electricity-price and generation proxy panel."""

    manifest_path = latest_manifest(repo_root, "eia_electricity_bulk")
    if manifest_path is None:
        raise FileNotFoundError("EIA electricity bulk archive is absent")
    source = manifest_path.parent / "data/electricity.zip"
    output = repo_root / "outputs/research/alpha_lab/shared/eia_electricity_controls.csv"
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(".{}.tmp".format(output.name))
    rows = []
    observed = set()
    with zipfile.ZipFile(source) as archive:
        members = [name for name in archive.namelist() if name.lower().endswith(".txt")]
        if not members:
            raise ValueError("EIA electricity archive contains no text payload")
        with archive.open(members[0]) as stream:
            for raw_line in stream:
                try:
                    record = json.loads(raw_line)
                except (TypeError, json.JSONDecodeError):
                    continue
                series_id = str(record.get("series_id") or "")
                if series_id not in _ELECTRICITY_CONTROLS:
                    continue
                observed.add(series_id)
                for period, value in record.get("data") or []:
                    period_text = str(period)
                    if not ("201101" <= period_text <= "202606"):
                        continue
                    rows.append(
                        {
                            "series_id": series_id,
                            "name": record.get("name"),
                            "period": period_text,
                            "value": value,
                            "units": record.get("units"),
                            "frequency": record.get("f"),
                            "series_last_updated": record.get("last_updated"),
                            "available_at": record.get("last_updated"),
                            "vintage_classification": "CURRENT_BULK_VINTAGE_PROXY_ONLY",
                        }
                    )
    rows.sort(key=lambda row: (row["period"], row["series_id"]))
    fields = (
        "series_id", "name", "period", "value", "units", "frequency",
        "series_last_updated", "available_at", "vintage_classification",
    )
    with temporary.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(output)
    manifest = {
        "schema_version": "caerus_alpha_lab_eia_electricity_controls_v1",
        "classification": "RESEARCH_ONLY_NON_EXECUTIONAL",
        "source_archive": str(source.relative_to(repo_root)),
        "source_sha256": sha256_file(source),
        "output": str(output.relative_to(repo_root)),
        "output_sha256": sha256_file(output),
        "row_count": len(rows),
        "observed_series": sorted(observed),
        "missing_requested_series": sorted(_ELECTRICITY_CONTROLS - observed),
        "historical_point_in_time_verified": False,
        "current_bulk_vintage_proxy_only": True,
        "trading_behavior_changed": False,
    }
    manifest_output = output.with_name("eia_electricity_controls_manifest.json")
    manifest_output.write_text(json.dumps(manifest, separators=(",", ":"), sort_keys=True) + "\n", encoding="utf-8")
    return {
        "eia_electricity_control_rows": len(rows),
        "eia_electricity_series_count": len(observed),
        "eia_electricity_controls_path": str(output),
    }
