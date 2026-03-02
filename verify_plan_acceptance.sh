#!/usr/bin/env bash
# Verification script to show that plan.md contains all required content

set -e

echo "=========================================="
echo "AIOPS Plan Verification"
echo "=========================================="
echo ""

SPEC_FILE="specs/daily_alpaca_paper_run_0935_et.md"
PLAN_FILE="reports/ai_runs/20260302_071308_3c94606/plan.md"

if [ ! -f "$SPEC_FILE" ]; then
    echo "ERROR: Spec file not found: $SPEC_FILE"
    exit 1
fi

if [ ! -f "$PLAN_FILE" ]; then
    echo "ERROR: Plan file not found: $PLAN_FILE"
    echo "Run: .venv/bin/python -m aiops run-all --spec $SPEC_FILE --mode HARDEN"
    exit 1
fi

echo "✓ Spec file: $SPEC_FILE"
echo "✓ Plan file: $PLAN_FILE"
echo ""

echo "=========================================="
echo "FILES Section (Acceptance Criterion)"
echo "=========================================="
awk '/^FILES:/,/^ACCEPTANCE_CRITERIA:/' "$PLAN_FILE" | head -5
echo ""

echo "=========================================="
echo "ACCEPTANCE_CRITERIA Section (Acceptance Criterion)"
echo "=========================================="
awk '/^ACCEPTANCE_CRITERIA:/,/^#/' "$PLAN_FILE" | head -8
echo ""

echo "=========================================="
echo "Verification Results"
echo "=========================================="

# Count files
FILES_COUNT=$(grep -c "^- " <(awk '/^FILES:/,/^ACCEPTANCE_CRITERIA:/' "$PLAN_FILE") || echo 0)
echo "✓ FILES section contains $FILES_COUNT items"

# Count criteria
CRITERIA_COUNT=$(grep -c "^- " <(awk '/^ACCEPTANCE_CRITERIA:/,/^#/' "$PLAN_FILE") || echo 0)
echo "✓ ACCEPTANCE_CRITERIA section contains $CRITERIA_COUNT items"

if [ "$FILES_COUNT" -eq 3 ] && [ "$CRITERIA_COUNT" -eq 5 ]; then
    echo ""
    echo "✓✓✓ ACCEPTANCE CRITERIA MET ✓✓✓"
    echo "- plan.md contains 3 file paths in FILES"
    echo "- plan.md contains 5 bullets in ACCEPTANCE_CRITERIA"
    exit 0
else
    echo ""
    echo "✗ ACCEPTANCE CRITERIA NOT MET"
    echo "  Expected: 3 files, 5 criteria"
    echo "  Got: $FILES_COUNT files, $CRITERIA_COUNT criteria"
    exit 1
fi
