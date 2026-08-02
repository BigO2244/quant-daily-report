# AEG-002 Validation Summary

As of: 2026-08-02T22:00:00Z
Branch: `agent/aeg-002-operationalize-aegis`
Stacked base: `origin/agent/aegis-control-plane-166` (`ae29d8a`)

## Targeted Aegis and AIOPS validation

- `pytest -q Tests/test_aegis.py Tests/test_aegis_operational.py Tests/test_aiops_cli.py Tests/test_aiops_plan.py`
  - **30 passed**.
  - Covers mission/task lifecycle, v1→v2 migration, foreign keys, import
    idempotency and rollback, stable IDs, edge identity, task/hierarchy/graph
    cycle rejection, reconciliation, priority stability, decision persistence,
    brief reproducibility, persisted approval enforcement, REST, CLI, and the
    boundary scanner.
- Relevant AIOPS/Aegis plus execution-integrity, lifecycle/timeline,
  confirmation, transition determinism, cron-reference, and dashboard-shell
  contracts: **72 passed**.
- `python -m py_compile aiops/cli.py aiops/aegis/*.py scripts/validate_aegis_boundaries.py`: passed.
- `git diff --check`: passed.
- No repository format/lint command is configured; compilation, tests, JSON
  parsing, and diff checks were used.

## Boundary validation

- `python scripts/validate_aegis_boundaries.py --base origin/agent/aegis-control-plane-166`
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
- Total records inspected: 105; unresolved state: Atlas (no configured current
  repository evidence found).
- Dry-run completed without writes; actual import generated the required
  manifests, reconciliation outputs, source provenance, Mission Control, five
  decision entries, and approval-required mission
  `mission_7d6814f51ff218af9539`.
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
