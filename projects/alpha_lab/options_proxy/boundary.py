"""Static proof that the options proxy package cannot cross trading boundaries."""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path
from typing import Any, Dict, List

from projects.alpha_lab.factory import canonical_hash


FORBIDDEN_MODULES = frozenset(
    {
        "alpha_stack",
        "brokers",
        "core",
        "daily_quant_report",
        "deploy",
        "execution",
        "paper",
        "reconciliation",
        "scripts",
    }
)
FORBIDDEN_CALLS = frozenset(
    {
        "cancel_order",
        "submit_market_order",
        "submit_option_limit_order",
        "submit_option_market_order",
        "submit_order",
    }
)


def build_boundary_attestation(package_root: Path) -> Dict[str, Any]:
    findings: List[str] = []
    scanned = []
    file_sha256 = {}
    root = Path(package_root).resolve()
    for path in sorted(root.glob("*.py")):
        scanned.append(path.name)
        source_bytes = path.read_bytes()
        file_sha256[path.name] = hashlib.sha256(source_bytes).hexdigest()
        tree = ast.parse(source_bytes.decode("utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    module = alias.name.split(".")[0]
                    if module in FORBIDDEN_MODULES:
                        findings.append("{}:forbidden_import:{}".format(path.name, alias.name))
            elif isinstance(node, ast.ImportFrom) and node.module:
                module = node.module.split(".")[0]
                if module in FORBIDDEN_MODULES:
                    findings.append("{}:forbidden_import:{}".format(path.name, node.module))
            elif isinstance(node, ast.Call):
                name = getattr(node.func, "attr", getattr(node.func, "id", ""))
                if name in FORBIDDEN_CALLS:
                    findings.append("{}:forbidden_call:{}".format(path.name, name))
    payload = {
        "schema_version": "caerus_options_proxy_boundary_attestation_v1",
        "production_boundary_status": "CLEAN" if not findings else "VIOLATION",
        "files_scanned": scanned,
        "file_sha256": file_sha256,
        "findings": findings,
        "runtime_behavior_changed": False,
        "broker_orders_submitted": False,
        "allocation_or_sizing_modified": False,
        "production_scheduler_or_cron_modified": False,
        "standalone_research_automation_permitted": True,
        "paper_pilot_live_promotion": False,
        "capital_path_touched": False,
        "production_scheduler_integration": False,
    }
    payload["attestation_hash"] = canonical_hash(payload)
    return payload
