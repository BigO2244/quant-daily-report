"""Read-only VM shadow deployment readiness checks."""

from __future__ import annotations

import importlib.util
import os
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class RuntimeReadinessReport:
    status: str
    checks: dict[str, str]
    findings: list[str] = field(default_factory=list)


class RuntimeReadinessCheck:
    def run(self, *, registry_db_path: str | Path | None = None, require_read_only: bool = False) -> RuntimeReadinessReport:
        checks: dict[str, str] = {}
        findings: list[str] = []

        checks["networkx_dependency"] = "PASS" if importlib.util.find_spec("networkx") else "FAIL"
        checks["sqlite_available"] = "PASS" if sqlite3.sqlite_version else "FAIL"
        checks["broker_env_absent"] = "PASS" if not self._broker_env_present() else "FAIL"
        if checks["broker_env_absent"] == "FAIL":
            findings.append("BROKER_ENV_PRESENT")

        if registry_db_path is not None:
            path = Path(registry_db_path)
            checks["registry_db_exists"] = "PASS" if path.exists() else "FAIL"
            if require_read_only and path.exists():
                checks["registry_db_read_only"] = "PASS" if not os.access(path, os.W_OK) else "FAIL"
                if checks["registry_db_read_only"] == "FAIL":
                    findings.append("REGISTRY_DB_WRITABLE")
        else:
            checks["registry_db_exists"] = "NOT_REQUIRED"

        status = "PASS" if all(value in {"PASS", "NOT_REQUIRED"} for value in checks.values()) else "FAIL"
        return RuntimeReadinessReport(status=status, checks=checks, findings=findings)

    def _broker_env_present(self) -> bool:
        broker_keys = {
            "ALPACA_API_KEY",
            "ALPACA_SECRET_KEY",
            "ALPACA_API_SECRET",
            "ALPACA_BASE_URL",
        }
        return any(os.environ.get(key) for key in broker_keys)
