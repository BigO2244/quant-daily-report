from __future__ import annotations

from typing import Any


def build_recommendations(
    *,
    inventory: dict[str, Any],
    lineage: dict[str, Any],
    freshness: dict[str, Any],
    consistency: dict[str, Any],
) -> str:
    lines = [
        "# Documentation Recommendations",
        "",
        "## Summary",
        f"- Documents inventoried: {inventory.get('doc_count', 0)}",
        f"- Missing references: {len(lineage.get('missing_references') or [])}",
        f"- Orphan docs: {len(lineage.get('orphan_docs') or [])}",
        f"- Missing metadata: {freshness.get('missing_metadata_count', 0)}",
        f"- Stale docs: {freshness.get('stale_count', 0)}",
        f"- Consistency warnings: {consistency.get('warn_count', 0)}",
        "",
        "## Recommended Next Steps",
        "1. Add lightweight metadata to canonical docs first, not every historical note.",
        "2. Promote `docs/governance/governance_taxonomy.md` as the category source of truth.",
        "3. Keep AGENTS.md concise and reference canonical docs instead of duplicating deep architecture.",
        "4. Review orphan docs before moving or deleting anything; preserve history.",
        "5. Treat missing references as documentation debt, not runtime failures.",
        "",
    ]
    if lineage.get("orphan_docs"):
        lines.append("## Orphan Candidates")
        for path in lineage["orphan_docs"][:25]:
            lines.append(f"- `{path}`")
        lines.append("")
    if freshness.get("missing_metadata"):
        lines.append("## Metadata Candidates")
        for path in freshness["missing_metadata"][:25]:
            lines.append(f"- `{path}`")
        lines.append("")
    return "\n".join(lines)

