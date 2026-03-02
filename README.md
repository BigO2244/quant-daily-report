# Quant Daily Report

## Production scope

Mainline runtime is Alpha-only.
Legacy sleeves and legacy entrypoints are archived under `archive/` and are not part of production execution.
See `ARCHIVING.md` and `README_PROD.md`.

## Paper trading state

Paper broker runtime state (ledger/trades) is stored in `outputs/paper_state/` by default.
Set `PAPER_STATE_DIR` to override this location.
Legacy `paper/ledger.csv` and `paper/trades.csv` are treated only as one-time seed inputs when state files are first initialized.

## Run Archiving

Daily runs now use immutable canonical artifacts under `outputs/runs/<RUN_ID>/`.
`outputs/latest.json` is a mutable pointer to the latest run.
See `docs/run_archiving.md` for structure, integrity files, and reproduction steps.

## Alpha standalone report (local)

```bash
python3 scripts/alpha_report.py --apply-costs --cost-bps 25
python3 scripts/daily_alpha_run.py
SMTP_HOST=... SMTP_PORT=... SMTP_USER=... SMTP_PASSWORD=... REPORT_TO_EMAIL=... python3 scripts/email_alpha_report.py
```

Notes:
- Requires `matplotlib` (plus pandas/numpy).
- Uses artifacts in `outputs/research` as source inputs.

## Audit Workflows

Audit export and Monte Carlo workflows are documented in `docs/audit.md`.

## AIOps Lifecycle Commands

The AIOps module provides a fully orchestrated workflow for managing specifications, plans, and execution with deterministic contracts.

### Quick Start

```bash
# Parse and validate a spec
aiops parse specs/my_spec.md

# Generate a plan (prints 4 artifact lines to stdout)
aiops plan --spec specs/my_spec.md --mode BUILD

# Full lifecycle: parse → plan → dispatch → run → verify
aiops run-all --spec specs/my_spec.md --mode BUILD
```

### Commands

- **`aiops parse <spec>`** — Validate spec syntax and headers
- **`aiops plan <spec> [--mode MODE]`** — Create deterministic plan artifacts
- **`aiops verify <spec> [--mode MODE]`** — Run mode-gated verification checks
- **`aiops dispatch --run <RUN_ID>`** — Execute plan (codex or fallback)
- **`aiops run <spec> [--mode MODE]`** — Full workflow except verify
- **`aiops run-all <spec> --mode MODE`** — Fully orchestrated lifecycle

### Codex Installation

If Codex is available, `dispatch` will execute it automatically.
Install Codex CLI via Homebrew or npm:

```bash
brew install codex
# or
npm install -g @openai/codex

# Verify:
which codex && codex --help
```

### Exit Codes

`run-all` returns stable exit codes for CI/CD integration:

| Code | Status | Meaning |
|------|--------|---------|
| 0 | OK | All stages succeeded |
| 2 | NEEDS_OPERATOR | Codex unavailable; manual task file prepared |
| 3 | VERIFY_FAILED | Verify checks failed |
| 4 | PARSE_OR_PLAN_FAILED | Spec invalid or plan failed |
| 5 | DISPATCH_FAILED | Dispatch execution failed |
| 6 | RUN_FAILED | Run stage failed |

### Documentation

For complete contract specifications, workflow details, and troubleshooting:

- **[System Contract v0.1](specs/aiops_system_contract_v0_1.md)** — CLI contracts, exit codes, artifact formats
- **[Workflow & Operator Guide](docs/aiops_workflow.md)** — Commands, examples, recovery procedures
- **[Test Fixtures README](tests/fixtures/README.md)** — Golden test patterns and determinism guarantees

### Testing & CI Gates

Run the full test suite to validate AIOps contracts:

```bash
# All tests (including AIOps contract tests)
pytest -q

# AIOps contract tests only
pytest tests/test_aiops_contracts.py -v

# Specific contract category
pytest tests/test_aiops_contracts.py::TestContractPlanCommand -v
```

The contract test suite enforces:
- Deterministic CLI outputs (no random variance)
- Stable exit codes (0, 2, 3, 4, 5, 6)
- Golden outputs (plan.md, run_all_summary.md)
- No secrets in stdout/stderr
- Correct artifact formatting and directory structure

CI/CD should fail on `pytest -q` exit code != 0.


## Alpaca Paper Execution

Set credentials and paper toggle:

```bash
export TRADING_MODE=alpaca
export ALPACA_PAPER=1
export ALPACA_API_KEY_ID="YOUR_KEY"
export ALPACA_API_SECRET_KEY="YOUR_SECRET"
```

Smoke-test connectivity:

```bash
python3 alpaca_smoke_test.py
# or
python3 scripts/alpaca_smoke_test.py
```

Run a daily report:

```bash
REPORT_DATE=2026-02-24 PAPER_TRADING=1 MODE=SHADOW python3 daily_quant_report.py
```

Note: `REPORT_DATE` must be a real date string (`YYYY-MM-DD`), not placeholders like `YYYY-MM-DD`.

Email failure mode:
- `EMAIL_STRICT=0` (default): SMTP/email failures are warnings only and do not fail `daily_quant_report.py`.
- `EMAIL_STRICT=1`: SMTP/email failures are fatal and fail the run.
- `EMAIL_DRY_RUN=1`: skips SMTP sends while still producing persisted email artifacts.

## Local Green Loop

```bash
bash scripts/local_green_loop.sh
```
