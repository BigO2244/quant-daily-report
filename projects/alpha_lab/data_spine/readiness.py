"""Consolidated readiness evidence for the shared research data spine."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from projects.alpha_lab.factory import canonical_json

from .boundary import build_boundary_attestation
from .registry import SourceRegistry
from .storage import latest_manifest, write_bundle


def _manifest(repo_root: Path, source_id: str) -> Dict[str, Any] | None:
    path = latest_manifest(repo_root, source_id)
    if path is None:
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else None


def _manifest_with_forms(
    repo_root: Path,
    source_id: str,
    required_forms: set[str],
) -> Dict[str, Any] | None:
    root = repo_root / "outputs/research/alpha_lab/data_spine" / source_id
    for path in sorted(root.glob("*/manifest.json"), reverse=True):
        value = json.loads(path.read_text(encoding="utf-8"))
        forms = {
            str(item).upper()
            for item in (value.get("metadata") or {}).get("forms", [])
        }
        if forms.intersection(required_forms):
            return value
    return None


def build_readiness(
    *,
    repo_root: Path,
    registry: SourceRegistry,
    checked_at: datetime | None = None,
) -> Dict[str, Any]:
    timestamp = checked_at or datetime.now(timezone.utc)
    source_ids = (
        "sharadar_access",
        "sharadar_tickers",
        "sharadar_sep",
        "sharadar_sep_stream",
        "sharadar_actions",
        "sharadar_sf1",
        "sharadar_sf1_bulk",
        "sharadar_daily",
        "sec_reference",
        "sec_event_index",
        "sec_companyfacts",
        "sec_insider_quarterly",
        "sec_original_filings",
        "sec_original_filings_stream",
        "sec_submissions",
        "factor_library",
        "fred_alfred",
        "eia_access",
        "eia_bulk",
        "eia_electricity_bulk",
        "bea_input_output_reference",
        "bea_input_output_api",
        "alpha_vantage_free_proxy",
        "yfinance_analyst_proxy",
        "usaspending_government_customer_proxy",
        "occ_reference",
        "occ_manual_intake",
        "vendor_analyst_estimates_gate",
        "vendor_supply_chain_gate",
    )
    sources = {}
    for source_id in source_ids:
        manifest = _manifest(repo_root, source_id)
        status = "CAPTURED" if manifest else "NOT_CAPTURED"
        if (
            source_id == "alpha_vantage_free_proxy"
            and manifest
            and (manifest.get("metadata") or {}).get("demo_key_used") is True
        ):
            status = "DEMO_ONLY"
        sources[source_id] = {
            "status": status,
            "bundle_id": manifest.get("bundle_id") if manifest else None,
            "retrieved_at": manifest.get("retrieved_at") if manifest else None,
        }
    for virtual_id, required_forms in (
        ("sec_original_form4_stream", {"4", "4/A"}),
        ("sec_original_8k_stream", {"8-K", "8-K/A"}),
    ):
        manifest = _manifest_with_forms(
            repo_root,
            "sec_original_filings_stream",
            required_forms,
        )
        sources[virtual_id] = {
            "status": "CAPTURED" if manifest else "NOT_CAPTURED",
            "bundle_id": manifest.get("bundle_id") if manifest else None,
            "retrieved_at": manifest.get("retrieved_at") if manifest else None,
            "forms": (manifest.get("metadata") or {}).get("forms", []) if manifest else [],
            "candidate_count": (
                (manifest.get("metadata") or {}).get("candidate_count") if manifest else None
            ),
        }
    derived_form4 = _manifest(repo_root, "form4_original_event_tape")
    sources["form4_original_event_tape"] = {
        "status": "CAPTURED" if derived_form4 else "NOT_CAPTURED",
        "bundle_id": derived_form4.get("bundle_id") if derived_form4 else None,
        "retrieved_at": derived_form4.get("retrieved_at") if derived_form4 else None,
        "coverage_scope": (
            (derived_form4.get("metadata") or {}).get("coverage_scope")
            if derived_form4
            else None
        ),
        "quality_status": (
            (derived_form4.get("metadata") or {}).get("quality_status")
            if derived_form4
            else None
        ),
    }
    sharadar_key = bool(
        os.environ.get(str(registry.sources["sharadar"]["api_key_env"]))
    )
    sec_agent = os.environ.get(str(registry.sources["sec"]["user_agent_env"]))
    eia_key = bool(os.environ.get(str(registry.sources["eia"]["api_key_env"])))
    bea_key = bool(os.environ.get(str(registry.sources["bea"]["api_key_env"])))
    alpha_vantage_key = bool(
        os.environ.get(str(registry.sources["alpha_vantage"]["api_key_env"]))
    )
    boundary = build_boundary_attestation()
    blockers = []
    if not sharadar_key:
        blockers.append("sharadar_key_not_exposed_to_project")
    if not sec_agent or "@" not in sec_agent:
        blockers.append("sec_contact_user_agent_not_configured")
    if sources["factor_library"]["status"] != "CAPTURED":
        blockers.append("factor_library_not_captured")
    if sources["fred_alfred"]["status"] != "CAPTURED":
        blockers.append("fred_alfred_not_captured")
    if sources["sec_companyfacts"]["status"] != "CAPTURED":
        blockers.append("sec_companyfacts_not_captured")
    if sources["sec_insider_quarterly"]["status"] != "CAPTURED":
        blockers.append("sec_insider_quarterly_not_captured")
    if boundary["production_boundary_status"] != "CLEAN":
        blockers.append("production_boundary_violation")
    payload = {
        "schema_version": "caerus_alpha_lab_data_spine_readiness_v1",
        "classification": "RESEARCH_ONLY_NON_EXECUTIONAL",
        "checked_at": timestamp.isoformat(),
        "credentials": {
            "sharadar_key_present": sharadar_key,
            "sec_user_agent_present": bool(sec_agent and "@" in sec_agent),
            "fred_key_present": bool(
                os.environ.get(str(registry.sources["fred"]["api_key_env"]))
            ),
            "eia_key_present": eia_key,
            "bea_key_present": bea_key,
            "alpha_vantage_key_present": alpha_vantage_key,
            "credential_values_persisted": False,
        },
        "sources": sources,
        "boundary": boundary,
        "blockers": blockers,
        "overall_status": "READY_FOR_DATA_GATES" if not blockers else "PARTIAL",
        "sharadar_access_decision": (
            "CAPTURE_COMPLETE_REASSESS_RENEWAL"
            if all(
                sources[source_id]["status"] == "CAPTURED"
                for source_id in (
                    "sharadar_tickers",
                    "sharadar_actions",
                    "sharadar_sep_stream",
                )
            )
            else (
                "AUDIT_AND_CAPTURE_BEFORE_EXPIRY"
                if sharadar_key
                else "KEY_REQUIRED_TO_AUDIT_OR_CAPTURE"
            )
        ),
        "eia_access_decision": (
            "KEYED_API_AVAILABLE"
            if eia_key
            else "BULK_ONLY_AVAILABLE_WITHOUT_KEY"
        ),
        "alpha_claim_permitted": False,
        "trading_behavior_changed": False,
    }
    bundle = write_bundle(
        repo_root=repo_root,
        source_id="readiness",
        files={"readiness.json": (canonical_json(payload) + "\n").encode("utf-8")},
        metadata={"config_hash": registry.config_hash},
        retrieved_at=timestamp,
    )
    return {"readiness": payload, **bundle}
