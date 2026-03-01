# AIOps Workflow & Operator Guide

**Version**: 1.0  
**Target Audience**: Operators, CI/CD engineers, developers  
**Date**: 2026-03-01

## Table of Contents

1. [Quick Start](#quick-start)
2. [Workflow Diagram](#workflow-diagram)
3. [Command Reference](#command-reference)
4. [Exit Codes & Recovery](#exit-codes--recovery)
5. [Determinism & Reproducibility](#determinism--reproducibility)
6. [Troubleshooting](#troubleshooting)

---

## Quick Start

### Installation

Ensure Python 3.11+ and git are available:
```bash
git clone <repo>
cd quant-daily-report-main
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

### Minimal Validation Run

```bash
# Parse spec to check headers
aiops parse specs/myspec.md

# Generate plan and print artifact paths
aiops plan --spec specs/myspec.md --mode BUILD

# Expected output (4 lines):
# RUN_ID: 20260301_143022_a1b2c3d
# RUN_DIR: /path/to/reports/ai_runs/20260301_143022_a1b2c3d
# PLAN_PATH: /path/to/reports/ai_runs/20260301_143022_a1b2c3d/plan.md
# SPEC_SNAPSHOT_PATH: /path/to/reports/ai_runs/20260301_143022_a1b2c3d/spec_snapshot.md
```

### Full Lifecycle (With Codex)

```bash
# One command: parse → plan → dispatch → run → verify
aiops run-all --spec specs/myspec.md --mode BUILD

# Exit code 0 = success
# Exit code 2 = needs operator (codex unavailable)
# Exit code 3 = verify failed
# Exit code 4 = parse/plan failed
# ...see table below for all codes

# Inspect summary:
cat reports/ai_runs/20260301_143022_a1b2c3d/run_all_summary.md
```

---

## Workflow Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                   aiops run-all                                 │
│    (Orchestrates full lifecycle with summary artifacts)         │
└──────────────────────┬──────────────────────────────────────────┘
                       │
         ┌─────────────┼─────────────┐
         │             │             │
         ▼             ▼             ▼
    [parse]       [plan]       [dispatch]
     (*.md)      (runs)        (codex/manual)
                   │
         ┌─────────┴─────────┐
         │                   │
         ▼                   ▼
      [run]              [verify]
    (execution)        (mode-gated)
         │                   │
         └─────────┬─────────┘
                   │
         ┌─────────▼─────────┐
         │ run_all_summary.md│
         │  + exit code      │
         └───────────────────┘

Entry Points:
• aiops parse <spec>              → Quick syntax check
• aiops plan <spec> [--mode ...]  → Generate artifacts only
• aiops verify <spec> [--mode ...]→ Gated checks only
• aiops dispatch --run <RUN_ID>   → Execute plan
• aiops run <spec> [--mode ...]   → parse + plan + dispatch (no run/verify)
• aiops run-all <spec> --mode ... → Full lifecycle
```

---

## Command Reference

### 1. `aiops parse <spec>`

**Purpose**: Validate spec syntax and required headers without side effects.

**Usage**:
```bash
aiops parse specs/aiops_system_contract_v0_1.md
```

**Output** (JSON, stdout):
```json
{
  "MODE": "HARDEN",
  "PROJECT_TYPE": "quant-research",
  "RISK_TIER": "high",
  "OBJECTIVE": "Harden AIOPS lifecycle..."
}
```

**Exit Codes**:
- `0`: Valid spec
- `1`: Invalid syntax, missing header, file not found

---

### 2. `aiops plan --spec <spec> [--mode <MODE>]`

**Purpose**: Create deterministic planning artifacts.

**Usage**:
```bash
aiops plan --spec specs/myspec.md
aiops plan --spec specs/myspec.md --mode HARDEN
```

**Output** (stdout, exactly 4 lines on success):
```
RUN_ID: 20260301_143022_a1b2c3d
RUN_DIR: /absolute/path/reports/ai_runs/20260301_143022_a1b2c3d
PLAN_PATH: /absolute/path/reports/ai_runs/20260301_143022_a1b2c3d/plan.md
SPEC_SNAPSHOT_PATH: /absolute/path/reports/ai_runs/20260301_143022_a1b2c3d/spec_snapshot.md
```

**Artifacts Created**:
- `reports/ai_runs/<RUN_ID>/plan.md` — Canonical plan with PLAN_HASH
- `reports/ai_runs/<RUN_ID>/spec_snapshot.md` — Snapshot of input spec
- `reports/ai_runs/<RUN_ID>/codex_task.txt` — Task file if applicable

**Exit Codes**:
- `0`: Plan created successfully
- `1`: Parse failed, mode invalid, or IO error

**Determinism Notes**:
- RUN_ID includes timestamp (YYYYMMDD_HHMMSS) + git short SHA
- Two calls within same second with same spec/mode produce identical plan content
- Plan hash deterministic (SHA256 of canonical body)

---

### 3. `aiops verify <spec> [--mode <MODE>]`

**Purpose**: Run mode-gated verification checks.

**Usage**:
```bash
aiops verify specs/myspec.md --mode BUILD
```

**Behavior by Mode**:
- **EXPLORE**: Light checks (syntax, headers, required sections)
- **BUILD**: Medium checks (add file consistency, code quality)
- **HARDEN**: Strict checks (all above + security, determinism, contracts)

**Exit Codes**:
- `0`: All checks passed
- `1`: One or more checks failed
- `2`: Operator intervention required (future use)

---

### 4. `aiops dispatch --run <RUN_ID>`

**Purpose**: Execute codex on plan task, or return NEEDS_OPERATOR if unavailable.

**Usage**:
```bash
# Extract RUN_ID from plan output
RUN_ID="20260301_143022_a1b2c3d"
aiops dispatch --run "$RUN_ID"
```

**Requires** (in `reports/ai_runs/<RUN_ID>/`):
- `plan.md` — Plan artifact from plan stage
- `codex_task.txt` — Task description

**Codex Detection**:
```bash
which codex  # If found on PATH, use it
# If not, return EXIT_NEEDS_OPERATOR (2) + write codex_task.txt
```

**Exit Codes**:
- `0`: Dispatch succeeded (codex executed or fallback prepared)
- `1`: Artifacts missing or dispatch failed
- `2`: NEEDS_OPERATOR (codex unavailable, task file prepared for manual run)
- `3`: Verify failed after successful dispatch

**Manual Dispatch** (if codex unavailable):
```bash
# Install codex
brew install codex
# OR
npm install -g @codex-js/cli

# Re-run dispatch
aiops dispatch --run "$RUN_ID"
```

---

### 5. `aiops run <spec> [--mode BUILD]`

**Purpose**: Convenience wrapper: parse → plan → dispatch (end-to-end, no verify).

**Usage**:
```bash
aiops run --spec specs/myspec.md --mode BUILD
```

**This is equivalent to**:
```bash
aiops parse specs/myspec.md && \
aiops plan --spec specs/myspec.md --mode BUILD && \
aiops dispatch --run <RUN_ID_from_plan_output>
```

**Exit Codes**: Propagates from subprocess stages (0, 1, 2, 3)

---

### 6. `aiops run-all --spec <spec> --mode <MODE>` ⭐

**Purpose**: Full lifecycle orchestration with deterministic summary.

**Usage**:
```bash
aiops run-all --spec specs/myspec.md --mode HARDEN
# Output: 4 artifact lines + final status
```

**Stages Executed** (in order, with early exit on failure):
1. **parse** - Validate spec headers
2. **plan** - Create artifacts, generate RUN_ID
3. **dispatch** - Execute codex or return NEEDS_OPERATOR
4. **run** - Execute model/analysis (if dispatch succeeded)
5. **verify** - Run mode-gated checks (if run succeeded)

**Stdout** (minimal):
```
RUN_ID: 20260301_143022_a1b2c3d
RUN_DIR: /path/reports/ai_runs/20260301_143022_a1b2c3d
PLAN_PATH: /path/reports/ai_runs/20260301_143022_a1b2c3d/plan.md
SPEC_SNAPSHOT_PATH: /path/reports/ai_runs/20260301_143022_a1b2c3d/spec_snapshot.md
RUN_ALL_STATUS: SUCCESS
```

**Artifacts**:
- All from parse, plan, dispatch, run, verify stages
- Plus `run_all_summary.md` with stage exit codes

**Exit Codes** (stable, deterministic):
| Code | Name | Meaning |
|------|------|---------|
| 0 | EXIT_OK | All stages succeeded |
| 2 | EXIT_NEEDS_OPERATOR | Dispatch needs operator (codex unavailable) |
| 3 | EXIT_VERIFY_FAILED | Verify failed after successful run |
| 4 | EXIT_PARSE_OR_PLAN_FAILED | Parse or plan stage failed |
| 5 | EXIT_DISPATCH_FAILED | Dispatch failed (not NEEDS_OPERATOR) |
| 6 | EXIT_RUN_FAILED | Run execution stage failed |

---

## Exit Codes & Recovery

### Exit 0: SUCCESS ✓

```bash
# Inspect summary to confirm all stages
cat reports/ai_runs/<RUN_ID>/run_all_summary.md
```

---

### Exit 2: NEEDS_OPERATOR

**Cause**: Codex CLI not found on PATH; manual execution required.

**Recovery**:
```bash
# Option 1: Install codex (Homebrew)
brew install codex
aiops run-all --spec specs/myspec.md --mode BUILD

# Option 2: Install codex (npm)
npm install -g @codex-js/cli
aiops run-all --spec specs/myspec.md --mode BUILD

# Option 3: Manual execution (if codex not available)
RUN_ID="20260301_143022_a1b2c3d"
cat reports/ai_runs/$RUN_ID/codex_task.txt
# Read task, execute manually in external tool, return results to run directory
```

---

### Exit 3: VERIFY_FAILED

**Cause**: One or more verification checks failed after run succeeded.

**Recovery**:
```bash
# Re-run verify to see which check failed
RUN_ID="20260301_143022_a1b2c3d"
aiops verify specs/myspec.md --mode HARDEN

# Review run_all_summary.md to understand which stage failed
cat reports/ai_runs/$RUN_ID/run_all_summary.md
```

---

### Exit 4: PARSE_OR_PLAN_FAILED

**Cause**: Spec syntax invalid, required header missing, or plan IO error.

**Recovery**:
```bash
# Validate spec syntax first
aiops parse specs/myspec.md

# Check spec file
cat specs/myspec.md | head -10  # Verify MODE, PROJECT_TYPE, RISK_TIER, OBJECTIVE headers

# Check disk space / permissions
ls -la reports/ai_runs/
du -sh reports/

# Re-run plan with verbose error
aiops plan --spec specs/myspec.md --mode BUILD
```

---

### Exit 5: DISPATCH_FAILED

**Cause**: Codex execution failed (timeout, crashed, invalid task format).

**Recovery**:
```bash
# Check task file
RUN_ID="20260301_143022_a1b2c3d"
cat reports/ai_runs/$RUN_ID/codex_task.txt

# Check codex installation
which codex && codex --version

# Re-run dispatch with increased timeout
CODEX_TIMEOUT_SECONDS=3600 aiops dispatch --run "$RUN_ID"
```

---

### Exit 6: RUN_FAILED

**Cause**: Execution stage (trading logic, analysis, etc.) failed.

**Recovery**:
```bash
# Check run directory for logs
RUN_ID="20260301_143022_a1b2c3d"
ls -la reports/ai_runs/$RUN_ID/

# Inspect stderr logs if available
cat reports/ai_runs/$RUN_ID/run.log  # (if logging enabled)

# Re-run in isolation for debugging
aiops run --spec specs/myspec.md --mode BUILD
```

---

## Determinism & Reproducibility

### Why Determinism Matters

1. **CI/CD repeatability**: Same spec, same outputs (no random variance)
2. **Audit trail**: Hashing plan content enables change detection
3. **Contract enforcement**: Tests can validate exact output format

### Determinism Guarantees

✓ **RUN_ID deterministic within 1-second window**
```bash
# Run plan twice rapidly with same spec
aiops plan --spec specs/myspec.md --mode BUILD  # RUN_ID: 20260301_143022_abc1234
aiops plan --spec specs/myspec.md --mode BUILD  # RUN_ID: 20260301_143022_abc1234 (same)
```

✓ **Plan hash stable across runs**
```bash
# Same spec → same plan.md content
# plan.md includes PLAN_HASH footer for verification
grep "PLAN_HASH:" reports/ai_runs/*/plan.md
```

✓ **No timestamps beyond RUN_ID**
- run_all_summary.md has no datetime fields (only RUN_ID and stage codes)
- Artifacts sorted in stable order

✓ **No randomness in sorting or selection**
- Files section extracted in order from spec
- Acceptance criteria in order from spec
- Stage results table row order: parse, plan, dispatch, run, verify

### Testing Determinism

```bash
# Golden test example
python -m pytest tests/test_aiops_contracts.py::test_plan_deterministic -v

# Full contract test suite
pytest tests/test_aiops_contracts.py -v
```

---

## Troubleshooting

### Common Issues

#### Issue: `ERROR: Spec file not found`

```bash
# Check spec path
ls -la specs/myspec.md

# Use absolute path if relative path unclear
aiops parse /full/path/to/specs/myspec.md
```

#### Issue: `ERROR: MODE not found in spec`

```bash
# Check spec headers
head -20 specs/myspec.md

# Spec must contain:
# MODE: BUILD
# PROJECT_TYPE: quant-research
# RISK_TIER: high
# OBJECTIVE: ...
```

#### Issue: `ERROR: Reports directory not writable`

```bash
# Check permissions
ls -ld reports/ai_runs/

# Fix permissions
chmod 755 reports/ai_runs

# Or create if missing
mkdir -p reports/ai_runs
```

#### Issue: Exit code 2 but codex available

```bash
# Verify codex on PATH
which codex  # Should return path
codex --help

# Check shebang/executable
file $(which codex)

# If still not found, reinstall
brew uninstall codex
brew install codex
```

#### Issue: `run_all_summary.md` not created

```bash
# Check if run directory was created
ls -la reports/ai_runs/

# Summary.md should be created even on failure
# If missing, check disk space and permissions
df -h reports/
```

---

## Integration with CI/CD

### GitHub Actions Example

```yaml
name: AIOps HARDEN

on:
  push:
    branches: [ main ]

jobs:
  harden:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2
      - uses: actions/setup-python@v2
        with:
          python-version: '3.11'
      
      - name: Install dependencies
        run: |
          pip install -e .
      
      - name: Run aiops full lifecycle
        run: |
          aiops run-all --spec specs/aiops_system_contract_v0_1.md --mode HARDEN
      
      - name: Fail on contract breach
        if: failure()
        run: |
          cat reports/ai_runs/*/run_all_summary.md
          exit 1
```

### Local Pre-Commit

```bash
#!/bin/bash
# .githooks/pre-commit

set -e

echo "Running AIOps contract validation..."
python -m pytest tests/test_aiops_contracts.py -q --tb=short

if [ $? -ne 0 ]; then
  echo "❌ Contract tests failed. Commit blocked."
  exit 1
fi

echo "✓ All contracts validated."
exit 0
```

---

## Glossary

| Term | Definition |
|------|-----------|
| **Spec** | Markdown file with MODE, PROJECT_TYPE, RISK_TIER, OBJECTIVE headers + sections |
| **RUN_ID** | Unique identifier: YYYYMMDD_HHMMSS_<git_short_sha> |
| **Plan** | Canonical artifact: plan.md with extracted sections + PLAN_HASH |
| **Dispatch** | Stage that executes codex or returns NEEDS_OPERATOR |
| **Golden test** | Test validating deterministic output against known-good artifact |
| **NEEDS_OPERATOR** | Exit code 2; indicates manual intervention required (e.g., codex missing) |

---

## Next Steps

- See [System Contract v0.1](../specs/aiops_system_contract_v0_1.md) for detailed exit code semantics
- Read [Tests README](../tests/fixtures/README.md) for test fixture structure
- See `pytest -q` for all test results and coverage

