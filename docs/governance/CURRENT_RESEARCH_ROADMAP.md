# CURRENT RESEARCH ROADMAP — Caerus Source of Truth

Status: Canonical
Owner: Caerus Research Program
Last Updated: 2026-06-23 (Polaris_Alpha and Orion_Alpha promoted to official
SHADOW status for forward concentration evidence collection; Polaris and Orion
baselines are preserved as comparison controls. This is non-capital,
non-executing, and does not alter paper, live, allocator, broker, scheduler, or
production behavior.)
Governance Label: RESEARCH_ONLY
Execution Impact: NON_EXECUTIONAL (this document changes no execution, broker, cron, registry, or paper/live behavior)

The Caerus Investment Doctrine at `docs/governance/caerus_investment_doctrine.md`
is the canonical strategic north star for strategy, sleeve, promotion, and
portfolio-construction work unless explicitly amended.

Priority note 2026-06-17: FR-070 cash-gating / post-sell buy-budget remediation
remains in observation/monitoring. The 2026-06-17 high-cash target was not an
execution defect; the trend sleeve was invalidated for non-finite terminal
equity and the allocation engine correctly routed freed weight to CASH. FR-073
adds diagnostic-only numeric traces at
`outputs/runs/<RUN_ID>/audit/sleeve_numeric_trace_<sleeve_id>_<trade_date>.json`
when a run context is available. FR-069 modular sleeve / research-lab
architecture is still the next major architecture workstream and must align
with the doctrine. The Orion/Lyra retain-or-retire decision is deferred to the
data-driven sleeve architecture review; no immediate retirement or Lyra-name
reuse is approved. Phoenix remains the next major sleeve-design focus after the
architecture is settled. Dashboard auth cleanup remains operational hygiene,
not a research priority.

---

## 0. How to use this document

This is the **reconciliation / index layer** for Caerus research governance. It does
not replace existing docs; it declares which of them is canonical and records the
current verified state. Future agents and contributors **must** read this file plus
the canonical specs it points to **before** creating any new FR, design spec, or
strategy module. Do not create a new spec for a strategy that already has a canonical
spec. Do not invent a new strategy name or reassign an existing one without an
explicit decision recorded here.

Canonical sources, in order of authority:

1. **Machine state (authoritative for code):** `config/research/strategy_registry.json`
2. **Narrative roadmap (authoritative for intent):** `docs/governance/Strategy_Roadmap_And_Research_Backlog.md`
3. **Per-strategy research specs (canonical):** FR-050..FR-053 (see table)
4. **This file:** reconciles 1–3 and records open conflicts.

Where the registry and a spec disagree, that disagreement is an **open conflict**
listed in Section 4 and must be resolved by explicit decision — not by silently
editing code or specs.

---

## 1. Verified state (as of 2026-06-17)

- **Repo:** quant-daily-report / Caerus Quant / Alpha Stack
- **Pre-triage deployed baseline:** `efd193dc3520e7383ced00e6e0bc6e4f0c431e78`
- **Production posture:** paper remains the production posture. A separate,
  manual, tightly capped FR-104 Level 2.5 live-pilot evidence lane may collect
  forward broker/operational evidence only when explicitly approved; it is not
  production, not cron-enabled, not dynamic allocation, and not proof of
  promotion readiness. No shorting, no leverage.
- **Active paper strategy:** Caerus Polaris (`caerus_polaris`), wired to the `growth_engine_v4` baseline engine.
- **Shadow (non-blocking):** Caerus Orion, Caerus Lyra, Polaris_Alpha,
  Orion_Alpha. SPY = benchmark.
- **Concentration alpha shadows:** Polaris_Alpha (`caerus_polaris_alpha`) and
  Orion_Alpha (`caerus_orion_alpha`) are forward shadow-only concentration
  variants activated 2026-06-23. Polaris_Alpha is Top 4 / 20% cap versus the
  preserved Polaris Top 10 baseline. Orion_Alpha is Top 3 / 25% cap versus the
  preserved Orion Top 5 baseline. Review checkpoints are 20 and 60 trading days.
- **Shadow observation methodology:** `dated_same_day_close_to_close_v1`; canonical operational observation window begins `2026-05-12`.
- **Shadow scorecard health:** Fresh; NAV integrity OK as of recovered artifacts through `2026-06-12`.
- **Promotion ladder:** research → backtest → shadow → paper → live. No automated promotion. Promotion is conservative and research-only.
- **Hard rule:** do not change production trading, broker submission, or cron timing casually.
- **FR-073 observability:** invalid sleeve numeric states should emit a
  run-root `sleeve_numeric_trace_*` artifact and cash-routing report line when
  a run context is available; this is diagnostic-only and does not alter
  allocation, execution, or risk controls.

---

## 2. Canonical FR roadmap table

| FR | Strategy | Canonical role (intended) | Canonical spec (authoritative) | Status in spec | Code module? | Registry status |
|----|----------|---------------------------|--------------------------------|----------------|--------------|-----------------|
| FR-050 | Phoenix | Crisis reversal | `fr_archive/fr_050_phoenix_research_spec.md`; Phase C onboarding: `fr_active/fr_069_phoenix_onboarding_packet.md`; evidence template: `fr_active/fr_069_phoenix_evidence_envelope_template.json` | NOT_VIABLE_CURRENT_PHASE_B — Nasdaq Data Link `QELx06` cleared and Sharadar SEP OHLCV cache/panel rebuilt 2026-06-18; Phase C measured 80/80 candidate rows but failed capacity at 5% ADV (`capacity_below_5pct_adv_policy`; min 5% ADV capacity about `$74.6k` at `$1M` reference capital). Phoenix remains Research-stage only and is not Shadow-readiness eligible. | `research_registry/research/phoenix.py` + `phoenix_evidence_tracker.py` + `phoenix_phase_b_review.py`; `scripts/research/hydrate_sharadar_sep.py`; `scripts/research/build_pit_liquidity_panel.py`; `outputs/research/phoenix_evidence/`; `outputs/research/pit_liquidity/` | research / not_viable_current_phase_b |
| FR-051 | Cygnus | Earnings / post-earnings drift | `fr_archive/fr_051_cygnus_research_spec.md`; Phase C onboarding: `fr_active/fr_069_cygnus_onboarding_packet.md` | V0_SHELVED — Phase C research-stage onboarded; v1 vendor-gated; not Shadow-active | `research/cygnus/` research-only modules | research (`earnings_drift`) |
| FR-052 | Cassiopeia | Event-driven (catalysts) | `fr_archive/fr_052_cassiopeia_research_spec.md`; Phase C onboarding: `fr_active/fr_069_cassiopeia_onboarding_packet.md` | ACTIVE_RESEARCH — Phase C research-stage onboarded; spec-only; not Shadow-active | none | research (`event_driven`) |
| FR-053 | Argo | Regime allocation overlay / model-selection layer | `fr_archive/fr_053_argo_research_spec.md`; Phase C onboarding: `fr_active/fr_069_argo_onboarding_packet.md`; Phase A framework: `fr_active/fr_069_argo_phase_a_evidence_framework.md`; Phase B priority framework: `fr_active/fr_069_argo_phase_b_research_priority_framework.md` | ACTIVE_RESEARCH — Phase A evidence-consumer framework and Phase B research-priority framework added; Argo evaluates sleeve evidence and ranks research effort for advisory governance only while remaining non-allocating/non-executing | `research_registry/research/argo.py` + `argo_phase_b_validation.py`; `scripts/research/build_argo_phase_a_evidence_framework.py`; `scripts/research/build_argo_phase_b_research_priority.py`; `outputs/research/argo/argo_phase_a_evidence_framework_2026-06-17.json`; `outputs/research/argo/argo_phase_b_research_priority_2026-06-17.json` | research (`meta_model`, `regime_overlay`, `selector`) |
| FR-054 | — | Dynamic strategy registry audit | `fr_archive/fr_054_dynamic_strategy_registry_audit.md` | Audit | n/a | n/a |
| FR-055 | — | Registry surface cleanup audit | `fr_archive/fr_055_registry_surface_cleanup_audit.md` | Audit | n/a | n/a |
| FR-056 | Cygnus | *(design draft — DUPLICATE of FR-051)* | `fr_archive/fr_056_cygnus_design_spec.md` | RETIRED 2026-06-10 (owner-approved) → FR-051 | Retired | none | n/a |
| FR-057 | Argo | *(design draft — CONFLICTS with FR-053)* | `fr_archive/fr_057_argo_design_spec.md` | superseded/quarantined → FR-053 | Design Only | none | n/a |
| FR-063 | Cross-strategy | Strategy differentiation deep dive | `fr_active/fr_063_orion_lyra_redundancy_study.md` | ACTIVE_RESEARCH — supporting differentiation evidence under FR-069; no Orion/Lyra retirement, promotion, allocation, or Lyra-name reuse decision | `strategy_differentiation_deep_dive.py` plus future research-only redundancy artifacts | n/a |
| FR-064 | Portfolio research | Multi-asset research framework | `fr_archive/fr_064_multi_asset_research_framework.md` | DRAFT_RESEARCH | `multi_asset_research_framework.py` | n/a |
| FR-065 | Dashboard / model-quality evidence | Dashboard decision-grade consolidation | `fr_archive/fr_065_dashboard_decision_grade_consolidation.md` | ACTIVE_RESEARCH | dashboard data model + terminal panel | n/a |
| FR-066 | — (operational) | Canonical NAV track record integrity (daily build, inception backfill, SPY/beta-adjusted scoreboard, fail-loud freshness) | `fr_archive/fr_066_canonical_nav_track_record_spec.md` | DEPLOYED_OBSERVING — VM backfill/write completed; cron installed | `scripts/backfill_portfolio_history.py`, `scripts/build_portfolio_history.py`, `core/portfolio_history_escalation.py` | n/a |
| FR-067 | Vela (proposed) | Small-cap momentum sleeve (capacity-advantaged venue test); Stage 0 PIT source gate | `fr_archive/fr_067_vela_research_spec.md` | STAGE0_CLOSED_PASS 2026-06-10 — Sharadar delisted-price coverage verified (100/100, pct 1.0, median 0.999); approved as PIT price/security source for FR-068. Caveats: no S&P 600 membership (market-cap band); Cygnus v1 consensus still blocked | `scripts/research/verify_sharadar_coverage.py` | not yet registered |
| FR-068 | — (operational) | Point-in-Time universe foundation + Polaris/Orion/Lyra rebaseline (survivorship remediation) | `research/pit_universe_architecture_2026-06-10.md`; Orion/Lyra PIT artifact: `outputs/research/pit_rebaseline/orion_lyra_matched_2026-06-17.json`; packet: `fr_active/fr_068_orion_lyra_pit_rebaseline_packet.md`; resolver certification: `reports/pit_universe_certification.md` | PHASES 1-4 COMPLETE; resolver certification added 2026-06-22 — PIT universe (20,618 secs, 14,790 delisted) + Universe(as_of_date); caerus_large_cap family (1,600; 354 delisted) + full SEP price hydration; `Universe(as_of_date, "caerus_large_cap")` now resolves the family artifact for canonical research access. Polaris priced rebaseline = MATERIAL (Sharpe 1.05->0.85, MaxDD -43%->-54%). Orion/Lyra matched PIT artifact generated with 2,767 observations and no statistically meaningful Lyra lead. Remaining decision-grade blocker: current-scale `scalemarketcap` large-cap membership must be replaced by PIT-valid, survivorship-free, security-id keyed, date-effective membership; DAILY market cap is one acceptable implementation, not the sole approved path. | `research/pit_universe.py`, `scripts/research/build_pit_universe_from_sharadar.py`, `scripts/research/hydrate_sharadar_sep.py`, `research/pit_large_cap_family.py`, `research/run_polaris_pit_priced_rebaseline.py`, `scripts/research/build_orion_lyra_pit_rebaseline.py` | n/a |
| FR-069 | — (architecture) | Research Lab / modular sleeve architecture (research-only scaffold; no production refactor) | `fr_active/fr_069_research_lab_modular_sleeve_architecture.md`; Phase A package: `fr_active/fr_069_phase_a_architecture_package.md`; Phase B scaffold: `fr_active/fr_069_phase_b_scaffolding.md`; Phase C readiness: `fr_active/fr_069_phase_c_readiness.md` | PHASE_B_IMPLEMENTED_RESEARCH_ONLY — research-only sleeve manifest, manifest validator, evidence-envelope validator, read-only MCP inventory, Polaris parity plan, Orion/Lyra PIT evidence plan, future sleeve onboarding placeholders, and Phase C readiness lifecycle gates. FR-069 child lane opened 2026-06-22 for canonical PIT replay panel, decision tapes, replay certification, allocator baseline, and exposure-matched framework. Phase C implementation requires separate owner approval even after scaffold validation/tests pass | `research_registry/sleeves/manifest.json`, `research_registry/sleeves/evidence.py`, `scripts/research/validate_sleeve_manifest.py`, `scripts/research/validate_sleeve_evidence.py`, `fr069_sleeve_inventory` | research-only |

Note: FR-056 and FR-057 are later design drafts (created 2026-06-08) that duplicate or
contradict the canonical FR-051/FR-053 research specs (created 2026-06-03). They are
**not** new roadmap items. See Section 4.

Numbering note: the requested investment-confidence wave originally referenced
FR-058, FR-059, and FR-060, but those IDs are already active operational-telemetry
work in `fr_active_backlog.md` (FR-058 through FR-062). To preserve source-of-truth
lineage and avoid duplicate FR numbers, the new investment-confidence items are
assigned to the next open IDs: FR-063 strategy differentiation, FR-064 multi-asset
framework, and FR-065 dashboard decision-grade consolidation.

---

## 3. Strategy state table (documented vs actual)

| Strategy | Registry type / family | Registry status | Module | CLI | Shadow | Promotion-eligible | Actual state |
|----------|------------------------|-----------------|--------|-----|--------|--------------------|--------------|
| Polaris | security_selection / core_momentum | paper | growth_engine_v4 | yes | yes | no | paper_control |
| Polaris_Alpha | security_selection / core_momentum | shadow | (variant) | — | yes | yes | official_shadow_concentration_variant; Top 4 / 20% cap; compare only against preserved Polaris baseline; no capital |
| Orion | security_selection / core_momentum | shadow | (variant) | — | yes | yes | shadow_only |
| Orion_Alpha | security_selection / core_momentum | shadow | (variant) | — | yes | yes | official_shadow_concentration_variant; Top 3 / 25% cap; compare only against preserved Orion baseline; no capital |
| Lyra | security_selection / core_momentum | shadow | constrained_lyra.py | yes | yes | yes | shadow_only |
| Phoenix | security_selection / crisis_reversal | research / not_viable_current_phase_b | phoenix.py | run_phoenix_research.py | no | no | research_module / produces_artifacts; PIT liquidity evidence is decision-grade but current Phase B candidate fails 5% ADV capacity policy; not Shadow-readiness eligible |
| Cygnus | security_selection / earnings_drift | research | research/cygnus | run_cygnus_research.py | no | no | v0_shelved_after_stage2_fail; v1_vendor_gated |
| Cassiopeia | security_selection / event_driven | research | none (spec-only) | none | no | no | spec_only — canonical EVENT-DRIVEN strategy (FR-052); selector code re-homed to Argo 2026-06-08 |
| Argo | meta_model / regime_overlay | research | argo.py | run_argo_regime_selection.py; build_argo_phase_a_evidence_framework.py; build_argo_phase_b_research_priority.py | no | no | regime overlay / model-selection layer (FR-053); active selector re-homed from Cassiopeia 2026-06-08; Phase A evidence consumer and Phase B research-priority engine are research-only |
| SPY | benchmark | shadow | n/a | n/a | no | no | benchmark |
| growth_engine_v4 | (engine behind Polaris) | n/a | core/growth_engine_v4.py | n/a | n/a | n/a | live paper baseline engine |

**FR-053 evidence state (2026-06-08):** Argo now emits a governance-valid
`argo_regime_selection.*` artifact even when evidence is stale or sparse. The
current 2026-06-08 selector state is `PARTIAL`, with `recommended_strategy:
null`, `decision_grade: false`, and stale-input blockers from shadow performance
(2026-04-30) plus promotion governance/readiness evidence (2026-06-02). The
dashboard decision-grade section therefore remains `BLOCKED` for substantive
evidence reasons, not because the Argo selector artifact is missing.

---

## 4. Taxonomy conflicts

**Conflict A — RESOLVED 2026-06-08 (Option A).** The regime / model-selection layer
implemented in code has been re-homed from `caerus_cassiopeia` to `caerus_argo`
(meta_model, schema `caerus_argo_regime_selection_v1`, artifact `argo_regime_selection.*`,
module `research_registry/research/argo.py`). `caerus_cassiopeia` is restored to its
canonical EVENT-DRIVEN definition (FR-052), spec-only. FR-057 is retired. The historical
narrative below is retained for lineage. Conflict B (Cygnus definition drift) remains OPEN.

**Conflict A — the "event-driven" role is double-specified and unimplemented.**
- Canonical intent (roadmap + FR-052): **Cassiopeia = event-driven** catalyst strategy.
- Canonical intent (roadmap + FR-053): **Argo = regime allocation overlay** (not security-selecting).
- BUT: the code module `cassiopeia.py` (`SCHEMA_VERSION = caerus_cassiopeia_model_selection_v1`) and CLI `run_cassiopeia_model_selection.py` implement Cassiopeia as a **regime-aware model selector / meta-model** — i.e. it occupies Argo's regime role, under Cassiopeia's name.
- AND: the later design draft `fr_archive/fr_057_argo_design_spec.md` redefines **Argo as an event-driven event sleeve** — i.e. it occupies Cassiopeia's role, under Argo's name.
- Net effect: the event-driven alpha source (intended Cassiopeia) is specified twice (FR-052 and FR-057) and implemented zero times; the regime/selector function is implemented once but mislabeled as Cassiopeia; the registry lists BOTH Cassiopeia and Argo as `regime_overlay`.

**Recommended resolution (for decision, not yet applied):** keep the canonical mapping
Cassiopeia = event-driven (FR-052) and Argo = regime overlay (FR-053). Re-home the
existing regime/meta-model code currently named `caerus_cassiopeia` under the Argo
identity (or a new explicit `caerus_selector` identity), and leave the event-driven
Cassiopeia as unimplemented spec. This requires a strategy-ID rename and registry
edit and therefore explicit approval — it is intentionally NOT done in this cleanup.

**Conflict B — RESOLVED 2026-06-10 (owner-approved).** Cygnus is **earnings drift**
(`earnings_drift`), the canonical FR-051 definition. The later design draft
`fr_archive/fr_056_cygnus_design_spec.md` — which broadened Cygnus into a generic
"persistent, slow-moving factor or price drift" sleeve — is **retired** and is
non-canonical. Rationale (FR-051 addendum A1): the earnings-event underreaction
thesis is what makes Cygnus a distinct return stream from the Polaris/Orion
momentum family; a generic drift sleeve would reproduce the 97%+ correlation
problem documented in FR-063. The registry `earnings_drift` family for
`caerus_cygnus` is unchanged and remains source of truth. No strategy ID, registry,
or execution change results from this resolution.

*Historical statement (retained for lineage):* Canonical FR-051 and the registry
defined Cygnus as earnings drift; the FR-056 draft described a generic price/factor
drift sleeve; these were related but not identical, and the decision was to retire
or fold FR-056 — now done.

---

## 5. Current blockers (research-grade findings, non-decision-grade until resolved)

- **Survivorship remediation (FR-068) — PIT foundation now built; legacy backtests
  are NON-DECISION-GRADE.** The static `data/universe.csv` (200 current survivors)
  is confirmed SEVERELY survivorship-biased; the PIT universe (20,618 securities,
  71.7% delisted) and `Universe(as_of_date)` now exist. The Polaris priced
  rebaseline on the honest large-cap universe is **MATERIAL** (Sharpe overstated
  1.05→0.85, max drawdown understated −43%→−54%). **Promotion requirement (new):**
  all promotion evidence must carry `universe_method = pit_universe`;
  current-universe backtests are retained as `legacy_current_universe` for lineage
  only. Orion/Lyra matched PIT artifact now finds no statistically meaningful
  Lyra lead over 2,767 pre-holdout matched observations. Canonical
  `Universe(as_of_date, "caerus_large_cap")` resolver wiring is certified, but
  allocator and promotion replay remain blocked from decision-grade status until
  the current-scale `scalemarketcap` large-cap family is replaced by PIT-valid,
  survivorship-free, security-id keyed, date-effective membership and certified
  replay artifacts.
- **Portfolio history stale** — freshness audit added (`portfolio_history_freshness.py`, untracked); confirms staleness needs resolution before promotion-grade evaluation.
- **Security master auth / missing artifact** — `security_master_diagnostics.py` / `audit_security_master_refresh.py` (untracked) flag auth or missing-artifact gaps blocking PIT-safe alias resolution.
- **BK → BNY universe migration** — pending; do NOT migrate `universe.csv` without explicit approval.
- **Phoenix needs passive (out-of-sample) evidence** before any shadow/promotion consideration. The FR-069 Phase C onboarding packet makes Phoenix a governed Research-stage candidate only; it does not activate Shadow or allocation behavior.
- **Cygnus definition drift** — RESOLVED 2026-06-10 (owner-approved): Cygnus is
  earnings-drift (canonical FR-051); FR-056 retired. See Section 4, Conflict B.
- **Cygnus v0 validation** — FR-051 Stage 2 verdict is FAIL (4/6 criteria).
  The tune window also failed; v0 is shelved and must not be re-tuned. The
  2025-forward holdout remains untouched and preserved. Cygnus v1 is gated on
  EPS-surprise / consensus data vendor selection. The FR-069 Phase C onboarding
  packet keeps Cygnus governed Research-stage only.
- **Cassiopeia needs event-contract evidence** before any shadow/promotion
  consideration. The FR-069 Phase C onboarding packet preserves Cassiopeia as
  the canonical event-driven sleeve and does not activate runtime behavior.
- **Argo needs frozen member-sleeve inputs and no-live-switching evidence**
  before any shadow/promotion consideration. The FR-069 Phase C onboarding
  packet keeps Argo research-only and does not authorize allocation switching.
- **FR-066 NAV provenance** — VM backfill/write and cron install completed
  2026-06-10. The corrected Alpaca portfolio-history series is continuous from
  2026-03-03, clean versus existing `nav.csv`, and includes SPY/beta columns;
  broker snapshot comparisons remain non-clean because snapshots are point-in-time
  account captures rather than the same EOD portfolio-history source. The Apr 8
  canonical row is `$9,751.97`; the older `$9,715.45` baseline is not used as
  source truth.
- **FR-067 vendor gate** — CLOSED_PASS on 2026-06-10. Sharadar coverage
  verification passed and is the approved PIT price/security-history source for
  FR-068. Remaining caveats are index-membership supplementation and the separate
  Cygnus v1 consensus/EPS-surprise dependency.
- **Shadow NAV observation reset** — the operational Shadow scorecard now uses
  the owner-approved `dated_same_day_close_to_close_v1` observation series from
  2026-05-12 forward. Legacy mixed-convention Shadow performance remains
  lineage-only and must not be combined with the canonical observation series for
  promotion or retirement evidence.
- **Post-submit snapshot baseline failures** — known unrelated validation backlog.
- **MCP full-suite order pollution** — known unrelated full-suite ordering backlog.

---

## 6. Instruction for future agents (mandatory)

Before creating any new FR, design spec, or strategy module:

1. Read this file and `config/research/strategy_registry.json`.
2. Check whether a canonical spec already exists (FR-050..FR-053 or FR-063..FR-065). If it does, extend
   the canonical spec — do **not** create a parallel "design" spec under a new FR number.
3. Never reassign or rename an existing strategy ID without recording the decision in
   Section 4 and obtaining explicit owner approval.
4. Never change execution, broker submission, cron timing, registry semantics, or
   paper/live behavior as part of documentation work.
