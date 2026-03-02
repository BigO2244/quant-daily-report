# AIOPS Spec Parser Grammar & Fix Summary

## Issue
The `aiops run-all` command was generating plan.md with empty FILES and ACCEPTANCE_CRITERIA sections despite having these fields in the spec file.

**Initial Spec Format (Incorrect):**
```markdown
MODE: HARDEN
OBJECTIVE: ...
FILES:
  - item1
  - item2
ACCEPTANCE_CRITERIA:
  - criterion1
  - criterion2
```

**Problem:** The parser treated FILES and ACCEPTANCE_CRITERIA as top-level headers (KEY: value pairs), but the plan extraction logic looks for **markdown level-2 headings** (`## SECTION_NAME`), not top-level headers.

---

## AIOPS Parser Architecture

### Part 1: Header Extraction (spec_parser.py)
`parse_headers()` uses regex pattern `^([A-Z_]+):\s*(.+?)\s*$` to extract:
- **MODE** — Execution mode (EXPLORE, BUILD, HARDEN, DEPLOY)
- **OBJECTIVE** — Brief description of the change
- Optional: **PROJECT_TYPE**, **RISK_TIER** (required for VERIFY stage only)

These must be at the **top of the spec file** as simple KEY: value pairs.

### Part 2: Section Extraction (plan.py)
`_extract_section(spec_text, section_name)` uses regex pattern `^##\s+(.+?)\s*$` to:
1. Find level-2 markdown heading matching exact section name
2. Extract all content between that heading and the next heading (or EOF)
3. Strip leading/trailing whitespace

This extraction happens for:
- **FILES** — List of files affected by the change
- **ACCEPTANCE CRITERIA** — Success criteria/verification points

---

## Required Spec Format

```markdown
MODE: <mode_value>
OBJECTIVE: <objective_description>

## FILES

- file1.yml
- file2.py
- file3.md

## ACCEPTANCE CRITERIA

- First criterion here.
- Second criterion here.
- Third criterion here.

# Main Spec Content

... markdown content ...
```

**Key Requirements:**
1. `MODE` and `OBJECTIVE` are top-level headers (lines 1-2)
2. One blank line after `OBJECTIVE` before first markdown section
3. `## FILES` is a level-2 heading with list items below it
4. `## ACCEPTANCE CRITERIA` is a level-2 heading with list items below it
5. List items use standard markdown format: `- item text`
6. Section headings must match exactly (case-sensitive)

---

## Fix Applied

**File:** `specs/daily_alpaca_paper_run_0935_et.md`

Changed from:
```markdown
MODE: HARDEN
OBJECTIVE: ...
FILES:
  - .github/workflows/daily_quant_report_premarket.yml
  - .github/workflows/alpaca_paper_execute_open.yml
  - specs/daily_alpaca_paper_run_0935_et.md
ACCEPTANCE_CRITERIA:
  - Pre-market report runs...
  - Execution runs...
  - Both jobs implement...
  - Execution is idempotent...
  - Each run writes...

# Spec: Daily Alpaca Paper Run @ 9:35 ET...
```

To:
```markdown
MODE: HARDEN
OBJECTIVE: Auto-run Alpaca paper execution daily at 09:35 ET on trading days...

## FILES

- .github/workflows/daily_quant_report_premarket.yml
- .github/workflows/alpaca_paper_execute_open.yml
- specs/daily_alpaca_paper_run_0935_et.md

## ACCEPTANCE CRITERIA

- Pre-market report runs on trading days and delivers suggested trades before 09:35 ET (target 08:00 ET).
- Execution runs on trading days at 09:35 ET and routes orders to Alpaca PAPER endpoints only.
- Both jobs implement trading-day guard; weekends/holidays log SKIP and exit 0.
- Execution is idempotent per REPORT_DATE (no duplicate orders on rerun).
- Each run writes RUN_ID archive artifacts (report summary, orders/fills/recon, health payload).

# Spec: Daily Alpaca Paper Run @ 9:35 ET...
```

---

## Validation

A spec lint test was added to detect this issue in the future:

**File:** `Tests/test_spec_lint.py`

Tests verify:
1. `MODE` and `OBJECTIVE` headers are present
2. `FILES` section can be extracted and is non-empty
3. `ACCEPTANCE CRITERIA` section can be extracted and is non-empty

Run test:
```bash
.venv/bin/python -m pytest Tests/test_spec_lint.py -v
```

---

## Result

**Before Fix:**
```
plan.md FILES:
(empty)

plan.md ACCEPTANCE_CRITERIA:
(empty)
```

**After Fix:**
```
plan.md FILES:
- .github/workflows/daily_quant_report_premarket.yml
- .github/workflows/alpaca_paper_execute_open.yml
- specs/daily_alpaca_paper_run_0935_et.md

plan.md ACCEPTANCE_CRITERIA:
- Pre-market report runs on trading days and delivers suggested trades before 09:35 ET (target 08:00 ET).
- Execution runs on trading days at 09:35 ET and routes orders to Alpaca PAPER endpoints only.
- Both jobs implement trading-day guard; weekends/holidays log SKIP and exit 0.
- Execution is idempotent per REPORT_DATE (no duplicate orders on rerun).
- Each run writes RUN_ID archive artifacts (report summary, orders/fills/recon, health payload).
```

---

## Future Prevention

When creating or updating spec files:
1. Use `## FILES` and `## ACCEPTANCE CRITERIA` as markdown headings
2. Place them after `OBJECTIVE` header and before main content
3. Use standard markdown list format (`- item`) for entries
4. Run the spec lint test to validate:
   ```bash
   .venv/bin/python -m pytest Tests/test_spec_lint.py::test_daily_alpaca_paper_spec_files_section -v
   ```
