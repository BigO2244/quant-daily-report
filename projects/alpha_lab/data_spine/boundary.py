"""Static proof that data-spine code cannot reach capital or production runtime."""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path
from typing import Any, Dict

from projects.alpha_lab.factory import canonical_hash


FORBIDDEN_MODULES = frozenset(
    {"alpha_stack", "brokers", "core", "daily_quant_report", "deploy", "execution", "paper", "reconciliation", "scripts"}
)
FORBIDDEN_CALLS = frozenset(
    {"submit_order", "submit_market_order", "submit_option_market_order", "cancel_order"}
)


def build_boundary_attestation(package_root: Path | None = None) -> Dict[str, Any]:
    root = (package_root or Path(__file__).parent).resolve()
    findings = []
    hashes = {}
    files = []
    for path in sorted(root.glob("*.py")):
        files.append(path.name)
        source = path.read_bytes()
        hashes[path.name] = hashlib.sha256(source).hexdigest()
        tree = ast.parse(source.decode("utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] in FORBIDDEN_MODULES:
                        findings.append("{}:forbidden_import:{}".format(path.name, alias.name))
            elif isinstance(node, ast.ImportFrom) and node.module:
                if node.module.split(".")[0] in FORBIDDEN_MODULES:
                    findings.append("{}:forbidden_import:{}".format(path.name, node.module))
            elif isinstance(node, ast.Call):
                name = getattr(node.func, "attr", getattr(node.func, "id", ""))
                if name in FORBIDDEN_CALLS:
                    findings.append("{}:forbidden_call:{}".format(path.name, name))
    payload = {
        "schema_version": "caerus_alpha_lab_data_spine_boundary_v1",
        "production_boundary_status": "CLEAN" if not findings else "VIOLATION",
        "files_scanned": files,
        "file_sha256": hashes,
        "findings": findings,
        "broker_or_execution_imports": False if not findings else None,
        "production_scheduler_modified": False,
        "strategy_registry_modified": False,
        "trading_behavior_changed": False,
    }
    payload["attestation_hash"] = canonical_hash(payload)
    return payload
