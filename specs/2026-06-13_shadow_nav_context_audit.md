MODE: HARDEN
PROJECT_TYPE: quant-research
RISK_TIER: high
OBJECTIVE: Audit Shadow NAV scorecard corruption, preserve evidence, verify recent execution and FR-069 governance changes, and patch only confirmed research/reporting defects.

# 2026-06-13 Shadow NAV Context Audit

## FILES

- `outputs/shadow_candidates/performance/shadow_nav_series.csv`
- `outputs/shadow_candidates/performance/shadow_summary.json`
- `outputs/shadow_candidates/latest/`
- `outputs/shadow_candidates/2026-06-12/`
- `outputs/price_hydration/2026-06-12/`
- `scripts/send_shadow_cio_report.py`
- `scripts/refresh_shadow_scorecard_artifacts.py`
- `scripts/backfill_shadow_artifacts.py`
- `scripts/check_shadow_scorecard_health.py`
- `research/shadow_tracking/run.py`
- `research/shadow_tracking/strategies.py`
- `core/strategy_registry.py`
- `config/research/strategy_registry.json`
- `docs/governance/fr_active/fr_069_phase_a_architecture_package.md`
- `docs/governance/fr_active/fr_069_phase_b_scaffolding.md`
- `docs/governance/fr_active_backlog.md`
- `docs/governance/fr_registry.md`
- `docs/governance/CURRENT_RESEARCH_ROADMAP.md`
- `docs/execution_contract.md`
- `docs/execution_integrity_contract.md`
- `docs/execution_integrity_runbook.md`
- `reports/agent_loops/2026-06-13_codex_mini_context_audit/`
- `reports/incidents/2026-06-12_shadow_nav_scorecard_corruption.md`

## ACCEPTANCE_CRITERIA

- Evidence is preserved before artifact repair.
- Root cause is classified as confirmed, high-confidence, plausible, or unproven.
- Shadow NAV continuity cannot silently reset from an established chain.
- Scorecard health distinguishes fresh-valid from fresh-corrupt.
- Corrupt performance windows suppress YTD, seven-day, rankings, and promotion signals.
- Historical recovery, if performed, has a manifest and preserves daily-return methodology.
- Recent execution changes are checked against execution contracts.
- FR-069 remains research-only and Orion/Lyra are not retired.
- Deterministic reports are written under `reports/agent_loops/2026-06-13_codex_mini_context_audit/`.
- No broker, execution, allocation, strategy, cron, live-capital, or secret behavior changes.
