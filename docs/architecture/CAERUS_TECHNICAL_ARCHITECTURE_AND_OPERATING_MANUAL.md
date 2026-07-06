# Caerus Technical Architecture And Operating Manual

Status: architecture-pack operating manual draft
Scope: documentation only
Last reviewed: 2026-06-26

Canonical status: Needs Repository Verification until this architecture pack is
committed and reconciled with `origin/main`.

This manual is the end-to-end operating narrative for Caerus. It explains how
the system fits together, where source-of-truth documents live, what should not
be changed casually, and how future engineers and AI agents should navigate the
repository without changing runtime behavior.

## Document Contract

| Field | Value |
|---|---|
| Purpose | Explain Caerus architecture and operations at system level while routing detailed ownership to canonical docs. |
| Owner | Not named in repository; architecture/governance ownership requires repository verification. |
| Inputs | Architecture pack, governance docs, execution contracts, runbooks, source maps, tests-as-docs, artifact patterns. |
| Outputs | Operating manual for engineers, operators, Claude, GPT, and Codex. |
| Related Documents | `docs/architecture/README.md`, `docs/architecture/SYSTEM_MAP.md`, `docs/architecture/DOCUMENT_INDEX.md`, `docs/architecture/GLOSSARY.md`. |
| Related Tests | Tests referenced by subsystem below and in `DOCUMENT_INDEX.md`. |
| Related Implementation | Cross-system; implementation files are named by section. |
| Related Artifacts | Precompute, run, broker, audit, dashboard, research, and governance artifact families. |
| Known Gaps | Dirty-worktree evidence, candidate lifecycle, FR-105, RDP, and governance changes require repository verification. |

Authoritative References:

- `docs/architecture/DOCUMENTATION_GOVERNANCE.md`
- `docs/architecture/DOCUMENT_INDEX.md`

## Evidence Policy

- Cite repository paths rather than unsupported prose.
- Link source-of-truth docs instead of duplicating them.
- Mark unverified claims as `Needs Repository Verification`.
- Treat generated reports and artifacts as evidence, not operator instructions,
  unless governance promotes them.
- Treat broker state as authoritative for actual cash, buying power, positions,
  account status, and order state.

Authoritative References:

- `docs/architecture/DOCUMENTATION_GOVERNANCE.md`
- `docs/architecture/DOCUMENT_INDEX.md`
- `docs/architecture/DOCUMENT_GAPS.md`

## 1. Executive Overview

Caerus is a paper-traded quantitative investment operating system for US
long-only equities with a gated options overlay and a separately governed
live-pilot evidence lane. Production-like paper execution remains the normal
path. Shadow strategies generate artifacts without capital. Live-pilot
submission is disabled by default unless explicit FR-104 controls are satisfied.

The system is artifact-driven. Daily decisions, execution behavior, broker
state, reconciliation, dashboard data, governance state, and research evidence
are persisted under deterministic paths so operators and agents can inspect
facts without rerunning strategy logic.

Authoritative References:

- `README.md` (repo root)
- `AGENTS.md`
- `docs/governance/ORCHESTRATOR_CONTEXT.md`
- `docs/governance/caerus_investment_doctrine.md`
- `docs/governance/CURRENT_RESEARCH_ROADMAP.md`
- `docs/governance/fr_registry.md`

## 2. System Philosophy

Caerus separates intent, permission, execution, broker truth, and reporting:

- Governance docs define strategic permission.
- Model/research code defines targets, ranks, and portfolio intent.
- Execution code translates validated intent into planned, intended, submitted,
  accepted, filled, clipped, suppressed, or rejected candidate states.
- Alpaca is authoritative for broker truth.
- Local artifacts are audit and replay surfaces.
- Reports, email, and dashboard views should expose explicit reasons rather
  than hide missing or contradictory evidence.

Authoritative References:

- `docs/architecture/ARCHITECTURE_PRINCIPLES.md`
- `docs/governance/caerus_investment_doctrine.md`
- `specs/broker_authoritative_execution_model.md`
- `docs/execution_integrity_contract.md`

## 3. Architecture Principles

The durable principles are:

1. Broker truth outranks stale local artifacts.
2. Governance permission is separate from implementation possibility.
3. Execution should fail closed when evidence is contradictory or missing.
4. Generated artifacts are evidence, not instructions.
5. Documentation routes to sources of truth.
6. Tests are behavioral documentation, not runtime proof by themselves.
7. Dirty-worktree evidence must be marked `Needs Repository Verification`.

Authoritative References:

- `docs/architecture/ARCHITECTURE_PRINCIPLES.md`
- `docs/architecture/DOCUMENTATION_GOVERNANCE.md`
- `docs/artifact_governance.md`

## 4. Daily Operating Lifecycle

The current operational scheduler is VM cron. `scripts/crontab.txt` is the
current schedule evidence. `.github/workflows/daily-alpaca-paper.yml` is a
deprecated wrapper that directs operators to VM cron.

| Time ET | Phase | Entry point | Primary outputs |
|---|---|---|---|
| 1:00 AM weekdays | Overnight agents | `scripts/cron_overnight.sh` | `outputs/overnight_signals/` |
| 6:30 AM weekdays | Research digest | `scripts/cron_research.sh` | research digest outputs |
| 6:45 AM weekdays | Security master refresh | `scripts/cron_security_master.sh` | security-master artifacts |
| 7:00 AM weekdays | Precompute | `scripts/cron_precompute.sh` | `outputs/precompute/<date>/` |
| 9:35 AM weekdays | Paper execution | `scripts/cron_execute.sh` | `outputs/runs/<run_id>/` |
| 10:00 AM weekdays | Confirmation/email | `scripts/cron_confirm.sh` | confirmation/email artifacts |
| 6:30 PM weekdays | Price hydration | `python3 -m scripts.hydrate_price_cache_only --refresh-shadow-artifacts --strict` | price hydration and refreshed shadow artifacts |
| 7:15 PM weekdays | Portfolio history | `scripts/build_portfolio_history.py`, `core.portfolio_history_escalation` | portfolio history and escalation output |
| 9:00 PM weekdays | Shadow CIO report | `python3 -m scripts.send_shadow_cio_report` | shadow scorecard/report email |
| Monday 8:00 AM | Weekly review | `scripts/cron_weekly_review.sh` | weekly review artifacts |

Authoritative References:

- `scripts/crontab.txt`
- `docs/runbook.md`
- `docs/OPERATIONS.md`
- `.github/workflows/daily-alpaca-paper.yml`

## 5. Repository Organization

High-risk runtime areas:

- `core/`: allocation, summaries, integrity, reliability, timelines.
- `paper/`: paper broker, email, ledger, NAV, report generation.
- `brokers/`: Alpaca broker adapters and snapshots.
- `scripts/run_precomputed_alpaca_execution.py`: paper execution runner.
- `scripts/cron_*.sh`: VM scheduler phases.

Architecture and evidence areas:

- `docs/`: canonical docs, governance, architecture, runbooks, specs.
- `docs/architecture/`: architecture indices and durable context.
- `docs/governance/`: doctrine, roadmap, FRs, operating context.
- `Tests/`: behavioral documentation and regression coverage.
- `outputs/`: generated artifacts.
- `reports/`: generated reports, incident evidence, research evidence.

Authoritative References:

- `docs/architecture/SYSTEM_MAP.md`
- `docs/architecture/KNOWLEDGE_GRAPH.md`
- `docs/architecture/DOCUMENT_INVENTORY.md`

## 6. Alpha Architecture

The stable conceptual architecture is the seven-layer Alpha Stack: data,
features, signals, regime, portfolio construction, execution, and attribution.
Older Alpha Stack docs provide lineage. Current strategy status and promotion
permission must defer to doctrine, roadmap, registry, backlog, and strategy
registry.

Sleeve architecture is governed through research-stage evidence and promotion
rules. FR-069 is research-only unless a separate production implementation
phase is approved.

Authoritative References:

- `docs/Alpha_Stack_Architecture_Reference.md`
- `docs/alpha_stack/architecture_overview.md`
- `docs/governance/fr_active/fr_069_research_lab_modular_sleeve_architecture.md`
- `config/research/strategy_registry.json`

## 7. Portfolio Construction

Portfolio construction follows the Caerus Investment Doctrine. Runtime
construction is implemented through allocation and target portfolio surfaces,
with execution downstream converting intent into broker orders.

FR-105 studies whether sleeve-local candidate sets should be replaced or
supplemented by a global optimizer that can explain why each name is held,
excluded, clipped, blocked, or unexecuted. FR-105 is current-worktree,
research-only evidence and does not authorize allocator replacement, sizing
changes, broker changes, scheduler changes, or promotion.

Authoritative References:

- `docs/governance/caerus_investment_doctrine.md`
- `core/portfolio_alloc.py`
- `docs/governance/fr_active/fr_105_global_portfolio_optimizer_and_decision_provenance.md`
- `Tests/test_allocation.py`
- `Tests/test_fr105_*.py`

## 8. Execution Lifecycle

Execution is planned-payload first, broker-authoritative, artifact-heavy, and
fail-closed where missing or contradictory evidence would otherwise create
false success.

Normal path:

1. Precompute writes `outputs/precompute/<date>/planned_execution_payload.json`.
2. `scripts/run_precomputed_alpaca_execution.py` loads and validates the bundle.
3. Nonempty planned payloads default to exact planned-payload execution unless
   explicitly opted out.
4. Pretrade broker snapshot and pretrade reconciliation run before submission.
5. `paper/paper_broker.py` filters executable trades and writes intended orders.
6. Sell orders execute first when present.
7. Post-sell broker state refreshes cash, buying power, equity, and positions.
8. Buy orders are rebuilt, clipped, suppressed, or submitted according to
   budget, min-notional, buying power, and safety gates.
9. Alpaca submissions capture broker response metadata.
10. Posttrade state, reconciliation, target-attainment, reliability, lifecycle,
    email, summary, and dashboard artifacts surface the result.

Recent June 24-25 current-worktree lifecycle work adds an audit-only candidate
lifecycle artifact through `core/candidate_trade_lifecycle.py`. It reconstructs
candidate disposition from already-written artifacts. It does not size, filter,
route, or submit orders.

The 8 planned / 2 submitted case is a deterministic June 25 regression fixture,
not broker runtime history unless a broker-authoritative run bundle is provided
and reconciled.

| Candidate | Side | Disposition | Verified reason |
|---|---|---|---|
| MAR | SELL | Submitted and filled | Passed min-notional and reached broker submission. |
| MO | SELL | Suppressed before intended orders | `min_notional` after executable normalization. |
| NEE | SELL | Suppressed before intended orders | `min_notional` after executable normalization. |
| NSC | BUY | Suppressed after intended orders | `buy_blocked_insufficient_buying_power` in post-sell rebudget. |
| VZ | BUY | Suppressed after intended orders | `buy_blocked_insufficient_buying_power` in post-sell rebudget. |
| UNP | BUY | Suppressed after intended orders | `buy_blocked_insufficient_buying_power` in post-sell rebudget. |
| UNH | BUY | Suppressed after intended orders | `buy_blocked_insufficient_buying_power` in post-sell rebudget. |
| SPG | BUY | Clipped, submitted, and filled | `post_sell_rebudget_capital_clipped`. |

Fixture counts from `Tests/test_candidate_trade_lifecycle.py`: 8 planned, 6
intended/executable, 2 submitted, 2 accepted, 2 filled, 6 suppressed, 1 clipped.

Authoritative References:

- `docs/execution_contract.md`
- `docs/execution_integrity_contract.md`
- `docs/execution_integrity_runbook.md`
- `scripts/run_precomputed_alpaca_execution.py`
- `paper/paper_broker.py`
- `core/candidate_trade_lifecycle.py`
- `Tests/test_candidate_trade_lifecycle.py`

## 9. Broker Integration

Alpaca is authoritative for actual positions, cash, buying power, account
status, account valuation, and submitted/open/filled/rejected order state. The
model is authoritative for desired targets, ranking, and allocation intent.

If UI visibility, local artifacts, and broker truth disagree, query broker
truth directly by order ID or client order ID before concluding that an order
is missing.

Authoritative References:

- `specs/broker_authoritative_execution_model.md`
- `brokers/alpaca_broker.py`
- `brokers/alpaca_snapshot.py`
- `paper/paper_broker.py`
- `scripts/export_alpaca_broker_snapshot.py`

## 10. Reconciliation

Caerus has separate reconciliation questions:

| Question | Surface | Meaning |
|---|---|---|
| Did pretrade state allow execution? | pretrade reconciliation and broker preflight | Blocks or permits execution before submission. |
| Did intended orders become payload/submissions? | execution integrity and candidate lifecycle | Detects dropped, suppressed, clipped, or submitted candidates. |
| Did broker positions match expected post-execution state? | `recon_posttrade_<date>.json` | Broker-state reconciliation. |
| Did the actual portfolio reach the risk-adjusted target? | `target_attainment_<date>.json` | Target-attainment reconciliation. |
| Did operational invariants pass? | `execution_reliability_report_<date>.json` | Cross-artifact reliability classification. |

`OK_RECONCILED` does not prove target attainment.

Authoritative References:

- `docs/execution_integrity_contract.md`
- `docs/execution_integrity_runbook.md`
- `core/execution_integrity.py`
- `core/operational_invariants.py`
- `research/target_attainment.py`
- `Tests/test_reconciliation.py`
- `Tests/test_target_attainment.py`

## 11. Dashboard

The dashboard is a static-file operator surface served locally or on the VM.
Current dashboard docs describe multiple generations. Live dashboard claims
require current build/served-file or VM proof, not just docs.

Dashboard source families include dashboard builders, `web/dashboard/` files,
and generated dashboard payloads.

Authoritative References:

- `docs/dashboard_v1_source_map.md`
- `docs/dashboard_refresh_spec.md`
- `docs/quant_dashboard.md`
- `scripts/research/build_dashboard_v1.py`
- `scripts/research/build_quant_dashboard.py`
- `web/dashboard/`

## 12. Reporting

Reporting surfaces include execution summaries, trading-day summaries,
execution email, shadow CIO reporting, research packets, dashboard payloads,
and generated incident/research reports. Operator-facing reports should expose
machine-readable and human-readable reasons rather than silently degrading.

Authoritative References:

- `docs/execution_summary.md`
- `docs/trading_email_governance.md`
- `docs/shadow_scoreboard_email.md`
- `core/execution_summary.py`
- `core/trading_day_summary.py`
- `paper/build_execution_email.py`

## 13. Governance

Governance is a control surface. Registry status outranks folder location, and
research-only work must not drift into execution, allocation, broker, cron,
promotion, or runtime behavior. Architecture-pack governance rules live in the
companion governance document.

Authoritative References:

- `docs/architecture/DOCUMENTATION_GOVERNANCE.md`
- `docs/governance/caerus_investment_doctrine.md`
- `docs/governance/CURRENT_RESEARCH_ROADMAP.md`
- `docs/governance/fr_registry.md`
- `docs/governance/fr_active_backlog.md`
- `docs/governance/fr_governance_model.md`
- `docs/governance/STRATEGIC_ESCALATION_POLICY.md`

## 14. Testing Philosophy

Tests are behavioral documentation. They document expected behavior and protect
against regression, but they do not replace broker-authoritative runtime proof,
VM validation, or served dashboard proof.

For docs-only work, prefer:

```bash
git diff --check
git diff --stat
git diff --name-only
```

For runtime/Python work, prefer the repo venv and targeted tests.

Authoritative References:

- `AGENTS.md`
- `docs/validation_isolation_policy.md`
- `docs/operational_validation.md`
- `Tests/test_documentation_governance.py`
- `Tests/test_governance_hygiene_agent.py`

## 15. Incident History

Important incident classes remain visible because they define durable operating
constraints:

| Date / ID | Class | Durable lesson |
|---|---|---|
| HOTFIX-2026-05-27 | Buy-leg suppression visibility | Planned, submitted, budget-skipped, and suppressed buys must be distinguishable. |
| 2026-06-09 FR-031 wave | Fractional preservation and post-sell rebudgeting | Fractional quantities and confirmed sell proceeds must survive safeguards. |
| 2026-06-12 | Cash discrepancy forensic | Distinguish broker truth, persisted fact, and inference. |
| HOTFIX-2026-06-15-FR070 | Sell-fill observation | Submitted/accepted is not the same as filled. |
| 2026-06-19 planned-payload invariant | Dropped planned payload | Nonempty planned payloads must not degrade silently to `NO_ACTION`. |
| 2026-06-22/23 FR-104 | Live-pilot evidence lane | Keep live pilot manual, capped, separately approved, and broker-truth reconciled. |
| 2026-06-25 lifecycle fixture | Candidate disposition transparency | Every candidate needs stage, reason, artifact, and responsible code path. |

Authoritative References:

- `docs/governance/fr_registry.md`
- `docs/governance/operational_lessons.md`
- `docs/execution_integrity_runbook.md`
- `reports/incidents/README.md`

## 16. Engineering Standards

Engineering changes should preserve:

- Explicit scope boundaries.
- Broker-authoritative reconciliation.
- Fail-closed execution behavior.
- Deterministic artifacts.
- Source-of-truth documentation routing.
- Dirty-worktree safety.
- Governance separation between research, paper, live pilot, and production.

Authoritative References:

- `AGENTS.md`
- `docs/architecture/CONTRIBUTING_ARCHITECTURE.md`
- `docs/architecture/ARCHITECTURE_PRINCIPLES.md`
- `docs/governance/ORCHESTRATOR_CONTEXT.md`

## 17. AI Collaboration Model

AI agents should read `AGENTS.md`, `ORCHESTRATOR_CONTEXT.md`, and this
architecture pack before changing code or docs. They must preserve unrelated
user changes, check `git status --short`, mark unverified evidence, and avoid
runtime-adjacent changes unless explicitly authorized.

Authoritative References:

- `AGENTS.md`
- `docs/governance/ORCHESTRATOR_CONTEXT.md`
- `docs/governance/AI_ORCHESTRATION_MODEL.md`
- `docs/governance/CODEX_TASK_TEMPLATE.md`
- `docs/architecture/CONTRIBUTING_ARCHITECTURE.md`

## 18. Operator Runbooks

Use runbooks rather than improvising. The architecture operator runbook routes
common operational questions to source runbooks and artifact families.

Authoritative References:

- `docs/architecture/OPERATOR_RUNBOOK.md`
- `docs/runbook.md`
- `docs/OPERATIONS.md`
- `docs/execution_integrity_runbook.md`
- `docs/deployment_workflow.md`
- `scripts/crontab.txt`

## 19. Technical Debt

The current documentation debt is tracked in `DOCUMENT_GAPS.md`. Major classes
include dirty-worktree verification, candidate lifecycle promotion, missing
broker runtime proof for the June 25 fixture, dashboard source ownership,
Research MCP refresh, RDP canonical status, and documentation taxonomy cleanup.

Authoritative References:

- `docs/architecture/DOCUMENT_GAPS.md`
- `docs/architecture/ENGINEERING_DECISION_INDEX.md`
- `docs/documentation_taxonomy.md`

## 20. Roadmap

Near-term architecture roadmap:

1. Commit and reconcile the architecture pack as a docs-only change.
2. Verify candidate lifecycle code/test and link real run artifacts when
   available.
3. Refresh dashboard source map against current builders and served payloads.
4. Refresh Research MCP current-state docs from code/tests.
5. Confirm RDP, FR-105, FR-104, roadmap, registry, and backlog canonical state.
6. Keep future architecture additions link-oriented, deterministic, and scoped.

Authoritative References:

- `docs/architecture/DOCUMENT_GAPS.md`
- `docs/architecture/ENGINEERING_DECISION_INDEX.md`
- `docs/governance/CURRENT_RESEARCH_ROADMAP.md`
- `docs/governance/fr_active_backlog.md`

## 21. Glossary References

The architecture glossary owns shared vocabulary for recommendation, candidate,
planned, submitted, accepted, filled, partially filled, clipped, suppressed,
rejected, reconciled, and broker-authoritative.

Authoritative References:

- `docs/architecture/GLOSSARY.md`
- `docs/architecture/SYSTEM_MAP.md`
- `docs/execution_contract.md`
- `docs/execution_integrity_contract.md`
- `specs/broker_authoritative_execution_model.md`
