# Active FR Reconciliation Audit Inventory

Generated: `2026-06-14T14:03:31Z`

Scope: Prompt 1 audit only. No source governance status, runtime code,
execution code, broker code, cron file, strategy registry, promotion state, or
artifact state was changed by this audit. New files in this directory are audit
outputs only.

## Repository State

| Check | Evidence |
|---|---|
| Local branch | `main` |
| Local HEAD | `e1792cde79b8d7f2dcd8324451b2258910824bd0` |
| `origin/main` | `e1792cde79b8d7f2dcd8324451b2258910824bd0` |
| Local tracked status | Clean |
| Pre-existing untracked files | `docs/governance/AI_ORCHESTRATION_MODEL.md`, `docs/governance/CODEX_TASK_TEMPLATE.md`, `docs/governance/STRATEGIC_ESCALATION_POLICY.md`, `reports/agent_loops/2026-06-13_codex_mini_context_audit/evidence_backup/` |
| Current commit subject | `Document Shadow same-day NAV recovery` |

## VM State

| Check | Evidence |
|---|---|
| Host | `alpha-stack-scheduler` |
| VM command time | `2026-06-14T09:57:21-04:00` |
| VM branch | `main` |
| VM HEAD | `e1792cde79b8d7f2dcd8324451b2258910824bd0` |
| VM `origin/main` | `e1792cde79b8d7f2dcd8324451b2258910824bd0` |
| VM tracked status | Clean (`## main...origin/main`) |
| VM recent log | `e1792cd`, `0884a2a`, `0e443d3` |
| VM cron hash | `2fdee9971064545eed54961b8eaca214b8bb6c3541440b5609c794dc91e9869e` |
| Local `scripts/crontab.txt` hash | `2fdee9971064545eed54961b8eaca214b8bb6c3541440b5609c794dc91e9869e` |

Deployment conclusion: the VM is fast-forwarded to current `origin/main`; repo
deployment status can be distinguished from local implementation status.

## Shadow NAV Recovery Evidence

| Artifact / Check | Evidence |
|---|---|
| Active VM NAV path | `outputs/shadow_candidates/performance/shadow_nav_series.csv` |
| Active VM NAV rows | `23` |
| Active VM NAV date range | `2026-05-12` through `2026-06-12` |
| `shadow_summary.json` schema | `shadow_operational_same_day_summary_v1` |
| `shadow_summary.json` methodology | `canonical_operational_shadow_observation` |
| `shadow_summary.json` return convention | `dated_same_day_close_to_close_v1` |
| `shadow_summary.json` legacy status | `SUPERSEDED_BY_OWNER_DECISION` |
| Restatement manifest schema | `shadow_operational_same_day_restatement_manifest_v1` |
| Restatement manifest row count | `23` |
| Restatement manifest daily-return validations | `92`, status `PASS` |
| Restatement manifest observation window | `2026-05-12` through `2026-06-12` |
| Restatement manifest owner decision | Option 3 approved on 2026-06-13 |
| Active NAV hash from recovery report | `6a48b74c1c4b5a7af0a22210e21b70522bb24c0b84f6cd12dc11d1668a1b2de2` |
| Active summary hash from recovery report | `266314b2abffbd49afc7b0eeb9dcb11dbc4f0fdd52b296228abd63786d4d2c7c` |
| Restatement manifest hash from recovery report | `76cf50519e414772b1cfb8ab06967d979bb29f331e1691975fe94c837f999d81` |

Conclusion: the Shadow NAV incident is resolved for the active operational
observation series. The canonical observation clock begins on `2026-05-12`.
Legacy mixed-convention history is superseded and non-decision-grade for
promotion evidence.

## Shadow Health and Presentation Evidence

| Check | Evidence |
|---|---|
| VM scorecard dry run | Data through `2026-06-12`; data health `Fresh`; source date `2026-06-12` |
| VM scorecard label | `YTD (from 2026-05-12)` and `Excess vs SPY (YTD)` |
| Required canonical label | `Since Observation Inception (2026-05-12)` or equivalent |
| VM scorecard corrected returns | Polaris `+11.41%`, Orion `+19.19%`, Lyra `+12.58%`, SPY `+0.48%` over the observation window |
| VM scorecard promotion surface | Advisory `PROMOTE_CANDIDATE` appears for Orion/Lyra despite no authorized promotion action |
| `shadow_evaluation.json` valid-day counts | `39` for Polaris/Orion/Lyra/SPY |
| Canonical NAV row count | `23` |
| Non-strict health command | Exits `WARN`; internal `scorecard_data_health=Fresh` and `performance_integrity.status=OK` |
| Strict health command | Exits `FAIL`; failure reason is `PRICE_CACHE_STALE` on `2026-05-25`, not NAV chain corruption |

Conclusion: the active scorecard data are Fresh and NAV integrity is OK, but
operator-facing wording still says YTD and the health checker has baseline
semantics that can return WARN/FAIL despite Fresh+OK internals. This is
governance/presentation drift, not a recovered NAV-chain failure.

## Strategy Registry Evidence

Registry file: `config/research/strategy_registry.json`

Hash: `8a30c7ae153d4b87c358e641a4bd61fa7aa9b027d8350ff34a2b040f13db078c`

| Strategy | Registry status | Role | Execution impact |
|---|---|---|---|
| `caerus_polaris` | `paper` | `baseline` | `NON_EXECUTIONAL` |
| `caerus_orion` | `shadow` | `challenger` | `NON_EXECUTIONAL` |
| `caerus_lyra` | `shadow` | `challenger` | `NON_EXECUTIONAL` |
| `caerus_phoenix` | `research` | `research_candidate` | `NON_EXECUTIONAL` |
| `caerus_cygnus` | `research` | `research_candidate` | `NON_EXECUTIONAL` |
| `caerus_cassiopeia` | `research` | `research_candidate` | `NON_EXECUTIONAL` |
| `caerus_argo` | `research` | `selector` | `NON_EXECUTIONAL` |
| `spy_benchmark` | `shadow` | `benchmark` | `NON_EXECUTIONAL` |

Conclusion: the registry matches owner intent. Orion and Lyra remain shadow
challengers. No registry lifecycle change is indicated.

## FR-069 / MCP Evidence

| Check | Evidence |
|---|---|
| Sleeve manifest hash | `4417fc647a2cbfaa0941f3aa72bcf7d75ace275a7a5280ffc37f0f1abfe1ad5e` |
| Sleeve manifest validator | Local `python3 scripts/research/validate_sleeve_manifest.py --inventory` returned `status=OK`, `error_count=0` |
| Manifest current sleeves | Polaris, Orion, Lyra |
| Manifest future placeholders | Phoenix, Cygnus, Cassiopeia, Argo |
| Phase B behavior flag | `behavior_change_allowed=false` for every sleeve |
| MCP schema tool count | `27` |
| MCP tools relevant to stale backlog rows | `attribution_analysis`, `stable_window_evaluation`, `promotion_readiness` with explicit `strategies`, `execution_target_attainment`, `fr069_sleeve_inventory` |
| VM targeted MCP/manifest tests | `Tests/test_sleeve_manifest.py Tests/test_research_registry_mcp_server.py -q`: `33 passed in 4.59s` |
| Local targeted MCP test | Blocked by missing local dependency `networkx`; not treated as code failure |

Conclusion: FR-069 Phase B scaffold is implemented and deployed as read-only
research scaffolding. FR-036b/c/d backlog language is stale because those tools
are now registered, implemented, and deployed on VM.

## Governance Surfaces Read

Core docs read or inspected:

- `AGENTS.md`
- `docs/governance/ORCHESTRATOR_CONTEXT.md`
- `docs/governance/caerus_investment_doctrine.md`
- `docs/governance/CURRENT_RESEARCH_ROADMAP.md`
- `docs/governance/fr_active_backlog.md`
- `docs/governance/fr_registry.md`
- `docs/governance/fr_governance_model.md`
- `docs/governance/operational_lessons.md`
- `docs/artifact_registry.md`
- `docs/artifact_governance.md`
- `docs/artifact_ownership_matrix.md`
- `docs/governance/fr_active/fr_069_phase_a_architecture_package.md`
- `docs/governance/fr_active/fr_069_phase_b_scaffolding.md`
- `docs/governance/fr_active/fr_069_research_lab_modular_sleeve_architecture.md`
- `docs/governance/fr_active/fr_070_cash_gating_post_sell_budget_reconciliation.md`
- `reports/incidents/2026-06-12_shadow_nav_scorecard_corruption.md`
- `reports/agent_loops/2026-06-13_shadow_nav_same_day_restatement/05_final_summary.md`
- `config/research/strategy_registry.json`
- `research_registry/sleeves/manifest.json`
- `research_registry/mcp_server/schemas.py`
- `research_registry/mcp_server/tools.py`
- `scripts/crontab.txt`
- `scripts/send_shadow_cio_report.py`
- `scripts/check_shadow_scorecard_health.py`

Path note: the prompt referenced `docs/governance/artifact_registry.md` and
related governance artifact files. The repository-local canonical artifact files
are currently under `docs/`, not `docs/governance/`.

## Audit Coverage

Unique FR identifiers found across `fr_active_backlog.md`, `fr_registry.md`, and
`CURRENT_RESEARCH_ROADMAP.md`: `65`.

Audited identifiers:

`FR-001`, `FR-002`, `FR-003`, `FR-004`, `FR-005`, `FR-006`, `FR-007`, `FR-008`,
`FR-009`, `FR-010`, `FR-011`, `FR-012`, `FR-013`, `FR-014`, `FR-015`, `FR-016`,
`FR-017`, `FR-018`, `FR-019`, `FR-020`, `FR-021`, `FR-022`, `FR-023`, `FR-024`,
`FR-025`, `FR-026`, `FR-027`, `FR-028`, `FR-029`, `FR-030`, `FR-031`, `FR-032`,
`FR-033`, `FR-034`, `FR-035`, `FR-036`, `FR-036a`, `FR-036b`, `FR-036c`,
`FR-036d`, `FR-037`, `FR-038`, `FR-050`, `FR-051`, `FR-052`, `FR-053`, `FR-054`,
`FR-055`, `FR-056`, `FR-057`, `FR-058`, `FR-059`, `FR-060`, `FR-061`, `FR-062`,
`FR-063`, `FR-064`, `FR-065`, `FR-066`, `FR-067`, `FR-068`, `FR-069`, `FR-070`,
`FR-071`, `FR-072`.
