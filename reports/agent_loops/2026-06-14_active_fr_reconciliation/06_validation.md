# Validation Report

Generated: `2026-06-14`

## Commands and Results

| Command | Result |
|---|---|
| `python3 -m pytest Tests/test_shadow_cio_report.py Tests/test_shadow_scorecard_health.py -q` | PASS: `18 passed in 0.21s` |
| `python3 -m py_compile scripts/send_shadow_cio_report.py scripts/check_shadow_scorecard_health.py` | PASS |
| `python3 scripts/research/validate_sleeve_manifest.py --inventory` | PASS: validator emitted inventory JSON with `status=OK` |
| `python3 -m pytest Tests/test_sleeve_manifest.py -q` | PASS: `7 passed in 0.04s` |
| `python3 -m pytest Tests/test_sleeve_manifest.py Tests/test_strategy_registry.py -q` | LOCAL ENV BLOCKED: local Python imports incompatible `numpy` architecture through pandas |
| `python3 -m pytest Tests/test_research_registry_mcp_server.py -q` | LOCAL ENV BLOCKED in Prompt 1: missing local dependency `networkx` |
| VM `~/.venvs/quant-daily-report/bin/python3 -m pytest Tests/test_sleeve_manifest.py Tests/test_research_registry_mcp_server.py Tests/test_strategy_registry.py -q` | PASS: `39 passed in 3.66s` |
| `git diff --check` | PASS |
| Changed-file forbidden area check | PASS: no `paper/`, broker, cron, allocation, strategy registry, execution-order, or trading files changed |
| `git diff -- scripts/crontab.txt core/strategy_registry.py config/research/strategy_registry.json paper scripts/run_precomputed_alpaca_execution.py scripts/execute_alpaca_orders.py` | PASS: no diff |
| Audit-report JSON parse: `python3 -m json.tool reports/agent_loops/2026-06-14_active_fr_reconciliation/audit_manifest.json` | PASS |
| Audit-report whitespace check | PASS |

## Search Checks

Stale-string search:

```bash
rg -n 'YTD from `2026-05-12`|YTD from 2026-05-12|YTD \(from 2026-05-12\)|216ac5f|as of 2026-06-08|Research not started|BACKLOG_REVIEW|Primary active architecture/research workstream|primary active architecture/research workstream|pending a trial-key|trial key is not yet available' docs/governance docs/artifact_* reports/incidents reports/agent_loops/2026-06-13_shadow_nav_same_day_restatement scripts/send_shadow_cio_report.py Tests/test_shadow_cio_report.py
```

Remaining matches are intentional:

- `Tests/test_shadow_cio_report.py` contains a negative assertion that
  `YTD (from 2026-05-12)` is absent.
- `docs/governance/fr_archive/fr_067_stage0_source_comparison.md` retains
  historical pre-approval trial-key language in an archived source-comparison
  document.
- FR-057, FR-059, and FR-060 retain `status_review_needed` in the active backlog
  because audit evidence did not prove deployment.

Positive current-state search confirmed:

- `dated_same_day_close_to_close_v1` appears in roadmap/backlog/registry/context
  and artifact docs.
- `Since Observation Inception` appears in scorecard code and tests.
- FR-036b/c/d are documented as implemented/deployed-observing.
- FR-063 is documented as `ACTIVE_RESEARCH` supporting evidence.
- FR-069 remains research-only and Phase C owner-gated.
- FR-070 remains `DEPLOYED_OBSERVING` with next-run validation gates.

## Local Environment Notes

The local Python environment is not suitable for some broad research-registry
tests:

- `networkx` is missing for MCP imports.
- Local `numpy` is x86_64 while the running Python expects arm64, causing pandas
  import failure in strategy-registry tests.

The deployed VM virtualenv ran the registry/MCP/sleeve suite successfully:
`39 passed in 3.66s`.

## Safety Checks

- No cron file changed.
- No execution or broker file changed.
- No allocation, model, or strategy logic changed.
- No strategy registry lifecycle state changed.
- No new FR number introduced.
- No secrets were printed or committed.
