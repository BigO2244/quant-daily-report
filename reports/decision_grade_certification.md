# Decision-Grade Certification

Date: 2026-06-22
Program: Decision-Grade PIT Research Infrastructure Completion
Phase: 6 - Decision-Grade Certification
Governance label: RESEARCH_ONLY / NON_EXECUTIONAL
Runtime impact: none
Result: FAIL

## Executive Summary

The program cannot proceed to conviction-allocation rebaseline or sleeve-promotion rebaseline. The new infrastructure is security_id keyed, reproducible, and useful, but it is not decision-grade because the current `caerus_large_cap` universe family is based on current `scalemarketcap`, not date-effective DAILY market cap.

## Formal Certifier Result

Command:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python3 scripts/research/certify_canonical_replay.py \
  --panel outputs/research/canonical_pit_replay/2026-06-22/price_panel.parquet \
  --manifest outputs/research/canonical_pit_replay/2026-06-22/manifest.json \
  --output outputs/research/canonical_pit_replay/2026-06-22/decision_grade_certification_result.json \
  --require-decision-grade-scale
```

Result:

```json
{
  "status": "FAIL",
  "decision_grade_status": "FAIL",
  "findings": [
    "PIT_EXACT_LARGE_CAP_DAILY_MARKETCAP_MISSING"
  ],
  "warnings": [
    "MEMBERSHIP_SCALE_NOT_PIT_EXACT:PIT_APPROXIMATE_SCALE"
  ]
}
```

## Adversarial Review

| Category | Verdict | Finding |
| --- | --- | --- |
| Survivorship contamination | PARTIAL | Delisted securities are included, but historical membership still depends on current scale |
| Look-ahead contamination | FAIL | Current `scalemarketcap` can project later/current size status backward |
| Universe contamination | FAIL | `caerus_large_cap` is `PIT_APPROXIMATE_SCALE`, not DAILY-marketcap PIT-exact |
| Pricing contamination | PARTIAL | SEP closeadj is complete for the family, but terminal delisting-return/action enrichment is not fully certified |
| Replay contamination | PARTIAL | Canonical tapes are security_id keyed, but downstream allocation replay is not yet certified |
| Security-id keying | PASS | Panel and tapes use `security_id`, with zero duplicate `date, security_id` rows |
| Reproducibility | PASS | Decision tape rebuild produced identical tape hashes |
| Lineage | PASS after correction | Phase 2 report and old Phase 1 gate report were updated to match final artifacts and supersession state |

## Current Certified Artifacts

| Artifact | Status |
| --- | --- |
| `Universe(as_of_date, "caerus_large_cap")` resolver | PASS for existing artifact wiring |
| Security_id replay price panel | PASS / decision-grade PARTIAL |
| Decision tapes | PASS / decision-grade PARTIAL |
| Canonical allocator baseline spec | PASS |
| Exposure-matched framework | PASS |
| Decision-grade replay certification | FAIL |

## Why Phase 7 And Phase 8 Are Blocked

Phase 7 requires Phase 6 PASS. Phase 6 failed. Therefore:

- conviction allocation was not re-run;
- prior conviction findings were not promoted, defended, or rejected under decision-grade evidence;
- sleeve-promotion rebaseline was not run;
- no promotion-readiness conclusions changed;
- no production allocator, execution, broker, scheduler, paper/live, or registry behavior changed.

## Required Remediation

1. Obtain or build a date-effective SHARADAR/DAILY market-cap cache.
2. Rebuild `caerus_large_cap` from DAILY numeric market cap by decision date.
3. Re-run `Universe(as_of_date, "caerus_large_cap")` certification against the PIT-exact family.
4. Rebuild the canonical replay panel.
5. Rebuild decision tapes.
6. Re-run decision-grade certification.
7. Proceed to Phase 7 only if certification result is PASS.

## Gate

FAIL. Stop before Phase 7.
