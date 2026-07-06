# Architecture Operator Runbook

Status: architecture-pack finalization draft
Scope: documentation only
Last reviewed: 2026-06-26

Canonical status: Needs Repository Verification until this architecture pack is
committed and reconciled with `origin/main`.

## Document Contract

| Field | Value |
|---|---|
| Purpose | Route operators and agents to the correct operational documents and first checks. |
| Owner | Not named in repository; operations ownership requires repository verification. |
| Inputs | Existing runbooks, execution contracts, deployment docs, scheduler docs, architecture pack. |
| Outputs | Navigation runbook for operational triage. |
| Related Documents | `docs/runbook.md`, `docs/OPERATIONS.md`, `docs/execution_integrity_runbook.md`, `docs/deployment_workflow.md`. |
| Related Tests | Operational, execution, broker, scheduler, dashboard, and governance tests listed in `DOCUMENT_INDEX.md`. |
| Related Implementation | Cron scripts, execution runner, broker modules, dashboard builders, validation helpers. |
| Related Artifacts | `outputs/workflow/<date>/`, `outputs/runs/<run_id>/`, dashboard payloads, VM logs. |
| Known Gaps | VM truth requires live `ssh caerus-vm` validation when operational state matters. |

## First Checks

1. Confirm scope: docs-only, research-only, operational, broker, scheduler, or
   governance.
2. Check `git status --short` and preserve unrelated changes.
3. Read `AGENTS.md` and `docs/governance/ORCHESTRATOR_CONTEXT.md`.
4. For execution incidents, identify `outputs/latest_run.json` and the current
   `outputs/runs/<run_id>/` bundle.
5. For scheduler questions, read `scripts/crontab.txt`.
6. For dashboard claims, verify the built/served payload, not only docs.
7. For broker disagreements, query broker truth directly by order ID or client
   order ID before concluding an order is missing.

## Runbook Routing

| Scenario | Start with | Then inspect |
|---|---|---|
| Daily operations | `docs/runbook.md`, `docs/OPERATIONS.md` | `scripts/crontab.txt`, `outputs/workflow/<date>/` |
| Execution incident | `docs/execution_integrity_runbook.md` | `outputs/runs/<run_id>/execution_results.json`, broker/audit artifacts |
| Broker truth dispute | `specs/broker_authoritative_execution_model.md` | Broker API/export artifacts |
| Dashboard issue | `docs/dashboard_v1_source_map.md` | Dashboard builders and `web/dashboard/` payloads |
| Deployment/VM proof | `docs/deployment_workflow.md` | VM validation scripts and served-file hashes |
| Research state | `docs/governance/CURRENT_RESEARCH_ROADMAP.md` | FR docs, research artifacts, registry tests |
| Governance state | `docs/governance/fr_registry.md` | Backlog, governance model, hygiene outputs |

## Execution Incident Artifact Order

1. `outputs/latest_run.json`
2. `outputs/runs/<run_id>/operator_summary.json`
3. `outputs/runs/<run_id>/execution_results.json`
4. `outputs/runs/<run_id>/broker/intended_orders_<date>.json`
5. `outputs/runs/<run_id>/broker/post_sell_rebudget_<date>.json`
6. `outputs/runs/<run_id>/broker/recon_posttrade_<date>.json`
7. `outputs/runs/<run_id>/audit/execution_integrity.json`
8. `outputs/runs/<run_id>/audit/execution_reliability_report_<date>.json`
9. `outputs/runs/<run_id>/audit/candidate_trade_lifecycle_<date>.json`
10. Direct broker query when local evidence and broker truth disagree.

## Authoritative References

- `docs/runbook.md`
- `docs/OPERATIONS.md`
- `docs/execution_integrity_runbook.md`
- `docs/deployment_workflow.md`
- `scripts/crontab.txt`
