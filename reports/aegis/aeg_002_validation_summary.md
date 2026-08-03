# AEG-002 Validation Summary

As of: 2026-08-03T18:10:00Z
Branch: `agent/aeg-002-operationalize-aegis`
Current base: `origin/main` (`8423812`)

## Targeted Aegis and AIOPS validation

- `pytest -q Tests/test_aegis.py Tests/test_aegis_operational.py Tests/test_aiops_cli.py Tests/test_aiops_plan.py`
  - **33 passed**.
  - Covers mission/task lifecycle, v1→v2 migration, foreign keys, import
    idempotency and rollback, stable IDs, edge identity, task/hierarchy/graph
    cycle rejection, reconciliation, priority stability, decision persistence,
    brief reproducibility, persisted approval enforcement, REST, CLI, and the
    boundary scanner.
- Relevant AIOPS/Aegis, Alpha Lab v1/v2, execution-integrity,
  lifecycle/timeline, confirmation, transition determinism, cron-reference,
  and dashboard-shell contracts: **85 passed** with seven existing pandas
  future warnings.
- `python -m py_compile aiops/cli.py aiops/aegis/*.py scripts/validate_aegis_boundaries.py`: passed.
- `git diff --check`: passed.
- No repository format/lint command is configured; compilation, tests, JSON
  parsing, and diff checks were used.

## Boundary validation

- `python scripts/validate_aegis_boundaries.py --base origin/main`
  - `AEGIS_BOUNDARY_STATUS: CLEAN`.
- AST scan found no Aegis imports from broker, order-submission/execution,
  allocation, scheduler, paper, pilot, live, capital, OpenAI, or Anthropic
  modules.
- Changed-path scan found no execution, allocation, scheduler/cron, deployment,
  VM, paper, pilot, live, capital, or workflow path changes.
- No VM, broker, scheduler, deployment, or governed AIOPS dispatch command ran.

## Import and first-mission validation

- Live read-only GitHub import via authenticated `gh`: 13 open records.
- Repository evidence import: 92 taxonomy/strategy/FR records.
- Pinned Alpha Lab PR #160 import: 38 initiative, research-family, blocker,
  experiment, and owner-decision records from four governance files at commit
  `2b4f6c99216a2764d3692735f0e3f783ce7dca0a`.
- Total records inspected: 143; unresolved state: Atlas (no configured current
  repository evidence found).
- Dry-run completed without writes; actual import generated the required
  manifests, reconciliation outputs, source provenance, Mission Control, and
  approval-required consolidation mission `mission_32751b57af1889a0ea85`.
- The isolated Alpha Lab MVP mission is
  `mission_1ace1edede9d73889ccf`: 14 research-family rows, eight explicit
  blockers, and three evidence-backed PARK decisions. Source-reported state is
  explicitly dated 2026-07-24 even though the snapshot was captured later.
- Decision generation never pads a queue when persisted evidence supports fewer
  concrete decisions.
- A second live import against the same database and snapshot reported zero
  changed records for repository, GitHub, and Alpha Lab sources.
- JSON artifacts passed `python -m json.tool` parsing.

## Full-suite status

The full suite was attempted. It does **not** pass. To preserve actionable
output, the known parity file was also run separately.

- Full suite excluding `Tests/parity/test_paper_execution_parity.py`:
  **2813 passed, 18 failed, 1 skipped, 5 subtests passed**.
- The same 18 exact failing nodes were rerun on PR #167 head `ae29d8a`:
  **18 failed with the same assertions**, proving they predate AEG-002. They
  cover existing Argo/Phoenix expectations, differentiation fixtures, feedback
  index residue, alpha-variant strategy expectations, absent local PIT data,
  execution-target status expectations, and research-packet fixtures.
- Paper parity file: **3 passed, 8 failed** on AEG-002. The required synthetic
  scenario also fails identically on PR #167 head.
- Combined observed status: **2816 passed, 26 failed, 1 skipped**. No failing
  golden or source was modified.

### Paper-parity expected versus actual

For `test_paper_native_execution_matches_golden[2026_07_07_synthetic_38pos]`,
the recursive comparison has exactly two differences, both under
`inputs_summary.config`:

- Expected golden has no `allow_fractional_sells`; actual contains `false`.
- Expected golden has no `fractional_sell_min_trade_dollars`; actual contains
  `1.0`.

The same two-field mismatch reproduces at `ae29d8a`. Updating parity goldens was
not authorized and was not performed.
