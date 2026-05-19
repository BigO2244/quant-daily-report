#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.documentation.doc_consistency import build_consistency_report
from core.documentation.doc_change_intelligence import build_change_intelligence
from core.documentation.doc_freshness import build_freshness_report
from core.documentation.doc_inventory import build_inventory
from core.documentation.doc_lineage import build_lineage
from core.documentation.doc_recommendations import build_recommendations


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate Caerus documentation governance consistency.")
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--output-dir", type=Path, default=Path("outputs/documentation_validation"))
    parser.add_argument("--changed-file", action="append", default=None, help="Changed file to analyze for documentation impact. Repeatable.")
    parser.add_argument("--strict", action="store_true", help="Exit non-zero on FAIL findings.")
    return parser.parse_args()


def _write_json(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _write_text(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")
    return path


def build_payload(repo_root: Path, *, changed_files: list[str] | None = None) -> dict:
    if changed_files is None:
        changed_files = _detect_changed_files(repo_root)
    inventory = build_inventory(repo_root)
    lineage = build_lineage(repo_root, inventory)
    freshness = build_freshness_report(inventory)
    consistency = build_consistency_report(repo_root, inventory, lineage)
    change_intelligence = build_change_intelligence(
        repo_root,
        inventory,
        lineage,
        freshness,
        changed_files=changed_files,
    )
    recommendations = build_recommendations(
        inventory=inventory,
        lineage=lineage,
        freshness=freshness,
        consistency=consistency,
    )
    status = "FAIL" if consistency["fail_count"] else "WARN" if consistency["warn_count"] or freshness["missing_metadata_count"] else "PASS"
    return {
        "status": status,
        "inventory": inventory,
        "lineage": lineage,
        "freshness": freshness,
        "consistency": consistency,
        "change_intelligence": change_intelligence,
        "recommendations_markdown": recommendations,
    }


def write_reports(payload: dict, output_dir: Path) -> list[Path]:
    paths = [
        _write_json(output_dir / "documentation_validation_report.json", payload),
        _write_json(
            output_dir / "documentation_inventory.json",
            payload["inventory"],
        ),
        _write_json(
            output_dir / "documentation_lineage.json",
            payload["lineage"],
        ),
        _write_json(
            output_dir / "stale_docs_report.json",
            payload["freshness"],
        ),
        _write_json(
            output_dir / "orphaned_docs_report.json",
            {
                "orphan_docs": payload["lineage"]["orphan_docs"],
                "missing_references": payload["lineage"]["missing_references"],
            },
        ),
        _write_text(
            output_dir / "canonicalization_summary.md",
            _canonicalization_summary(payload),
        ),
        _write_text(
            output_dir / "governance_consistency_report.md",
            _governance_consistency_markdown(payload),
        ),
        _write_text(
            output_dir / "documentation_drift_report.md",
            payload["recommendations_markdown"],
        ),
        _write_text(
            output_dir / "documentation_recommendations.md",
            payload["recommendations_markdown"],
        ),
        _write_json(
            output_dir / "impacted_docs_report.json",
            {
                "changed_files": payload["change_intelligence"]["changed_files"],
                "impacted_domains": payload["change_intelligence"]["impacted_domains"],
                "impacted_docs": payload["change_intelligence"]["impacted_docs"],
                "likely_stale_canonical_docs": payload["change_intelligence"]["likely_stale_canonical_docs"],
                "runtime_semantic_drift": payload["change_intelligence"]["runtime_semantic_drift"],
                "architecture_drift": payload["change_intelligence"]["architecture_drift"],
                "mutation_policy": payload["change_intelligence"]["mutation_policy"],
            },
        ),
        _write_text(
            output_dir / "proposed_doc_updates.md",
            payload["change_intelligence"]["proposed_updates_markdown"],
        ),
        _write_json(
            output_dir / "documentation_review_queue.json",
            {"review_queue": payload["change_intelligence"]["review_queue"]},
        ),
        _write_json(
            output_dir / "documentation_debt_report.json",
            payload["change_intelligence"]["documentation_debt"],
        ),
    ]
    return paths


def _canonicalization_summary(payload: dict) -> str:
    hierarchy = payload["consistency"]["canonical_hierarchy"]
    lines = ["# Canonicalization Summary", ""]
    for path, purpose in hierarchy.items():
        lines.append(f"- `{path}`: {purpose}")
    lines.extend(
        [
            "",
            f"- Missing references: {len(payload['lineage']['missing_references'])}",
            f"- Orphan docs: {len(payload['lineage']['orphan_docs'])}",
            f"- Missing metadata: {payload['freshness']['missing_metadata_count']}",
        ]
    )
    return "\n".join(lines)


def _governance_consistency_markdown(payload: dict) -> str:
    lines = [
        "# Governance Consistency Report",
        "",
        f"- Status: `{payload['status']}`",
        f"- Failures: {payload['consistency']['fail_count']}",
        f"- Warnings: {payload['consistency']['warn_count']}",
        "",
        "## Findings",
    ]
    findings = payload["consistency"]["findings"]
    if not findings:
        lines.append("- No consistency findings.")
    for finding in findings:
        lines.append(f"- `{finding['severity']}` `{finding['code']}`: {finding['detail']}")
    return "\n".join(lines)


def _detect_changed_files(repo_root: Path) -> list[str]:
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repo_root,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception:
        return []
    if result.returncode != 0:
        return []
    changed: list[str] = []
    for raw_line in result.stdout.splitlines():
        if not raw_line:
            continue
        path = raw_line[3:].strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1].strip()
        if path:
            changed.append(path)
    return sorted(set(changed))


def main() -> int:
    args = parse_args()
    repo_root = args.repo_root.resolve()
    output_dir = args.output_dir
    if not output_dir.is_absolute():
        output_dir = repo_root / output_dir
    payload = build_payload(repo_root, changed_files=args.changed_file)
    paths = write_reports(payload, output_dir)
    print(json.dumps({"status": payload["status"], "paths": [str(path) for path in paths]}, indent=2))
    if args.strict and payload["status"] == "FAIL":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
