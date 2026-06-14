# Conflicts and Governance Drift

Generated: `2026-06-14T14:03:31Z`

This file separates confirmed stale documentation from lower-confidence or
owner-gated decisions. No source governance file was modified during this audit.

## Critical

No critical governance contradiction requires `NEEDS_OPERATOR` for Prompt 1.
Owner intent is internally consistent:

- FR-070 remains the highest immediate operational observation priority.
- FR-069 remains the next major research-only architecture workstream.
- Orion and Lyra continue evaluation.
- FR-063 remains active supporting differentiation evidence.
- No promotion, retirement, rename, allocation, model, strategy, broker,
  execution, or cron change is authorized.

## High-Confidence Drift

| Item | Evidence | Risk | Recommended Prompt 2 Action |
|---|---|---|---|
| Roadmap header is stale | `CURRENT_RESEARCH_ROADMAP.md` says last updated `2026-06-12`, verified state `as of 2026-06-08`, local HEAD `216ac5f`; current local/origin/VM HEAD is `e1792cd`. | Future agents may anchor to stale commit/state. | Update verified-state language and remove stale current SHA/date claims. |
| Shadow recovery not fully reflected in governance docs | Recovery report and VM artifacts show `dated_same_day_close_to_close_v1`, observation window `2026-05-12`..`2026-06-12`, `23` rows, Fresh + NAV integrity OK. Some docs still discuss unresolved Shadow timing generically. | Mixed-convention evidence could be reused as decision-grade. | Record canonical method, observation inception, 23-row count, and legacy-series supersession in roadmap/backlog/registry/artifact docs. |
| Scorecard label says YTD | VM dry-run prints `YTD (from 2026-05-12)` and `Excess vs SPY (YTD)`. | Misrepresents the recovered observation period and invites mixing with calendar-year history. | Presentation-only patch to show `Since Observation Inception (2026-05-12)` or equivalent; preserve calculations. |
| FR-028 needs canonical observation clock | Backlog still describes Phase C sidecars without the recovered same-day series boundary. | Promotion evidence may combine incompatible Shadow series. | Update FR-028 wording: canonical operational Shadow observation begins `2026-05-12`; legacy mixed-convention history is non-decision-grade. |
| FR-070 active spec tail is stale | `fr_070_cash_gating_post_sell_budget_reconciliation.md` ends with “Research not started”; backlog/registry say June 12 remediation deployed and observing. | Agents could reopen already-remediated work or misclassify observation state. | Update spec current status: deployed remediation, open observation gates, Shadow NAV incident separate. |
| FR-032 / FR-070 separation is incomplete | June 12 execution cash discrepancy was `ARTIFACT_TIMING_FAILURE`; Shadow NAV incident is separate. | Future agents may conflate execution post-buy timing with Shadow NAV recovery. | Add explicit separation in roadmap/backlog/spec language. |
| MCP FR-036b/c/d are stale | Source registers `attribution_analysis`, `stable_window_evaluation`, and strategy-aware `promotion_readiness`; VM MCP tests passed `33 passed`. | Completed MCP capabilities remain listed as not-started backlog work. | Move FR-036b/c/d out of BACKLOG to implemented/deployed-observing language; keep FR-036a backlog. |
| FR-036 tool-count language is stale | Backlog says `20 tools`; current schema lists `27`. | MCP capability inventory is outdated. | Update tool count/current-state wording. |
| FR-055 detailed section conflicts with summary | Active summary says `DEPLOYED_OBSERVING`; detail section says `IN_PROGRESS` while its current state says deployed and observing. | Agents could regress status_review_needed language. | Normalize FR-055 detail status to `DEPLOYED_OBSERVING`. |
| FR-063 status conflicts with owner intent | Registry says `ACTIVE_RESEARCH`; backlog says `BACKLOG_REVIEW`/deprioritized. Owner states FR-063 is active supporting differentiation evidence. | Agents could silently retire/demote FR-063 or treat Orion/Lyra disposition as resolved. | Normalize to `ACTIVE_RESEARCH` supporting differentiation evidence; require sufficient canonical new-series history before retirement conclusions. |
| FR-069 Phase A status is stale | Phase A package status still `ACTIVE_PHASE_A`; Phase B scaffold is implemented and VM tests pass. | Agents may re-run Phase A or start Phase C prematurely. | Update Phase A/Phase B docs: Phase B implemented research-only; Phase C requires explicit approval, not just tests. |
| Roadmap FR-067 blocker contradicts registry | Roadmap blocker says Sharadar trial key pending; FR-067 is `CLOSED_PASS` and FR-068 used Sharadar. | Agents may incorrectly block PIT work. | Remove/replace stale FR-067 pending-trial blocker. |
| Artifact docs review dates/Shadow timing wording stale | Artifact docs still have `last_reviewed: 2026-05-22`; `artifact_registry.md` says unresolved Shadow timing downgrades interpretation. | Artifact trust semantics lag the owner-approved recovery. | Update reviewed date and Shadow performance-series wording. |

## Medium-Confidence Drift / Owner-Gated

| Item | Evidence | Risk | Recommendation |
|---|---|---|---|
| FR-034 may be superseded by FR-070 | June 12 cash drift was resolved as artifact timing under FR-070; no distinct FR-034 implementation evidence found. | Closing it without owner approval could hide a broader post-submit cash-drift audit need. | Leave open in Prompt 2 unless owner approves supersession; optionally mark “review for supersession by FR-070.” |
| Scorecard `PROMOTE_CANDIDATE` wording may overstate authority | VM dry-run prints `PROMOTE_CANDIDATE` for Orion/Lyra after NAV integrity passes; governance says no promotion action authorized. | Operators may treat advisory label as an owner decision. | Presentation-only caveat is safe; changing promotion logic is not authorized in Prompt 2 unless explicitly scoped. |
| Scorecard valid-day count differs from canonical NAV rows | `shadow_evaluation.json` reports `rolling_count_of_valid_days=39`; canonical recovered NAV has `23` rows. | Promotion-signal wording may rely on non-canonical valid-day evidence. | Do not alter logic in Prompt 2 unless authorized; document discrepancy and consider later scorecard/promotion evidence alignment. |
| Health checker overall status differs from internal Fresh/OK | Non-strict exits `WARN`, strict exits `FAIL` due `2026-05-25 PRICE_CACHE_STALE`; internal fields show Fresh and NAV integrity OK. | Governance could overstate strict health pass. | Record as health-gate semantics drift; code fix is out of Prompt 2 unless explicitly approved. |
| Older `DEPLOYED_OBSERVING` FRs may be closable | Many May FRs remain observing. | Active backlog may stay too large. | Do not close without objective observation evidence; create a later closure audit if desired. |

## Confirmed Completed / Historical Items

These belong in registry/history and should not be re-added to active backlog:

`FR-004`, `FR-006`, `FR-008`, `FR-009`, `FR-011`, `FR-013`, `FR-015`,
`FR-017`, `FR-018`, `FR-019`, `FR-020`, `FR-023`, `FR-024`, `FR-025`,
`FR-026`, `FR-027`, `FR-030`, `FR-054`, `FR-067`.

## Confirmed Still Active

These should remain active/open after Prompt 2 unless a separate owner decision
is provided:

`FR-021`, `FR-028`, `FR-029`, `FR-031`, `FR-032`, `FR-033`, `FR-034`,
`FR-035`, `FR-036`, `FR-036a`, `FR-037`, `FR-038`, `FR-050`, `FR-051`,
`FR-052`, `FR-053`, `FR-057`, `FR-059`, `FR-060`, `FR-063`, `FR-064`,
`FR-065`, `FR-066`, `FR-068`, `FR-069`, `FR-070`, `FR-071`, `FR-072`.

FR-036b/c/d are active governance rows today, but the evidence supports moving
them out of backlog/not-started status because the capabilities are implemented
and deployed.

## No Stop Condition Triggered

The audit can proceed to a high-confidence implementation patch because:

- Owner intent is not contradictory.
- Deployment evidence is objective: VM `HEAD` equals `origin/main` at `e1792cd`.
- Orion/Lyra and FR-063 disposition is unambiguous: continued evaluation, no
  retirement, no rename.
- Implementation status and deployment status can be distinguished.
- No strategy registry lifecycle state change is required.
