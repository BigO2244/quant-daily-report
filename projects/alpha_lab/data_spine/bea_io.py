"""Free BEA input-output reference and credential-gated data capture."""

from __future__ import annotations

import json
import os
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable
from urllib.request import Request, urlopen

from .storage import write_bundle


Fetcher = Callable[[str], bytes]


def _fetch(url: str) -> bytes:
    request = Request(url, headers={"User-Agent": "Caerus Alpha Lab research-data-client"})
    with urlopen(request, timeout=180) as response:
        return response.read()


def collect_bea_io_reference(
    *, repo_root: Path, fetcher: Fetcher = _fetch, retrieved_at: datetime | None = None
) -> Dict[str, Any]:
    """Capture BEA's public NAICS concordance and IO API guide without a key."""

    urls = {
        "bea_industry_commodity_naics_concordance.xlsx": (
            "https://www.bea.gov/sites/default/files/2023-10/"
            "BEA-Industry-and-Commodity-Codes-and-NAICS-Concordance.xlsx"
        ),
        "bea_web_service_api_user_guide.pdf": (
            "https://apps.bea.gov/api/_pdf/bea_web_service_api_user_guide.pdf"
        ),
    }
    files = {name: fetcher(url) for name, url in urls.items()}
    for name, payload in files.items():
        if not payload:
            raise ValueError("BEA reference download was empty: {}".format(name))
    return write_bundle(
        repo_root=repo_root,
        source_id="bea_input_output_reference",
        files=files,
        metadata={
            "api_key_required": False,
            "source_urls": urls,
            "industry_level_proxy_only": True,
            "not_issuer_relationship_edges": True,
        },
        retrieved_at=retrieved_at or datetime.now(timezone.utc),
    )


def _bea_json(url: str) -> Dict[str, Any]:
    return json.loads(_fetch(url))


def collect_bea_io_api(
    *,
    repo_root: Path,
    table_ids: Iterable[int] = (),
    years: str = "ALL",
    fetcher: Callable[[str], Dict[str, Any]] = _bea_json,
    retrieved_at: datetime | None = None,
) -> Dict[str, Any]:
    """Capture current-vintage BEA IO metadata and selected tables using a free key."""

    api_key = os.environ.get("BEA_API_KEY")
    if not api_key:
        raise RuntimeError("BEA_API_KEY is required; registration is free")
    base = "https://apps.bea.gov/api/data/"

    def request(params: Dict[str, str]) -> Dict[str, Any]:
        query = dict(params)
        query.update({"UserID": api_key, "DataSetName": "InputOutput", "ResultFormat": "JSON"})
        payload = fetcher(base + "?" + urllib.parse.urlencode(query))
        results = ((payload.get("BEAAPI") or {}).get("Results") or {})
        if results.get("Error"):
            raise RuntimeError("BEA API returned an error")
        return payload

    table_values = request({"method": "GetParameterValues", "ParameterName": "TableID"})
    year_values = request({"method": "GetParameterValues", "ParameterName": "Year"})
    files: Dict[str, bytes] = {
        "table_ids.json": (json.dumps(table_values, sort_keys=True, separators=(",", ":")) + "\n").encode(),
        "years.json": (json.dumps(year_values, sort_keys=True, separators=(",", ":")) + "\n").encode(),
    }
    selected = tuple(dict.fromkeys(int(value) for value in table_ids))
    if selected:
        data = request(
            {"method": "GetData", "TableID": ",".join(map(str, selected)), "Year": years}
        )
        files["input_output_tables.json"] = (
            json.dumps(data, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode()
    return write_bundle(
        repo_root=repo_root,
        source_id="bea_input_output_api",
        files=files,
        metadata={
            "api_key_required": True,
            "credential_value_persisted": False,
            "dataset": "InputOutput",
            "table_ids": list(selected),
            "years": years,
            "current_vintage_proxy_only": True,
            "not_issuer_relationship_edges": True,
        },
        retrieved_at=retrieved_at or datetime.now(timezone.utc),
    )
