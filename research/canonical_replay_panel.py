"""Canonical PIT replay price panel builder (RESEARCH_ONLY / NON_EXECUTIONAL).

Builds a long, security_id-keyed price panel from FR-068 PIT family membership
and Sharadar SEP adjusted close files. Tickers are display/source metadata only.
"""
from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from research.pit_universe import DEFAULT_DATA_DIR, FAMILY_MEMBERSHIP_FILES

SCHEMA_VERSION = "caerus_canonical_replay_price_panel_v1"
PRODUCED_BY = "research.canonical_replay_panel"
PRICE_SOURCE = "sharadar_sep_closeadj"
CERTIFIED_MEMBERSHIP_METHODS = {
    "PIT_DAILY_MARKETCAP",
    "PIT_INDEX_MEMBERSHIP",
    "PIT_RECONSTITUTION_MEMBERSHIP",
    "PIT_DECISION_TAPE",
    "PIT_SHARES_PRICE_RECONSTRUCTION",
    "PIT_HYBRID_MEMBERSHIP",
}
DISALLOWED_MEMBERSHIP_METHODS = {"CURRENT_SCALE_APPROXIMATION"}


@dataclass(frozen=True)
class CanonicalPricePanelResult:
    panel: pd.DataFrame
    manifest: dict[str, Any]


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def stable_digest(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _membership_path(data_dir: Path, family: str) -> Path:
    if family == "sharadar_security_existence":
        return data_dir / "membership_universe.csv"
    file_name = FAMILY_MEMBERSHIP_FILES.get(family)
    if not file_name:
        raise ValueError(f"unknown PIT membership family: {family}")
    return data_dir / file_name


def _infer_membership_method(row: dict[str, str]) -> str:
    explicit = str(row.get("membership_method") or row.get("membership_certification_method") or "").strip()
    if explicit:
        return explicit
    scale_source = str(row.get("scale_source") or "").strip()
    if scale_source == "marketcap":
        return "PIT_DAILY_MARKETCAP"
    if scale_source == "scalemarketcap":
        return "CURRENT_SCALE_APPROXIMATION"
    if scale_source:
        return f"UNKNOWN_SCALE_SOURCE:{scale_source}"
    return "UNKNOWN_MEMBERSHIP_METHOD"


def _classify_membership_certification(rows: list[dict[str, str]]) -> dict[str, Any]:
    if not rows:
        return {
            "status": "FAIL",
            "methods": [],
            "blockers": ["MISSING_MEMBERSHIP_ROWS"],
            "warnings": [],
        }
    methods = sorted({_infer_membership_method(row) for row in rows})
    blockers: list[str] = []
    warnings: list[str] = []
    if any(method in DISALLOWED_MEMBERSHIP_METHODS for method in methods):
        blockers.extend([
            "PIT_DATE_EFFECTIVE_LARGE_CAP_MEMBERSHIP_REQUIRED",
            "CURRENT_SCALE_MEMBERSHIP_NOT_DECISION_GRADE",
        ])
    unknown = [method for method in methods if method not in CERTIFIED_MEMBERSHIP_METHODS | DISALLOWED_MEMBERSHIP_METHODS]
    if unknown:
        blockers.append("UNKNOWN_MEMBERSHIP_CERTIFICATION_METHOD")
        warnings.append(f"UNKNOWN_MEMBERSHIP_METHODS:{','.join(unknown)}")
    status = "PASS" if not blockers and set(methods).issubset(CERTIFIED_MEMBERSHIP_METHODS) else "FAIL"
    return {
        "status": status,
        "methods": methods,
        "blockers": sorted(set(blockers)),
        "warnings": sorted(set(warnings)),
    }


def _classify_scale_precision(rows: list[dict[str, str]]) -> tuple[str, list[str]]:
    """Backward-compatible scale label; decision-grade uses membership status."""
    scale_sources = sorted({str(row.get("scale_source") or "").strip() for row in rows if row.get("scale_source")})
    if not scale_sources:
        return "NO_SCALE_SOURCE", ["SCALE_SOURCE_MISSING"]
    if scale_sources == ["marketcap"]:
        return "PIT_EXACT_SCALE", []
    if "scalemarketcap" in scale_sources:
        return "PIT_APPROXIMATE_SCALE", ["PIT_DATE_EFFECTIVE_LARGE_CAP_MEMBERSHIP_REQUIRED"]
    return "UNKNOWN_SCALE_SOURCE", ["UNKNOWN_SCALE_SOURCE"]


def _sep_file_name(ticker: str) -> str:
    return f"{ticker.replace('/', '_')}.csv"


def build_canonical_price_panel(
    *,
    repo_root: Path | str = Path("."),
    membership_family: str = "caerus_large_cap",
    start_date: str | None = None,
    end_date: str | None = None,
    data_dir: Path | str | None = None,
    sep_cache_dir: Path | str | None = None,
    replay_id: str = "canonical_pit_replay",
) -> CanonicalPricePanelResult:
    """Return a security_id keyed panel and deterministic lineage manifest."""
    root = Path(repo_root)
    pit_dir = Path(data_dir) if data_dir is not None else root / DEFAULT_DATA_DIR.relative_to(DEFAULT_DATA_DIR.parents[1])
    sep_dir = Path(sep_cache_dir) if sep_cache_dir is not None else root / "data" / "research_cache" / "sharadar_sep"
    security_master_path = pit_dir / "security_master.csv"
    membership_path = _membership_path(pit_dir, membership_family)

    master_rows = _read_csv(security_master_path)
    membership_rows = [
        row for row in _read_csv(membership_path)
        if str(row.get("membership_family")) == membership_family
    ]
    master_by_id = {str(row.get("security_id")): row for row in master_rows}
    scale_precision, scale_blockers = _classify_scale_precision(membership_rows)
    membership_certification = _classify_membership_certification(membership_rows)

    frames: list[pd.DataFrame] = []
    source_files: dict[str, dict[str, Any]] = {}
    missing_price_files: list[str] = []
    malformed_price_files: list[str] = []

    for member in sorted(membership_rows, key=lambda row: (str(row.get("security_id")), str(row.get("ticker")))):
        security_id = str(member.get("security_id") or "").strip()
        ticker = str(member.get("ticker") or "").strip().upper()
        if not security_id or not ticker:
            continue
        sep_path = sep_dir / _sep_file_name(ticker)
        if not sep_path.exists():
            missing_price_files.append(ticker)
            continue
        sha = sha256_file(sep_path)
        try:
            piece = pd.read_csv(sep_path)
        except Exception:
            malformed_price_files.append(ticker)
            continue
        if "date" not in piece.columns or "closeadj" not in piece.columns:
            malformed_price_files.append(ticker)
            continue
        piece = piece[["date", "closeadj"]].copy()
        piece["date"] = pd.to_datetime(piece["date"], errors="coerce")
        piece["closeadj"] = pd.to_numeric(piece["closeadj"], errors="coerce")
        piece = piece.dropna(subset=["date", "closeadj"])
        if piece.empty:
            malformed_price_files.append(ticker)
            continue

        membership_start = str(member.get("membership_start_date") or "")[:10]
        membership_end = str(member.get("membership_end_date") or "")[:10]
        start_bound = max([d for d in [membership_start, start_date] if d], default=None)
        end_bound = min([d for d in [membership_end, end_date] if d], default=None)
        if start_bound:
            piece = piece[piece["date"] >= pd.Timestamp(start_bound)]
        if end_bound:
            piece = piece[piece["date"] <= pd.Timestamp(end_bound)]
        if piece.empty:
            source_files[ticker] = {"path": str(sep_path), "sha256": sha, "rows_used": 0}
            continue

        master = master_by_id.get(security_id, {})
        piece["date"] = piece["date"].dt.strftime("%Y-%m-%d")
        piece["security_id"] = security_id
        piece["display_ticker"] = ticker
        piece["source_ticker"] = ticker
        piece["source_file_sha256"] = sha
        piece["price_source"] = PRICE_SOURCE
        piece["membership_family"] = membership_family
        piece["membership_start_date"] = membership_start or None
        piece["membership_end_date"] = membership_end or None
        piece["scale_source"] = member.get("scale_source") or None
        piece["is_delisted_security"] = str(master.get("isdelisted") or "").upper().startswith("Y")
        frames.append(piece)
        source_files[ticker] = {
            "path": str(sep_path),
            "sha256": sha,
            "rows_used": int(len(piece)),
            "security_id": security_id,
        }

    panel_columns = [
        "date",
        "security_id",
        "display_ticker",
        "closeadj",
        "source_ticker",
        "source_file_sha256",
        "price_source",
        "membership_family",
        "membership_start_date",
        "membership_end_date",
        "scale_source",
        "is_delisted_security",
    ]
    panel = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=panel_columns)
    panel = panel[panel_columns].sort_values(["date", "security_id"]).reset_index(drop=True)
    duplicate_key_count = int(panel.duplicated(["date", "security_id"]).sum()) if not panel.empty else 0

    date_start = str(panel["date"].min()) if not panel.empty else None
    date_end = str(panel["date"].max()) if not panel.empty else None
    source_paths = {
        "security_master": str(security_master_path),
        "membership": str(membership_path),
        "sep_cache_dir": str(sep_dir),
    }
    source_hashes = {
        "security_master_sha256": sha256_file(security_master_path),
        "membership_sha256": sha256_file(membership_path),
    }
    lineage_payload = {
        "schema_version": SCHEMA_VERSION,
        "membership_family": membership_family,
        "source_hashes": source_hashes,
        "source_files": source_files,
        "start_date": start_date,
        "end_date": end_date,
    }
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat(),
        "governance_label": "RESEARCH_ONLY",
        "execution_impact": "NON_EXECUTIONAL",
        "produced_by": PRODUCED_BY,
        "replay_id": replay_id,
        "universe_method": "pit_universe",
        "membership_family": membership_family,
        "identity_key": "security_id",
        "ticker_role": "display_only",
        "price_source": PRICE_SOURCE,
        "source_paths": source_paths,
        "source_hashes": source_hashes,
        "source_file_count": len(source_files),
        "row_count": int(len(panel)),
        "security_count": int(panel["security_id"].nunique()) if not panel.empty else 0,
        "date_start": date_start,
        "date_end": date_end,
        "requested_start_date": start_date,
        "requested_end_date": end_date,
        "duplicate_date_security_id_count": duplicate_key_count,
        "missing_price_file_count": len(missing_price_files),
        "missing_price_file_sample": missing_price_files[:25],
        "malformed_price_file_count": len(malformed_price_files),
        "malformed_price_file_sample": malformed_price_files[:25],
        "membership_scale_source_values": sorted({str(row.get("scale_source") or "") for row in membership_rows}),
        "membership_scale_precision": scale_precision,
        "membership_certification_status": membership_certification["status"],
        "membership_certification_methods": membership_certification["methods"],
        "membership_certification_warnings": membership_certification["warnings"],
        "decision_grade_blockers": membership_certification["blockers"] or scale_blockers,
        "lineage_digest": stable_digest(lineage_payload),
    }
    return CanonicalPricePanelResult(panel=panel, manifest=manifest)


def write_panel_artifacts(result: CanonicalPricePanelResult, output_dir: Path | str) -> dict[str, str]:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    panel_path = out / "price_panel.parquet"
    manifest_path = out / "manifest.json"
    result.panel.to_parquet(panel_path, index=False)
    manifest = dict(result.manifest)
    manifest["artifact_paths"] = {"price_panel": str(panel_path), "manifest": str(manifest_path)}
    manifest["artifact_hashes"] = {"price_panel_sha256": sha256_file(panel_path)}
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"price_panel": str(panel_path), "manifest": str(manifest_path)}
