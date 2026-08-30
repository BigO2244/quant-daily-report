#!/usr/bin/env python3
"""Build an immutable, hash-bound NAV restatement proposal.

This utility is deliberately non-authoritative.  It compares the immutable
canonical portfolio-history NAV with a later broker-derived source, records
every disagreement above the governed tolerance, and can materialize a
versioned review package.  It never rewrites canonical ``nav.csv`` and it never
changes which artifact downstream reporting consumes.

The two-step write contract prevents an input from changing between review and
materialization:

1. Run without ``--write-proposal`` and review the hashes/disagreements.
2. Re-run with both reviewed hashes and ``--write-proposal``.

Accepting a proposal or switching a consumer is a separate owner action and is
intentionally outside this script.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.backfill_portfolio_history import RECON_TOLERANCE_BPS  # noqa: E402
from scripts.build_portfolio_history import NAV_FIELDS, _iso_date  # noqa: E402

SCHEMA_VERSION = "caerus_nav_restatement_proposal_v1"
CLASSIFICATION = "SOURCE_MIGRATION_PROVISIONAL_BROKER_HISTORY"
DEFAULT_BASE = "outputs/portfolio_history/nav.csv"
DEFAULT_SOURCE = "outputs/ledger/paper/daily_nav.csv"
DEFAULT_PROPOSAL_ROOT = "outputs/portfolio_history/restatement_proposals"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _read_unique_rows(path: Path, *, label: str) -> tuple[list[str], list[dict[str, str]]]:
    if not path.is_file():
        raise RuntimeError(f"{label} artifact is missing: {path}")
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        rows = [dict(row) for row in reader]
    if "date" not in fieldnames or "equity" not in fieldnames:
        raise RuntimeError(f"{label} must contain date and equity columns: {path}")
    seen: set[str] = set()
    for row in rows:
        date = _iso_date(row.get("date"))
        equity = _to_float(row.get("equity"))
        if not date or equity is None or equity <= 0.0:
            raise RuntimeError(f"{label} contains an invalid date/equity row: {row}")
        if date in seen:
            raise RuntimeError(f"{label} contains duplicate date {date}; refusing ambiguous evidence")
        seen.add(date)
        row["date"] = date
    return fieldnames, rows


def _conflicts(
    base_rows: list[dict[str, str]],
    source_rows: list[dict[str, str]],
    *,
    tolerance_bps: float,
) -> list[dict[str, Any]]:
    source_by_date = {row["date"]: row for row in source_rows}
    conflicts: list[dict[str, Any]] = []
    for base in base_rows:
        source = source_by_date.get(base["date"])
        if source is None:
            continue
        old = float(base["equity"])
        new = float(source["equity"])
        relative_bps = abs(new - old) / old * 10_000.0
        if relative_bps <= tolerance_bps:
            continue
        conflicts.append(
            {
                "date": base["date"],
                "field": "equity",
                "base_value": round(old, 8),
                "proposed_value": round(new, 8),
                "absolute_difference": round(new - old, 8),
                "absolute_difference_bps": round(relative_bps, 6),
                "base_source": base.get("source") or None,
                "proposed_source": source.get("source") or None,
                "classification": CLASSIFICATION,
                "status": "PROPOSED_NOT_ACCEPTED",
            }
        )
    return conflicts


def _proposal_id(*, base_hash: str, source_hash: str, conflicts: list[dict[str, Any]]) -> str:
    identity = {
        "schema_version": SCHEMA_VERSION,
        "base_sha256": base_hash,
        "source_sha256": source_hash,
        "conflicts": conflicts,
    }
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:20]


def _project_rows(
    base_rows: list[dict[str, str]], source_rows: list[dict[str, str]], conflicts: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    replacement_dates = {item["date"] for item in conflicts}
    source_by_date = {row["date"]: row for row in source_rows}
    projected: list[dict[str, Any]] = []
    for original in base_rows:
        row: dict[str, Any] = {field: original.get(field, "") for field in NAV_FIELDS}
        if original["date"] in replacement_dates:
            source = source_by_date[original["date"]]
            row["equity"] = source["equity"]
            row["source"] = f"restatement_proposal:{source.get('source') or 'broker_daily_nav'}"
        projected.append(row)

    first_equity: float | None = None
    previous_equity: float | None = None
    for row in projected:
        equity = float(row["equity"])
        if first_equity is None:
            first_equity = equity
        row["return_1d"] = "" if previous_equity is None else (equity / previous_equity) - 1.0
        row["cumulative_return"] = (equity / first_equity) - 1.0
        previous_equity = equity
    return projected


def _json_bytes(payload: Any) -> bytes:
    return (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _csv_bytes(rows: list[dict[str, Any]]) -> bytes:
    import io

    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=NAV_FIELDS, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({field: row.get(field, "") for field in NAV_FIELDS})
    return buffer.getvalue().encode("utf-8")


def _disposition_bytes(conflicts: list[dict[str, Any]], *, proposal_id: str) -> bytes:
    records = []
    for item in conflicts:
        record = dict(item)
        record["proposal_id"] = proposal_id
        records.append(json.dumps(record, sort_keys=True, separators=(",", ":")))
    return (("\n".join(records) + "\n") if records else "").encode("utf-8")


def _write_once(path: Path, content: bytes) -> None:
    if path.exists():
        if path.read_bytes() != content:
            raise RuntimeError(f"immutable proposal artifact already exists with different bytes: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, staged_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(staged_name, path)
    finally:
        staged = Path(staged_name)
        if staged.exists():
            staged.unlink()


def build_proposal(
    *,
    repo_root: Path | str,
    base_nav: Path | str = DEFAULT_BASE,
    source_nav: Path | str = DEFAULT_SOURCE,
    proposal_root: Path | str = DEFAULT_PROPOSAL_ROOT,
    tolerance_bps: float = RECON_TOLERANCE_BPS,
    generated_at: str | None = None,
    write_proposal: bool = False,
    expected_base_sha256: str | None = None,
    expected_source_sha256: str | None = None,
) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    base_path = Path(base_nav)
    source_path = Path(source_nav)
    proposal_path = Path(proposal_root)
    if not base_path.is_absolute():
        base_path = root / base_path
    if not source_path.is_absolute():
        source_path = root / source_path
    if not proposal_path.is_absolute():
        proposal_path = root / proposal_path

    base_fields, base_rows = _read_unique_rows(base_path, label="base NAV")
    _, source_rows = _read_unique_rows(source_path, label="source NAV")
    missing_fields = [field for field in NAV_FIELDS if field not in base_fields]
    if missing_fields:
        raise RuntimeError(f"base NAV is missing canonical fields: {missing_fields}")

    base_hash = _sha256(base_path)
    source_hash = _sha256(source_path)
    conflicts = _conflicts(base_rows, source_rows, tolerance_bps=tolerance_bps)
    proposal_id = _proposal_id(base_hash=base_hash, source_hash=source_hash, conflicts=conflicts)
    projection = _project_rows(base_rows, source_rows, conflicts)
    projection_bytes = _csv_bytes(projection)
    dispositions_bytes = _disposition_bytes(conflicts, proposal_id=proposal_id)

    manifest: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "proposal_id": proposal_id,
        "generated_at": generated_at or datetime.now(tz=timezone.utc).isoformat(),
        "governance_label": "OPERATIONAL_TELEMETRY",
        "execution_impact": "NON_EXECUTIONAL",
        "authority": "PROPOSAL_ONLY",
        "consumer_switch_authorized": False,
        "classification": CLASSIFICATION,
        "tolerance_bps": tolerance_bps,
        "base": {
            "path": str(base_path),
            "sha256": base_hash,
            "rows": len(base_rows),
        },
        "source": {
            "path": str(source_path),
            "sha256": source_hash,
            "rows": len(source_rows),
        },
        "conflict_count": len(conflicts),
        "conflict_dates": [item["date"] for item in conflicts],
        "projection_sha256": hashlib.sha256(projection_bytes).hexdigest(),
        "dispositions_sha256": hashlib.sha256(dispositions_bytes).hexdigest(),
        "canonical_base_unchanged": True,
        "review_required": "Owner approval is required before acceptance or any consumer switch.",
        "mode": "write_proposal" if write_proposal else "dry_run",
    }

    if write_proposal:
        if not expected_base_sha256 or not expected_source_sha256:
            raise RuntimeError(
                "--write-proposal requires both reviewed --expected-base-sha256 and "
                "--expected-source-sha256 values"
            )
        if expected_base_sha256 != base_hash:
            raise RuntimeError("base NAV hash changed after review; refusing proposal write")
        if expected_source_sha256 != source_hash:
            raise RuntimeError("source NAV hash changed after review; refusing proposal write")
        destination = proposal_path / proposal_id
        existing_manifest_path = destination / "manifest.json"
        if existing_manifest_path.exists():
            try:
                existing_manifest = json.loads(existing_manifest_path.read_text(encoding="utf-8"))
            except Exception as exc:
                raise RuntimeError(f"existing immutable proposal manifest is unreadable: {exc}") from exc
            bindings = (
                existing_manifest.get("proposal_id") == proposal_id,
                (existing_manifest.get("base") or {}).get("sha256") == base_hash,
                (existing_manifest.get("source") or {}).get("sha256") == source_hash,
                existing_manifest.get("projection_sha256") == hashlib.sha256(projection_bytes).hexdigest(),
                existing_manifest.get("dispositions_sha256") == hashlib.sha256(dispositions_bytes).hexdigest(),
            )
            if not all(bindings):
                raise RuntimeError("existing immutable proposal does not match the reviewed input bindings")
            if not (destination / "nav_as_restated.csv").is_file() or not (
                destination / "dispositions.jsonl"
            ).is_file():
                raise RuntimeError("existing immutable proposal is incomplete")
            if _sha256(destination / "nav_as_restated.csv") != existing_manifest["projection_sha256"]:
                raise RuntimeError("existing proposal projection hash is invalid")
            if _sha256(destination / "dispositions.jsonl") != existing_manifest["dispositions_sha256"]:
                raise RuntimeError("existing proposal disposition hash is invalid")
            existing_manifest["proposal_directory"] = str(destination)
            return existing_manifest
        _write_once(destination / "dispositions.jsonl", dispositions_bytes)
        _write_once(destination / "nav_as_restated.csv", projection_bytes)
        _write_once(destination / "manifest.json", _json_bytes(manifest))
        if _sha256(base_path) != base_hash:
            raise RuntimeError("canonical base NAV changed during proposal materialization")
        manifest["proposal_directory"] = str(destination)
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--base-nav", default=DEFAULT_BASE)
    parser.add_argument("--source-nav", default=DEFAULT_SOURCE)
    parser.add_argument("--proposal-root", default=DEFAULT_PROPOSAL_ROOT)
    parser.add_argument("--tolerance-bps", type=float, default=RECON_TOLERANCE_BPS)
    parser.add_argument("--write-proposal", action="store_true")
    parser.add_argument("--expected-base-sha256")
    parser.add_argument("--expected-source-sha256")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    manifest = build_proposal(
        repo_root=args.repo_root,
        base_nav=args.base_nav,
        source_nav=args.source_nav,
        proposal_root=args.proposal_root,
        tolerance_bps=args.tolerance_bps,
        write_proposal=args.write_proposal,
        expected_base_sha256=args.expected_base_sha256,
        expected_source_sha256=args.expected_source_sha256,
    )
    if args.json:
        print(json.dumps(manifest, indent=2, sort_keys=True))
    else:
        print(
            f"NAV restatement proposal {manifest['proposal_id']}: "
            f"{manifest['conflict_count']} conflict(s), mode={manifest['mode']}"
        )
        print(f"base sha256:   {manifest['base']['sha256']}")
        print(f"source sha256: {manifest['source']['sha256']}")
        if manifest.get("proposal_directory"):
            print(f"proposal:      {manifest['proposal_directory']}")
        else:
            print("DRY RUN: no files written; canonical NAV remains unchanged.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
