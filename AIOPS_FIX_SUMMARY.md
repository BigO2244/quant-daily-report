# AIOPS Spec Parsing Fix - Deliverables Summary

## ✓ Completed Tasks

### 1. Identified Root Cause 
- **Issue:** Spec file had FILES and ACCEPTANCE_CRITERIA as top-level headers, but AIOPS parser expects level-2 markdown headings (`##`)
- **Parser Location:** 
  - Header parsing: [aiops/spec_parser.py](aiops/spec_parser.py) - extracts MODE, OBJECTIVE, etc.
  - Section parsing: [aiops/plan.py](aiops/plan.py) - `_extract_section()` function looks for `## SECTION_NAME` headings

### 2. Fixed Spec File Header Formatting
- **File:** [specs/daily_alpaca_paper_run_0935_et.md](specs/daily_alpaca_paper_run_0935_et.md)
- **Changes:**
  - Converted `FILES:` (top-level header) → `## FILES` (markdown heading)
  - Converted `ACCEPTANCE_CRITERIA:` → `## ACCEPTANCE CRITERIA` (markdown heading)
  - Preserved exact list item formatting
  - Maintained single blank line between headers and markdown content

### 3. Added Automated Spec Linting Test
- **File:** [Tests/test_spec_lint.py](Tests/test_spec_lint.py)
- **Tests:**
  - `test_spec_mode_and_objective_headers()` - Validates MODE/OBJECTIVE presence
  - `test_daily_alpaca_paper_spec_files_section()` - Validates FILES extraction
  - `test_daily_alpaca_paper_spec_acceptance_criteria_section()` - Validates ACCEPTANCE_CRITERIA extraction
- **Run Commands:**
  ```bash
  .venv/bin/python -m pytest Tests/test_spec_lint.py -v
  .venv/bin/python Tests/test_spec_lint.py  # Direct execution
  ```

### 4. Created Parser Grammar Documentation
- **File:** [AIOPS_SPEC_PARSER_GRAMMAR.md](AIOPS_SPEC_PARSER_GRAMMAR.md)
- **Contents:**
  - Detailed explanation of parser architecture (2-part system)
  - Required spec format with examples
  - Key grammar requirements
  - Validation procedures
  - Future prevention guidelines

### 5. Created Verification Script
- **File:** [verify_plan_acceptance.sh](verify_plan_acceptance.sh)
- **Purpose:** Demonstrates acceptance criteria compliance
- **Output:** Shows FILES and ACCEPTANCE_CRITERIA sections with item counts

---

## ✓ Acceptance Criteria Met

### Primary Criterion: Plan.md Content
```
✓ plan.md contains 3 file paths in FILES section:
  1. .github/workflows/daily_quant_report_premarket.yml
  2. .github/workflows/alpaca_paper_execute_open.yml
  3. specs/daily_alpaca_paper_run_0935_et.md

✓ plan.md contains 5 bullets in ACCEPTANCE_CRITERIA section:
  1. Pre-market report runs on trading days and delivers suggested trades before 09:35 ET (target 08:00 ET).
  2. Execution runs on trading days at 09:35 ET and routes orders to Alpaca PAPER endpoints only.
  3. Both jobs implement trading-day guard; weekends/holidays log SKIP and exit 0.
  4. Execution is idempotent per REPORT_DATE (no duplicate orders on rerun).
  5. Each run writes RUN_ID archive artifacts (report summary, orders/fills/recon, health payload).
```

### Command Verification
```bash
$ .venv/bin/python -m aiops run-all --spec specs/daily_alpaca_paper_run_0935_et.md --mode HARDEN
RUN_ID: 20260302_071308_3c94606
RUN_DIR: .../reports/ai_runs/20260302_071308_3c94606
PLAN_PATH: .../reports/ai_runs/20260302_071308_3c94606/plan.md
SPEC_SNAPSHOT_PATH: .../reports/ai_runs/20260302_071308_3c94606/spec_snapshot.md
```

✓ Spec parsing succeeds (parse stage = 0)
✓ Plan generation succeeds (plan stage = 0)
✓ plan.md contains non-empty FILES and ACCEPTANCE_CRITERIA

---

## Changes Summary

### Modified Files
1. **specs/daily_alpaca_paper_run_0935_et.md** - Spec header formatting fix
   - Removed: Top-level `FILES:` and `ACCEPTANCE_CRITERIA:` headers
   - Added: Level-2 markdown headings `## FILES` and `## ACCEPTANCE CRITERIA`
   - Impact: +1 blank line, ~1 line reordering (minimal/deterministic)

### New Files
1. **Tests/test_spec_lint.py** - Spec validation tests
2. **AIOPS_SPEC_PARSER_GRAMMAR.md** - Parser documentation
3. **verify_plan_acceptance.sh** - Acceptance verification script

### No Changes to Model Logic
- ✓ No changes to AIOPS parser logic
- ✓ No changes to trading/business logic
- ✓ No changes to test suite beyond adding spec lint tests
- ✓ Minimal, deterministic formatting changes only

---

## Test Results

```
$ .venv/bin/python -m pytest Tests/test_spec_lint.py -v
============================= test session starts ==============================
...
Tests/test_spec_lint.py::test_daily_alpaca_paper_spec_files_section PASSED
Tests/test_spec_lint.py::test_daily_alpaca_paper_spec_acceptance_criteria_section PASSED
Tests/test_spec_lint.py::test_spec_mode_and_objective_headers PASSED

============================== 3 passed in 0.04s ===============================
```

---

## Key Insight: AIOPS Parser Grammar

The AIOPS parser uses a **two-stage approach**:

1. **Stage 1 - Header Extraction** (for metadata)
   - Looks for `KEY: VALUE` patterns at the top of the file
   - Extracts: MODE, OBJECTIVE, PROJECT_TYPE, RISK_TIER
   
2. **Stage 2 - Section Extraction** (for plan content)
   - Looks for `## SECTION_NAME` markdown headings
   - Extracts content between headings
   - Used for: FILES, ACCEPTANCE CRITERIA

**Previous Error:** Spec had FILES and ACCEPTANCE_CRITERIA as top-level headers (Stage 1), but parser was looking for them as markdown headings (Stage 2), resulting in empty sections.

---

## Prevention & Maintenance

### For Future Specs
Use this template structure:
```markdown
MODE: <value>
OBJECTIVE: <description>

## FILES

- item1
- item2
- item3

## ACCEPTANCE CRITERIA

- criterion1
- criterion2
- criterion3

# Main Content Heading

... markdown content ...
```

### For Regression Detection
```bash
# Run spec validation before committing
.venv/bin/python -m pytest Tests/test_spec_lint.py -v

# Or run verification script
bash verify_plan_acceptance.sh
```

---

## Next Steps (Beyond Spec Parsing)

The full `aiops run-all` lifecycle involves:
1. ✓ **parse** - Extract headers (DONE)
2. ✓ **plan** - Generate plan.md (DONE)
3. **dispatch** - Execute code generation via codex (requires external tool)
4. **run** - Run implementation (requires codex changes)
5. **verify** - Validate results (depends on implementation)

The spec parsing fix (items 1-2) is complete. Items 3-5 are downstream and depend on the codex tool and change implementation.
