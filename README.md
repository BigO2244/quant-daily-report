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

## AIOps Codex Runner (Dispatch)

`aiops dispatch --run <RUN_ID>` attempts non-interactive Codex execution when a `codex` CLI is available on PATH.
If `codex` is missing, dispatch preserves fallback behavior by writing `reports/ai_runs/<RUN_ID>/codex_task.txt` and exiting non-zero.

Install Codex CLI (choose one):

```bash
brew install codex
# or
npm install -g @openai/codex
```

Validate installation:

```bash
which codex
codex --help
codex exec --help
```

Optional timeout for long Codex runs:

```bash
export AIOPS_CODEX_TIMEOUT_SECONDS=1800
```

AIOps full lifecycle command:

```bash
aiops run-all --spec specs/your_spec.md --mode BUILD
```

`run-all` exit codes:
- `0`: OK
- `2`: NEEDS_OPERATOR (codex missing; `codex_task.txt` written)
- `3`: VERIFY_FAILED
- `4`: PARSE_OR_PLAN_FAILED
- `5`: DISPATCH_FAILED
- `6`: RUN_FAILED

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

## Local Green Loop

```bash
bash scripts/local_green_loop.sh
```
