# docs/governance — Index

Start here before creating or editing any governance/strategy/FR document.

**Canonical source of truth (read first):**

- `CURRENT_RESEARCH_ROADMAP.md` — verified state, canonical FR table, strategy state
  table, open taxonomy conflicts, blockers, and the mandatory instruction for future
  agents. This is the reconciliation/index layer.
- `Strategy_Roadmap_And_Research_Backlog.md` — canonical narrative roadmap (intent).
- `../../config/research/strategy_registry.json` — authoritative machine state for code.

**Canonical per-strategy research specs:**

- `fr_050_phoenix_research_spec.md` — Phoenix (crisis reversal)
- `fr_051_cygnus_research_spec.md` — Cygnus (earnings drift)
- `fr_052_cassiopeia_research_spec.md` — Cassiopeia (event-driven)
- `fr_053_argo_research_spec.md` — Argo (regime allocation overlay)

**Audits:**

- `fr_054_dynamic_strategy_registry_audit.md`
- `fr_055_registry_surface_cleanup_audit.md`

**Superseded / conflicting (NON-canonical — do not treat as roadmap items):**

- `fr_056_cygnus_design_spec.md` — DUPLICATE of FR-051; definition drift (see file header + roadmap §4 Conflict B).
- `fr_057_argo_design_spec.md` — RETIRED (Option A, 2026-06-08); event-driven content belongs to Cassiopeia (FR-052). Argo is the active regime / model-selection layer (FR-053).

**Other governance references:** `fr_registry.md`, `fr_active_backlog.md`,
`caerus_strategic_backlog.md`, `fr_governance_model.md`, `governance_taxonomy.md`,
`operational_lessons.md`, `repo_artifact_policy.md`, `change_lineage_standard.md`.

> Rule: do not create a parallel "design" spec for a strategy that already has a
> canonical FR spec. Extend the canonical spec instead. Never reassign a strategy ID
> or change registry/execution/broker/cron behavior as part of documentation work.
