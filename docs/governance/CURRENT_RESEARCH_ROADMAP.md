# CURRENT RESEARCH ROADMAP — Caerus Source of Truth

Status: Canonical
Owner: Caerus Research Program
Last Updated: 2026-06-08
Governance Label: RESEARCH_ONLY
Execution Impact: NON_EXECUTIONAL (this document changes no execution, broker, cron, registry, or paper/live behavior)

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

## 1. Verified state (as of 2026-06-08)

- **Repo:** quant-daily-report / Caerus Quant / Alpha Stack
- **Local HEAD:** `ac70e7b` — "Add model tournament quality packet" (branch `main`)
- **Production posture:** paper only, US long-only equities + options overlay, Alpaca paper broker. No shorting, no leverage, no real capital.
- **Active paper strategy:** Caerus Polaris (`caerus_polaris`), wired to the `growth_engine_v4` baseline engine.
- **Shadow (non-blocking):** Caerus Orion, Caerus Lyra. SPY = benchmark.
- **Promotion ladder:** research → backtest → shadow → paper → live. No automated promotion. Promotion is conservative and research-only.
- **Hard rule:** do not change production trading, broker submission, or cron timing casually.

---

## 2. Canonical FR roadmap table

| FR | Strategy | Canonical role (intended) | Canonical spec (authoritative) | Status in spec | Code module? | Registry status |
|----|----------|---------------------------|--------------------------------|----------------|--------------|-----------------|
| FR-050 | Phoenix | Crisis reversal | `fr_050_phoenix_research_spec.md` | Draft | `research_registry/research/phoenix.py` + `phoenix_evidence_tracker.py` | research |
| FR-051 | Cygnus | Earnings / post-earnings drift | `fr_051_cygnus_research_spec.md` | Draft | none (spec only) | research (`earnings_drift`) |
| FR-052 | Cassiopeia | Event-driven (catalysts) | `fr_052_cassiopeia_research_spec.md` | Draft | **conflict — see §4** | research (`meta_model`) |
| FR-053 | Argo | Regime allocation overlay | `fr_053_argo_research_spec.md` | Draft | none (spec only) | research (`overlay`) |
| FR-054 | — | Dynamic strategy registry audit | `fr_054_dynamic_strategy_registry_audit.md` | Audit | n/a | n/a |
| FR-055 | — | Registry surface cleanup audit | `fr_055_registry_surface_cleanup_audit.md` | Audit | n/a | n/a |
| FR-056 | Cygnus | *(design draft — DUPLICATE of FR-051)* | superseded → FR-051 | Design Only | none | n/a |
| FR-057 | Argo | *(design draft — CONFLICTS with FR-053)* | superseded/quarantined → FR-053 | Design Only | none | n/a |

Note: FR-056 and FR-057 are later design drafts (created 2026-06-08) that duplicate or
contradict the canonical FR-051/FR-053 research specs (created 2026-06-03). They are
**not** new roadmap items. See Section 4.

---

## 3. Strategy state table (documented vs actual)

| Strategy | Registry type / family | Registry status | Module | CLI | Shadow | Promotion-eligible | Actual state |
|----------|------------------------|-----------------|--------|-----|--------|--------------------|--------------|
| Polaris | security_selection / core_momentum | paper | growth_engine_v4 | yes | yes | no | paper_control |
| Orion | security_selection / core_momentum | shadow | (variant) | — | yes | yes | shadow_only |
| Lyra | security_selection / core_momentum | shadow | constrained_lyra.py | yes | yes | yes | shadow_only |
| Phoenix | security_selection / crisis_reversal | research | phoenix.py | run_phoenix_research.py | no | no | research_module / produces_artifacts |
| Cygnus | security_selection / earnings_drift | research | none | none | no | no | spec_only |
| Cassiopeia | security_selection / event_driven | research | none (spec-only) | none | no | no | spec_only — canonical EVENT-DRIVEN strategy (FR-052); selector code re-homed to Argo 2026-06-08 |
| Argo | meta_model / regime_overlay | research | argo.py | run_argo_regime_selection.py | no | no | regime overlay / model-selection layer (FR-053); active selector re-homed from Cassiopeia 2026-06-08 |
| SPY | benchmark | shadow | n/a | n/a | no | no | benchmark |
| growth_engine_v4 | (engine behind Polaris) | n/a | core/growth_engine_v4.py | n/a | n/a | n/a | live paper baseline engine |

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
- AND: the later design draft `fr_057_argo_design_spec.md` redefines **Argo as an event-driven event sleeve** — i.e. it occupies Cassiopeia's role, under Argo's name.
- Net effect: the event-driven alpha source (intended Cassiopeia) is specified twice (FR-052 and FR-057) and implemented zero times; the regime/selector function is implemented once but mislabeled as Cassiopeia; the registry lists BOTH Cassiopeia and Argo as `regime_overlay`.

**Recommended resolution (for decision, not yet applied):** keep the canonical mapping
Cassiopeia = event-driven (FR-052) and Argo = regime overlay (FR-053). Re-home the
existing regime/meta-model code currently named `caerus_cassiopeia` under the Argo
identity (or a new explicit `caerus_selector` identity), and leave the event-driven
Cassiopeia as unimplemented spec. This requires a strategy-ID rename and registry
edit and therefore explicit approval — it is intentionally NOT done in this cleanup.

**Conflict B — Cygnus definition drift.** Canonical FR-051 and the registry define
Cygnus as **earnings drift** (`earnings_drift`). The later design draft
`fr_056_cygnus_design_spec.md` describes Cygnus as a generic "persistent, slow-moving
factor or price drift" sleeve. These are related but not identical. Confirm whether
Cygnus is earnings-drift (canonical) or broadened to price/factor drift, then retire
or fold FR-056.

---

## 5. Current blockers (research-grade findings, non-decision-grade until resolved)

- **Portfolio history stale** — freshness audit added (`portfolio_history_freshness.py`, untracked); confirms staleness needs resolution before promotion-grade evaluation.
- **Security master auth / missing artifact** — `security_master_diagnostics.py` / `audit_security_master_refresh.py` (untracked) flag auth or missing-artifact gaps blocking PIT-safe alias resolution.
- **BK → BNY universe migration** — pending; do NOT migrate `universe.csv` without explicit approval.
- **Phoenix needs passive (out-of-sample) evidence** before any shadow/promotion consideration.
- **Cassiopeia / Argo / Cygnus taxonomy ambiguity** — see Section 4. Highest-priority documentation blocker.

---

## 6. Instruction for future agents (mandatory)

Before creating any new FR, design spec, or strategy module:

1. Read this file and `config/research/strategy_registry.json`.
2. Check whether a canonical spec already exists (FR-050..FR-053). If it does, extend
   the canonical spec — do **not** create a parallel "design" spec under a new FR number.
3. Never reassign or rename an existing strategy ID without recording the decision in
   Section 4 and obtaining explicit owner approval.
4. Never change execution, broker submission, cron timing, registry semantics, or
   paper/live behavior as part of documentation work.
