# AIOPS Evidence Schema (v0.1)

## Purpose

Define the minimum reproducibility contract for any governed run.

All Tier B/C runs should include structured evidence.

---

## Required Artifact Structure

reports/ai_runs/<RUN_ID>/

Required files:
- plan.md
- spec_snapshot.md
- run_all_summary.md

Optional (future enforcement):
- evidence.json
- verify_report.md
- diff.patch

---

## Recommended evidence.json Fields

{
  "run_id": "",
  "spec_path": "",
  "mode": "",
  "risk_tier": "",
  "spec_hash": "",
  "plan_hash": "",
  "git_sha": "",
  "exit_code": "",
  "pytest_passed": true,
  "verify_passed": true,
  "codex_version": "",
  "environment": {
    "python_version": "",
    "platform": ""
  }
}

---

## Determinism Rules

- No timestamps beyond RUN_ID.
- Stable ordering in markdown sections.
- No environment-dependent ordering.
- No randomness.

---

## Reproducibility Standard

A future session must be able to:
1. Read the spec.
2. Inspect artifacts.
3. Re-run the lifecycle.
4. Produce identical artifacts (excluding RUN_ID path).

If not reproducible, the run fails contract.

---

## Version

v0.1