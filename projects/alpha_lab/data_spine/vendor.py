"""Vendor-neutral sample gates for estimates and supply-chain trial extracts."""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from projects.alpha_lab.factory import canonical_json

from .storage import write_bundle


def _schema() -> Dict[str, Any]:
    return json.loads(Path(__file__).with_name("vendor_schemas.json").read_text(encoding="utf-8"))


def _physical_fields(path: Path) -> set[str]:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        with path.open("r", encoding="utf-8", newline="") as stream:
            return {str(value).strip() for value in next(csv.reader(stream), [])}
    if suffix in {".jsonl", ".ndjson"}:
        fields = set()
        with path.open("r", encoding="utf-8") as stream:
            for index, line in enumerate(stream):
                if index >= 1000:
                    break
                value = json.loads(line)
                if isinstance(value, dict):
                    fields.update(str(key) for key in value)
        return fields
    if suffix == ".json":
        value = json.loads(path.read_text(encoding="utf-8"))
        rows = value if isinstance(value, list) else [value]
        return {str(key) for row in rows[:1000] if isinstance(row, dict) for key in row}
    if suffix in {".parquet", ".pq"}:
        try:
            import pyarrow.parquet as parquet
        except ImportError as exc:
            raise RuntimeError("pyarrow is required to inspect a parquet vendor sample") from exc
        return set(parquet.read_schema(path).names)
    raise ValueError("vendor sample must be CSV, JSON, JSONL, or Parquet")


def validate_vendor_sample(
    *,
    repo_root: Path,
    kind: str,
    sample_path: Path,
    checked_at: datetime | None = None,
) -> Dict[str, Any]:
    schemas = _schema()
    if kind not in schemas:
        raise ValueError("unknown vendor sample kind")
    path = Path(sample_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    available = _physical_fields(path)
    required = set(schemas[kind]["required_fields"])
    missing = sorted(required - available)
    timestamp = checked_at or datetime.now(timezone.utc)
    payload = {
        "schema_version": "caerus_vendor_sample_gate_v1",
        "kind": kind,
        "provider_contract": schemas[kind]["provider_contract"],
        "checked_at": timestamp.isoformat(),
        "sample_filename": path.name,
        "fields_available": sorted(available),
        "required_fields": sorted(required),
        "missing_fields": missing,
        "status": "SCHEMA_PASS_PIT_AUDIT_PENDING" if not missing else "BLOCKED_SCHEMA",
        "historical_point_in_time_verified": False,
        "alpha_claim_permitted": False,
    }
    bundle = write_bundle(
        repo_root=repo_root,
        source_id="vendor_{}_gate".format(kind),
        files={"gate.json": (canonical_json(payload) + "\n").encode("utf-8")},
        metadata={"sample_content_not_copied": True, "kind": kind},
        retrieved_at=timestamp,
    )
    return {"gate": payload, **bundle}
