from __future__ import annotations

import shutil
from pathlib import Path


INCIDENT_PACKAGE_FILES = (
    "execution_timeline.json",
    "recovery_risk_report.json",
    "recovery_governance_report.json",
    "portfolio_drift.json",
    "eventual_settlement.json",
    "lifecycle_summary.md",
    "recovery_decision_trace.md",
    "recovery_lineage.json",
    "lifecycle_graph.json",
    "recovery_certification_summary.json",
)


def build_incident_package(*, output_dir: Path) -> dict[str, object]:
    package_dir = output_dir / "incident_package"
    package_dir.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    missing: list[str] = []
    for name in INCIDENT_PACKAGE_FILES:
        src = output_dir / name
        if not src.exists():
            missing.append(name)
            continue
        dst = package_dir / name
        shutil.copyfile(src, dst)
        copied.append(name)
    manifest = {
        "package_type": "RECOVERY_INCIDENT_PACKAGE",
        "source_dir": str(output_dir),
        "package_dir": str(package_dir),
        "copied": copied,
        "missing": missing,
        "complete": not missing,
    }
    (package_dir / "manifest.json").write_text(
        __import__("json").dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest

