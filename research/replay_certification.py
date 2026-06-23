"""Certification checks for canonical PIT replay artifacts.

This module is research-only. It validates artifact lineage and keying; it does
not call production allocation, execution, broker, scheduler, paper, or live
trading paths.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from research.canonical_replay_panel import stable_digest

SCHEMA_VERSION = "caerus_replay_certification_v1"
PROHIBITED_INPUT_FRAGMENTS = (
    "data/universe.csv",
    "outputs/research/flow_detection_v1/price_panel.parquet",
)
REQUIRED_PANEL_COLUMNS = {
    "date",
    "security_id",
    "display_ticker",
    "closeadj",
    "source_ticker",
    "source_file_sha256",
    "price_source",
    "membership_family",
}


@dataclass(frozen=True)
class ReplayCertificationResult:
    status: str
    decision_grade_status: str
    findings: list[str]
    warnings: list[str]
    digest: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": self.status,
            "decision_grade_status": self.decision_grade_status,
            "findings": self.findings,
            "warnings": self.warnings,
            "digest": self.digest,
        }


def _manifest_paths(manifest: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    for key in ("source_paths", "artifact_paths", "input_paths"):
        value = manifest.get(key)
        if isinstance(value, dict):
            paths.extend(str(v) for v in value.values())
        elif isinstance(value, list):
            paths.extend(str(v) for v in value)
    return paths


def _has_prohibited_path(paths: list[str]) -> bool:
    normalized = [path.replace("\\", "/") for path in paths]
    return any(fragment in path for path in normalized for fragment in PROHIBITED_INPUT_FRAGMENTS)


def certify_security_id_price_panel(
    panel: pd.DataFrame,
    manifest: dict[str, Any],
    *,
    require_decision_grade_scale: bool = False,
    require_decision_grade_membership: bool = False,
    require_decision_tape: bool = False,
    decision_tape: pd.DataFrame | None = None,
) -> ReplayCertificationResult:
    findings: list[str] = []
    warnings: list[str] = []

    missing_columns = sorted(REQUIRED_PANEL_COLUMNS - set(panel.columns))
    if missing_columns:
        findings.append(f"MISSING_PANEL_COLUMNS:{','.join(missing_columns)}")
    if "ticker" in panel.columns and "security_id" not in panel.columns:
        findings.append("TICKER_KEYED_PANEL")
    if "security_id" in panel.columns:
        if panel["security_id"].isna().any() or (panel["security_id"].astype(str).str.strip() == "").any():
            findings.append("EMPTY_SECURITY_ID")
    if {"date", "security_id"}.issubset(panel.columns):
        duplicate_count = int(panel.duplicated(["date", "security_id"]).sum())
        if duplicate_count:
            findings.append(f"DUPLICATE_DATE_SECURITY_ID:{duplicate_count}")
    if manifest.get("identity_key") != "security_id":
        findings.append("MANIFEST_IDENTITY_KEY_NOT_SECURITY_ID")
    if manifest.get("ticker_role") != "display_only":
        findings.append("TICKER_ROLE_NOT_DISPLAY_ONLY")
    if manifest.get("universe_method") != "pit_universe":
        findings.append("UNIVERSE_METHOD_NOT_PIT")
    if manifest.get("price_source") != "sharadar_sep_closeadj":
        findings.append("PRICE_SOURCE_NOT_SHARADAR_SEP_CLOSEADJ")
    if manifest.get("duplicate_date_security_id_count"):
        findings.append(f"MANIFEST_DUPLICATE_DATE_SECURITY_ID:{manifest.get('duplicate_date_security_id_count')}")
    if not manifest.get("source_hashes"):
        findings.append("MISSING_SOURCE_HASHES")
    if not manifest.get("lineage_digest"):
        findings.append("MISSING_LINEAGE_DIGEST")
    paths = _manifest_paths(manifest)
    if _has_prohibited_path(paths):
        findings.append("PROHIBITED_INPUT_PATH")

    scale_precision = manifest.get("membership_scale_precision")
    membership_status = manifest.get("membership_certification_status")
    if membership_status is None:
        membership_status = "PASS" if scale_precision == "PIT_EXACT_SCALE" else "FAIL"
    scale_blockers = list(manifest.get("decision_grade_blockers") or [])
    membership_warnings = list(manifest.get("membership_certification_warnings") or [])
    if membership_status != "PASS":
        warnings.append(f"MEMBERSHIP_NOT_DECISION_GRADE:{membership_status}")
        warnings.extend(str(warning) for warning in membership_warnings)
        if require_decision_grade_membership or require_decision_grade_scale:
            findings.extend(scale_blockers or ["PIT_DATE_EFFECTIVE_LARGE_CAP_MEMBERSHIP_REQUIRED"])
    if require_decision_tape:
        if decision_tape is None:
            findings.append("MISSING_DECISION_TAPE")
        else:
            required_tape = {"trade_date", "security_id", "ticker", "sleeve", "candidate", "rank", "score", "target_weight"}
            missing_tape_columns = sorted(required_tape - set(decision_tape.columns))
            if missing_tape_columns:
                findings.append(f"MISSING_DECISION_TAPE_COLUMNS:{','.join(missing_tape_columns)}")

    status = "PASS" if not findings else "FAIL"
    decision_grade_status = "PASS" if status == "PASS" and membership_status == "PASS" else "PARTIAL"
    if status == "FAIL":
        decision_grade_status = "FAIL"
    payload = {
        "status": status,
        "decision_grade_status": decision_grade_status,
        "findings": sorted(set(findings)),
        "warnings": sorted(set(warnings)),
        "manifest_digest": stable_digest(manifest),
        "row_count": int(len(panel)),
    }
    return ReplayCertificationResult(
        status=status,
        decision_grade_status=decision_grade_status,
        findings=payload["findings"],
        warnings=payload["warnings"],
        digest=stable_digest(payload),
    )


def certify_panel_artifacts(
    *,
    panel_path: Path | str,
    manifest_path: Path | str,
    require_decision_grade_scale: bool = False,
    require_decision_grade_membership: bool = False,
    output_path: Path | str | None = None,
) -> ReplayCertificationResult:
    panel = pd.read_parquet(panel_path)
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    result = certify_security_id_price_panel(
        panel,
        manifest,
        require_decision_grade_scale=require_decision_grade_scale,
        require_decision_grade_membership=require_decision_grade_membership,
    )
    if output_path is not None:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result
