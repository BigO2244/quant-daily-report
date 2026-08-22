# FR-069 Phase B Research-Only Scaffolding

Status: PHASE_B_IMPLEMENTED_RESEARCH_ONLY
Owner: Caerus Research Program
Last Updated: 2026-06-16
Governance Label: RESEARCH_ONLY
Execution Impact: NON_EXECUTIONAL

Phase B converts the Phase A sleeve architecture into machine-readable,
research-only scaffolding. It does not change trading, broker submission,
execution, portfolio construction, allocation, model logic, strategy logic,
cron, live-capital behavior, holdout access, or production registry semantics.

## Orchestrator Role Summary

| Role | Phase B responsibility | Outcome |
|---|---|---|
| Governance auditor | Preserve FR-070 monitoring state, FR-063 deferral, and Orion/Lyra no-retirement boundary. | Phase B remains research-only and non-executional. |
| Architecture/file-structure mapper | Locate the scaffold under research-registry surfaces rather than runtime execution code. | Manifest and validator live under `research_registry/sleeves/`; CLI lives under `scripts/research/`. |
| Manifest designer | Encode current and future sleeves with explicit lifecycle and behavior-change controls. | `research_registry/sleeves/manifest.json` is the canonical Phase B sleeve manifest. |
| MCP read-only tool planner | Expose manifest inventory through existing MCP dispatch without mutation. | `fr069_sleeve_inventory` returns compact metadata and validation state. |
| Test/scaffold planner | Add deterministic tests for manifest validation, semantic failures, and MCP inventory. | Targeted tests verify required sleeves, placeholder controls, duplicate IDs, missing fields, and JSON-RPC access. |
| Evidence-envelope planner | Add a static research-only evidence validator for sleeve promotion packets. | Phase B2 validates metadata completeness, PIT/holdout decision-grade markers, and non-executional impact without running backtests. |
| Final reviewer | Check that the package is additive and does not touch production behavior. | Phase B gates Phase C; it does not authorize production refactors. |

## Research-Only Sleeve Manifest

Canonical manifest:

`research_registry/sleeves/manifest.json`

The manifest includes current sleeves:

- Polaris: historical comparison control / `shadow_observed`
- Orion: PAPER capital sleeve / continuing `shadow_observed` comparison
- Lyra: `current_shadow_challenger` / `shadow_observed`

The manifest includes future placeholders:

- Phoenix: crisis-reversal research placeholder
- Cygnus: earnings-drift / post-earnings-drift research placeholder, with v0 shelved
- Cassiopeia: event-driven research placeholder
- Argo: regime/model-selection meta-model placeholder

Every sleeve sets `behavior_change_allowed=false`. Future sleeves must remain
`research_placeholder` in Phase B and must not be marked active, paper,
promoted, live, or production.

## Artifact-Envelope Validator

Validator module:

`research_registry/sleeves/manifest.py`

CLI:

`scripts/research/validate_sleeve_manifest.py`

The validator checks:

- manifest schema version and FR/phase identity;
- top-level `research_only=true` and `behavior_change_allowed=false`;
- required sleeve fields;
- unique `sleeve_id` values;
- allowed status, lifecycle, sleeve type, and implementation-status values;
- required current sleeves and future placeholders;
- artifact requirement definitions;
- PIT universe-policy requirement;
- no future placeholder is marked active/paper/promoted/live/production;
- no Phase B sleeve allows behavior changes.

The CLI emits deterministic JSON and returns nonzero on validation failure. It
does not generate trades, read broker state, modify registry behavior, or write
artifacts.

Example:

```bash
python3 scripts/research/validate_sleeve_manifest.py --inventory
```

## Read-Only MCP Sleeve Inventory

MCP tool:

`fr069_sleeve_inventory`

Output shape:

- `manifest_path`
- `manifest_schema_version`
- `manifest_version`
- `governance_fr`
- `phase`
- `research_only`
- `behavior_change_allowed`
- `sleeve_count`
- `counts_by_status`
- `counts_by_lifecycle_stage`
- `current_sleeves`
- `future_placeholders`
- `sleeves`
- `validation`

The tool is read-only. It loads static manifest metadata and does not mutate
files, call brokers, generate artifacts, change `config/research/strategy_registry.json`,
or alter production strategy behavior.

## Phase B2 Evidence-Envelope Validator

Validator module:

`research_registry/sleeves/evidence.py`

CLI:

`scripts/research/validate_sleeve_evidence.py`

The validator checks static evidence-envelope metadata only:

- required evidence fields such as `sleeve_id`, `name`, `thesis`, `status`,
  `owner`, `source`, `hypothesis_class`, `data_requirements`, `artifact_paths`,
  `benchmark`, `evaluation_window`, `metrics_required`, `known_bias_risks`,
  `promotion_blockers`, `production_impact`, `decision_state`, and
  `evidence_last_updated`;
- sleeve membership against `research_registry/sleeves/manifest.json`;
- `production_impact` limited to `none` or `research_only`;
- `decision_state` limited to `draft`, `research_ready`,
  `shadow_candidate`, or `blocked`;
- PIT/holdout decision-grade markers: `universe_method=pit_universe`,
  non-empty `universe_snapshot_hash`, `holdout_excluded=true`,
  `governance_label=RESEARCH_ONLY`, and
  `execution_impact=NON_EXECUTIONAL`.

Missing required fields fail validation. Legacy current-universe evidence can
remain readable but is classified as non-decision-grade. Optional PIT/holdout
gaps warn and demote decision-grade status.

Example:

```bash
python3 scripts/research/validate_sleeve_evidence.py --artifact path/to/evidence.json
```

The CLI emits deterministic JSON and returns nonzero only when the envelope is
invalid. It does not run backtests, read broker state, generate production
artifacts, alter allocations, or change production strategy behavior.

## Polaris Parity Harness Scaffold

Plan:

`docs/governance/fr_active/fr_069_polaris_parity_harness_plan.md`

Phase B defines the future parity invariant but does not implement production
migration. Any Phase C/D harness must prove that a generalized sleeve interface
reproduces current Polaris artifacts within documented tolerance before Orion,
Lyra, or future sleeves migrate onto it.

## Orion/Lyra PIT Evidence Packet Plan

Plan:

`docs/governance/fr_active/fr_069_orion_lyra_pit_evidence_plan.md`

Phase B preserves the current decision: Orion and Lyra continue evaluation. No
retirement, promotion, rename, or Lyra-name reuse is approved. Future evidence
must use PIT universe membership and include correlation, return/risk/drawdown,
turnover/concentration, regime decomposition, and explicit owner decision gates.

## Future Sleeve Onboarding Specs

Future sleeves are represented in the manifest with non-production onboarding
requirements:

| Sleeve | Theme | Phase B status |
|---|---|---|
| Phoenix | Crisis reversal | Research placeholder; needs crisis-window definition, PIT evidence, and drawdown/recovery analysis. |
| Cygnus | Drift / earnings event reaction | Research placeholder; v0 remains shelved until consensus/EPS-surprise dependency is solved. |
| Cassiopeia | Event-driven | Research placeholder; needs event taxonomy and PIT event tape before implementation. |
| Argo | Regime / model selection | Research placeholder meta-model; no production switching or allocation behavior. |

## Phase C Gate

Phase C must not begin until:

- `research_registry/sleeves/manifest.json` validates cleanly;
- `scripts/research/validate_sleeve_manifest.py` passes;
- `scripts/research/validate_sleeve_evidence.py` validates candidate evidence
  envelopes before they are used for promotion/retirement review;
- `fr069_sleeve_inventory` is listed and callable through MCP;
- targeted manifest, evidence, and MCP tests pass;
- no execution, broker, allocation, portfolio construction, strategy, model,
  cron, or live-capital behavior has changed.
- a separate owner-approved Phase C task authorizes the next implementation
  boundary.

Phase C readiness is documented in
`docs/governance/fr_active/fr_069_phase_c_readiness.md`. That packet defines
the lifecycle gate matrix, sleeve gap matrix, minimum evidence envelope,
promotion/redundancy rules, and acceptance criteria for future sleeve
onboarding. It remains research-only and does not authorize production harness
migration, sleeve activation, allocation changes, or execution changes.

FR-070 remains observation-monitoring. It does not block FR-069 unless the next
live run produces a classified failure requiring renewed remediation.
