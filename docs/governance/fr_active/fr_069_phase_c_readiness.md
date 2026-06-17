# FR-069 Phase C Readiness Packet

Status: READY_FOR_OWNER_REVIEW
Owner: Caerus Research Program
Last Updated: 2026-06-17
Governance Label: RESEARCH_ONLY
Execution Impact: NON_EXECUTIONAL

This packet defines what Phase C means before any modular sleeve implementation
begins. It is readiness documentation only. It does not activate sleeves, change
strategy selection, change allocation, alter risk controls, submit broker
orders, install cron, or migrate production registry behavior.

## Phase C Definition

Phase C is the governed onboarding protocol for research-lab sleeves. It turns
the Phase B manifest and evidence-envelope validators into a repeatable review
process for future sleeves without changing live or paper trading behavior.

In current repo terms, Phase C means:

- every sleeve has a manifest entry in
  `research_registry/sleeves/manifest.json`;
- every candidate evidence packet validates through
  `scripts/research/validate_sleeve_evidence.py`;
- every sleeve state transition is documented before implementation;
- shadow, paper, pilot-capital, production, and retirement gates are explicit;
- correlated sleeves such as Orion and Lyra require redundancy evidence before
  disposition;
- future sleeves such as Phoenix, Cygnus, Cassiopeia, and Argo remain
  research-only until an owner-approved task changes their lifecycle state.

Phase C does not authorize a generalized production harness. Any code path that
changes paper/live holdings, order generation, target weights, sleeve
allocation, execution, broker interaction, or cron scheduling requires a later
approval.

## Existing Phase B Assets

| Asset | Path | Current state |
|---|---|---|
| Sleeve manifest | `research_registry/sleeves/manifest.json` | Contains Polaris, Orion, Lyra, Phoenix, Cygnus, Cassiopeia, and Argo. |
| Manifest validator | `research_registry/sleeves/manifest.py` | Read-only static validation. |
| Manifest CLI | `scripts/research/validate_sleeve_manifest.py` | Deterministic JSON output; nonzero on invalid manifest. |
| Evidence validator | `research_registry/sleeves/evidence.py` | Validates static evidence envelope and PIT/holdout decision-grade markers. |
| Evidence CLI | `scripts/research/validate_sleeve_evidence.py` | Deterministic JSON output; no backtests or broker calls. |
| MCP inventory | `fr069_sleeve_inventory` | Read-only manifest inventory surface. |
| Polaris parity plan | `docs/governance/fr_active/fr_069_polaris_parity_harness_plan.md` | Future harness parity requirements. |
| Orion/Lyra PIT plan | `docs/governance/fr_active/fr_069_orion_lyra_pit_evidence_plan.md` | Differentiation evidence before disposition. |
| Orion/Lyra redundancy packet | `docs/governance/fr_active/fr_069_orion_lyra_redundancy_packet.md` | Research-only current disposition packet; no promotion or retirement action. |
| Phoenix onboarding packet | `docs/governance/fr_active/fr_069_phoenix_onboarding_packet.md` | First Phase C research-stage onboarding packet; no Shadow or runtime activation. |
| Cassiopeia onboarding packet | `docs/governance/fr_active/fr_069_cassiopeia_onboarding_packet.md` | Research-stage event-driven onboarding packet; no Shadow or runtime activation. |
| Cygnus onboarding packet | `docs/governance/fr_active/fr_069_cygnus_onboarding_packet.md` | Research-stage earnings-drift onboarding packet; v0 remains shelved and no Shadow or runtime activation is authorized. |
| Argo onboarding packet | `docs/governance/fr_active/fr_069_argo_onboarding_packet.md` | Research-stage regime/model-selection onboarding packet; no allocation switching or runtime activation. |

## Lifecycle Gate Matrix

| Lifecycle | Required manifest state | Required evidence | Allowed behavior | Blockers |
|---|---|---|---|---|
| Research | `research_placeholder`, `spec_only`, or equivalent research state | Valid evidence envelope; PIT/holdout gaps may warn | Research artifacts and docs only | Missing manifest row, unknown sleeve id, production impact not `none`/`research_only`. |
| Shadow | Owner-approved transition plus validated decision-grade evidence | PIT universe, universe snapshot hash, holdout excluded, benchmark, metrics, reason codes | Non-blocking shadow artifacts only | Missing parity/differentiation evidence, short observation window, unresolved bias risks. |
| Paper | Separate owner-approved FR after shadow evidence | Paper-readiness packet, execution-risk review, target-attainment expectations | Paper target generation only after explicit approval | Any unresolved execution, allocation, or risk-control ambiguity. |
| Pilot Capital | Separate owner approval and production risk review | Live-readiness packet, capital cap, rollback plan, broker safety review | Limited live capital only under documented cap | Missing broker/reconciliation controls or unclear rollback path. |
| Production | Separate owner approval and promotion record | Full production promotion packet, monitoring plan, runbook, incident response | Approved production allocation | Missing long-window evidence, unstable correlations, or unclassified operational risk. |
| Retired | Owner-approved retirement or shelved verdict | Retirement evidence, lineage note, artifact-retention plan | No new allocations; historical artifacts retained | Ambiguous evidence, name reuse without approval, missing lineage. |

## Sleeve Gap Matrix

| Sleeve | Current role | Present artifacts | Phase C gap | Minimum next action |
|---|---|---|---|---|
| Polaris | Paper baseline/reference sleeve | Manifest row, parity plan, existing production behavior | Generalized harness parity is not implemented. | Keep production behavior unchanged; define fixture parity before any harness migration. |
| Orion | Shadow challenger | Manifest row, Orion/Lyra PIT plan, redundancy packet, shadow artifacts | PIT rebaseline and decision-grade disposition evidence incomplete. | Continue observing; no retirement decision. |
| Lyra | Shadow challenger | Manifest row, Orion/Lyra PIT plan, redundancy packet, shadow artifacts | PIT rebaseline and decision-grade disposition evidence incomplete. | Continue observing as current low-confidence watch-list leader; no promotion/retirement decision. |
| Phoenix | Research-stage crisis-reversal sleeve candidate | Manifest placeholder, archived spec, onboarding packet, evidence-envelope template, existing research artifacts | Decision-grade crisis-window evidence and Shadow-readiness packet missing. | Continue Research-stage evidence collection; no Shadow activation. |
| Cygnus | Research-stage earnings-drift sleeve; v0 shelved | Manifest placeholder, archived spec, onboarding packet, evidence-envelope template, v0 shelved evidence | v1 consensus/EPS-surprise data dependency unresolved. | Keep shelved; approve vendor/data requirements before new evidence. |
| Cassiopeia | Research-stage event-driven sleeve candidate | Manifest placeholder, archived spec, onboarding packet, evidence-envelope template | Event taxonomy and PIT event tape missing. | Define event contract and required PIT event tape. |
| Argo | Research-stage regime/meta-model overlay | Manifest placeholder, archived spec, onboarding packet, evidence-envelope template, Phase B validation work | Member-sleeve inputs and no-live-switching attestation missing. | Keep overlay research-only; consume only frozen sleeve evidence. |

## Minimum Evidence Envelope

Every Phase C candidate packet must validate with:

- `schema_version=caerus_sleeve_evidence_v1`;
- manifest membership by `sleeve_id`;
- `production_impact` of `none` or `research_only`;
- `execution_impact=NON_EXECUTIONAL`;
- `governance_label=RESEARCH_ONLY`;
- explicit `evaluation_window`;
- non-empty data requirements, artifact paths, metrics, known bias risks, and
  promotion blockers;
- PIT universe method, universe snapshot hash, and explicit
  `holdout_excluded=true` for decision-grade evidence.

Legacy current-universe evidence may remain readable for lineage, but it is
non-decision-grade and must not drive promotion, retirement, or allocation.

## Promotion and Redundancy Rules

- Shadow promotion requires owner approval and decision-grade PIT evidence.
- Paper promotion requires a separate execution-risk review and must not be
  bundled into research scaffolding.
- Orion/Lyra redundancy must be decided from canonical new-series and PIT-safe
  evidence, including correlation, active share, return/risk, drawdown,
  turnover, concentration, and regime decomposition.
- A sleeve may be retired only with explicit owner approval and a lineage note.
- Strategy names are not reusable until retirement and reuse are both recorded.

## Acceptance Criteria

Phase C readiness is satisfied when:

1. `scripts/research/validate_sleeve_manifest.py --inventory` passes.
2. Candidate evidence envelopes pass
   `scripts/research/validate_sleeve_evidence.py --artifact <path>`.
3. `Tests/test_sleeve_manifest.py` and `Tests/test_sleeve_evidence.py` pass.
4. Future sleeve docs identify data requirements, bias risks, blockers,
   benchmark, evaluation window, and non-goals.
5. VM validation can run non-interactively through
   `ssh caerus-vm 'cd ~/quant-daily-report && ./scripts/ops/run_vm_validation.sh'`.
6. No execution, broker, allocation, sizing, ranking, risk threshold, strategy
   selection, target-generation, cron, or live-capital behavior changes.

## Non-Goals

- No new active sleeve.
- No production harness migration.
- No Polaris behavior changes.
- No Orion/Lyra promotion, retirement, rename, or allocation change.
- No Phoenix, Cygnus, Cassiopeia, or Argo activation.
- No broker calls, execution changes, cron install, or risk-threshold changes.
